"""CPU-only, fail-closed lifecycle controller for a future on-demand gateway.

This module deliberately contains no RunPod/Open WebUI integration. Its default
ports are deterministic mocks; callers must inject ports explicitly. All audit
entries identify simulated behavior and exclude prompts, responses and secrets.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Callable, Protocol


class ControllerError(RuntimeError):
    """A requested state transition is not currently safe."""


class CorruptCheckpoint(ControllerError):
    """Persisted state is malformed; effects are blocked until reconciled."""


class ChatAdmissionError(ControllerError):
    """Fixed public outcome for the mock HTTP boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class MockChatTicket:
    session_id: str
    request_id: str
    pod_id: str


class Phase(str, Enum):
    ABSENT = "absent"
    PROVISIONING = "provisioning"
    CREATE_UNRESOLVED = "create_unresolved"
    BOOTSTRAPPING = "bootstrapping"
    LOADING = "loading"
    READY = "ready"
    DRAINING = "draining"
    EXPORTING = "exporting"
    TERMINATION_APPROVAL_PENDING = "termination_approval_pending"
    TERMINATING = "terminating"
    TERMINATION_UNCONFIRMED = "termination_unconfirmed"
    CLEANUP_EXPIRED = "cleanup_expired"
    FAILED = "failed"


CATALOG: tuple[dict[str, str], ...] = (
    {"id": "qwen-27b", "object": "model", "owned_by": "local-simulated"},
)
SCHEMA_VERSION = 2
REQUIRED_ACTIONS = frozenset({"create", "bootstrap", "load", "chat", "export", "terminate"})
CHAT_COST_USD = Decimal("0.01")
EXPORT_COST_USD = Decimal("0.0001")
TERMINATE_COST_USD = Decimal("0.0001")
EFFECT_SECONDS = {"create": 0.0, "bootstrap": 0.0, "load": 0.0,
                  "chat": 1.0, "export": 1.0, "terminate": 1.0}


def _finite_number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0) or (not positive and result < 0):
        raise ValueError(f"{label} is outside its valid range")
    return result


def _money(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be a finite non-negative amount")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} must be a finite non-negative amount") from None
    if not result.is_finite() or result < 0:
        raise ValueError(f"{label} must be a finite non-negative amount")
    return result


@dataclass(frozen=True)
class UsageScope:
    """An explicit session/target/action scope; not an account credential."""

    session_id: str
    target: str
    actions: frozenset[str]

    def __post_init__(self) -> None:
        if (type(self.session_id) is not str or type(self.target) is not str
                or not self.session_id or not self.target or not isinstance(self.actions, frozenset)
                or not self.actions or any(not isinstance(item, str) for item in self.actions)):
            raise ValueError("usage scope requires a session, target and actions")
        if not self.actions <= REQUIRED_ACTIONS:
            raise ValueError("usage scope contains an unknown action")


@dataclass(frozen=True)
class UsageLimits:
    max_runtime_seconds: float
    max_usd: Decimal
    expires_at: float
    scope: UsageScope
    cleanup_runtime_seconds: float
    cleanup_usd: Decimal

    def __post_init__(self) -> None:
        _finite_number(self.max_runtime_seconds, "max_runtime_seconds", positive=True)
        if not isinstance(self.max_usd, Decimal) or _money(self.max_usd, "max_usd") <= 0:
            raise ValueError("max_usd must be a positive Decimal")
        _finite_number(self.expires_at, "expires_at", positive=True)
        if not isinstance(self.scope, UsageScope):
            raise ValueError("scope is required")
        cleanup_seconds = _finite_number(self.cleanup_runtime_seconds, "cleanup_runtime_seconds", positive=True)
        if (cleanup_seconds < EFFECT_SECONDS["export"] + EFFECT_SECONDS["terminate"]
                or not isinstance(self.cleanup_usd, Decimal)
                or not EXPORT_COST_USD + TERMINATE_COST_USD <= self.cleanup_usd < self.max_usd):
            raise ValueError("explicit cleanup time/USD reserve must fit the total usage limit")

    @classmethod
    def create(
        cls, *, max_runtime_seconds: object, max_usd: object,
        expires_at: object, scope: UsageScope,
        cleanup_runtime_seconds: object = None, cleanup_usd: object = None,
    ) -> "UsageLimits":
        seconds = _finite_number(max_runtime_seconds, "max_runtime_seconds", positive=True)
        deadline = _finite_number(expires_at, "expires_at", positive=True)
        cost = _money(max_usd, "max_usd")
        if cost <= 0:
            raise ValueError("max_usd must be positive")
        if not isinstance(scope, UsageScope):
            raise ValueError("scope is required")
        return cls(seconds, cost, deadline, scope,
                   _finite_number(cleanup_runtime_seconds, "cleanup_runtime_seconds", positive=True),
                   _money(cleanup_usd, "cleanup_usd"))


@dataclass(frozen=True)
class TerminationApproval:
    """One-use approval tied to an exact session, Pod and verified export."""

    approval_id: str
    action: str
    session_id: str
    pod_id: str
    receipt_sha256: str
    expires_at: float

    def __post_init__(self) -> None:
        for value in (self.approval_id, self.action, self.session_id, self.pod_id, self.receipt_sha256):
            if type(value) is not str or not value:
                raise ValueError("approval identities/action must be non-empty strings")
        if self.action != "terminate" or len(self.receipt_sha256) != 64:
            raise ValueError("approval must target terminate and an exact SHA256 receipt")
        if any(char not in "0123456789abcdef" for char in self.receipt_sha256):
            raise ValueError("approval receipt hash is invalid")
        _finite_number(self.expires_at, "approval.expires_at", positive=True)


@dataclass(frozen=True)
class ArtifactReceipt:
    session_id: str
    pod_id: str
    artifact_root: str
    sha256: str
    simulated: bool = True


@dataclass(frozen=True)
class ReconcileResult:
    outcome: str  # found | unknown | absent
    pod_ids: tuple[str, ...] = ()
    complete: bool = False


@dataclass(frozen=True)
class Clock:
    now: Callable[[], float] = time.time


class Provider(Protocol):
    def create(self, session_id: str, operation_id: str) -> str: ...
    def reconcile_create(self, session_id: str, operation_id: str) -> ReconcileResult: ...
    def bootstrap(self, session_id: str, pod_id: str, operation_id: str) -> None: ...
    def terminate(self, session_id: str, pod_id: str, operation_id: str, allowlist: frozenset[str]) -> None: ...
    def is_absent(self, session_id: str, pod_id: str) -> bool: ...


class Transport(Protocol):
    def load(self, session_id: str, pod_id: str, operation_id: str) -> None: ...
    def chat(self, session_id: str, pod_id: str, request_id: str, prompt: str) -> str: ...


class ArtifactStore(Protocol):
    def export(self, session_id: str, pod_id: str, operation_id: str) -> ArtifactReceipt: ...
    def verify(self, receipt: ArtifactReceipt, session_id: str, pod_id: str) -> bool: ...


