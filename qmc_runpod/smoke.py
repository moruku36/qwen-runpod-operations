"""Chat-only measurement harness (text prompts, optional image). Replaces ``qmc.bench``, which also
loads and runs the image-generation backend. Records numbers only, never prompt or answer text."""

from __future__ import annotations

import statistics
import subprocess
import time


def gpu_memory_mib() -> dict:
    """GPU memory via nvidia-smi (llama-server is a separate process, so torch cannot see it)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip().splitlines()[0]
        name, used, total, driver = [x.strip() for x in out.split(",")]
        return {"gpu_name": name, "used_mib": float(used), "total_mib": float(total), "driver": driver}
    except Exception:  # noqa: BLE001
        return {}


def run_turn(app, text: str, files: list[str] | None = None, *, thinking: bool = False, web_search: str = "off") -> dict:
    """One turn in a fresh session. Returns first-event / first-text / total seconds and char count."""
    from qmc.controller import TurnOptions

    sid = app.sessions.create_session()
    t0 = time.monotonic()
    first_text = None
    chars = 0
    error = False
    try:
        for ev in app.controller.handle(sid, text, files or [], TurnOptions(thinking=thinking, web_search=web_search)):
            if ev.kind == "text":
                if first_text is None:
                    first_text = time.monotonic() - t0
                chars += len(str(ev.data))
            elif ev.kind == "error":
                error = True
            elif ev.kind == "done":
                break
    finally:
        total = time.monotonic() - t0
        app.sessions.delete_session(sid)  # measurement turns are not kept in history
    return {"first_text_s": first_text, "total_s": total, "chars": chars, "error": error}


def summarize(turns: list[dict]) -> dict:
    ok = [t for t in turns if not t["error"] and t["first_text_s"] is not None]
    out = {"turns": len(turns), "ok_turns": len(ok), "errors": sum(1 for t in turns if t["error"])}
    if ok:
        out["first_text_median_s"] = round(statistics.median(t["first_text_s"] for t in ok), 3)
        out["total_median_s"] = round(statistics.median(t["total_s"] for t in ok), 3)
        out["total_max_s"] = round(max(t["total_s"] for t in ok), 3)
    return out
