"""Independent Python environment for the app (standard library only).

The Pod image's system Python carries OS packages (e.g. PyGObject needing pycairo) that make
``pip check`` fail for reasons unrelated to this project. Everything here lives in its own venv under
the workspace, created without system site-packages, installed from the hash-locked file, and checked
(``pip check``) inside that venv only.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import layout

LOCK_PYTHON = "3.11"  # requirements-runpod.lock.txt is compiled for CPython 3.11 (the pod image has py3.11)
KERNEL_NAME = "qwen-venv"
KERNEL_DISPLAY = "Python (qwen-venv)"
PIP_ENV = {"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1", "PIP_PROGRESS_BAR": "off",
           "PIP_DEFAULT_TIMEOUT": "60", "PIP_RETRIES": "5", "PYTHONNOUSERSITE": "1"}


def venv_dir(root: Path | None = None) -> Path:
    return layout.paths(root)["root"] / "venv"


def venv_python(root: Path | None = None) -> Path:
    return venv_dir(root) / "bin" / "python"


def create_cmd(root: Path | None = None, python: str | None = None, *, without_pip: bool = False) -> list[str]:
    """``without_pip`` is the fallback for system Pythons that lack ensurepip (python3-venv not installed)."""
    return [python or sys.executable or "python3", "-m", "venv", *(["--without-pip"] if without_pip else []),
            str(venv_dir(root))]


def _pip(root: Path | None, host_pip: bool, python: str | None) -> list[str]:
    """pip of the venv, or (fallback) the host's pip aimed at the venv with ``--python`` (pip >= 22.3)."""
    if host_pip:
        return [python or sys.executable or "python3", "-m", "pip", "--python", str(venv_python(root))]
    return [str(venv_python(root)), "-m", "pip"]


def install_cmd(lock: Path, root: Path | None = None, *, host_pip: bool = False, python: str | None = None) -> list[str]:
    return [*_pip(root, host_pip, python), "install", "--require-hashes", "-r", str(lock)]


def check_cmd(root: Path | None = None, *, host_pip: bool = False, python: str | None = None) -> list[str]:
    return [*_pip(root, host_pip, python), "check"]


def kernel_cmd(root: Path | None = None, prefix: Path | None = None) -> list[str]:
    cmd = [str(venv_python(root)), "-m", "ipykernel", "install", "--name", KERNEL_NAME, "--display-name", KERNEL_DISPLAY]
    return cmd + (["--prefix", str(prefix)] if prefix else ["--user"])


def is_isolated(root: Path | None = None) -> tuple[bool, str]:
    """True if the venv exists and does not include system site-packages."""
    cfg = venv_dir(root) / "pyvenv.cfg"
    if not cfg.is_file():
        return False, "pyvenv.cfg missing"
    text = cfg.read_text()
    for line in text.splitlines():
        if line.replace(" ", "").lower().startswith("include-system-site-packages="):
            return line.split("=", 1)[1].strip().lower() == "false", line.strip()
    return False, "include-system-site-packages not found"


def running_in_venv(root: Path | None = None) -> bool:
    return Path(sys.prefix).resolve() == venv_dir(root).resolve()
