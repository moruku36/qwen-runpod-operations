"""Facts about what actually ran: commits, lock hash, CUDA/driver/image. Reads local state only."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def git_head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def tree_clean(repo: Path) -> bool:
    """True if there are no uncommitted changes (the code that ran is exactly the commit)."""
    return _git(repo, "status", "--porcelain") == ""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def nvcc_release() -> str:
    try:
        out = subprocess.run(["nvcc", "--version"], capture_output=True, text=True, timeout=15, check=True).stdout
        m = re.search(r"release\s+([\d.]+)", out)
        return m.group(1) if m else "unknown"
    except Exception:  # noqa: BLE001
        return "unavailable"


def driver_cuda_version() -> str:
    """CUDA version reported by the driver (nvidia-smi header)."""
    try:
        out = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=15, check=True).stdout
        m = re.search(r"CUDA Version:\s*([\d.]+)", out)
        return m.group(1) if m else "unknown"
    except Exception:  # noqa: BLE001
        return "unavailable"


def image_info(default_tag: str) -> dict:
    """Image tag as requested at create time. The digest is not exposed inside the Pod, so it is reported as such."""
    return {"image_tag": os.environ.get("QMC_IMAGE_TAG", default_tag), "image_digest": "unavailable_in_pod"}
