"""Pod filesystem layout and the non-secret environment the app reads. Colab paths and Drive are not used."""

from __future__ import annotations

import os
from pathlib import Path

WORKSPACE = Path(os.environ.get("QMC_WORKSPACE", "/workspace/qwen"))


def paths(root: Path | None = None) -> dict[str, Path]:
    root = Path(root) if root else WORKSPACE
    return {
        "root": root,
        "source": root / "source",
        "hf_cache": root / "hf-cache",
        "llama_src": root / "llama.cpp",
        "llama_bin": root / "llama-bin",
        "data": root / "data",
        "runs": root / "runs",
        "local_db": Path("/tmp/qmc/history.db"),
    }


def apply_env(root: Path | None = None, *, ctx: int | None = 8192, web_search: str = "off") -> dict[str, str]:
    """Set non-secret QMC_* variables in this process and return what was set (for the report)."""
    p = paths(root)
    env = {
        "QMC_DATA_DIR": str(p["data"]),
        "QMC_LOCAL_DB": str(p["local_db"]),
        "HF_HOME": str(p["hf_cache"]),
        "QMC_ASR_DEVICE": "cpu",
        "QMC_ASR_MODEL": "small",
        "QMC_TTS": "false",
        "QMC_SHARE": "false",
        "QMC_THINKING": "false",
        "QMC_WEB_SEARCH": web_search,
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }
    if ctx:
        env["QMC_CHAT_CTX"] = str(ctx)
    for key, value in env.items():
        os.environ[key] = value
    for d in ("source", "hf_cache", "data", "runs"):
        p[d].mkdir(parents=True, exist_ok=True)
    return env
