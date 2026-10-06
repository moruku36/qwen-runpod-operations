"""CPU-testable local execution control. No resource or remote-agent API calls.

The deadline cancels local processes only. An external owner must Stop the Pod.
Packets carry fresh observed-credit, reserve, runtime and manual-stop confirmations.
Local packet checks are estimates and are not provider-enforced spending caps.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import logchannel, pins

STAGES = ("selftest", "net", "source", "venv", "build", "trial")
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
    required = {"run_id", "commit", "root", "stages", "allocated_at", "test_deadline", "export_deadline", "stop_deadline",
                "approval_date", "resource_action", "pod_id", "pod_status", "start_time_source",
                "cap_usd", "quote_total_usd", "account_balance_usd", "storage_reserve_usd",
                "max_runtime_minutes", "absolute_stop_deadline", "stop_buffer_minutes", "stop_request_deadline", "stop_operator_confirmed",
                "console_stop_verified", "stop_operator", "no_autopay_confirmed", "balance_observed_at", "cost_record",
                "cost_estimates", "export_destination", "lock_sha256", "feature_options"}
    if "evaluation_profile" in p:
        required.add("evaluation_profile")
    profile = p.get("evaluation_profile", "full_features")
    if profile not in ("chat_only", "full_features"):
        raise ValueError("unknown evaluation profile")
    if set(p) != required:
        raise ValueError("packet keys differ from required schema")
    logchannel.safe_run_id(p["run_id"])
    try:
        approval_day = dt.date.fromisoformat(p["approval_date"])
    except (TypeError, ValueError):
        raise ValueError("explicit ISO approval date required") from None
    if p["resource_action"] not in ("create", "resume"):
        raise ValueError("explicit create or resume action required")
    if (not isinstance(p["pod_id"], str) or not re.fullmatch(r"[A-Za-z0-9]{6,40}", p["pod_id"])
            or p["pod_status"] != "RUNNING"):
        raise ValueError("exact RUNNING Pod identity required")
    if p["start_time_source"] not in ("create_response.startedAt", "resume_response.startedAt", "console_start_confirmed"):
        raise ValueError("verified actual allocation start-time source required")
    if p["resource_action"] == "create" and p["start_time_source"] not in ("create_response.startedAt", "console_start_confirmed"):
        raise ValueError("create action/start-time source mismatch")
    if p["resource_action"] == "resume" and p["start_time_source"] not in ("resume_response.startedAt", "console_start_confirmed"):
        raise ValueError("resume action/start-time source mismatch")
    if (p["stop_operator_confirmed"] is not True or p["console_stop_verified"] is not True
            or p["no_autopay_confirmed"] is not True or not isinstance(p["stop_operator"], str) or not p["stop_operator"].strip()):
        raise ValueError("current Stop operator, console Stop and no-Auto-Pay confirmations are required")
    for name in ("cap_usd", "quote_total_usd", "account_balance_usd", "storage_reserve_usd"):
        if isinstance(p[name], bool) or not isinstance(p[name], (int, float)) or not math.isfinite(p[name]) or p[name] <= 0:
            raise ValueError("invalid cost")
    if p["storage_reserve_usd"] >= p["account_balance_usd"]:
        raise ValueError("storage reserve consumes the full observed balance")
    if p["storage_reserve_usd"] < 0.528:
        raise ValueError("at least $0.528 must remain reserved for 24h of existing storage")
    spendable = p["account_balance_usd"] - p["storage_reserve_usd"]
    if not math.isclose(p["cap_usd"], spendable, rel_tol=0, abs_tol=0.005) or p["quote_total_usd"] > p["cap_usd"]:
        raise ValueError("quote exceeds current balance after storage reserve")
    if not isinstance(p["cost_record"], str) or not p["cost_record"].strip():
        raise ValueError("fresh cost evidence reference required")
    costs = p["cost_estimates"]
    cost_keys = {"gpu_rate_usd_per_hour", "running_storage_usd_per_hour", "session_quote_usd",
                 "additional_storage_24h_usd", "delayed_billing_margin_usd"}
    if not isinstance(costs, dict) or set(costs) != cost_keys:
        raise ValueError("complete fresh cost breakdown required")
    for name, value in costs.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid cost component")
    if costs["gpu_rate_usd_per_hour"] <= 0 or costs["session_quote_usd"] <= 0 or costs["delayed_billing_margin_usd"] <= 0:
        raise ValueError("positive live GPU rate, session estimate and delayed-billing margin required")
    if costs["session_quote_usd"] + costs["additional_storage_24h_usd"] + costs["delayed_billing_margin_usd"] > p["quote_total_usd"] + 0.005:
        raise ValueError("cost breakdown exceeds its total")
    runtime, stop_buffer = p["max_runtime_minutes"], p["stop_buffer_minutes"]
    if isinstance(runtime, bool) or not isinstance(runtime, int) or runtime <= 0:
        raise ValueError("explicit positive runtime limit required")
    if isinstance(stop_buffer, bool) or not isinstance(stop_buffer, int) or stop_buffer < 10:
        raise ValueError("explicit Stop confirmation buffer of at least 10 minutes required")
    start, tests, export, request, stop, absolute_stop, balance_seen = (instant(p[k]) for k in
        ("allocated_at", "test_deadline", "export_deadline", "stop_request_deadline", "stop_deadline", "absolute_stop_deadline", "balance_observed_at"))
    jst = dt.timezone(dt.timedelta(hours=9))
    if start.astimezone(jst).date() != approval_day:
        raise ValueError("allocation date does not match explicit approval date")
    if balance_seen.astimezone(jst).date() != approval_day or abs((start - balance_seen).total_seconds()) > 900:
        raise ValueError("balance snapshot must be from the same date and within 15 minutes of allocation")
    if absolute_stop.astimezone(jst).date() != approval_day or absolute_stop <= start:
        raise ValueError("explicit same-day absolute Stop deadline after allocation required")
    expected_stop = min(start + dt.timedelta(minutes=runtime), absolute_stop)
    expected_request = expected_stop - dt.timedelta(minutes=stop_buffer)
    expected_tests = min(start + dt.timedelta(minutes=80), expected_request - dt.timedelta(minutes=30))
    expected_export = min(start + dt.timedelta(minutes=85), expected_request - dt.timedelta(minutes=20))
    if (tests, export, request, stop) != (expected_tests, expected_export, expected_request, expected_stop):
        raise ValueError("deadlines do not match explicit runtime and Stop buffer")
    if not start < tests < export < request < stop or (tests-start).total_seconds() < 3600:
        raise ValueError("invalid deadlines or less than 60 minutes of work")
    minimum_session = (costs["gpu_rate_usd_per_hour"] + costs["running_storage_usd_per_hour"]) * runtime / 60
    if costs["session_quote_usd"] + 0.005 < minimum_session:
        raise ValueError("session estimate does not cover runtime at live rates")
    expected_total = costs["session_quote_usd"] + costs["additional_storage_24h_usd"] + costs["delayed_billing_margin_usd"]
    if not math.isclose(p["quote_total_usd"], expected_total, rel_tol=0, abs_tol=0.005):
        raise ValueError("cost components do not reconcile to the quoted total")
    if now is not None and not start <= now < tests:
        raise ValueError("execution outside allocation/test window")
    if now is not None and now.astimezone(jst).date() != approval_day:
        raise ValueError("packet is stale or outside today's explicitly approved date")
    if p["stages"] != list(STAGES):
        raise ValueError("stages must be ordered without omissions")
    if not Path(p["root"]).is_absolute() or not p["export_destination"]:
        raise ValueError("absolute root and private export destination required")
    f = p["feature_options"]
    if profile == "chat_only":
        if not isinstance(f, dict) or set(f) != {"allow_public_search"} or f["allow_public_search"] is not False:
            raise ValueError("chat-only profile must disable search and contain no audio fixtures")
    else:
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


def _live_group(proc: subprocess.Popen) -> bool:
    """Linux session IDs stay reserved while stragglers exist; reject a reused leader PID."""
    proc.poll()  # reap our leader, but do not confuse its exit with child cleanup
    if sys.platform.startswith("linux"):
        live = False
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                tail = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(tail[2]) != proc.pid or int(tail[3]) != proc.pid:
                    continue
                if entry.name == str(proc.pid) and getattr(proc, "_qmc_start", None) != tail[19]:
                    return False  # PID reused; never signal this unrelated group
                if tail[0] != "Z":
                    live = True
            except (OSError, ValueError, IndexError):
                continue
        return live
    try:
        os.killpg(proc.pid, 0)
        return True
    except ProcessLookupError:
        return False


def terminate_tree(proc: subprocess.Popen, *, grace_seconds: float = 3) -> None:
    if os.name == "posix":
        if not getattr(proc, "_qmc_owned_session", False):
            raise ValueError("refuse to signal a group not created by this controller")
        def send(sig):
            if not _live_group(proc):
                return False
            try:
                os.killpg(proc.pid, sig)
                return True
            except ProcessLookupError:
                return False
        if send(signal.SIGTERM):
            end = time.monotonic() + max(0, min(grace_seconds, 30))
            while _live_group(proc) and time.monotonic() < end:
                time.sleep(.05)
            killed = send(signal.SIGKILL)  # includes TERM-ignoring children after the leader exits
            if killed:
                end = time.monotonic() + 2
                while _live_group(proc) and time.monotonic() < end:
                    time.sleep(.05)
                if _live_group(proc):
                    raise RuntimeError("owned process group still live after bounded cleanup")
        proc.wait(timeout=3)
        return
    else:
        if proc.poll() is not None:
            return
        # A Windows venv launcher may have a Python child holding the log handle.
        # Kill only the process tree rooted at the PID this controller just started.
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        if proc.poll() is None:
            proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def bounded_command(cmd: list[str], *, seconds: float, cancel_file: Path, output: Path, grace_seconds: float = 3) -> int:
    """Bound silent commands too; Linux process groups cancel pip/build/server children."""
    if seconds <= 0 or cancel_file.exists():
        return 124
    with output.open("a", encoding="utf-8") as f:
        proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                start_new_session=os.name == "posix")
        proc._qmc_owned_session = os.name == "posix"
        if sys.platform.startswith("linux"):
            try:
                proc._qmc_start = Path(f"/proc/{proc.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
            except (OSError, IndexError):
                proc._qmc_start = None
        end = time.monotonic() + seconds
        try:
            while proc.poll() is None:
                if cancel_file.exists() or time.monotonic() >= end:
                    terminate_tree(proc, grace_seconds=min(grace_seconds, max(0, end-time.monotonic())))
                    return 124
                time.sleep(min(0.1, max(0, end - time.monotonic())))
            return proc.returncode
        finally:
            terminate_tree(proc, grace_seconds=min(grace_seconds, max(0, end-time.monotonic())))


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
        if state.get("state") in ("failed", "cancelled"):
            raise ValueError("failed/cancelled run is terminal; use a fresh run ID and root")
        if state.get("state") == "running" and state.get("current_stage") == "trial" and "trial" not in state.get("completed", []):
            raise ValueError("interrupted or in-flight trial is terminal; use a fresh run ID and root")
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
        left = (instant(p["export_deadline"] if stage == "trial" else p["test_deadline"]) - clock()).total_seconds()
        if clock() >= instant(p["test_deadline"]):
            state.update(state="cancelled", exit_code=124); save(); return 124
        cmd = [sys.executable, str(repo / "scripts/pod_run.py"), stage, "--root", str(root), "--run-id", p["run_id"]]
        if stage == "trial":
            profile = p.get("evaluation_profile", "full_features")
            cmd += ["--warm", "--evaluation-profile", profile,
                    "--test-deadline", str(instant(p["test_deadline"]).timestamp()),
                    "--export-deadline", str(instant(p["export_deadline"]).timestamp())]
            if profile == "full_features":
                f = p["feature_options"]
                cmd += ["--features", "--hold-min", "2", "--allow-public-search", "--audio", f["audio"],
                        "--transcript-file", f["transcript_file"]]
        state.update(state="running", current_stage=stage); save()
        try:
            rc = command(cmd, seconds=left, cancel_file=root / "CANCEL", output=root / "bootstrap.log",
                         grace_seconds=min(30, max(0, left)) if stage == "trial" else 3)
        except BaseException as exc:
            state.update(state="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                         exit_code=1, error_type=type(exc).__name__)
            save()
            raise
        if rc != 0:
            state.update(state="cancelled" if rc == 124 else "failed", exit_code=rc,
                         action="external operator must Stop exact Pod; application exit does not stop billing")
            save(); return rc
        status = json.loads((root / "runs/status.json").read_text())
        if status.get(stage, {}).get("state") != "ok":
            state.update(state="failed", exit_code=1); save(); return 1
        state["completed"].append(stage); save()
    state.update(state="chat_execution_complete_export_stop_pending" if p.get("evaluation_profile") == "chat_only" else "baseline_complete_features_pending",
                 action="verified off-Pod export and external Stop still required; omitted features are not passed")
    save()
    return 0