def _empty_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION, "phase": Phase.ABSENT.value, "session": None,
        "audit": [], "used_approvals": [], "closed_sessions": [], "last_now": 0.0,
    }


def _validate_state(state: object) -> dict[str, Any]:
    if (not isinstance(state, dict) or isinstance(state.get("schema"), bool)
            or state.get("schema") != SCHEMA_VERSION):
        raise CorruptCheckpoint("unsupported checkpoint schema")
    try:
        phase = Phase(state["phase"])
        last_now = _finite_number(state["last_now"], "last_now")
        audit = state["audit"]
        used = state["used_approvals"]
        closed = state["closed_sessions"]
        session = state["session"]
    except (KeyError, TypeError, ValueError) as exc:
        raise CorruptCheckpoint("checkpoint fields are invalid") from exc
    if not isinstance(audit, list) or not isinstance(used, list) or not isinstance(closed, list):
        raise CorruptCheckpoint("checkpoint journals are invalid")
    if any(not isinstance(row, dict) or row.get("simulated") is not True for row in audit):
        raise CorruptCheckpoint("audit journal is invalid")
    if any(not isinstance(row.get("event"), str) for row in audit):
        raise CorruptCheckpoint("audit event is invalid")
    if any(not isinstance(item, str) for item in used + closed):
        raise CorruptCheckpoint("checkpoint identity journal is invalid")
    if (phase == Phase.ABSENT) != (session is None):
        raise CorruptCheckpoint("checkpoint phase/session relationship is invalid")
    if session is not None:
        required = {"id", "phase", "target", "limits", "started_at", "last_activity", "deadline", "cleanup_deadline", "effects", "idle_timeout_seconds", "idle_deadline", "pod_id", "pending", "queue", "active", "spent_usd", "receipt", "approval", "completed", "terminate_attempted"}
        if not isinstance(session, dict) or not required <= session.keys():
            raise CorruptCheckpoint("session checkpoint is incomplete")
        if session["phase"] != phase.value or not isinstance(session["queue"], list):
            raise CorruptCheckpoint("session phase/queue is invalid")
        if not isinstance(session["id"], str) or not session["id"]:
            raise CorruptCheckpoint("session identity is invalid")
        try:
            limits = session["limits"]
            if not isinstance(limits, dict) or session["target"] != "new-pod": raise ValueError
            if limits.get("target") != session["target"]: raise ValueError
            actions = limits["actions"]
            if (not isinstance(actions, list) or actions != sorted(actions)
                    or not REQUIRED_ACTIONS <= set(actions)): raise ValueError
            seconds = _finite_number(limits["max_runtime_seconds"], "max_runtime_seconds", positive=True)
            expiry = _finite_number(limits["expires_at"], "expires_at", positive=True)
            typed = UsageLimits.create(max_runtime_seconds=seconds, max_usd=limits["max_usd"],
                expires_at=expiry, scope=UsageScope(session["id"], session["target"], frozenset(actions)),
                cleanup_runtime_seconds=limits["cleanup_runtime_seconds"], cleanup_usd=limits["cleanup_usd"])
            started = _finite_number(session["started_at"], "started_at")
            deadline = _finite_number(session["deadline"], "deadline", positive=True)
            activity = _finite_number(session["last_activity"], "last_activity")
            idle_timeout = _finite_number(session["idle_timeout_seconds"], "idle_timeout_seconds", positive=True)
            idle_deadline = _finite_number(session["idle_deadline"], "idle_deadline", positive=True)
            if (deadline != min(started + seconds, expiry - typed.cleanup_runtime_seconds) or activity < started
                    or idle_deadline != activity + idle_timeout): raise ValueError
            if session["cleanup_deadline"] != min(started + seconds + typed.cleanup_runtime_seconds, expiry):
                raise ValueError
            for action in ("create", "bootstrap", "load", "export", "terminate"):
                expected = hashlib.sha256(f"{session['id']}:{action}".encode()).hexdigest()
                if session[f"{action}_operation"] != expected: raise ValueError
            if session["pod_id"] is not None and (not isinstance(session["pod_id"], str) or not session["pod_id"]): raise ValueError
            if not isinstance(session["pending"], dict) or not isinstance(session["completed"], list): raise ValueError
            if any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
                   or not isinstance(item.get("prompt"), str) for item in session["queue"]): raise ValueError
            if len({item["id"] for item in session["queue"]}) != len(session["queue"]): raise ValueError
            if any(not isinstance(item, str) for item in session["completed"]): raise ValueError
            if not isinstance(session["effects"], dict) or type(session["terminate_attempted"]) is not bool:
                raise ValueError
            active = session["active"]
            if active is not None and (phase != Phase.READY or not isinstance(active, dict)
                    or type(active.get("id")) is not str or type(active.get("prompt")) is not str):
                raise ValueError
            if phase in {Phase.DRAINING, Phase.EXPORTING, Phase.TERMINATION_APPROVAL_PENDING,
                         Phase.TERMINATING, Phase.TERMINATION_UNCONFIRMED, Phase.CLEANUP_EXPIRED} and session["queue"]:
                raise ValueError
            if phase in {Phase.TERMINATION_APPROVAL_PENDING, Phase.TERMINATING, Phase.TERMINATION_UNCONFIRMED}:
                receipt = session["receipt"]
                if (not isinstance(receipt, dict) or receipt.get("session_id") != session["id"]
                        or receipt.get("pod_id") != session["pod_id"] or receipt.get("simulated") is not True):
                    raise ValueError
            if phase in {Phase.TERMINATING, Phase.TERMINATION_UNCONFIRMED}:
                approval = session["approval"]
                TerminationApproval(approval["id"], approval["action"], approval["session_id"],
                                    approval["pod_id"], approval["receipt_sha256"], approval["expires_at"])
            if session["terminate_attempted"]:
                if phase != Phase.TERMINATION_UNCONFIRMED or not session["approval_used"]:
                    raise ValueError
                if session["approval"]["id"] not in used: raise ValueError
            _money(session["spent_usd"], "spent_usd")
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise CorruptCheckpoint("session constraints are invalid") from exc
    return state


class _MemoryLock:
    def __init__(self, owner: "MemoryStateStore") -> None:
        self.owner = owner
        self.state: dict[str, Any] | None = None

    def __enter__(self) -> "_MemoryLock":
        self.owner._lock.acquire()
        try:
            self.state = self.owner._decode(self.owner._raw)
        except BaseException:
            self.owner._lock.release()
            raise
        return self

    def load(self) -> dict[str, Any]:
        assert self.state is not None
        return self.state

    def save(self) -> None:
        assert self.state is not None
        _validate_state(self.state)
        self.owner._raw = json.dumps(self.state, sort_keys=True, separators=(",", ":"))

    def __exit__(self, *_: object) -> None:
        self.owner._lock.release()


