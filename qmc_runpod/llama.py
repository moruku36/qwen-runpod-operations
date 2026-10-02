"""Build llama-server at the pinned commit under the Pod workspace (no Colab paths, no Drive cache)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from . import layout, pins


def runtime_tag() -> str:
    return f"py{platform.python_version_tuple()[0]}{platform.python_version_tuple()[1]}-{platform.machine()}"


def bin_dir(archs: str, root: Path | None = None) -> Path:
    # keyed by commit, GPU arch and runtime so a binary is never reused on a different GPU/image
    return layout.paths(root)["llama_bin"] / f"{pins.LLAMA_CPP_COMMIT[:10]}-sm{archs.replace(';', '_')}-{runtime_tag()}"


def install(root: Path | None = None, *, run=None) -> Path:
    from qmc import colab

    if colab.LLAMA_CPP_COMMIT != pins.LLAMA_CPP_COMMIT:
        raise RuntimeError("upstream llama.cpp pin changed; update pins.py after reviewing the diff")
    run = run or colab._run
    if shutil.which("nvcc") is None:
        raise RuntimeError("nvcc がありません。CUDA devel 系イメージのPodで実行してください")
    detected = colab.detect_cuda_arch()
    if not detected and not os.environ.get("QMC_CUDA_ARCHS"):
        raise RuntimeError("GPU の compute capability を検出できません（nvidia-smi）。固定arch推測でのビルドはしません")
    archs = os.environ.get("QMC_CUDA_ARCHS") or detected
    p = layout.paths(root)
    out = bin_dir(archs, root)
    server = out / "llama-server"
    if server.exists():
        os.environ["QMC_LLAMA_BIN_DIR"] = str(out)
        return server
    src = p["llama_src"]
    if not (src / ".git").exists():
        run(["git", "clone", "--filter=blob:none", colab.LLAMA_CPP_REPO, str(src)])
    run(["git", "-C", str(src), "fetch", "--depth", "1", "origin", pins.LLAMA_CPP_COMMIT])
    run(["git", "-C", str(src), "checkout", "-q", pins.LLAMA_CPP_COMMIT])
    build = src / "build"
    run(colab.cmake_configure_cmd(src, build, archs))
    run(["cmake", "--build", str(build), "--config", "Release", "--target", "llama-server",
         "-j", str(os.cpu_count() or 4)])
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(build / "bin" / "llama-server", server)
    server.chmod(0o755)
    os.environ["QMC_LLAMA_BIN_DIR"] = str(out)
    return server


def server_version(server: Path) -> str:
    try:
        r = subprocess.run([str(server), "--version"], capture_output=True, text=True, timeout=30, check=False)
        return (r.stdout + r.stderr).strip().splitlines()[0][:200]
    except Exception:  # noqa: BLE001
        return "unknown"
