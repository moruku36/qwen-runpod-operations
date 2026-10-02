"""Assemble the trial report from explicit fields. Nothing is copied wholesale from the environment,
the config, or any API response; the Pod section takes named fields from an already-allowlisted summary."""

from __future__ import annotations

import datetime as dt
import platform
from pathlib import Path

from . import pins, podapi, provenance

POD_REPORT_FIELDS = {"id": "id", "gpu_id": "gpu_type", "dataCenterId": "data_center", "startedAt": "started_at",
                     "status": "final_status"}


def pod_section(summary: dict | None, price_per_hr: float | None = None) -> dict:
    """Pick named fields only. Anything else in ``summary`` (env, ssh, runtime...) is dropped."""
    out = {}
    for src, dst in POD_REPORT_FIELDS.items():
        value = (summary or {}).get(src)
        if isinstance(value, str) and value:
            out[dst] = value
    if price_per_hr is not None:
        out["price_per_hr"] = float(price_per_hr)
    return out


def build_report(*, trial_id: str, repo_root: Path, lock_path: Path, model_records: list[dict], settings: dict,
                 gpu: dict, build: dict, checks: dict, metrics: dict | None = None, timings_s: dict | None = None,
                 server_version: str = "unknown", pod: dict | None = None, notes: list[str] | None = None,
                 gradio_version: str = "unknown") -> dict:
    jst = dt.timezone(dt.timedelta(hours=9))
    image = provenance.image_info(podapi.IMAGE)
    rep = {
        "trial_id": trial_id,
        "created_at_jst": dt.datetime.now(jst).isoformat(timespec="seconds"),
        "source": {"ops_commit": provenance.git_head(repo_root), "ops_tree_clean": provenance.tree_clean(repo_root),
                   "lock_sha256": provenance.file_sha256(lock_path)},
        "upstream": {"sha": pins.UPSTREAM_SHA, "notebook_blob": pins.UPSTREAM_NOTEBOOK_BLOB},
        "llama_cpp": {"commit": pins.LLAMA_CPP_COMMIT, "version_line": server_version, **build},
        "models": [{k: r[k] for k in ("role", "file", "revision", "sha256_verified", "size_bytes")} for r in model_records],
        "environment": {
            "gpu_name": gpu.get("gpu_name", "unknown"), "vram_mib": gpu.get("total_mib", 0),
            "driver": gpu.get("driver", "unknown"), "cuda_toolkit": provenance.nvcc_release(),
            "cuda_driver_api": provenance.driver_cuda_version(), "python": platform.python_version(),
            "gradio": gradio_version, **image},
        "settings": settings,
        "checks": checks,
        "pod": pod or {},
        "notes": notes or [],
    }
    if metrics:
        rep["metrics"] = metrics
    if timings_s:
        rep["timings_s"] = timings_s
    return rep
