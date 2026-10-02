"""Minimal RunPod REST API v2 client for one trial: read availability, create, read, stop.

Authentication is NOT handled here: no key is read, stored or sent by this code. Requests go out
without an Authorization header and rely on the environment-side credential injection. There is
deliberately no terminate/delete function; deletion stays a console action confirmed by a person.
Only allowlisted fields of API responses are ever returned or logged.
"""

from __future__ import annotations

import datetime as dt
import re
import secrets
import time

BASE = "https://api.runpod.io/v2"
APPROVAL = "LAUNCH-PAID-POD"
POD_ID_RE = re.compile(r"^[A-Za-z0-9]{6,40}$")
IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"  # official template runpod-torch-v240
POD_FIELDS = ("id", "name", "status", "actions", "cost", "dataCenterId", "cloud", "locked", "startedAt", "cudaVersion")
HOURS_PER_MONTH = 720.0
JST = dt.timezone(dt.timedelta(hours=9))
NAME_RE = re.compile(r"^qwen-trial-\d{8}-\d{6}-[0-9a-f]{4}$")
CONSOLE_PODS_URL = "https://www.runpod.io/console/pods"
ESTIMATE_DISCLAIMER = (
    "これは事前の見積であり、自動停止や課金上限を保証するものではありません。"
    "停止は人が確認し、Billingで実額を照合してください。"
)


class PodApiError(RuntimeError):
    pass


class StopFailed(PodApiError):
    pass


class CreateUncertain(PodApiError):
    """The create call timed out or failed in a way that may still have created a billed pod."""

    def __init__(self, name: str, reason: str):
        self.pod_name = name
        super().__init__(create_uncertain_message(name, reason))


def estimate_cost(price_per_hr: float, hours: float, *, container_gb: int = 20, volume_gb: int = 80,
                  volume_stopped_hours: float = 0.0) -> float:
    """Plan formula: GPU + 0.10/GB/month container & volume while running + 0.20/GB/month volume while stopped."""
    storage = (container_gb * 0.10 * hours + volume_gb * (0.10 * hours + 0.20 * volume_stopped_hours)) / HOURS_PER_MONTH
    return round(price_per_hr * hours + storage, 4)


def estimate_session(price_per_hr: float, launch_at: dt.datetime, stop_at: dt.datetime, *,
                     stop_lag_min: float = 10.0, hold_hours_after_stop: float = 24.0, margin: float = 0.20,
                     container_gb: int = 20, volume_gb: int = 80) -> dict:
    """Estimate for one session: run from launch to the stop target (+ a lag for the stop to take effect),
    the Pod Volume kept after stop until it is deleted, and a margin for tax/fees/rounding.

    The margin is an assumption, not a published rate. This is an estimate only; it neither stops the Pod
    nor caps spending.
    """
    run_h = (stop_at - launch_at).total_seconds() / 3600.0 + stop_lag_min / 60.0
    if run_h <= 0:
        raise PodApiError("stop target is not after the launch time")
    gpu = price_per_hr * run_h
    storage_running = (container_gb * 0.10 + volume_gb * 0.10) * run_h / HOURS_PER_MONTH
    storage_stopped = volume_gb * 0.20 * hold_hours_after_stop / HOURS_PER_MONTH
    subtotal = gpu + storage_running + storage_stopped
    total = subtotal * (1 + margin)
    return {
        "launch_at": launch_at.isoformat(timespec="minutes"), "stop_at": stop_at.isoformat(timespec="minutes"),
        "run_hours": round(run_h, 3), "gpu_usd": round(gpu, 4), "storage_running_usd": round(storage_running, 4),
        "storage_after_stop_usd": round(storage_stopped, 4), "hold_hours_after_stop": hold_hours_after_stop,
        "subtotal_usd": round(subtotal, 4), "margin_pct": round(margin * 100, 1), "total_usd": round(total, 4),
        "disclaimer": ESTIMATE_DISCLAIMER,
    }


