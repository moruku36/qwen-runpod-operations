"""Durable one-attempt effect boundary, independent of the mock HTTP type gates.

I/O runs outside the ledger mutex. Reservations persist before an effect, and an
unknown/late/crashed operation is never retried. No live gateway is constructed.
The lifecycle host must supply reviewed scope and commit callbacks explicitly.
"""
from dataclasses import dataclass
import threading
import time

from .c1_authority import AuthorityJournal
from .c1_ports import PortError, identity
from .execution_adapters import Effect, ExecutionGate


@dataclass(frozen=True)
class EffectResult:
    operation_id: str
    outcome: str


class EffectBoundary:
    """Service-owned journal protects one-attempt intents, not request bodies.

    check/commit are trusted control-plane code, never credentials/body claims.
    commit must independently apply exact session/generation results and export
    newly found stale resources for explicit cleanup; it must not bind successors.
    Shared journal owner lock excludes multiple live boundary owners.
    """
    def __init__(self, *, journal=None, check=None, commit=None):
        if journal is not None and type(journal) is not AuthorityJournal: raise PortError("journal_required")
        self._journal, self._check, self._commit = journal, check, commit
        self._lock = threading.RLock(); self._closed = False
        self._records, self._active = {}, {}
        if journal is not None:
            data = journal.latest()
            if data is not None:
                if type(data) is not dict or data.get("schema") != "effect-boundary-v1": raise PortError("boundary_checkpoint_invalid")
                for op, row in data["operations"].items():
                    effect = Effect(row["session_id"], op, row["action"], row["pod_id"], row["deadline"])
                    if row["outcome"] not in {"unknown", "committed", "late_result", "cancelled"}: raise PortError("boundary_checkpoint_invalid")
                    self._records[op] = dict(row)

    def _persist(self):
        if self._journal is None: raise PortError("durable_boundary_required")
        self._journal.append({"schema": "effect-boundary-v1", "operations": self._records})

    def _approved(self, effect, cancel):
        if self._closed or self._journal is None or self._check is None or self._commit is None:
            raise PortError("production_boundary_disabled")
        ExecutionGate(self._check).check(effect, cancel)

    def execute(self, effect, invoke):
        if type(effect) is not Effect or not callable(invoke): raise PortError("typed_effect_required")
        cancel = threading.Event()
        with self._lock:
            self._approved(effect, cancel)
            if effect.operation_id in self._records: raise PortError("effect_not_replayable")
            if len(self._records) >= 4096: raise PortError("boundary_history_bound")
            self._records[effect.operation_id] = {"session_id": effect.session_id, "pod_id": effect.pod_id,
                "action": effect.action, "deadline": effect.deadline, "outcome": "unknown"}
            self._persist()  # Crash immediately after this cannot grant replay.
            self._active[effect.operation_id] = cancel
        try:
            # A stop/close can acquire the control lock while I/O is blocked.
            # Pass this fence to every adapter connect/send/read/result boundary.
            def fence():
                with self._lock: self._approved(effect, cancel)
            fence()
            value = invoke(effect, cancel, fence)
            with self._lock:
                try: self._approved(effect, cancel)
                except PortError:
                    self._records[effect.operation_id]["outcome"] = "late_result"
                    self._persist()
                    return EffectResult(effect.operation_id, "late_result")
                if self._commit(effect, value) is not True:
                    self._records[effect.operation_id]["outcome"] = "late_result"
                else: self._records[effect.operation_id]["outcome"] = "committed"
                self._persist()
                return EffectResult(effect.operation_id, self._records[effect.operation_id]["outcome"])
        except Exception:
            # Unknown is already durable. Do not return an exception containing
            # provider payloads, stderr or a bearer from an arbitrary driver.
            return EffectResult(effect.operation_id, "unknown")
        finally:
            with self._lock: self._active.pop(effect.operation_id, None)

    def stop(self, session_id):
        identity(session_id)
        with self._lock:
            for operation, cancel in self._active.items():
                if self._records[operation]["session_id"] == session_id: cancel.set()

    def status(self):
        with self._lock:
            return {"closed": self._closed, "operations": {op: dict(row) for op, row in self._records.items()}}

    def reconcile(self, effect, observe, *, verified_commit):
        """Explicit read-only observation of original intent; no invoke replay.

        Unknown creation needs trusted original operation knowledge. A name/list
        match is insufficient. GET absence is scoped to a previously owned Pod.
        Neither the journal nor provider response alone supplies that permission.
        """
        cancel = threading.Event()
        with self._lock:
            self._approved(effect, cancel)
            row = self._records.get(effect.operation_id)
            if (row is None or row["session_id"] != effect.session_id or row["action"] != effect.action
                    or row["pod_id"] != effect.pod_id or row["deadline"] != effect.deadline):
                raise PortError("original_effect_required")
            if row["outcome"] == "committed": return EffectResult(effect.operation_id, "committed")
        value = observe(effect, cancel)
        with self._lock:
            self._approved(effect, cancel)
            if verified_commit(effect, value) is not True: raise PortError("reconciliation_not_proven")
            row["outcome"] = "committed"; self._persist()
            return EffectResult(effect.operation_id, "committed")

    def close(self):
        with self._lock:
            self._closed = True
            for cancel in self._active.values(): cancel.set()
