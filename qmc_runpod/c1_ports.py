"""Offline provider/bootstrap/cost/export ports for one minimal C1 flow.

No HTTP client, socket, subprocess, environment or credential discovery exists.
Provider request DTOs follow documented REST v1 shapes, but dispatcher always
refuses live execution. The harness pairs plans with existing mock effects only.
"""
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json
import re

from .ondemand import ControllerError, UsageLimits, _finite_number, _money


class PortError(ControllerError):
    pass


def identity(value):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise PortError("invalid_identity")
    return value


def sha(value, length=64):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value):
        raise PortError("invalid_pin")
    return value


@dataclass(frozen=True)
class PodSpec:
    account: str = "public-fixture-account"
    gpu_type: str = "NVIDIA A100 80GB PCIe"
    image: str = "public-fixture-image@sha256:" + "1" * 64
    volume_gb: int = 80
    container_gb: int = 20

    def __post_init__(self):
        identity(self.account)
        if (type(self.gpu_type) is not str or not 1 <= len(self.gpu_type) <= 128
                or not re.fullmatch(r"[A-Za-z0-9 ./_-]+", self.gpu_type)
                or type(self.image) is not str or not re.fullmatch(r"[A-Za-z0-9./_-]+@sha256:[0-9a-f]{64}", self.image)
                or any(type(n) is not int or not 1 <= n <= 200 for n in (self.volume_gb, self.container_gb))):
            raise PortError("invalid_pod_spec")


@dataclass(frozen=True)
class ProviderRequest:
    method: str
    path: str
    session_id: str
    operation_id: str
    body: bytes = field(repr=False)
    simulated: bool = True