def make_trial_name(now: dt.datetime | None = None) -> str:
    """Unique, searchable name: lets a person find the pod in the list/console if the create call is ambiguous."""
    now = now or dt.datetime.now(JST)
    return f"qwen-trial-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def build_create_body(*, name: str, gpu_id: str, data_center_ids: list[str] | None = None,
                      container_gb: int = 20, volume_gb: int = 80) -> dict:
    """Body for POST /v2/pods. Secure cloud, 1 GPU, Jupyter on, no SSH, no network volume, no env/secrets."""
    if not NAME_RE.match(name):
        raise PodApiError("pod name must come from make_trial_name() so it can be searched for later")
    body = {
        "name": name,
        "cloud": "SECURE",
        "image": IMAGE,
        "gpu": {"id": gpu_id, "count": 1},
        "disk": container_gb,
        "mounts": {"persistent": {"size": volume_gb, "path": "/workspace"}},
        "ports": ["8888/http", "7860/http"],
        "startJupyter": True,
        "startSsh": False,
    }
    if data_center_ids:
        body["dataCenterIds"] = list(data_center_ids)
    return body


class PodApi:
    def __init__(self, session=None, base: str = BASE, timeout: float = 30):
        if session is None:
            import requests

            session = requests.Session()
        self.s, self.base, self.timeout = session, base.rstrip("/"), timeout

    def _call(self, method: str, path: str, **kw):
        r = self.s.request(method, self.base + path, timeout=self.timeout, **kw)
        body = None
        if r.content:
            try:
                body = r.json()
            except ValueError:
                body = None
        return r.status_code, body

    # ---- read-only
    def gpu_availability(self, gpu_id: str) -> dict:
        code, body = self._call("GET", "/catalog/gpus", params={
            "include": "AVAILABILITY", "product": "POD", "cloud": "SECURE", "count": 1})
        if code != 200 or not body:
            raise PodApiError(f"catalog/gpus: HTTP {code}")
        for g in body.get("gpus", []):
            if g.get("id") == gpu_id:
                return {"id": gpu_id, "secure_price_per_hr": g["price"]["secure"],
                        "availability": g.get("availability"), "max_count": g.get("maxCount", {}).get("secure")}
        raise PodApiError(f"GPU type not in catalog: {gpu_id}")

    def datacenters_with_gpu(self, gpu_id: str) -> list[dict]:
        code, body = self._call("GET", "/catalog/datacenters", params={"include": "GPU_AVAILABILITY"})
        if code != 200 or not body:
            raise PodApiError(f"catalog/datacenters: HTTP {code}")
        out = []
        for dc in body.get("dataCenters", []):
            for g in dc.get("gpuAvailability") or dc.get("gpus") or dc.get("availability") or []:
                if g.get("id") == gpu_id and g.get("availability") not in (None, "NONE"):
                    out.append({"id": dc["id"], "availability": g["availability"]})
        order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        return sorted(out, key=lambda d: order.get(d["availability"], 9))

    def get_pod(self, pod_id: str) -> dict:
        _check_id(pod_id)
        code, body = self._call("GET", f"/pods/{pod_id}")
        if code != 200 or not isinstance(body, dict):
            raise PodApiError(f"GET pod: HTTP {code}")
        out = {k: body.get(k) for k in POD_FIELDS}
        out["has_cost"] = "cost" in body  # distinguishes "missing" from null/0
        gpu = body.get("gpu") or {}
        out["gpu_id"], out["gpu_count"] = gpu.get("id"), gpu.get("count")
        return out

    # ---- paid action (guarded)
    def create_pod(self, body: dict, *, approval: str, estimate: dict, limit_usd: float = 10.0) -> dict:
        """POST /v2/pods once. Never retries. On a timeout/connection error/5xx the pod may exist and be billed:
        raises CreateUncertain (look it up by name before doing anything else)."""
        if approval != APPROVAL:
            raise PodApiError("paid launch not approved")
        if estimate["total_usd"] > limit_usd:
            raise PodApiError(
                f"estimate ${estimate['total_usd']} is above your limit ${limit_usd}. "
                "(The estimate check is not a spending cap.)")
        name = body["name"]
        try:
            code, resp = self._call("POST", "/pods", json=body)
        except Exception as exc:  # noqa: BLE001 - timeouts and connection errors are indistinguishable from "created"
            raise CreateUncertain(name, type(exc).__name__) from None
        if code >= 500:
            raise CreateUncertain(name, f"HTTP {code}")
        if code != 201 or not isinstance(resp, dict) or "id" not in resp:
            raise PodApiError(f"POST pods: HTTP {code} (not created)")  # response body is not echoed
        return {k: resp.get(k) for k in POD_FIELDS}

    def find_pods_by_name(self, name: str, max_pages: int = 5) -> list[dict]:
        """Read-only: exact-name match over GET /v2/pods (paged). Allowlisted fields only."""
        found, cursor = [], None
        for _ in range(max_pages):
            params = {"limit": 1000, **({"cursor": cursor} if cursor else {})}
            code, body = self._call("GET", "/pods", params=params)
            if code != 200 or not isinstance(body, dict):
                raise PodApiError(f"GET pods: HTTP {code}")
            found += [{k: p.get(k) for k in POD_FIELDS} for p in body.get("pods", []) if p.get("name") == name]
            page = body.get("pagination") or {}
            cursor = page.get("nextCursor")
            if not page.get("hasNextPage") or not cursor:
                break
        return found

    # ---- stop
    def stop_pod(self, pod_id: str) -> int:
        _check_id(pod_id)
        code, _ = self._call("POST", f"/pods/{pod_id}/action", json={"action": "stop"})
        return code

    def stop_and_confirm(self, pod_id: str, *, timeout_s: float = 300, interval_s: float = 10,
                         sleep=None, clock=None) -> dict:
        """Stop, then read back until the deadline. Confirmed only when status is EXITED *and* ``cost`` is
        present and the number 0 (missing/null/bool/other values are not treated as 0). Communication errors
        do not end the loop early. Raises StopFailed (with the pod id and console steps) otherwise."""
        _check_id(pod_id)
        sleep, clock = sleep or time.sleep, clock or time.monotonic  # resolved per call, so tests can patch them
        deadline = clock() + timeout_s
        stop_code, last, last_error = None, None, None
        while True:
            try:
                if stop_code != 200:
                    stop_code = self.stop_pod(pod_id)  # a 409 (already stopping/stopped) falls through to the read-back
                last = self.get_pod(pod_id)
                if cost_is_confirmed_zero(last) and last.get("status") == "EXITED":
                    return last
            except Exception as exc:  # noqa: BLE001 - keep trying until the deadline
                last_error = type(exc).__name__
            if clock() >= deadline:
                break
            sleep(interval_s)
        raise StopFailed(console_stop_instructions(pod_id, stop_code, last, last_error))