class MemoryStateStore:
    """Shared, serialized checkpoint store for deterministic tests and mocks."""

    def __init__(self, initial_json: str | None = None) -> None:
        self._lock = threading.RLock()
        self._raw = initial_json or json.dumps(_empty_state())

    @staticmethod
    def _decode(raw: str) -> dict[str, Any]:
        try:
            return _validate_state(json.loads(raw))
        except (json.JSONDecodeError, CorruptCheckpoint) as exc:
            raise CorruptCheckpoint("checkpoint cannot be read") from exc

    def locked(self) -> _MemoryLock:
        return _MemoryLock(self)

    def snapshot(self) -> dict[str, Any]:
        with self.locked() as lock:
            return json.loads(json.dumps(lock.load()))


class _FileLock:
    def __init__(self, owner: "JsonStateStore") -> None:
        self.owner = owner
        self.file: Any = None
        self.acquired = False
        self.state: dict[str, Any] | None = None

    def __enter__(self) -> "_FileLock":
        self.owner._thread_lock.acquire()
        try:
            self.owner.path.parent.mkdir(parents=True, exist_ok=True)
            self.file = open(str(self.owner.path) + ".lock", "a+b")
            if os.name == "nt":
                import msvcrt
                # Reading byte zero before locking races with another Windows
                # holder: mandatory byte locks reject the read itself.
                if os.fstat(self.file.fileno()).st_size == 0:
                    self.file.write(b"\0"); self.file.flush()
                self.file.seek(0); msvcrt.locking(self.file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX)
            self.acquired = True
            self.state = self.owner._read_unlocked()
            return self
        except BaseException:
            try: self._release()
            finally: self.owner._thread_lock.release()
            raise

    def load(self) -> dict[str, Any]:
        assert self.state is not None
        return self.state

    def save(self) -> None:
        assert self.state is not None
        _validate_state(self.state)
        encoded = json.dumps(self.state, sort_keys=True, separators=(",", ":")).encode()
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.owner.path.name}.", dir=self.owner.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp_name, self.owner.path)
        finally:
            if os.path.exists(temp_name): os.unlink(temp_name)

    def __exit__(self, *_: object) -> None:
        try:
            self._release()
        finally:
            self.owner._thread_lock.release()

    def _release(self) -> None:
        if self.file is None: return
        try:
            if self.acquired:
                if os.name == "nt":
                    import msvcrt
                    self.file.seek(0); msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass  # Closing the descriptor also releases its OS lock.
        finally:
            self.file.close()
            self.file = None
            self.acquired = False


class JsonStateStore:
    """Atomic JSON checkpoint with a separate OS lock file for concurrent controllers."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._thread_lock = threading.RLock()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists(): return _empty_state()
        try:
            return _validate_state(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, CorruptCheckpoint) as exc:
            raise CorruptCheckpoint("checkpoint cannot be read; effects are disabled") from exc

    def locked(self) -> _FileLock:
        return _FileLock(self)

    def snapshot(self) -> dict[str, Any]:
        with self.locked() as lock:
            return json.loads(json.dumps(lock.load()))


class MockProvider:
    """In-memory provider fake. It records idempotent operations only."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.pods: dict[str, str] = {}
        self.results: dict[str, str] = {}
        self.completed: set[str] = set()
        self.termination_allowlists: list[frozenset[str]] = []
        self.create_mode = "success"
        self.terminate_mode = "success"

    def create(self, session_id: str, operation_id: str) -> str:
        self.calls.append(("create", operation_id))
        if self.create_mode == "unknown": raise TimeoutError("simulated ambiguous create")
        if operation_id not in self.results:
            pod_id = "mock-" + hashlib.sha256((session_id + operation_id).encode()).hexdigest()[:12]
            self.results[operation_id] = pod_id; self.pods[pod_id] = session_id
        if self.create_mode == "timeout_after_create": raise TimeoutError("simulated lost create response")
        if self.create_mode == "malformed": return ""
        return self.results[operation_id]

    def reconcile_create(self, session_id: str, operation_id: str) -> ReconcileResult:
        self.calls.append(("reconcile", operation_id))
        pod_id = self.results.get(operation_id)
        if pod_id and self.pods.get(pod_id) == session_id:
            return ReconcileResult("found", (pod_id,), True)
        return ReconcileResult("unknown", (), False)

    def bootstrap(self, session_id: str, pod_id: str, operation_id: str) -> None:
        if operation_id in self.completed: return
        self.calls.append(("bootstrap", operation_id))
        if self.pods.get(pod_id) != session_id: raise ControllerError("mock Pod ownership mismatch")
        self.completed.add(operation_id)

    def terminate(self, session_id: str, pod_id: str, operation_id: str, allowlist: frozenset[str]) -> None:
        if operation_id in self.completed: return
        self.calls.append(("terminate", operation_id))
        self.termination_allowlists.append(allowlist)
        if allowlist != frozenset({pod_id}) or self.pods.get(pod_id) != session_id:
            raise ControllerError("mock exact-Pod allowlist/owner mismatch")
        if self.terminate_mode == "success":
            self.pods.pop(pod_id, None); self.completed.add(operation_id)

    def is_absent(self, session_id: str, pod_id: str) -> bool:
        self.calls.append(("absence", pod_id))
        return pod_id not in self.pods


class MockTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.outputs: dict[tuple[str, str], str] = {}
        self.response = "simulated response"
        self.completed: set[str] = set()
        self.fail_next_chat = False

    def load(self, session_id: str, pod_id: str, operation_id: str) -> None:
        if operation_id in self.completed: return
        self.calls.append(("load", operation_id))
        self.completed.add(operation_id)

    def chat(self, session_id: str, pod_id: str, request_id: str, prompt: str) -> str:
        if self.fail_next_chat:
            self.fail_next_chat = False
            raise TimeoutError("simulated chat timeout")
        key = (session_id, request_id)
        if key not in self.outputs:
            self.calls.append(("chat", request_id))
            self.outputs[key] = self.response
        return self.outputs[key]


class MockArtifactStore:
    def __init__(self) -> None:
        self.receipts: dict[str, ArtifactReceipt] = {}
        self.tamper = False

    def export(self, session_id: str, pod_id: str, operation_id: str) -> ArtifactReceipt:
        digest = hashlib.sha256(f"simulation:{session_id}:{pod_id}:{operation_id}".encode()).hexdigest()
        receipt = self.receipts.setdefault(operation_id, ArtifactReceipt(session_id, pod_id, f"mock://{session_id}", digest))
        if self.tamper: return ArtifactReceipt(receipt.session_id, receipt.pod_id, receipt.artifact_root, "0" * 64)
        return receipt

    def verify(self, receipt: ArtifactReceipt, session_id: str, pod_id: str) -> bool:
        known = any(receipt == value for value in self.receipts.values())
        return (known and type(receipt.simulated) is bool and receipt.simulated is True
                and receipt.session_id == session_id and receipt.pod_id == pod_id
                and len(receipt.sha256) == 64 and receipt.sha256 != "0" * 64)


