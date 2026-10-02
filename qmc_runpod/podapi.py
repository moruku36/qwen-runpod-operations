"""Minimal RunPod REST API v2 client for one trial: read availability, create, read, stop.

Authentication is NOT handled here: no key is read, stored or sent by this code. Requests go out
without an Authorization header and rely on the environment-side credential injection. There is
deliberately no terminate/delete function; deletion stays a console action confirmed by a person.
Only allowlisted fields of API responses are ever returned or logged.
"""

from __future__ import annotations

import re
import time

BASE = "https://api.runpod.io/v2"
APPROVAL = "LAUNCH-PAID-POD"
POD_ID_RE = re.compile(r"^[A-Za-z0-9]{6,40}$")
IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"  # official template runpod-torch-v240
POD_FIELDS = ("id", "name", "status", "actions", "cost", "dataCenterId", "cloud", "locked", "startedAt", "cudaVersion")
HOURS_PER_MONTH = 720.0


class PodApiError(RuntimeError):
    pass


class StopFailed(PodApiError):
    pass


def estimate_cost(price_per_hr: float, hours: float, *, container_gb: int = 20, volume_gb: int = 80,
                  volume_stopped_hours: float = 0.0) -> float:
    """Plan formula: GPU + 0.10/GB/month container & volume while running + 0.20/GB/month volume while stopped."""
    storage = (container_gb * 0.10 * hours + volume_gb * (0.10 * hours + 0.20 * volume_stopped_hours)) / HOURS_PER_MONTH
    return round(price_per_hr * hours + storage, 4)


def build_create_body(*, name: str, gpu_id: str, data_center_ids: list[str] | None = None,
                      container_gb: int = 20, volume_gb: int = 80) -> dict:
    """Body for POST /v2/pods. Secure cloud, 1 GPU, Jupyter on, no SSH, no network volume, no env/secrets."""
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
        gpu = body.get("gpu") or {}
        out["gpu_id"], out["gpu_count"] = gpu.get("id"), gpu.get("count")
        return out

    # ---- paid action (guarded)
    def create_pod(self, body: dict, *, approval: str, price_per_hr: float, hours: float, budget_usd: float = 10.0) -> dict:
        if approval != APPROVAL:
            raise PodApiError("paid launch not approved")
        est = estimate_cost(price_per_hr, hours, container_gb=body["disk"],
                            volume_gb=body["mounts"]["persistent"]["size"])
        if est > budget_usd:
            raise PodApiError(f"estimated ${est} exceeds budget ${budget_usd}")
        code, resp = self._call("POST", "/pods", json=body)
        if code != 201 or not isinstance(resp, dict) or "id" not in resp:
            raise PodApiError(f"POST pods: HTTP {code}")  # response body is not echoed
        return {k: resp.get(k) for k in POD_FIELDS}

    # ---- stop
    def stop_pod(self, pod_id: str) -> int:
        _check_id(pod_id)
        code, _ = self._call("POST", f"/pods/{pod_id}/action", json={"action": "stop"})
        return code

    def stop_and_confirm(self, pod_id: str, *, timeout_s: float = 300, interval_s: float = 10,
                         sleep=time.sleep, clock=time.monotonic) -> dict:
        """POST stop, then read back with GET until status is EXITED and cost is 0. Raises StopFailed."""
        code = self.stop_pod(pod_id)
        last = None
        if code == 200:
            deadline = clock() + timeout_s
            while True:
                last = self.get_pod(pod_id)
                if last["status"] == "EXITED" and not last.get("cost"):
                    return last
                if clock() >= deadline:
                    break
                sleep(interval_s)
        raise StopFailed(console_stop_instructions(pod_id, code, last))


def _check_id(pod_id: str) -> None:
    if not POD_ID_RE.match(pod_id or ""):
        raise PodApiError("invalid pod id")


def console_stop_instructions(pod_id: str, http_status: int, last: dict | None) -> str:
    state = (last or {}).get("status", "unknown")
    return (
        f"Stop could not be confirmed (stop HTTP {http_status}, last status {state}).\n"
        f"Target pod id: {pod_id}\n"
        "Console steps: https://www.runpod.io/console/pods → open this pod id → Stop (not Terminate) →\n"
        "confirm status becomes Exited. If the pod is locked, unlock it first. Never delete other pods."
    )
