"""Offline toolchain regression checks; opt in to local-wheel CPU integration.

QMC_BUILD_TOOL_WHEEL_DIR may name an already-downloaded directory containing
both reviewed wheels. The opt-in test uses --no-index and never downloads.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from qmc_runpod import envsetup, toolchain


def _valid_runtime(monkeypatch):
    monkeypatch.setattr(toolchain.sys, "version_info", (3, 11, 0))
    monkeypatch.setattr(toolchain.sys, "platform", "linux")
    monkeypatch.setattr(toolchain.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(toolchain.platform, "machine", lambda: "x86_64")


def _passing():
    return {"ok": True, "checks": {"test": {"ok": True, "detail": "mocked CPU fixture"}}}


def _runner(tmp_path):
    root = tmp_path / "workspace"
    return SimpleNamespace(root=root, runs=root / "runs", run=lambda *args, **kw: pytest.fail("unexpected command"))


def _make_venv(root):
    path = envsetup.venv_dir(root)
    (path / "bin").mkdir(parents=True, exist_ok=True)
    (path / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    (path / "bin/python").write_text("#!/bin/sh\nexit 0\n")
    (path / "bin/python").chmod(0o755)


def _make_tools(root, versions=None):
    for name, version in (versions or toolchain.EXECUTABLE_VERSIONS).items():
        path = envsetup.venv_dir(root) / "bin" / name
        path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
        path.chmod(0o755)


def _fake_package_capture(monkeypatch):
    capture = toolchain._capture

    def fake(cmd, **kw):
        if "-c" in cmd and cmd[cmd.index("-c") + 1] == toolchain.VENV_RUNTIME_SCRIPT:
            return {"ok": True, "detail": '["CPython",[3,11],"linux","x86_64",true]'}
        if "-c" in cmd and "importlib.metadata" in cmd[cmd.index("-c") + 1]:
            return {"ok": True, "detail": json.dumps(toolchain.PACKAGE_PINS)}
        if "pip" in cmd and "--version" in cmd:
            return {"ok": True, "detail": "pip test fixture"}
        return capture(cmd, **kw)
    monkeypatch.setattr(toolchain, "_capture", fake)


def test_lock_has_exact_reviewed_versions_and_wheel_hashes(tmp_path):
    assert len(toolchain.lock_sha256()) == 64
    lock = tmp_path / "lock.txt"
    contents = toolchain.LOCK_PATH.read_text()
    lock.write_text(contents.replace(toolchain.WHEEL_HASHES["ninja"], "0" * 64))
    with pytest.raises(toolchain.ToolchainError, match="reviewed"):
        toolchain.lock_sha256(lock)
    lock.write_text(contents + "\n--extra-index-url https://example.invalid/simple\n")
    with pytest.raises(toolchain.ToolchainError, match="reviewed"):
        toolchain.lock_sha256(lock)


def test_install_command_is_binary_hash_locked_and_official_index_only(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://example.invalid/simple")
    monkeypatch.setenv("PIP_FIND_LINKS", "/unreviewed")
    cmd = toolchain.install_cmd(tmp_path)
    assert cmd[:3] == [str(envsetup.venv_python(tmp_path)), "-m", "pip"]
    for flag in ("--isolated", "--require-hashes", "--only-binary=:all:", "--no-deps", "--no-cache-dir", "--force-reinstall"):
        assert flag in cmd
    assert cmd[cmd.index("--index-url") + 1] == "https://pypi.org/simple"
    assert cmd[-2:] == ["-r", str(toolchain.LOCK_PATH)]
    assert toolchain.install_cmd(tmp_path, host_pip=True)[:5] == [sys.executable, "-m", "pip", "--python", str(envsetup.venv_python(tmp_path))]
    env = toolchain.install_env(tmp_path)
    assert env["PIP_CONFIG_FILE"] == os.devnull
    assert env["PIP_EXTRA_INDEX_URL"] == env["PIP_FIND_LINKS"] == env["PIP_TRUSTED_HOST"] == ""
    assert env["PATH"].split(os.pathsep)[0] == str(envsetup.venv_dir(tmp_path) / "bin")


def test_all_missing_system_prerequisites_fail_together_before_venv_or_download(tmp_path, monkeypatch):
    _valid_runtime(monkeypatch)
    monkeypatch.setattr(toolchain.shutil, "which", lambda *args, **kw: None)
    monkeypatch.setattr(toolchain, "_capture", lambda *args, **kw: pytest.fail("missing tool must not be run"))
    r = _runner(tmp_path)
    with pytest.raises(toolchain.ToolchainError) as error:
        toolchain.prepare(r)
    assert all(f"{name} not found on PATH" in str(error.value) for name in toolchain.SYSTEM_COMMANDS)
    assert "no dependencies downloaded" in str(error.value)
    assert not envsetup.venv_dir(r.root).exists()
    receipt = json.loads((r.runs / "toolchain.json").read_text())
    assert receipt["state"] == "fail" and not receipt["system"]["ok"]
    assert not receipt["cuda_probe_executed"] and not receipt["system_packages_installed"]


@pytest.mark.parametrize("version,system,machine", [((3, 12), "linux", "x86_64"), ((3, 11), "darwin", "x86_64"),
                                                    ((3, 11), "linux", "aarch64")])
def test_unsupported_python_or_platform_is_reported_with_missing_tools(tmp_path, monkeypatch, version, system, machine):
    _valid_runtime(monkeypatch)
    monkeypatch.setattr(toolchain.sys, "version_info", version)
    monkeypatch.setattr(toolchain.sys, "platform", system)
    monkeypatch.setattr(toolchain.platform, "machine", lambda: machine)
    monkeypatch.setattr(toolchain.shutil, "which", lambda *args, **kw: None)
    result = toolchain.audit_system(tmp_path)
    assert not result["ok"] and not result["checks"]["runtime"]["ok"]
    assert not result["checks"]["nvcc"]["ok"]


def test_compile_probes_link_cxx_and_cuda_libraries_without_executing(tmp_path, monkeypatch):
    _valid_runtime(monkeypatch)
    nvcc = tmp_path / "cuda/bin/nvcc"
    nvcc.parent.mkdir(parents=True)
    nvcc.write_text("")
    stubs = tmp_path / "cuda/lib64/stubs"
    stubs.mkdir(parents=True)
    monkeypatch.setattr(toolchain.shutil, "which", lambda name: str(nvcc) if name == "nvcc" else f"/system/{name}")
    calls = []

    def capture(cmd, **kw):
        calls.append((cmd, kw))
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"linked mock executable, never run")
        return {"ok": True, "detail": "available", "returncode": 0}
    monkeypatch.setattr(toolchain, "_capture", capture)
    result = toolchain.audit_system(tmp_path)
    assert result["ok"]
    assert len(calls) == len(toolchain.SYSTEM_COMMANDS) + 3
    assert all("toolchain-" not in Path(cmd[0]).name for cmd, _ in calls)
    c, cxx, cuda = [cmd for cmd, kw in calls if "-o" in cmd]
    assert "-std=c11" in c
    assert "-std=c++17" in cxx and "-pthread" in cxx
    assert {"--cudart=shared", "-arch=sm_80", "-lcublas", "-lcuda", "-ccbin", f"-L{stubs}"} <= set(cuda)
    assert all(kw["timeout"] == 90 for cmd, kw in calls if "-o" in cmd)
    assert "__global__" in toolchain.CUDA_SOURCE and "<<<" not in toolchain.CUDA_SOURCE
    assert result["checks"]["c_compile_link"]["executed"] is False
    assert result["checks"]["cxx_compile_link"]["executed"] is False
    assert result["checks"]["cuda_compile_link"]["executed"] is False
    assert all("LD_LIBRARY_PATH" not in kw.get("env", {}) for _, kw in calls)


def test_missing_headers_or_libraries_fail_before_install_and_are_aggregated(tmp_path, monkeypatch):
    _valid_runtime(monkeypatch)
    monkeypatch.setattr(toolchain.shutil, "which", lambda name: f"/system/{name}")

    def capture(cmd, **kw):
        if "-o" in cmd:
            return {"ok": False, "detail": "missing C++ headers" if cmd[0].endswith("g++") else "cannot find -lcublas"}
        return {"ok": True, "detail": "available"}
    monkeypatch.setattr(toolchain, "_capture", capture)
    r = _runner(tmp_path)
    with pytest.raises(toolchain.ToolchainError) as error:
        toolchain.prepare(r)
    assert "missing C++ headers" in str(error.value) and "cannot find -lcublas" in str(error.value)
    assert not envsetup.venv_dir(r.root).exists()


def test_missing_cmake_and_ninja_repaired_before_ready_receipt(tmp_path, monkeypatch):
    r = _runner(tmp_path)
    events = []
    monkeypatch.setattr(toolchain, "audit_system", lambda root: events.append("audit") or _passing())
    _fake_package_capture(monkeypatch)
    assert not (envsetup.venv_dir(r.root) / "bin/cmake").exists()
    assert not (envsetup.venv_dir(r.root) / "bin/ninja").exists()

    def run(stage, cmd, **kw):
        assert stage == "net" and events[0] == "audit"
        assert kw["env"]["PATH"].split(os.pathsep)[0] == str(envsetup.venv_dir(r.root) / "bin")
        if "venv" in cmd:
            events.append("venv")
            _make_venv(r.root)
        elif "install" in cmd:
            events.append("install")
            assert "--require-hashes" in cmd and "--force-reinstall" in cmd
            _make_tools(r.root)
        else:
            pytest.fail(f"unexpected command {cmd}")
        return 0
    r.run = run
    result = toolchain.prepare(r)
    assert events == ["audit", "venv", "install"]
    assert result["state"] == "ready" and result["tools"]["ok"]
    assert result["lock_sha256"] == toolchain.lock_sha256()
    assert result["tools"]["checks"]["ninja"]["version"] == "1.11.1.git.kitware.jobserver-1"
    assert toolchain.assert_ready(r.root)["VIRTUAL_ENV"] == str(envsetup.venv_dir(r.root))


def test_host_pip_fallback_targets_same_isolated_venv_without_deleting(tmp_path, monkeypatch):
    r = _runner(tmp_path)
    calls = []

    def run(stage, cmd, **kw):
        calls.append(cmd)
        _make_venv(r.root)
        return 0 if "--without-pip" in cmd else 1
    r.run = run
    monkeypatch.setattr(toolchain, "_capture", lambda cmd, **kw: {"ok": "--python" in cmd or "-c" in cmd,
                      "detail": '["CPython",[3,11],"linux","x86_64",true]' if "-c" in cmd else "fixture"})
    assert toolchain._ensure_venv(r)
    assert len(calls) == 2 and "--without-pip" in calls[1]
    assert envsetup.is_isolated(r.root)[0]


def test_nonisolated_venv_rejected_before_pip(tmp_path):
    r = _runner(tmp_path)
    _make_venv(r.root)
    (envsetup.venv_dir(r.root) / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
    with pytest.raises(toolchain.ToolchainError, match="isolated venv"):
        toolchain._ensure_venv(r)


def test_reused_venv_with_wrong_python_fails_before_pip(tmp_path, monkeypatch):
    r = _runner(tmp_path)
    _make_venv(r.root)
    monkeypatch.setattr(toolchain, "_capture", lambda cmd, **kw: {"ok": True,
                      "detail": '["CPython",[3,12],"linux","x86_64",true]'})
    with pytest.raises(toolchain.ToolchainError, match="venv must use CPython 3.11"):
        toolchain._ensure_venv(r)


def test_failed_install_replaces_stale_success_receipt(tmp_path, monkeypatch):
    r = _runner(tmp_path)
    monkeypatch.setattr(toolchain, "audit_system", lambda root: _passing())
    monkeypatch.setattr(toolchain, "_ensure_venv", lambda runner: False)

    def fail(*args, **kw):
        raise RuntimeError("deliberate install failure")
    r.run = fail
    toolchain._write_receipt(r.runs / "toolchain.json", {"state": "ready"})
    with pytest.raises(toolchain.ToolchainError, match="install failure"):
        toolchain.prepare(r)
    assert json.loads((r.runs / "toolchain.json").read_text())["state"] == "fail"
    with pytest.raises(toolchain.ToolchainError, match="stale or failed"):
        toolchain.assert_ready(r.root)


def test_assert_ready_requires_receipt_and_matching_lock_before_work(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, "audit_system", lambda root: pytest.fail("stale receipt must fail before probing"))
    with pytest.raises(toolchain.ToolchainError, match="receipt missing"):
        toolchain.assert_ready(tmp_path)
    receipt = {"schema_version": 1, "state": "ready", "root": str(tmp_path.resolve()),
               "package_pins": toolchain.PACKAGE_PINS, "lock_sha256": "stale"}
    toolchain._write_receipt(tmp_path / "runs/toolchain.json", receipt)
    with pytest.raises(toolchain.ToolchainError, match="stale or failed"):
        toolchain.assert_ready(tmp_path)


def test_wrong_executable_or_distribution_version_rejected(tmp_path, monkeypatch):
    _make_venv(tmp_path)
    _make_tools(tmp_path, {"cmake": "cmake version 99.0.0", "ninja": toolchain.EXECUTABLE_VERSIONS["ninja"]})
    _fake_package_capture(monkeypatch)
    inspected = toolchain.inspect_tools(tmp_path)
    assert not inspected["ok"] and not inspected["checks"]["cmake"]["ok"]
    assert inspected["checks"]["ninja"]["ok"]
    capture = toolchain._capture

    def wrong_package(cmd, **kw):
        if "-c" in cmd:
            return {"ok": True, "detail": '{"cmake":"3.31.6","ninja":"1.11.1"}'}
        return capture(cmd, **kw)
    monkeypatch.setattr(toolchain, "_capture", wrong_package)
    assert not toolchain.inspect_tools(tmp_path)["checks"]["package_versions"]["ok"]


def test_capture_bounds_timeout_and_missing_command():
    result = toolchain._capture([sys.executable, "-c", "import time;time.sleep(5)"], timeout=0.05)
    assert not result["ok"] and "TimeoutExpired" in result["detail"]
    assert not toolchain._capture(["/toolchain-fixture-no-such-executable"])["ok"]


def test_real_venv_subprocess_path_activation_without_network(tmp_path):
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(envsetup.venv_dir(tmp_path))], check=True)
    _make_tools(tmp_path)
    env = envsetup.activation_env(tmp_path)
    script = "import json,os,shutil,sys;print(json.dumps([sys.prefix,shutil.which('cmake'),shutil.which('ninja'),os.environ['VIRTUAL_ENV']]))"
    result = subprocess.run([str(envsetup.venv_python(tmp_path)), "-c", script], env=env, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == [str(envsetup.venv_dir(tmp_path)), str(envsetup.venv_dir(tmp_path) / "bin/cmake"),
                                         str(envsetup.venv_dir(tmp_path) / "bin/ninja"), str(envsetup.venv_dir(tmp_path))]
    for name, expected in toolchain.EXECUTABLE_VERSIONS.items():
        version = subprocess.run([name, "--version"], env=env, text=True, capture_output=True, check=True)
        assert version.stdout.strip() == expected


@pytest.mark.skipif(not os.environ.get("QMC_BUILD_TOOL_WHEEL_DIR"), reason="local reviewed wheel directory not supplied (offline-only integration)")
def test_real_pinned_wheels_cmake_ninja_and_cpu_build(tmp_path):
    wheels = Path(os.environ["QMC_BUILD_TOOL_WHEEL_DIR"]).resolve()
    assert wheels.is_dir(), "QMC_BUILD_TOOL_WHEEL_DIR must already exist; test never downloads"
    subprocess.run([sys.executable, "-m", "venv", str(envsetup.venv_dir(tmp_path))], check=True, timeout=90)
    cmd = toolchain.install_cmd(tmp_path)
    at = cmd.index("--index-url")
    cmd[at:at + 2] = ["--no-index", "--find-links", str(wheels)]
    subprocess.run(cmd, env=toolchain.install_env(tmp_path), check=True, capture_output=True, text=True, timeout=90)
    assert toolchain.inspect_tools(tmp_path)["ok"]
    old = tmp_path / "old-system-tools"
    old.mkdir()
    for name in toolchain.PACKAGE_PINS:
        (old / name).write_text("#!/bin/sh\nexit 99\n")
        (old / name).chmod(0o755)
    env = envsetup.activation_env(tmp_path, {**os.environ, "PATH": f"{old}{os.pathsep}{os.environ['PATH']}"})
    assert all(shutil.which(name, path=env["PATH"]) == str(envsetup.venv_dir(tmp_path) / "bin" / name) for name in toolchain.PACKAGE_PINS)
    src = tmp_path / "cpu-project"
    src.mkdir()
    (src / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.18)\nproject(toolchain_cpu LANGUAGES CXX)\nfind_package(Threads REQUIRED)\nadd_executable(smoke smoke.cpp)\ntarget_compile_features(smoke PRIVATE cxx_std_17)\ntarget_link_libraries(smoke PRIVATE Threads::Threads)\n")
    (src / "smoke.cpp").write_text(toolchain.CXX_SOURCE)
    build = tmp_path / "cpu-build"
    subprocess.run(["cmake", "-S", str(src), "-B", str(build), "-G", "Ninja"], env=env, check=True, capture_output=True, text=True, timeout=60)
    subprocess.run(["cmake", "--build", str(build), "--target", "smoke"], env=env, check=True, capture_output=True, text=True, timeout=60)
    assert (build / "smoke").is_file()  # CPU compile/link proof, no CUDA and no network.