def _limits_dict(limits: UsageLimits) -> dict[str, Any]:
    return {"max_runtime_seconds": limits.max_runtime_seconds, "max_usd": str(limits.max_usd),
            "expires_at": limits.expires_at, "target": limits.scope.target,
            "actions": sorted(limits.scope.actions),
            "cleanup_runtime_seconds": limits.cleanup_runtime_seconds,
            "cleanup_usd": str(limits.cleanup_usd)}


class MockAuthority:
    """Injected, trusted simulation ledger separate from untrusted checkpoints.

    It is deliberately in-memory, with no credential/key or OS security setup.
    An active checkpoint cannot restore authority into a fresh ledger. A caller
    must retain and explicitly inject the original trusted ledger for mock
    restart. Production durable authority/elapsed recovery is not implemented.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._digest: str | None = None
        self._high_water = 0.0
        self._records: dict[str, dict[str, Any]] = {}
        self._used_approvals: list[str] = []
        self._closed: list[str] = []

    @staticmethod
    def _hash(state: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def check(self, state: dict[str, Any]) -> None:
        with self._lock:
            digest = self._hash(state)
            if self._digest is None:
                if state != _empty_state() or self._records:
                    raise CorruptCheckpoint("checkpoint cannot create or restore execution authority")
                self._digest = digest
            if digest != self._digest:
                raise CorruptCheckpoint("checkpoint differs from the injected trusted authority")
            if (state["used_approvals"] != self._used_approvals
                    or state["closed_sessions"] != self._closed
                    or state["last_now"] != self._high_water):
                raise CorruptCheckpoint("checkpoint history differs from trusted authority")
            session = state["session"]
            if session:
                record = self._record(session["id"])
                if (session["limits"] != _limits_dict(record["limits"])
                        or session["started_at"] != record["started_at"]
                        or session["deadline"] != record["deadline"]
                        or session["cleanup_deadline"] != record["cleanup_deadline"]
                        or session["idle_timeout_seconds"] != record["idle_timeout_seconds"]
                        or session["spent_usd"] != str(record["work_spent"] + record["cleanup_spent"])
                        or session["effects"] != record["effects"]):
                    raise CorruptCheckpoint("checkpoint constraints/usage differ from trusted authority")

    def observe(self, raw_now: float) -> float:
        with self._lock:
            self._high_water = max(self._high_water, _finite_number(raw_now, "clock"))
            return self._high_water

    def register(self, session_id: str, limits: UsageLimits, now: float, idle: float) -> dict[str, Any]:
        with self._lock:
            if session_id in self._records:
                raise ControllerError("session identifiers cannot be reused")
            deadline = min(now + limits.max_runtime_seconds,
                           limits.expires_at - limits.cleanup_runtime_seconds)
            cleanup_deadline = min(now + limits.max_runtime_seconds + limits.cleanup_runtime_seconds,
                                   limits.expires_at)
            if now >= deadline or now >= cleanup_deadline:
                raise ControllerError("usage scope is expired or has no authorized work/cleanup window")
            record = {"limits": limits, "started_at": now, "deadline": deadline,
                      "cleanup_deadline": cleanup_deadline, "idle_timeout_seconds": idle,
                      "work_spent": Decimal("0"), "cleanup_spent": Decimal("0"), "effects": {}}
            self._records[session_id] = record
            return record

    def _record(self, session_id: str) -> dict[str, Any]:
        if session_id not in self._records or session_id in self._closed:
            raise ControllerError("no retained trusted authority for this session")
        return self._records[session_id]

    def bounds(self, session_id: str) -> tuple[float, float]:
        with self._lock:
            record = self._record(session_id)
            return record["deadline"], record["cleanup_deadline"]

    def check_effect(self, session_id: str, operation: str, action: str, now: float) -> None:
        with self._lock:
            record = self._record(session_id)
            limits = record["limits"]
            cleanup = action in {"export", "terminate"}
            deadline = record["cleanup_deadline"] if cleanup else record["deadline"]
            duration = EFFECT_SECONDS[action]
            if action == "export":
                duration += EFFECT_SECONDS["terminate"]  # Keep the individual deletion attempt's time slot.
            if now >= deadline or now + duration > deadline:
                raise ControllerError("authorized effect/cleanup deadline reached")
            if action not in limits.scope.actions:
                raise ControllerError("action is outside trusted usage scope")
            old = record["effects"].get(operation)
            if old and old["action"] != action:
                raise ControllerError("operation identity has different action scope")
            cost = (Decimal("0") if old else
                    {"chat": CHAT_COST_USD, "export": EXPORT_COST_USD,
                     "terminate": TERMINATE_COST_USD}.get(action, Decimal("0")))
            cap = limits.cleanup_usd if cleanup else limits.max_usd - limits.cleanup_usd
            spent = record["cleanup_spent"] if cleanup else record["work_spent"]
            if spent + cost > cap or record["work_spent"] + record["cleanup_spent"] + cost > limits.max_usd:
                raise ControllerError("incremental effect cost exceeds the reserved USD allowance")

    def reserve(self, session_id: str, operation: str, action: str, now: float) -> None:
        with self._lock:
            self.check_effect(session_id, operation, action, now)
            record = self._record(session_id)
            if operation in record["effects"]:
                if action in {"create", "terminate"}:
                    raise ControllerError("uncertain destructive/allocation attempt cannot be replayed")
                return  # Only the documented idempotent mock ports may replay.
            cost = {"chat": CHAT_COST_USD, "export": EXPORT_COST_USD,
                    "terminate": TERMINATE_COST_USD}.get(action, Decimal("0"))
            bucket = "cleanup_spent" if action in {"export", "terminate"} else "work_spent"
            record[bucket] += cost  # Conservative maximum charge retained on timeout/crash.
            record["effects"][operation] = {"action": action, "reserved_usd": str(cost), "attempted": True}

    def consume_approval(self, approval_id: str) -> None:
        with self._lock:
            if approval_id in self._used_approvals:
                raise ControllerError("approval has already been consumed")
            self._used_approvals.append(approval_id)

    def close(self, session_id: str) -> None:
        with self._lock:
            self._record(session_id)
            self._closed.append(session_id)

    def sync(self, state: dict[str, Any]) -> None:
        with self._lock:
            state["last_now"] = self._high_water
            state["used_approvals"] = list(self._used_approvals)
            state["closed_sessions"] = list(self._closed)
            session = state["session"]
            if session:
                record = self._record(session["id"])
                session["spent_usd"] = str(record["work_spent"] + record["cleanup_spent"])
                session["effects"] = json.loads(json.dumps(record["effects"]))

    def seal(self, state: dict[str, Any]) -> None:
        with self._lock:
            self._digest = self._hash(state)


class LifecycleController:
    """Mock-only worker with explicitly injected, non-checkpoint execution authority."""

    def __init__(self, *, store: MemoryStateStore | JsonStateStore | None = None,
                 clock: Clock | None = None, provider: Provider | None = None,
                 transport: Transport | None = None, artifacts: ArtifactStore | None = None,
                 authority: MockAuthority | None = None, idle_timeout_seconds: float = 300.0) -> None:
        self.store = store if store is not None else MemoryStateStore()
        self.clock = clock if clock is not None else Clock()
        self.provider = provider if provider is not None else MockProvider()
        self.transport = transport if transport is not None else MockTransport()
        self.artifacts = artifacts if artifacts is not None else MockArtifactStore()
        self.authority = authority if authority is not None else MockAuthority()
        self.idle_timeout_seconds = _finite_number(idle_timeout_seconds, "idle_timeout_seconds", positive=True)
        self._http_inflight: dict[str, MockChatTicket] = {}
        self.http_release_count = 0

    def admit_http_chat(self, session_id: str, request_id: str, prompt: str) -> MockChatTicket:
        """Atomically test READY, reserve work, and register one mock HTTP request."""
        with self.store.locked() as lock:
            state, now = self._begin(lock)
            if Phase(state["phase"]) != Phase.READY:
                raise ChatAdmissionError("backend_not_ready")
            session = self._session(state)
            if session["id"] != session_id:
                raise ChatAdmissionError("control_conflict")
            if now >= min(session["deadline"], session["idle_deadline"]):
                session["active"] = None
                session["queue"].clear()
                self._phase(state, session, Phase.DRAINING)
                self._save(lock)
                raise ChatAdmissionError("backend_not_ready")
            if session["active"] or session["queue"] or self._http_inflight:
                raise ChatAdmissionError("backend_busy")
            if request_id in session["completed"]:
                raise ChatAdmissionError("control_conflict")
            try:
                self.authority.reserve(session_id, self._chat_op(session_id, request_id), "chat", now)
            except ControllerError:
                self._phase(state, session, Phase.DRAINING)
                self._save(lock)
                raise ChatAdmissionError("backend_not_ready") from None
            ticket = MockChatTicket(session_id, request_id, self._owned_pod(session))
            self._http_inflight[request_id] = ticket
            session["active"] = {"id": request_id, "prompt": prompt}
            self._audit(state, "http_chat_admitted_simulated")
            try:
                self._save(lock)
            except BaseException:
                self._http_inflight.pop(request_id, None)
                raise
            return ticket

    def release_http_chat(self, ticket: MockChatTicket, *, successful: bool = False,
                          processing_deadline: float | None = None,
                          cancel_event: threading.Event | None = None) -> None:
        """Release once; old/cancelled workers cannot modify a replacement session."""
        with self.store.locked() as lock:
            if self._http_inflight.get(ticket.request_id) is not ticket:
                return
            self._http_inflight.pop(ticket.request_id)
            self.http_release_count += 1
            state, now = self._begin(lock)
            session = state["session"]
            if not session or session["id"] != ticket.session_id:
                return
            active = session["active"]
            if not active or active["id"] != ticket.request_id:
                return
            session["active"] = None
            session["completed"].append(ticket.request_id)
            processing_valid = (processing_deadline is not None and cancel_event is not None
                                and not cancel_event.is_set() and time.monotonic() < processing_deadline)
            if successful and processing_valid and Phase(state["phase"]) == Phase.READY and now < session["deadline"]:
                session["last_activity"] = now
                session["idle_deadline"] = now + session["idle_timeout_seconds"]
            elif now >= min(session["deadline"], session["idle_deadline"]):
                self._phase(state, session, Phase.DRAINING)
            self._audit(state, "http_chat_released_simulated")
            self._save(lock)

    def execute_http_chat(self, ticket: MockChatTicket, *, processing_deadline: float,
                          cancel_event: threading.Event, defer_release: bool = False) -> str:
        """Only the instantaneous mock transport is exposed by Phase 2a gateway."""
        successful = False
        _finite_number(processing_deadline, "processing_deadline", positive=True)
        if not isinstance(cancel_event, threading.Event):
            raise ValueError("explicit cancellation event is required")

        def check_processing() -> None:
            if cancel_event.is_set() or time.monotonic() >= processing_deadline:
                raise ChatAdmissionError("processing_timeout")

        try:
            with self.store.locked() as lock:
                check_processing()  # Includes elapsed time waiting to acquire the store lock.
                state, now = self._begin(lock)
                try:
                    session = state["session"]
                    if (session and session["id"] == ticket.session_id
                            and now >= min(session["deadline"], session["idle_deadline"])):
                        raise ChatAdmissionError("backend_not_ready")
                    if (self._http_inflight.get(ticket.request_id) is not ticket or not session
                            or session["id"] != ticket.session_id or Phase(state["phase"]) != Phase.READY
                            or not session["active"] or session["active"]["id"] != ticket.request_id):
                        raise ChatAdmissionError("control_conflict")
                    dispatch = self.transport.chat
                    now = self._logical_now(state)
                    self.authority.check_effect(ticket.session_id, self._chat_op(ticket.session_id, ticket.request_id), "chat", now)
                    if now >= session["idle_deadline"]:
                        raise ChatAdmissionError("backend_not_ready")
                    check_processing()  # No checkpoint or adapter lookup between this gate and dispatch.
                    response = dispatch(ticket.session_id, ticket.pod_id, ticket.request_id, session["active"]["prompt"])
                    after = self._logical_now(state)
                    check_processing()
                    if type(response) is not str or after >= session["deadline"]:
                        raise ChatAdmissionError("backend_not_ready")
                    successful = True
                    return response
                finally:
                    self._logical_now(state)
                    self._save(lock)  # Keep elapsed-time authority coherent before release/recovery.
        finally:
            if not defer_release:
                self.release_http_chat(ticket, successful=successful,
                                       processing_deadline=processing_deadline, cancel_event=cancel_event)

    def check_http_stream(self, ticket: MockChatTicket, *, processing_deadline: float,
                          cancel_event: threading.Event, claim_success: Callable[[], bool] | None = None) -> None:
        """Short locked fence only; optional terminal decision contains no I/O."""
        with self.store.locked() as lock:
            state, now = self._begin(lock)
            session = state["session"]
            # Scope read after checkpoint work; valid reads do not refresh idle/grants.
            now = max(now, _finite_number(self.clock.now(), "clock"))
            if cancel_event.is_set() or time.monotonic() >= processing_deadline:
                self.authority.observe(now); self._save(lock)
                raise ChatAdmissionError("processing_timeout")
            if (session and session["id"] == ticket.session_id
                    and now >= min(session["deadline"], session["idle_deadline"])):
                self.authority.observe(now)
                session["active"] = None; session["queue"].clear()
                self._phase(state, session, Phase.DRAINING); self._save(lock)
                raise ChatAdmissionError("backend_not_ready")
            if (self._http_inflight.get(ticket.request_id) is not ticket or not session
                    or session["id"] != ticket.session_id or Phase(state["phase"]) != Phase.READY
                    or not session["active"] or session["active"]["id"] != ticket.request_id):
                raise ChatAdmissionError("control_conflict")
            if cancel_event.is_set() or time.monotonic() >= processing_deadline:
                raise ChatAdmissionError("processing_timeout")
            if claim_success is not None and not claim_success():
                raise ChatAdmissionError("control_conflict")

    def advance_mock_start(self, session_id: str) -> str:
        """Memory-only HTTP start worker; stops rather than advancing cleanup."""
        if type(self.store) is not MemoryStateStore:
            raise ControllerError("mock HTTP worker requires memory state")
        with self.store.locked() as lock:
            state, _ = self._begin(lock, session_id)
            if Phase(state["phase"]) not in {Phase.PROVISIONING, Phase.BOOTSTRAPPING, Phase.LOADING}:
                return "stopped"
            return self.run_one_step()

    def _save(self, lock: Any) -> None:
        state = lock.load()
        self.authority.sync(state)
        lock.save()  # A failed write never grants permission to send an effect.
        self.authority.seal(state)

    def _begin(self, lock: Any, expected_session: str | None = None) -> tuple[dict[str, Any], float]:
        state = lock.load()
        self.authority.check(state)
        now = self._logical_now(state)  # Always under the mutation lock.
        self._save(lock)               # Persist high-water even when admission is rejected.
        if expected_session is not None:
            if type(expected_session) is not str or not state["session"] or state["session"]["id"] != expected_session:
                raise ControllerError("command session/generation does not match the active session")
        return state, now

    def list_models(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(row) for row in CATALOG)

    def status(self) -> dict[str, Any]:
        with self.store.locked() as lock:
            state = lock.load()
            self.authority.check(state)
            session = state["session"]
            return {"phase": state["phase"], "session_id": session["id"] if session else None,
                    "pod_id": session["pod_id"] if session else None,
                    "queue_depth": len(session["queue"]) if session else 0,
                    "work_deadline": session["deadline"] if session else None,
                    "cleanup_deadline": session["cleanup_deadline"] if session else None,
                    "simulated": True}

    def start(self, session_id: str, limits: UsageLimits) -> None:
        if (type(session_id) is not str or not session_id or not isinstance(limits, UsageLimits)
                or limits.scope.session_id != session_id or limits.scope.target != "new-pod"):
            raise ControllerError("typed fresh session-scoped work/cleanup authority is required")
        with self.store.locked() as lock:
            state, now = self._begin(lock)
            if state["session"] is not None:
                if state["session"]["id"] == session_id and state["session"]["limits"] == _limits_dict(limits):
                    return
                raise ControllerError("a session is already active or unresolved")
            if not REQUIRED_ACTIONS <= limits.scope.actions:
                raise ControllerError("usage scope does not cover lifecycle actions")
            grant = self.authority.register(session_id, limits, now, self.idle_timeout_seconds)
            state["phase"] = Phase.PROVISIONING.value
            state["session"] = {"id": session_id, "phase": Phase.PROVISIONING.value,
                "target": limits.scope.target, "limits": _limits_dict(limits),
                "started_at": now, "last_activity": now, "deadline": grant["deadline"],
                "cleanup_deadline": grant["cleanup_deadline"],
                "idle_timeout_seconds": self.idle_timeout_seconds, "idle_deadline": now + self.idle_timeout_seconds,
                "pod_id": None, "pending": {"create": self._op(session_id, "create")},
                "queue": [], "active": None, "spent_usd": "0", "effects": {},
                "receipt": None, "approval": None, "completed": [], "approval_used": False,
                "terminate_attempted": False,
                **{f"{action}_operation": self._op(session_id, action)
                   for action in ("create", "bootstrap", "load", "export", "terminate")}}
            self._audit(state, "start_requested_simulated")
            self._save(lock)

    def reconcile_start(self, *, session_id: str) -> str:
        with self.store.locked() as lock:
            state, now = self._begin(lock, session_id)
            session = self._session(state)
            if Phase(state["phase"]) != Phase.CREATE_UNRESOLVED:
                raise ControllerError("there is no unresolved create to reconcile")
            if now >= session["cleanup_deadline"]:
                return "unknown"  # No provider call after the scope expires.
            try:
                result = self.provider.reconcile_create(session_id, session["create_operation"])
            except Exception:
                result = None
            self._logical_now(state)
            if (isinstance(result, ReconcileResult) and result.outcome == "found"
                    and result.complete is True and len(result.pod_ids) == 1
                    and type(result.pod_ids[0]) is str and result.pod_ids[0]):
                session["pod_id"] = result.pod_ids[0]
                after = self._logical_now(state)
                self._phase(state, session, Phase.DRAINING if after >= session["deadline"] else Phase.BOOTSTRAPPING)
                session["queue"].clear() if after >= session["deadline"] else None
                self._audit(state, "create_reconciled_found_simulated"); self._save(lock); return "found"
            self._audit(state, "create_reconciliation_unknown_simulated"); self._save(lock); return "unknown"

    def queue_chat(self, request_id: str, prompt: str, *, session_id: str) -> None:
        if type(request_id) is not str or not request_id or type(prompt) is not str:
            raise ValueError("string request identity and prompt are required")
        with self.store.locked() as lock:
            state, now = self._begin(lock, session_id)
            session = self._session(state)
            if Phase(state["phase"]) not in {Phase.PROVISIONING, Phase.CREATE_UNRESOLVED,
                    Phase.BOOTSTRAPPING, Phase.LOADING, Phase.READY}:
                raise ControllerError("inference is not admitted in this state")
            if request_id in session["completed"]:
                raise ControllerError("completed request IDs cannot be reused")
            duplicate = next((item for item in session["queue"] if item["id"] == request_id), None)
            if duplicate is None and session["active"] and session["active"]["id"] == request_id:
                duplicate = session["active"]
            if duplicate:
                if duplicate["prompt"] != prompt: raise ControllerError("request identity has different content")
                return
            self.authority.check_effect(session_id, self._chat_op(session_id, request_id), "chat", now)
            session["queue"].append({"id": request_id, "prompt": prompt})
            self._audit(state, "chat_queued_simulated"); self._save(lock)

    def run_one_step(self) -> str:
        """Internal serialized worker; callback-facing commands carry explicit session IDs."""
        with self.store.locked() as lock:
            state, now = self._begin(lock)
            phase = Phase(state["phase"])
            if phase == Phase.ABSENT: return "idle"
            session = self._session(state)
            sid = session["id"]
            if phase in {Phase.CREATE_UNRESOLVED, Phase.TERMINATION_UNCONFIRMED}:
                return "reconciliation_required" if phase == Phase.CREATE_UNRESOLVED else "termination_unconfirmed"
            if now >= session["cleanup_deadline"]:
                session["queue"].clear(); session["active"] = None
                self._phase(state, session, Phase.CLEANUP_EXPIRED)
                self._audit(state, "cleanup_authority_expired_simulated"); self._save(lock); return "cleanup_expired"
            if now >= session["deadline"] and phase in {Phase.PROVISIONING, Phase.BOOTSTRAPPING, Phase.LOADING, Phase.READY}:
                session["queue"].clear(); session["active"] = None
                self._phase(state, session, Phase.CREATE_UNRESOLVED if phase == Phase.PROVISIONING else Phase.DRAINING)
                self._audit(state, "work_deadline_reached_simulated"); self._save(lock)
                return "unresolved" if phase == Phase.PROVISIONING else "draining"
            if phase == Phase.PROVISIONING:
                operation = session["create_operation"]
                self.authority.reserve(sid, operation, "create", now)
                self._phase(state, session, Phase.CREATE_UNRESOLVED)
                self._save(lock)  # Durable ambiguous state BEFORE calling create; no crash replay.
                try: pod_id = self.provider.create(sid, operation)
                except Exception:
                    self._logical_now(state)
                    self._audit(state, "create_outcome_unknown_simulated"); self._save(lock); return "create_unresolved"
                if type(pod_id) is not str or not pod_id:
                    self._logical_now(state)
                    self._audit(state, "create_response_malformed_simulated"); self._save(lock); return "create_unresolved"
                session["pod_id"] = pod_id; session["pending"] = {}
                after = self._logical_now(state)
                self._phase(state, session, Phase.DRAINING if after >= session["deadline"] else Phase.BOOTSTRAPPING)
                if after >= session["deadline"]: session["queue"].clear()
                self._audit(state, "pod_created_simulated"); self._save(lock); return "created"
            if phase in {Phase.BOOTSTRAPPING, Phase.LOADING}:
                action = "bootstrap" if phase == Phase.BOOTSTRAPPING else "load"
                operation = session[f"{action}_operation"]
                self.authority.reserve(sid, operation, action, now); self._save(lock)
                try:
                    if action == "bootstrap": self.provider.bootstrap(sid, self._owned_pod(session), operation)
                    else: self.transport.load(sid, self._owned_pod(session), operation)
                except Exception:
                    self._logical_now(state)
                    self._phase(state, session, Phase.FAILED); self._audit(state, f"{action}_failed_simulated")
                    self._save(lock); return "failed"
                after = self._logical_now(state)
                next_phase = Phase.LOADING if action == "bootstrap" else Phase.READY
                if after >= session["deadline"]:
                    next_phase = Phase.DRAINING; session["queue"].clear()
                self._phase(state, session, next_phase); self._audit(state, f"{action}_completed_simulated")
                self._save(lock); return "bootstrapped" if action == "bootstrap" else "ready"
            if phase == Phase.READY:
                if session["active"] or session["queue"]:
                    request = session["active"] or session["queue"][0]
                    operation = self._chat_op(sid, request["id"])
                    try: self.authority.reserve(sid, operation, "chat", now)
                    except ControllerError:
                        session["queue"].clear(); session["active"] = None
                        self._phase(state, session, Phase.DRAINING); self._audit(state, "work_budget_draining_simulated")
                        self._save(lock); return "draining"
                    if session["active"] is None: session["queue"].pop(0)
                    session["active"] = request; self._save(lock)
                    try:
                        response = self.transport.chat(sid, self._owned_pod(session), request["id"], request["prompt"])
                        if type(response) is not str: raise ControllerError("mock chat response is malformed")
                    except Exception:
                        session["active"] = None; session["queue"].insert(0, request)
                        self._logical_now(state); self._audit(state, "chat_retry_queued_simulated")
                        self._save(lock); return "chat_retry_queued"
                    after = self._logical_now(state)
                    session["active"] = None; session["completed"].append(request["id"])
                    if after >= session["deadline"]:
                        session["queue"].clear(); self._phase(state, session, Phase.DRAINING)
                        self._audit(state, "late_chat_result_withheld_simulated"); self._save(lock); return "chat_deadline_exceeded"
                    session["last_activity"] = after
                    session["idle_deadline"] = after + session["idle_timeout_seconds"]
                    self._audit(state, "chat_completed_simulated"); self._save(lock); return "chat:" + response
                if now >= session["idle_deadline"]:
                    self._phase(state, session, Phase.DRAINING); self._audit(state, "idle_draining_simulated")
                    self._save(lock); return "draining"
                return "ready"
            if phase == Phase.DRAINING:
                self._phase(state, session, Phase.EXPORTING); self._audit(state, "drained_simulated")
                self._save(lock); return "exporting"
            if phase == Phase.EXPORTING:
                try: self.authority.reserve(sid, session["export_operation"], "export", now)
                except ControllerError:
                    self._phase(state, session, Phase.CLEANUP_EXPIRED)
                    self._audit(state, "cleanup_reserve_unavailable_simulated")
                    self._save(lock); return "cleanup_expired"
                self._save(lock)
                try:
                    receipt = self.artifacts.export(sid, self._owned_pod(session), session["export_operation"])
                    valid = (isinstance(receipt, ArtifactReceipt) and receipt.simulated is True
                             and self.artifacts.verify(receipt, sid, self._owned_pod(session)) is True)
                except Exception: valid = False; receipt = None
                self._logical_now(state)
                if not valid:
                    self._phase(state, session, Phase.FAILED); self._audit(state, "export_unverified_simulated")
                    self._save(lock); return "export_unverified"
                session["receipt"] = self._receipt_dict(receipt)
                self._phase(state, session, Phase.TERMINATION_APPROVAL_PENDING)
                self._audit(state, "export_verified_simulated"); self._save(lock); return "approval_pending"
            if phase == Phase.TERMINATION_APPROVAL_PENDING: return "approval_required"
            if phase == Phase.TERMINATING:
                return "explicit_confirmation_required"
            return "cleanup_expired" if phase == Phase.CLEANUP_EXPIRED else "failed"

    def submit_termination_approval(self, approval: TerminationApproval) -> None:
        if not isinstance(approval, TerminationApproval): raise ValueError("typed individual deletion approval is required")
        # Revalidate even if a frozen object was constructed through an unsafe deserializer.
        TerminationApproval(approval.approval_id, approval.action, approval.session_id,
                            approval.pod_id, approval.receipt_sha256, approval.expires_at)
        with self.store.locked() as lock:
            state, now = self._begin(lock, approval.session_id)
            session = self._session(state)
            if Phase(state["phase"]) != Phase.TERMINATION_APPROVAL_PENDING:
                raise ControllerError("verified export is not awaiting individual deletion approval")
            if now >= session["cleanup_deadline"] or not self._approval_matches(approval, session, now):
                raise ControllerError("approval is expired or does not match the exact session/Pod/export")
            if approval.approval_id in state["used_approvals"]:
                raise ControllerError("approval has already been consumed")
            session["approval"] = {"id": approval.approval_id, "action": approval.action,
                "session_id": approval.session_id, "pod_id": approval.pod_id,
                "receipt_sha256": approval.receipt_sha256, "expires_at": approval.expires_at}
            self._phase(state, session, Phase.TERMINATING)
            self._audit(state, "individual_termination_approval_accepted_simulated"); self._save(lock)

    def confirm_termination(self, *, session_id: str, approval_id: str) -> str:
        with self.store.locked() as lock:
            state, now = self._begin(lock, session_id)
            session = self._session(state)
            approval = session["approval"]
            if type(approval_id) is not str or not approval or approval["id"] != approval_id:
                raise ControllerError("confirmation approval identity does not match")
            if Phase(state["phase"]) == Phase.TERMINATION_UNCONFIRMED:
                return "unconfirmed"  # Never replay a consumed attempt, including after restart.
            if Phase(state["phase"]) != Phase.TERMINATING:
                raise ControllerError("termination is not individually approved")
            typed = self._approval_from(approval)
            if not self._approval_matches(typed, session, now):
                raise ControllerError("individual deletion approval expired or no longer matches")
            try:
                verified = self.artifacts.verify(self._receipt_from(session["receipt"]), session_id, self._owned_pod(session))
            finally:
                self._logical_now(state)
                self._save(lock)  # Retain time spent verifying, including failed verification.
            if verified is not True:
                raise ControllerError("export verification no longer holds")

            def check_dispatch() -> float:
                current = self._logical_now(state)
                try:
                    if not self._approval_matches(typed, session, current):
                        raise ControllerError("individual deletion approval expired or no longer matches")
                    self.authority.check_effect(session_id, session["terminate_operation"], "terminate", current)
                except ControllerError:
                    self._save(lock)  # Rejection preserves the observed monotonic high-water.
                    raise
                return current

            now = check_dispatch()  # Verification/checkpoint work may have exhausted permission.
            self.authority.reserve(session_id, session["terminate_operation"], "terminate", now)
            self.authority.consume_approval(approval_id)
            session["approval_used"] = True; session["terminate_attempted"] = True
            self._phase(state, session, Phase.TERMINATION_UNCONFIRMED)
            self._audit(state, "termination_attempt_consumed_simulated")
            self._save(lock)  # Consume approval/operation and persist UNKNOWN BEFORE the call.
            pod_id = self._owned_pod(session)
            operation_id, allowlist = session["terminate_operation"], frozenset({pod_id})
            dispatch = self.provider.terminate
            check_dispatch()  # After intent storage and adapter lookup; no intervening checkpoint.
            try:
                dispatch(session_id, pod_id, operation_id, allowlist)
            except Exception:
                self._logical_now(state); self._audit(state, "termination_outcome_unknown_simulated")
                self._save(lock); return "unconfirmed"
            self._logical_now(state)
            if self._absence(session_id, pod_id, state):
                self._close(state, session); self._save(lock); return "absent"
            self._audit(state, "termination_unconfirmed_simulated"); self._save(lock); return "unconfirmed"

    def reconcile_termination(self, *, session_id: str) -> str:
        with self.store.locked() as lock:
            state, now = self._begin(lock, session_id)
            session = self._session(state)
            if Phase(state["phase"]) != Phase.TERMINATION_UNCONFIRMED:
                raise ControllerError("there is no unconfirmed termination to reconcile")
            if now >= session["cleanup_deadline"]: return "unconfirmed"
            if not self._absence(session_id, self._owned_pod(session), state):
                self._save(lock); return "unconfirmed"
            self._close(state, session); self._save(lock); return "absent"

    def _absence(self, session_id: str, pod_id: str, state: dict[str, Any]) -> bool:
        _, cleanup_deadline = self.authority.bounds(session_id)
        if self._logical_now(state) >= cleanup_deadline: return False
        try: absent = self.provider.is_absent(session_id, pod_id)
        except Exception: absent = None
        self._logical_now(state)
        return absent is True

    def _close(self, state: dict[str, Any], session: dict[str, Any]) -> None:
        self.authority.close(session["id"])
        self._audit(state, "absence_confirmed_simulated")
        state["session"] = None; state["phase"] = Phase.ABSENT.value

    def drop_chat(self, request_id: str, *, session_id: str) -> bool:
        if type(request_id) is not str or not request_id: raise ValueError("request identity must be a string")
        with self.store.locked() as lock:
            state, _ = self._begin(lock, session_id); session = self._session(state)
            before = len(session["queue"])
            session["queue"] = [item for item in session["queue"] if item["id"] != request_id]
            dropped = len(session["queue"]) != before
            if dropped: self._audit(state, "queued_chat_dropped_simulated")
            self._save(lock); return dropped

    def abort(self, *, session_id: str) -> str:
        with self.store.locked() as lock:
            state, _ = self._begin(lock, session_id); session = self._session(state)
            phase = Phase(state["phase"])
            if phase in {Phase.TERMINATING, Phase.TERMINATION_UNCONFIRMED}:
                raise ControllerError("cannot abort an individually approved/attempted termination")
            session["queue"].clear(); session["active"] = None
            next_phase = Phase.CREATE_UNRESOLVED if phase in {Phase.PROVISIONING, Phase.CREATE_UNRESOLVED} else Phase.DRAINING
            self._phase(state, session, next_phase); self._audit(state, "session_abort_scoped_simulated")
            self._save(lock); return next_phase.value

    def _logical_now(self, state: dict[str, Any]) -> float:
        return self.authority.observe(self.clock.now())

    @staticmethod
    def _session(state: dict[str, Any]) -> dict[str, Any]:
        if state["session"] is None: raise ControllerError("there is no active session")
        return state["session"]

    @staticmethod
    def _phase(state: dict[str, Any], session: dict[str, Any], phase: Phase) -> None:
        state["phase"] = phase.value; session["phase"] = phase.value

    @staticmethod
    def _owned_pod(session: dict[str, Any]) -> str:
        pod = session["pod_id"]
        if type(pod) is not str or not pod: raise ControllerError("no controller-owned Pod identity")
        return pod

    @staticmethod
    def _op(session_id: str, action: str) -> str:
        return hashlib.sha256(f"{session_id}:{action}".encode()).hexdigest()

    @classmethod
    def _chat_op(cls, session_id: str, request_id: str) -> str:
        return cls._op(session_id, "chat:" + request_id)

    @staticmethod
    def _approval_matches(approval: TerminationApproval, session: dict[str, Any], now: float) -> bool:
        receipt = session["receipt"]
        return (not session["queue"] and session["active"] is None and receipt is not None
                and approval.action == "terminate" and approval.session_id == session["id"]
                and approval.pod_id == session["pod_id"] and approval.receipt_sha256 == receipt["sha256"]
                and now < approval.expires_at)

    @staticmethod
    def _approval_from(data: dict[str, Any]) -> TerminationApproval:
        return TerminationApproval(data["id"], data["action"], data["session_id"], data["pod_id"], data["receipt_sha256"], data["expires_at"])

    @staticmethod
    def _receipt_dict(receipt: ArtifactReceipt) -> dict[str, Any]:
        return {"session_id": receipt.session_id, "pod_id": receipt.pod_id, "artifact_root": receipt.artifact_root,
                "sha256": receipt.sha256, "simulated": receipt.simulated}

    @staticmethod
    def _receipt_from(data: dict[str, Any]) -> ArtifactReceipt:
        return ArtifactReceipt(data["session_id"], data["pod_id"], data["artifact_root"], data["sha256"], data["simulated"])

    @staticmethod
    def _audit(state: dict[str, Any], event: str) -> None:
        state["audit"].append({"event": event, "simulated": True})
