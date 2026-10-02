"""Chat-only measurements that keep cold and warm apart. Numbers only: prompts/answers are never recorded.

Replaces ``qmc.bench`` (it also loads and runs the image backend). Requests go straight to the chat
backend so the output cap (``max_tokens``) is explicit and applied.

Phases (never mixed):
  load        model start (llama-server up and healthy)         -> ``load_model``
  first       the first request after load, short, small cap    -> ``first_response``   (cold request)
  warm        later requests, same cap                          -> ``warm_runs``
The plan's usability criteria are evaluated only from warm runs (>= 10, max_tokens 256) by
``evaluate_criteria``; anything less returns "not_evaluated". They are a proposal, not a guarantee.
"""

from __future__ import annotations

import statistics
import subprocess
import time

FIRST_MAX_TOKENS = 64
WARM_MAX_TOKENS = 256
WARM_MIN_RUNS = 10
CRITERIA = {"first_token_median_s": 5.0, "completion_median_s": 30.0}  # from docs/session-checklist.ja.md
SHORT_PROMPT = "日本の四季を一文で説明して。"


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


def load_model(app) -> dict:
    """Start the chat model and time it. This is the load phase, kept out of every request measurement."""
    t0 = time.monotonic()
    app.manager.ensure("chat")
    return {"load_s": round(time.monotonic() - t0, 3)}


def generate(app, prompt: str, *, max_tokens: int, thinking: bool = False) -> dict:
    """One request to the loaded chat backend with an explicit output cap.

    Returns first_delta_s (time to first streamed content), total_s, stream_deltas (≈ output tokens: llama-server
    streams about one delta per token; it is a count of chunks, not a tokenizer count), finish_reason, max_tokens.
    """
    from qmc.backends.base import ChatParams

    model = app.manager.get("chat")
    if not app.manager.is_loaded("chat"):
        raise RuntimeError("model is not loaded; call load_model first so load time is not counted as a request")
    params = ChatParams(thinking=thinking, max_tokens=max_tokens)
    t0 = time.monotonic()
    first, deltas, finish, error = None, 0, None, False
    try:
        for d in model.stream_chat([{"role": "user", "content": prompt}], params):
            if d.content or d.reasoning:
                deltas += 1
                if first is None:
                    first = time.monotonic() - t0
            if d.finish_reason:
                finish = d.finish_reason
    except Exception:  # noqa: BLE001
        error = True
    return {"first_delta_s": None if first is None else round(first, 3), "total_s": round(time.monotonic() - t0, 3),
            "stream_deltas": deltas, "finish_reason": finish or "unknown", "max_tokens": max_tokens, "error": error}


def first_response(app, prompt: str = SHORT_PROMPT, max_tokens: int = FIRST_MAX_TOKENS) -> dict:
    """The single short smoke request run first. It is the cold request, not a performance sample."""
    return {"phase": "first", **generate(app, prompt, max_tokens=max_tokens)}


def warm_runs(app, prompts: list[str], max_tokens: int = WARM_MAX_TOKENS, *, on_sample=None, before_sample=None) -> list[dict]:
    """Run only after first_response succeeded. Same cap for every run."""
    runs = []
    for p in prompts:
        if before_sample:
            before_sample()
        runs.append({"phase": "warm", **generate(app, p, max_tokens=max_tokens)})
        if on_sample:
            on_sample(runs)
    return runs


def summarize(runs: list[dict]) -> dict:
    ok = [r for r in runs if not r["error"] and r["first_delta_s"] is not None
          and r.get("finish_reason") in ("stop", "length")]
    out = {"runs": len(runs), "ok_runs": len(ok), "errors": sum(1 for r in runs if r["error"])}
    if ok:
        out["first_delta_median_s"] = round(statistics.median(r["first_delta_s"] for r in ok), 3)
        out["total_median_s"] = round(statistics.median(r["total_s"] for r in ok), 3)
        out["total_max_s"] = round(max(r["total_s"] for r in ok), 3)
    return out


def evaluate_criteria(warm: list[dict]) -> dict:
    """"not_evaluated" unless there are >= 10 successful warm runs capped at 256 tokens. Never pass/fail otherwise."""
    usable = [r for r in warm if r.get("phase") == "warm" and r["max_tokens"] == WARM_MAX_TOKENS]
    s = summarize(usable)
    if len(usable) < WARM_MIN_RUNS or s["ok_runs"] < WARM_MIN_RUNS or len(usable) != s["ok_runs"]:
        return {"verdict": "not_evaluated", "reason": f"needs >= {WARM_MIN_RUNS} warm runs at max_tokens={WARM_MAX_TOKENS}"}
    ok = (s["first_delta_median_s"] <= CRITERIA["first_token_median_s"]
          and s["total_median_s"] <= CRITERIA["completion_median_s"])
    return {"verdict": "pass" if ok else "fail", **s, "note": "proposed criteria from the plan, not a guarantee"}


def run_turn(app, text: str, files: list[str] | None = None, *, thinking: bool = False, web_search: str = "off") -> dict:
    """End-to-end turn through the controller (history, vision, search). Output length is NOT capped here and
    cold/warm are not separated, so this is a functional check only, never a performance sample."""
    from qmc.controller import TurnOptions

    sid = app.sessions.create_session()
    t0 = time.monotonic()
    first_text, chars, error = None, 0, False
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
        app.sessions.delete_session(sid)
    return {"first_text_s": first_text, "total_s": total, "chars": chars, "error": error}
