"""Explicit synthetic C1 verification command; never creates a real resource."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from qmc_runpod.c1_runtime import OnDemandRuntime
from qmc_runpod.owui_intent_policy import ServerPurpose
from qmc_runpod.owui_v0114_offline import OfflineOWUICallSites
from qmc_runpod.ondemand import TerminationApproval, UsageLimits, UsageScope, REQUIRED_ACTIONS
from test_private_gateway import body, request
from test_ondemand_controller import FakeClock


def run():
    runtime = OnDemandRuntime(clock=FakeClock(), idle_seconds=10, verify_subject=lambda request: "public-fixture-user",
                              allowed_subjects=frozenset({"public-fixture-user"}))
    runtime.approve_session(UsageLimits.create(max_runtime_seconds=2000, max_usd="10", expires_at=5000,
        scope=UsageScope("smoke", "new-pod", REQUIRED_ACTIONS), cleanup_runtime_seconds=120, cleanup_usd=".10"))
    sites = OfflineOWUICallSites(runtime)
    sites.chat_entry(object(), body(True), server_route="/api/chat/completions", purpose=ServerPurpose.MANUAL_CHAT,
                     request_id="chat", session_id="smoke")
    checks = [runtime.controller.provider.calls == [], runtime.status()["queued"] == 1]
    checks.append([runtime.tick() for _ in range(3)] == ["created", "bootstrapped", "ready"])
    payload, headers = runtime.ready_request("chat")
    with runtime.gateway.serve() as address:
        status, _, wire = request(address, "POST", "/v1/chat/completions", payload, rid="chat", token=headers["X-Intent-Token"])
        checks.append(status == 200 and wire.count(b"data: [DONE]") == 1)
        runtime.controller.clock.set(110)
        checks.append([runtime.tick() for _ in range(3)] == ["draining", "exporting", "approval_pending"])
        checks.append(not any(a == "terminate" for a, _ in runtime.controller.provider.calls))
        state = runtime.controller.store.snapshot()["session"]
        approval = TerminationApproval("approved-smoke", "terminate", "smoke", state["pod_id"], state["receipt"]["sha256"], 5000)
        runtime.approve_cleanup(approval)
        checks.append(runtime.confirm_cleanup("smoke", approval.approval_id) == "absent")
        checks.append(runtime.exports.verify(approval.receipt_sha256))
    runtime.close()
    assert all(checks)
    return {"simulated": True, "checks": checks, "real_provider_calls": 0, "real_owui_changes": 0,
            "network_scope": "ephemeral_ipv4_loopback_fixture_only"}


if __name__ == "__main__": print(json.dumps(run(), indent=2))
