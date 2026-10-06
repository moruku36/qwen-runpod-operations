"""CPU-only bridge exercise. Never binds/listens/connects or loads credentials."""
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qmc_runpod.ondemand import LifecycleController, REQUIRED_ACTIONS, UsageLimits, UsageScope
from qmc_runpod.owui_binding import BindingError, OWUIBinding


class FixedClock:
    def now(self): return 100.0


def run():
    controller = LifecycleController(clock=FixedClock())
    limits = UsageLimits.create(max_runtime_seconds=120, max_usd="0.1002", expires_at=250,
        scope=UsageScope("smoke-session", "new-pod", REQUIRED_ACTIONS),
        cleanup_runtime_seconds=30, cleanup_usd="0.0002")
    controller.start("smoke-session", limits)
    for expected in ("created", "bootstrapped", "ready"):
        assert controller.run_one_step() == expected
    bridge = OWUIBinding(controller); bridge.bind_ready_session("smoke-session")
    payload = {"model": "qwen-27b", "messages": [{"role": "user", "content": "public fixture"}], "stream": False}
    before = controller.store.snapshot()["session"]
    effects = list(controller.provider.calls)
    bridge.catalog(); bridge.status()
    checks = [{"check": "catalog/status allocation-free", "pass": controller.provider.calls == effects}]
    try:
        bridge.json(payload, "background", deadline=time.monotonic() + 1, cancel=threading.Event())
        raise AssertionError("background admitted")
    except BindingError:
        checks.append({"check": "no background inference without trusted gesture", "pass": bridge.upstream.calls == 0})
    permit = bridge.permit_interactive(payload, "json-one")
    result = bridge.json(payload, "json-one", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event())
    checks.append({"check": "OWUI JSON bound without client session_id", "pass": result["id"] == "json-one"})
    payload["stream"] = True
    permit = bridge.permit_interactive(payload, "stream-one")
    with bridge.stream(payload, "stream-one", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event()) as output:
        counts = [piece.count(b"data: [DONE]") for piece in output]
    checks.append({"check": "bounded normalized SSE terminal", "pass": sum(counts) == 1})
    permit = bridge.permit_interactive(payload, "drop-one")
    with bridge.stream(payload, "drop-one", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event()) as output:
        next(output)
    checks.append({"check": "dropped consumer exact release", "pass": controller.http_release_count == 3})
    after = controller.store.snapshot()["session"]
    checks.append({"check": "no grant/idle renewal or provider mutation", "pass": before["deadline"] == after["deadline"]
                   and before["idle_deadline"] == after["idle_deadline"] and controller.provider.calls == effects})
    bridge.close()
    assert all(check["pass"] for check in checks)
    return {"simulated": True, "network_io": False, "checks": checks,
            "admission_releases": controller.http_release_count, "provider_mutations_after_binding": 0}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