def cost_is_confirmed_zero(pod: dict | None) -> bool:
    """True only if ``cost`` is present and is the number 0 (not None, not missing, not a bool or string)."""
    if not pod or not pod.get("has_cost"):
        return False
    c = pod.get("cost")
    return isinstance(c, (int, float)) and not isinstance(c, bool) and c == 0


def _check_id(pod_id: str) -> None:
    if not POD_ID_RE.match(pod_id or ""):
        raise PodApiError("invalid pod id")


def console_stop_instructions(pod_id: str, http_status: int | None, last: dict | None, error: str | None = None) -> str:
    state = (last or {}).get("status", "unknown")
    cost = "missing" if not (last or {}).get("has_cost") else repr((last or {}).get("cost"))
    return (
        f"STOP NOT CONFIRMED. Billing may still be running. (stop HTTP {http_status}, last status {state}, "
        f"last cost {cost}, last error {error or 'none'})\n"
        f"Target pod id: {pod_id}\n"
        f"Console steps: open {CONSOLE_PODS_URL} → find this pod id → Stop (not Terminate) →\n"
        "confirm the status becomes Exited and the hourly cost is 0. If the pod is locked, unlock it first.\n"
        "Do not touch other pods."
    )


def create_uncertain_message(name: str, reason: str) -> str:
    return (
        f"CREATE RESULT UNKNOWN ({reason}). A billed pod MAY have been created.\n"
        f"Search for the exact name: {name}\n"
        f"  python scripts/pod.py find {name}     (read-only)\n"
        f"  or the console: {CONSOLE_PODS_URL}\n"
        "Do NOT run create again until you have checked. If the pod exists, stop it with the stop command or the console."
    )
