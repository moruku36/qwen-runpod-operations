"""CPU-testable local execution control. No resource or remote-agent API calls.

The deadline cancels local processes only. An external owner must Stop the Pod.
The approved $10/120-minute proposal is not a provider-enforced spending cap.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import logchannel, pins

STAGES = ("selftest", "net", "source", "venv", "build", "trial")
APPROVED_DAY = "2026-10-03"
LOCK_SHA = "d9fb03b78fed58478ebe7e67023d93d331e5b9b8ffcba613f267d4599efdc8d0"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def instant(value: str) -> dt.datetime:
    x = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if x.tzinfo is None:
        raise ValueError("time must include UTC offset")
    return x


def validate_packet(p: dict, repo: Path, *, now: dt.datetime | None = None) -> None:
    """Validate an explicit immutable packet; no default budget or start time."""
    required = {"run_id", "commit", "root", "stages", "allocated_at", "test_deadline", "stop_deadline",
                "approval_date", "cap_usd", "quote_total_usd", "export_destination", "lock_sha256", "feature_options"}
    if set(p) != required:
        raise ValueError("packet keys differ from required schema")
    logchannel.safe_run_id(p["run_id"])
    if p["approval_date"] != APPROVED_DAY:
        raise ValueError("requires October 3 approval record")
    for name in ("cap_usd", "quote_total_usd"):
        if isinstance(p[name], bool) or not isinstance(p[name], (int, float)) or not math.isfinite(p[name]) or p[name] <= 0:
            raise ValueError("invalid cost")
    if p["cap_usd"] > 10 or p["quote_total_usd"] > p["cap_usd"]:
        raise ValueError("quote exceeds approved cap")
    start, tests, stop = (instant(p[k]) for k in ("allocated_at", "test_deadline", "stop_deadline"))
    if start.astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat() != APPROVED_DAY:
        raise ValueError("allocation date outside approval")
    if not start < tests < stop or (stop - start).total_seconds() > 7200:
        raise ValueError("invalid deadlines or session longer than 120 minutes")
    if now is not None and not start <= now < tests:
        raise ValueError("execution outside allocation/test window")
    if p["stages"] != list(STAGES):
        raise ValueError("stages must be ordered without omissions")
    if not Path(p["root"]).is_absolute() or not p["export_destination"]:
        raise ValueError("absolute root and private export destination required")
    f = p["feature_options"]
    if not isinstance(f, dict) or set(f) != {"allow_public_search", "audio", "audio_sha256", "transcript_file", "transcript_sha256", "licensed_audio"}:
        raise ValueError("feature fixture schema incomplete")
    if f["allow_public_search"] is not True or f["licensed_audio"] is not True:
        raise ValueError("approved public search and licensed speech fixture required")
    for name, hash_key in (("audio", "audio_sha256"), ("transcript_file", "transcript_sha256")):
        path = Path(f[name])
        if not path.is_absolute() or not path.is_file() or digest(path) != f[hash_key]:
            raise ValueError("feature fixture missing or hash mismatch")
    if p["lock_sha256"] != digest(repo / "requirements-runpod.lock.txt"):
        raise ValueError("dependency lock hash mismatch")
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True).strip()
    if p["commit"] != head or dirty:
        raise ValueError("requires exact clean execution commit")


def terminate_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(proc.pid, signal.SIGTERM)
    else:
        # A Windows venv launcher may have a Python child holding the log handle.
        # Kill only the process tree rooted at the PID this controller just started.
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        if proc.poll() is None:
            proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait(timeout=3)


def bounded_command(cmd: list[str], *, seconds: float, cancel_file: Path, output: Path) -> int:
    """Bound silent commands too; Linux process groups cancel pip/build/server children."""
    if seconds <= 0 or cancel_file.exists():
        return 124
    with output.open("a", encoding="utf-8") as f:
        proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                start_new_session=os.name == "posix")
        end = time.monotonic() + seconds
        try:
            while proc.poll() is None:
                if cancel_file.exists() or time.monotonic() >= end:
                    terminate_tree(proc)
                    return 124
                time.sleep(min(0.1, max(0, end - time.monotonic())))
            return proc.returncode
        finally:
            terminate_tree(proc)


def execute(p: dict, repo: Path, *, clock=None, command=None) -> int:
    """One local command resumes checkpoints; selftest needs an external verified receipt.

    Run IDs never reuse roots. Checkpoints bind exact packet bytes and successful stage exit.
    Exceptions/failures stop advancement. No create/start/stop API exists here.
    """
    clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
    command = command or bounded_command
    validate_packet(p, repo, now=clock())
    root = Path(p["root"])
    packet_hash = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
    checkpoint = root / "retry-checkpoint.json"
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        if state.get("packet_sha256") != packet_hash or state.get("run_id") != p["run_id"]:
            raise ValueError("checkpoint belongs to a different packet/run")
    else:
        if root.exists() and any(root.iterdir()):
            raise ValueError("fresh root required; stale workspace state cannot be reused")
        root.mkdir(parents=True, exist_ok=True)
        state = {"run_id": p["run_id"], "packet_sha256": packet_hash, "completed": [], "state": "running"}
    def save():
        tmp = checkpoint.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(checkpoint)
    save()
    for stage in STAGES:
        if stage in state["completed"]:
            continue
        if stage == "net":
            receipt_path = root / "selftest-receipt.json"
            if not receipt_path.exists():
                state["state"] = "awaiting_off_pod_selftest_receipt"; save(); return 2
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            expected = "selftest.txt=" + digest(root / "runs" / "selftest.txt")
            if receipt.get("run_id") != p["run_id"] or receipt.get("verified") is not True or expected not in receipt.get("expected_files", []) or "selftest:hello from the pod" not in receipt.get("expected_events", []):
                raise ValueError("selftest receipt lacks exact run/event/file hash")
        left = (instant(p["test_deadline"]) - clock()).total_seconds()
        cmd = [sys.executable, str(repo / "scripts/pod_run.py"), stage, "--root", str(root), "--run-id", p["run_id"]]
        if stage == "trial":
            f = p["feature_options"]
            cmd += ["--warm", "--features", "--hold-min", "2", "--allow-public-search", "--audio", f["audio"],
                    "--transcript-file", f["transcript_file"], "--test-deadline", str(instant(p["test_deadline"]).timestamp())]
        state.update(state="running", current_stage=stage); save()
        rc = command(cmd, seconds=left, cancel_file=root / "CANCEL", output=root / "bootstrap.log")
        if rc != 0:
            state.update(state="cancelled" if rc == 124 else "failed", exit_code=rc,
                         action="external operator must Stop exact Pod; application exit does not stop billing")
            save(); return rc
        status = json.loads((root / "runs/status.json").read_text())
        if status.get(stage, {}).get("state") != "ok":
            state.update(state="failed", exit_code=1); save(); return 1
        state["completed"].append(stage); save()
    state.update(state="baseline_complete_features_pending",
                 action="feature/UI evidence, verified off-Pod export and external Stop still required")
    save()
    return 0