class ProviderPlans:
    """Account/session/operation exact ownership from trusted create acknowledgement.

    Matching a name alone is NOT ownership. Unknown create requires a complete,
    external operation-index observation from the trusted provider/ledger port.
    API v1 does not promise an idempotency key; no blind POST retry is planned.
    """
    def __init__(self, spec=None):
        self.spec = spec if spec is not None else PodSpec()
        if type(self.spec) is not PodSpec: raise PortError("invalid_pod_spec")
        self.owned = {}
        self.attempted = set()
        self.requests = []

    def create(self, session_id, operation_id):
        identity(session_id); sha(operation_id)
        if operation_id in self.attempted: raise PortError("create_not_replayable")
        body = {"name": "qmc-" + hashlib.sha256(session_id.encode()).hexdigest()[:24],
                "cloudType": "SECURE", "computeType": "GPU", "interruptible": False,
                "gpuCount": 1, "gpuTypeIds": [self.spec.gpu_type], "imageName": self.spec.image,
                "volumeInGb": self.spec.volume_gb, "containerDiskInGb": self.spec.container_gb,
                "ports": ["22/tcp"], "supportPublicIp": True, "globalNetworking": False}
        self.attempted.add(operation_id)
        return self._record("POST", "/pods", session_id, operation_id, body)

    def acknowledge(self, session_id, operation_id, response, *, account):
        identity(session_id); sha(operation_id)
        if account != self.spec.account or operation_id not in self.attempted or type(response) is not dict:
            raise PortError("ownership_not_proven")
        pod_id = identity(response.get("id"))
        if pod_id == "synthetic-held-pod": raise PortError("retained_pod_excluded")
        existing = self.owned.get(pod_id)
        owner = (session_id, operation_id, account)
        if existing is not None and existing != owner: raise PortError("ownership_not_proven")
        self.owned[pod_id] = owner
        return pod_id

    def read(self, session_id, pod_id, operation_id):
        self.check_owner(session_id, pod_id)
        return self._record("GET", "/pods/" + pod_id, session_id, operation_id, {})

    def terminate(self, session_id, pod_id, operation_id, allowlist):
        self.check_owner(session_id, pod_id); sha(operation_id)
        if allowlist != frozenset({pod_id}) or operation_id in self.attempted:
            raise PortError("termination_scope_or_replay")
        self.attempted.add(operation_id)
        return self._record("DELETE", "/pods/" + pod_id, session_id, operation_id, {})

    def check_owner(self, session_id, pod_id):
        identity(session_id); identity(pod_id)
        owner = self.owned.get(pod_id)
        if not owner or owner[0] != session_id or owner[2] != self.spec.account or pod_id == "synthetic-held-pod":
            raise PortError("ownership_not_proven")

    def dispatch(self, request):
        raise PortError("live_provider_execution_disabled")

    def _record(self, method, path, session_id, operation_id, body):
        sha(operation_id)
        result = ProviderRequest(method, path, session_id, operation_id,
                                 json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
        self.requests.append(result)
        return result


@dataclass(frozen=True)
class ServerPins:
    llama_commit: str = "4da6337767f973e2b4d0797e5b323d77d8565e4a"
    model_revision: str = "ff733b88376282017b7f4675d6d96536c6ffa712"
    model_sha256: str = "fb8413d0b5cec5ad7055e49630a986518ba90fdacc95a337aa535e8ff5bf0d16"
    model_file: str = "Huihui-Qwen3.8-27B-abliterated-UD-DW-Q8_K_L.gguf"
    alias: str = "qwen-27b"
    context: int = 8192

    def __post_init__(self):
        sha(self.llama_commit, 40); sha(self.model_revision, 40); sha(self.model_sha256)
        if (self.alias != "qwen-27b" or type(self.context) is not int or self.context != 8192
                or not re.fullmatch(r"[A-Za-z0-9_.-]+\.gguf", self.model_file)):
            raise PortError("invalid_model_profile")


class BootstrapPlans:
    """Identity/readiness observations are trusted fixture proofs, not health claims.

    The actual SSH host, secret-file provisioning, known_hosts and service process
    attestation are C2 configuration. No SSH program is called by this port.
    Baseline source pins are historical immutable selections, not a claim that
    today's cached diagnostic used exactly these artifacts.
    """
    def __init__(self, pins=None):
        self.pins = pins if pins is not None else ServerPins()
        if type(self.pins) is not ServerPins: raise PortError("invalid_model_profile")
        self.observations = {}

    def plan(self, session_id, pod_id):
        identity(session_id); identity(pod_id)
        return {"session_id": session_id, "pod_id": pod_id, "model_revision": self.pins.model_revision,
                "model_sha256": self.pins.model_sha256, "llama_commit": self.pins.llama_commit,
                "bind": "127.0.0.1", "pod_port": 8080, "forward_bind": "127.0.0.1", "forward_port": 19181,
                "alias": self.pins.alias, "context": self.pins.context, "gpu_layers": 99,
                "model_auth_required": True, "host_fingerprint_required": True,
                "execute": False, "simulated": True}

    def attest_mock(self, session_id, pod_id, *, fingerprint, model_sha256, llama_commit,
                    alias="qwen-27b", context=8192, authenticated=False, process_owned=False):
        identity(session_id); identity(pod_id); sha(fingerprint)
        if (model_sha256 != self.pins.model_sha256 or llama_commit != self.pins.llama_commit
                or alias != self.pins.alias or type(context) is not int or context != self.pins.context
                or authenticated is not True or process_owned is not True):
            raise PortError("readiness_not_proven")
        self.observations[(session_id, pod_id)] = fingerprint

    def ready(self, session_id, pod_id):
        return (session_id, pod_id) in self.observations


@dataclass(frozen=True)
class Rate:
    gpu_usd_per_hour: Decimal = Decimal("1.80")
    storage_usd_per_hour: Decimal = Decimal("0.05")
    overhead_usd: Decimal = Decimal("0")

    def __post_init__(self):
        for value in (self.gpu_usd_per_hour, self.storage_usd_per_hour, self.overhead_usd):
            if type(value) is not Decimal or _money(value, "rate") != value: raise PortError("invalid_rate")
        if self.gpu_usd_per_hour <= 0: raise PortError("invalid_rate")

    def cost(self, started_at, now):
        elapsed = max(0, _finite_number(now, "now") - _finite_number(started_at, "started_at"))
        return (self.gpu_usd_per_hour + self.storage_usd_per_hour) * Decimal(str(elapsed)) / 3600 + self.overhead_usd

    def work_allowed(self, limits, started_at, now):
        if type(limits) is not UsageLimits: raise PortError("typed_grant_required")
        reserve = self.cost(0, limits.cleanup_runtime_seconds) - self.overhead_usd
        if reserve > limits.cleanup_usd: return False
        return self.cost(started_at, now) < limits.max_usd - limits.cleanup_usd


class ExportReadback:
    """One bounded synthetic artifact retained outside disposable mock Pod state."""
    def __init__(self):
        self._objects = {}

    def save(self, session_id, pod_id, pins, events):
        identity(session_id); identity(pod_id)
        if type(pins) is not ServerPins or type(events) is not list or len(events) > 256:
            raise PortError("invalid_export")
        if any(type(e) is not str or not re.fullmatch(r"[a-z_]{1,64}", e) for e in events):
            raise PortError("invalid_export")
        data = json.dumps({"session_id": session_id, "pod_id": pod_id, "model_sha256": pins.model_sha256,
                           "llama_commit": pins.llama_commit, "events": events, "simulated": True}, sort_keys=True).encode()
        digest = hashlib.sha256(data).hexdigest()
        self._objects[digest] = data
        return digest

    def verify(self, digest):
        sha(digest)
        return digest in self._objects and hashlib.sha256(self._objects[digest]).hexdigest() == digest
