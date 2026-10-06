"""Fail-fast CUDA build prerequisites and separately hash-pinned build tools.

This module imports only the standard library (and the stdlib-only envsetup).
The system audit and compile/link probes run before pip or any source checkout.
The probe executables are never run: no CUDA kernel or inference is launched.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import envsetup, layout

LOCK_PATH = Path(__file__).resolve().parents[1] / "requirements-build-tools.lock.txt"
PACKAGE_PINS = {"cmake": "3.31.6", "ninja": "1.11.1.3"}
WHEEL_HASHES = {
    "cmake": "1c8b05df0602365da91ee6a3336fe57525b137706c4ab5675498f662ae1dbcec",
    "ninja": "a27e78ca71316c8654965ee94b286a98c83877bfebe2607db96897bbfe458af0",
}
# The Python wheel's packaging revision is not the Ninja executable version.
EXECUTABLE_VERSIONS = {"cmake": "cmake version 3.31.6", "ninja": "1.11.1.git.kitware.jobserver-1"}
VENV_RUNTIME_SCRIPT = ("import json,platform,sys;print(json.dumps([platform.python_implementation(),"
                       "list(sys.version_info[:2]),sys.platform,platform.machine().lower(),sys.maxsize>2**32]))")
SYSTEM_COMMANDS = {
    "git": ["--version"], "gcc": ["--version"], "g++": ["--version"],
    "nvcc": ["--version"],
    "nvidia-smi": ["--query-gpu=name,driver_version", "--format=csv,noheader"],
}
CUDA_ARCH = "80"  # This reviewed candidate is scoped to the A100's numeric SM 80.
C_SOURCE = """#include <stdint.h>
#include <stdatomic.h>
int main(void) { atomic_int value = 0; atomic_store(&value, (int32_t)1); return atomic_load(&value) - 1; }
"""
CXX_SOURCE = """#include <iostream>
#include <string>
#include <thread>
#include <vector>
int main() { std::vector<std::string> v{"toolchain"}; std::thread t([] {}); t.join(); std::cout << v.at(0); }
"""
CUDA_SOURCE = """#include <cuda_runtime.h>
#include <cublas_v2.h>
#include <cuda.h>
__global__ void toolchain_device_compile_only(int *value) { *value = 1; }
int main() {
    int version = 0; cublasHandle_t handle = nullptr;
    cudaRuntimeGetVersion(&version); cublasCreate(&handle);
    return static_cast<int>(cuInit(0));
}
"""


class ToolchainError(RuntimeError):
    """A missing, stale or unusable build prerequisite: stop before downloading."""


def _capture(cmd: list[str], *, env: dict | None = None, timeout: float = 20) -> dict:
    try:
        proc = subprocess.run([str(x) for x in cmd], capture_output=True, text=True,
                              timeout=timeout, check=False, env=env)
        output = (proc.stdout + proc.stderr).strip()
        return {"ok": proc.returncode == 0, "returncode": proc.returncode, "detail": output[-4000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[-4000:]}


def audit_system(root: Path) -> dict:
    """Collect all missing system prerequisites; never install or contact a host.

    All independent compile/link checks are attempted when their compiler is
    available, even if another prerequisite failed, to report useful failures
    together. Neither produced executable is run.
    """
    version = ".".join(str(v) for v in sys.version_info[:2])
    runtime_ok = (version == "3.11" and platform.python_implementation() == "CPython"
                  and sys.platform == "linux" and platform.machine().lower() in {"x86_64", "amd64"}
                  and sys.maxsize > 2**32)
    checks = {"runtime": {"ok": runtime_ok,
                          "detail": f"{platform.python_implementation()} {version}; {sys.platform}; {platform.machine()} "
                                    "(requires CPython 3.11, 64-bit Linux x86_64)"}}
    paths = {}
    for name, args in SYSTEM_COMMANDS.items():
        path = shutil.which(name)
        paths[name] = path
        checks[name] = ({"path": path, **_capture([path, *args])} if path
                        else {"ok": False, "path": None, "detail": f"{name} not found on PATH"})
        if name == "nvidia-smi" and checks[name]["ok"] and not checks[name]["detail"]:
            checks[name] = {**checks[name], "ok": False, "detail": "nvidia-smi returned no GPU/driver information"}
    work = Path(root) / "runs"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="toolchain-", dir=work) as temporary:
        tmp = Path(temporary)
        for name, source, text, compiler, options in (
            ("c_compile_link", "smoke.c", C_SOURCE, "gcc", ["-std=c11"]),
            ("cxx_compile_link", "smoke.cpp", CXX_SOURCE, "g++", ["-std=c++17", "-pthread"]),
            ("cuda_compile_link", "smoke.cu", CUDA_SOURCE, "nvcc", ["--cudart=shared", "-std=c++17", f"-arch=sm_{CUDA_ARCH}"]),
        ):
            if not paths[compiler] or (compiler == "nvcc" and not paths["g++"]):
                checks[name] = {"ok": False, "detail": "compiler missing; compile/link probe not run"}
                continue
            src, output = tmp / source, tmp / name
            src.write_text(text)
            link_options = []
            if compiler == "nvcc":
                cuda_root = Path(paths["nvcc"]).resolve().parent.parent
                stub_dirs = (cuda_root / "lib64/stubs", cuda_root / "targets/x86_64-linux/lib/stubs")
                # Driver stubs are for this link check only, never LD_LIBRARY_PATH.
                options = [*options, "-ccbin", paths["g++"]]
                link_options = [*(f"-L{p}" for p in stub_dirs if p.is_dir()), "-lcublas", "-lcuda"]
            cmd = [paths[compiler], *options, str(src), "-o", str(output), *link_options]
            result = _capture(cmd, timeout=90)
            if result["ok"] and not output.is_file():
                result = {**result, "ok": False, "detail": "compiler succeeded but linked executable is missing"}
            checks[name] = {**result, "executed": False}
    return {"ok": all(item["ok"] for item in checks.values()), "checks": checks}


def lock_sha256(lock: Path | None = None) -> str:
    """Validate the bounded two-wheel lock, then return its provenance hash."""
    lock = Path(lock or LOCK_PATH)
    try:
        contents = lock.read_bytes()
        text = contents.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ToolchainError(f"cannot read build-tool lock: {exc}") from exc
    logical = re.sub(r"\\\s*\n", " ", text)
    lines = [" ".join(line.split()) for line in logical.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    expected = [f"{name}=={version} --hash=sha256:{WHEEL_HASHES[name]}" for name, version in PACKAGE_PINS.items()]
    if sorted(lines) != sorted(expected):
        raise ToolchainError("build-tool lock differs from the reviewed cmake/ninja versions and Linux x86_64 wheel hashes")
    return hashlib.sha256(contents).hexdigest()


def install_env(root: Path) -> dict[str, str]:
    """Ignore pip configuration, alternate indexes, user packages and caches."""
    return {**envsetup.activation_env(root), **envsetup.PIP_ENV, "PIP_CONFIG_FILE": os.devnull,
            "PIP_INDEX_URL": "https://pypi.org/simple", "PIP_EXTRA_INDEX_URL": "",
            "PIP_FIND_LINKS": "", "PIP_TRUSTED_HOST": "", "PYTHONPATH": ""}


def install_cmd(root: Path, lock: Path | None = None, *, host_pip: bool = False) -> list[str]:
    prefix = ([sys.executable, "-m", "pip", "--python", str(envsetup.venv_python(root))] if host_pip
              else [str(envsetup.venv_python(root)), "-m", "pip"])
    return [*prefix, "--isolated", "install", "--index-url", "https://pypi.org/simple", "--require-hashes",
            "--only-binary=:all:", "--no-deps", "--no-cache-dir", "--force-reinstall", "-r", str(lock or LOCK_PATH)]


def _inspect_venv_runtime(root: Path, env: dict) -> dict:
    result = _capture([str(envsetup.venv_python(root)), "-I", "-c", VENV_RUNTIME_SCRIPT], env=env)
    try:
        identity = json.loads(result["detail"]) if result["ok"] else None
    except (ValueError, TypeError):
        identity = None
    valid = (isinstance(identity, list) and len(identity) == 5
             and identity[:3] == ["CPython", [3, 11], "linux"]
             and identity[3] in {"x86_64", "amd64"} and identity[4] is True)
    return {**result, "ok": bool(result["ok"] and valid), "identity": identity}


def _ensure_venv(r) -> bool:
    """Create the shared app venv early; return whether host pip is needed."""
    env = install_env(r.root)
    if not envsetup.venv_python(r.root).exists():
        if r.run("net", envsetup.create_cmd(r.root), timeout=90, env=env, check=False) != 0:
            # Reusing the incomplete venv is safe; do not delete existing user data.
            r.run("net", envsetup.create_cmd(r.root, without_pip=True), timeout=90, env=env)
    isolated, why = envsetup.is_isolated(r.root)
    if not isolated:
        raise ToolchainError(f"build tools require an isolated venv: {why}")
    runtime = _inspect_venv_runtime(r.root, env)
    if not runtime["ok"]:
        raise ToolchainError("workspace venv must use CPython 3.11, 64-bit Linux x86_64: " + runtime["detail"])
    has_pip = _capture([str(envsetup.venv_python(r.root)), "-I", "-m", "pip", "--version"], env=env)["ok"]
    if not has_pip:
        check = _capture([sys.executable, "-m", "pip", "--python", str(envsetup.venv_python(r.root)), "--version"], env=env)
        if not check["ok"]:
            raise ToolchainError("venv pip is unavailable and host pip cannot target the venv with --python: " + check["detail"])
    return not has_pip


def inspect_tools(root: Path) -> dict:
    """Check installed package revisions and actual venv/bin executables."""
    env = install_env(root)
    isolated, why = envsetup.is_isolated(root)
    checks = {"isolated_venv": {"ok": isolated, "detail": why},
              "venv_runtime": _inspect_venv_runtime(root, env)}
    script = ("import importlib.metadata as m,json;print(json.dumps({n:m.version(n) "
              "for n in ('cmake','ninja')}))")
    package = _capture([str(envsetup.venv_python(root)), "-I", "-c", script], env=env)
    try:
        versions = json.loads(package["detail"]) if package["ok"] else {}
    except (ValueError, TypeError):
        versions = {}
    checks["package_versions"] = {"ok": versions == PACKAGE_PINS, "versions": versions,
                                  "detail": "exact package versions verified" if versions == PACKAGE_PINS else package["detail"]}
    for name, expected in EXECUTABLE_VERSIONS.items():
        path = envsetup.venv_dir(root) / "bin" / name
        selected = shutil.which(name, path=env["PATH"])
        if selected != str(path) or not path.is_file():
            checks[name] = {"ok": False, "path": str(path), "detail": f"{name} missing from the workspace venv/bin"}
            continue
        result = _capture([str(path), "--version"], env=env)
        first_line = result["detail"].splitlines()[0] if result["detail"] else ""
        checks[name] = {**result, "path": str(path), "version": first_line,
                        "ok": result["ok"] and first_line == expected}
    return {"ok": all(item["ok"] for item in checks.values()), "checks": checks}


def _write_receipt(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _failures(result: dict) -> str:
    return "; ".join(f"{name}: {item.get('detail', 'failed')}" for name, item in result["checks"].items() if not item["ok"])


def prepare(r) -> dict:
    """Early net-stage gate: audit first, then install only the small tool lock."""
    path = Path(r.runs) / "toolchain.json"
    report = {"schema_version": 1, "state": "checking", "root": str(Path(r.root).resolve()),
              "lock_file": LOCK_PATH.name, "package_pins": PACKAGE_PINS,
              "cuda_probe_executed": False, "cuda_compile_arch": CUDA_ARCH, "system_packages_installed": False}
    _write_receipt(path, report)  # invalidate a stale success before doing any work
    try:
        report["system"] = audit_system(r.root)
        if not report["system"]["ok"]:
            raise ToolchainError("system prerequisites failed; no dependencies downloaded: " + _failures(report["system"]))
        report["lock_sha256"] = lock_sha256()
        host_pip = _ensure_venv(r)
        r.run("net", install_cmd(r.root, host_pip=host_pip), timeout=300, env=install_env(r.root))
        report["tools"] = inspect_tools(r.root)
        if not report["tools"]["ok"]:
            raise ToolchainError("pinned build tools not ready: " + _failures(report["tools"]))
        report["state"] = "ready"
        _write_receipt(path, report)
        return report
    except Exception as exc:
        report.update(state="fail", error=str(exc))
        _write_receipt(path, report)
        if isinstance(exc, ToolchainError):
            raise
        raise ToolchainError(f"toolchain preparation failed: {exc}") from exc


def assert_ready(root: Path | None = None) -> dict[str, str]:
    """Refuse clone/build on a missing/stale receipt or changed prerequisites.

    No repair, pip or network is performed here. Return the actual subprocess
    environment; the caller must apply it or pass it into each subprocess.
    """
    root = layout.paths(root)["root"]
    try:
        receipt = json.loads((root / "runs" / "toolchain.json").read_text())
    except (OSError, ValueError) as exc:
        raise ToolchainError("toolchain receipt missing or invalid; run the net-stage preflight first") from exc
    if (not isinstance(receipt, dict) or receipt.get("schema_version") != 1 or receipt.get("state") != "ready"
            or receipt.get("root") != str(root.resolve()) or receipt.get("lock_sha256") != lock_sha256()
            or receipt.get("package_pins") != PACKAGE_PINS):
        raise ToolchainError("toolchain receipt is stale or failed; rerun the net-stage preflight before build")
    system, tools = audit_system(root), inspect_tools(root)
    if not system["ok"] or not tools["ok"]:
        raise ToolchainError("build prerequisites changed: " + "; ".join(filter(None, (_failures(system), _failures(tools)))))
    return envsetup.activation_env(root)
