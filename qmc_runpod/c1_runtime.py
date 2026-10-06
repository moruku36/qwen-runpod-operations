"""Minimal approved-manual→startup queue→READY→export/approval mock flow.

No production adapters or automatic live execution. Poll is an explicit CPU step,
not a background daemon. All original controller/gateway files are unchanged.
"""
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json
import threading
import time

from .c1_authority import DurableMockAuthority, MeteredMockAuthority, encode
from .c1_ports import BootstrapPlans, ExportReadback, PortError, ProviderPlans, Rate, identity
from .ondemand import (ArtifactReceipt, Clock, ControllerError, CorruptCheckpoint, LifecycleController,
                      MemoryStateStore, MockArtifactStore, MockProvider, MockTransport, Phase, UsageLimits, _finite_number)
from .owui_intent_policy import ManualContext, OWUIIntentPolicy, ServerPurpose
from .private_gateway import PrivateGateway
from .mock_gateway import GatewayError
from .upstream_sse import prepare


@dataclass(frozen=True)
class QueuedChat:
    request_id: str
    session_id: str
    deadline: float
    payload: bytes = field(repr=False)


class OnDemandRuntime:
    """Explicit trusted approval API; HTTP metadata cannot call approve_session.

    Pending payloads are in memory only. Journal records no body and restores no
    pending gesture or dispatch ticket. Restart drops interrupted requests without
    creating a new grant, Pod, lease or automatic inference retry.
    """
    def __init__(self, *, clock=None, journal=None, provider=None, upstream=None,
                 rate=None, idle_seconds=300, verify_subject=None, allowed_subjects=frozenset()):
        self._mutex = threading.RLock()
        self.rate = rate if rate is not None else Rate()
        if type(self.rate) is not Rate: raise PortError("typed_rate_required")
        self.plans, self.bootstrap, self.exports = ProviderPlans(), BootstrapPlans(), ExportReadback()
        self._approved, self._queued, self._states = {}, {}, {}
        self._ready_once = set()
        self._dispatched_sessions = set()
        self._closed = False
        self._aux_snapshot = {}
        authority = DurableMockAuthority(journal, self.rate) if journal is not None else MeteredMockAuthority(self.rate)
        store, aux = authority.restore() if journal is not None else (MemoryStateStore(), {})
        self.controller = LifecycleController(store=store, clock=clock or Clock(), authority=authority,
                        provider=provider, idle_timeout_seconds=idle_seconds)
        if aux:
            expected = self._configuration()
            if aux["configuration"] != expected: raise CorruptCheckpoint("runtime_configuration_changed")
            if self.controller.clock.now() < store.snapshot()["last_now"]:
                raise CorruptCheckpoint("clock_recovery_required")
            self._ready_once = set(aux["ready_once"])
            self._dispatched_sessions = set(aux.get("dispatched_sessions", []))
            self.plans.owned = {k: tuple(v) for k, v in aux["owned"].items()}
            self.plans.attempted = set(aux["attempted"])
            self.exports._objects = {k: v.encode() for k, v in aux["exports"].items()}
            self.bootstrap.observations = {tuple(k.split(":")): v for k, v in aux["readiness"].items()}
            if provider is None:
                self.controller.provider.pods = dict(aux["provider"]["pods"])
                self.controller.provider.results = dict(aux["provider"]["results"])
                self.controller.provider.completed = set(aux["provider"]["completed"])
            for operation, row in aux["receipts"].items():
                self.controller.artifacts.receipts[operation] = ArtifactReceipt(**row)
            self._states = {rid: "interrupted_not_replayed" for rid in aux["pending_ids"]}
        self.gateway = PrivateGateway(self.controller, upstream=upstream)
        # Instance-local composition adds price fences without modifying the
        # reviewed gateway/binding source or replacing any mock provider port.
        stream_guard, json_fence = self.gateway._stream_guard, self.gateway._json_response_fence
        def metered_stream(sock, stream, **kwargs):
            stream_guard(sock, stream, **kwargs)
            if not kwargs.get("error_write", False):
                self._write_cost_fence(stream.ticket.session_id)
        def metered_json(deadline, expected_binding):
            json_fence(deadline, expected_binding)
            self._write_cost_fence(expected_binding[0])
        self.gateway._stream_guard, self.gateway._json_response_fence = metered_stream, metered_json
        self.policy = OWUIIntentPolicy(self.gateway, verify_subject=verify_subject, allowed_subjects=allowed_subjects)
        # The save hook runs under the controller store lock. First READY and
        # its idle baseline/attestation form one checkpoint, not two seals.
        original_save = self.controller._save
        def coherent_save(lock):
            self._prepare_checkpoint(lock.load())
            original_save(lock)
        self.controller._save = coherent_save
        if journal is not None: authority.capture = self._capture_checkpoint
        self._snapshot_aux()
        status = self.controller.status()
        if status["session_id"] is not None:
            session = store.snapshot()["session"]
            if session["active"] is not None:
                self._states[session["active"]["id"]] = "interrupted_not_replayed"
                self.controller.abort(session_id=status["session_id"])
            elif status["phase"] in {"provisioning", "bootstrapping", "loading"}:
                # Pending bodies/gestures are not recovered. Do not create or
                # continue loading solely because an old startup checkpoint exists.
                self.stop(status["session_id"])
            elif status["phase"] == "ready":
                self.plans.check_owner(status["session_id"], status["pod_id"])
                if not self.bootstrap.ready(status["session_id"], status["pod_id"]): raise PortError("readiness_not_proven")
                self.gateway.binding.bind_ready_session(status["session_id"])
        self._persist()

    def _configuration(self):
        return {"gpu_rate": str(self.rate.gpu_usd_per_hour), "storage_rate": str(self.rate.storage_usd_per_hour),
                "overhead": str(self.rate.overhead_usd), "pins": vars(self.bootstrap.pins), "spec": vars(self.plans.spec)}

    def _snapshot_aux(self):
        self._aux_snapshot = {"configuration": self._configuration(), "ready_once": sorted(self._ready_once),
            "dispatched_sessions": sorted(self._dispatched_sessions),
            "owned": {k: list(v) for k, v in self.plans.owned.items()}, "attempted": sorted(self.plans.attempted),
            "exports": {k: v.decode() for k, v in self.exports._objects.items()},
            "readiness": {": ".join(k).replace(": ", ":"): v for k, v in self.bootstrap.observations.items()},
            "provider": {"pods": dict(self.controller.provider.pods), "results": dict(self.controller.provider.results),
                         "completed": sorted(self.controller.provider.completed)},
            "receipts": {k: vars(v) for k, v in self.controller.artifacts.receipts.items()},
            "pending_ids": sorted(self._queued)}

    def _prepare_checkpoint(self, state):
        session = state["session"]
        if not session: return
        sid, pod = session["id"], session["pod_id"]
        if pod:
            operation = session["create_operation"]
            if (self.controller.provider.pods.get(pod) == sid
                    and self.controller.provider.results.get(operation) == pod):
                self.plans.acknowledge(sid, operation, {"id": pod}, account=self.plans.spec.account)
        if state["phase"] == "ready" and sid not in self._ready_once:
            self.plans.check_owner(sid, pod)
            if session["load_operation"] not in self.controller.transport.completed:
                raise PortError("readiness_not_proven")
            pins = self.bootstrap.pins
            self.bootstrap.attest_mock(sid, pod, fingerprint=hashlib.sha256(pod.encode()).hexdigest(),
                model_sha256=pins.model_sha256, llama_commit=pins.llama_commit,
                authenticated=True, process_owned=True)
            # Use the transition's observed clock. Work/cleanup deadlines stay
            # unchanged, and a subsequent seal can never renew this baseline.
            session["last_activity"] = state["last_now"]
            session["idle_deadline"] = state["last_now"] + session["idle_timeout_seconds"]
            self._ready_once.add(sid)
            self.controller._audit(state, "first_ready_idle_started_simulated")
        if state["phase"] == "termination_approval_pending" and not session["receipt"]["artifact_root"].startswith("mock://external-report/"):
            digest = self.exports.save(sid, pod, self.bootstrap.pins,
                                       [row["event"] for row in state["audit"][-256:]])
            receipt = ArtifactReceipt(sid, pod, "mock://external-report/" + sid, digest)
            self.controller.artifacts.receipts[session["export_operation"]] = receipt
            session["receipt"] = self.controller._receipt_dict(receipt)

    def _capture_checkpoint(self, state):
        # No runtime mutex here: gateway seals hold store -> authority locks.
        # Queue/configuration metadata is an immutable published snapshot;
        # effect/ownership/readiness data is captured at THIS exact store seal.
        aux = dict(self._aux_snapshot)
        aux.update(ready_once=sorted(self._ready_once),
            owned={k: list(v) for k, v in self.plans.owned.items()},
            attempted=sorted(self.plans.attempted),
            readiness={":".join(k): v for k, v in self.bootstrap.observations.items()},
            exports={k: v.decode() for k, v in self.exports._objects.items()},
            provider={"pods": dict(self.controller.provider.pods), "results": dict(self.controller.provider.results),
                      "completed": sorted(self.controller.provider.completed)},
            receipts={k: vars(v) for k, v in self.controller.artifacts.receipts.items()})
        return aux

    def _persist(self):
        self._snapshot_aux()
        with self.controller.store.locked() as lock:
            self.controller.authority.check(lock.load())
            self.controller._save(lock)

    def approve_session(self, limits):
        self._open_required()
        if type(limits) is not UsageLimits: raise PortError("typed_grant_required")
        identity(limits.scope.session_id)
        with self._mutex:
            self._open_required()
            if len(self._approved) >= 16 and limits.scope.session_id not in self._approved:
                raise PortError("grant_limit")
            old = self._approved.get(limits.scope.session_id)
            if old is not None and old != limits: raise PortError("grant_replacement_refused")
            self._approved[limits.scope.session_id] = limits

    def _consume_manual(self, context):
        with self.policy._lock:
            if (type(context) is not ManualContext or self.policy._contexts.get(context.sequence) is not context
                    or context.expires_at <= time.monotonic()): raise PortError("manual_intent_required")
            if self.policy._subject(context.request) != context.subject or context.expires_at <= time.monotonic():
                raise PortError("manual_intent_required")
            self.policy._contexts.pop(context.sequence)

    def accept(self, context, payload, request_id, *, session_id):
        self._open_required()
        identity(session_id); clean = prepare(payload, request_id)
        with self._mutex:
            self._open_required()
            if len(self._queued) >= 8 or request_id in self._states: raise PortError("queue_limit_or_replay")
            self._consume_manual(context)
            status = self.controller.status()
            if status["session_id"] is None:
                grant = self._approved.get(session_id)
                if grant is None: raise PortError("approved_session_required")
                now = self.controller.clock.now()
                if not self.rate.work_allowed(grant, now, now): raise PortError("cleanup_rate_reserve_unavailable")
                self.controller.start(session_id, grant)
            elif status["session_id"] != session_id or status["phase"] not in {"provisioning", "create_unresolved", "bootstrapping", "loading", "ready"}:
                raise PortError("session_unavailable")
            session = self.controller.store.snapshot()["session"]
            self._work_fence()
            self._queued[request_id] = QueuedChat(request_id, session_id, session["deadline"], encode(clean))
            self._states[request_id] = "waiting_ready"
            self._persist()
            return {"request_id": request_id, "phase": self.controller.status()["phase"], "simulated": True}

    def status(self):
        # No polling side effects, worker advance, grant, or idle renewal.
        with self._mutex:
            return {**self.controller.status(), "queued": len(self._queued), "requests": dict(self._states)}

    def _open_required(self):
        if self._closed: raise PortError("runtime_closed")

    def _work_fence(self):
        state = self.controller.store.snapshot(); session = state["session"]
        if session is None: raise PortError("session_unavailable")
        now = max(state["last_now"], _finite_number(self.controller.clock.now(), "clock"))
        limits = self.controller.authority._records[session["id"]]["limits"]
        total = self.rate.cost(session["started_at"], now) + Decimal(session["spent_usd"])
        if now >= session["deadline"] or total + Decimal(".01") > limits.max_usd - limits.cleanup_usd:
            self.stop(session["id"]); raise PortError("work_budget_expired")

    def _write_cost_fence(self, session_id):
        try:
            with self.controller.store.locked():
                state = self.controller.store.snapshot(); session = state["session"]
                if not session or session["id"] != session_id: raise ControllerError("stale_scope")
                now = max(state["last_now"], _finite_number(self.controller.clock.now(), "clock"))
                record = self.controller.authority._record(session_id); limits = record["limits"]
                total = self.rate.cost(record["started_at"], now) + record["work_spent"] + record["cleanup_spent"]
                if total > limits.max_usd - limits.cleanup_usd: raise ControllerError("elapsed_rate_budget_expired")
        except (ControllerError, ValueError):
            raise GatewayError(503, "backend_not_ready") from None

    def stop(self, session_id):
        self._open_required(); identity(session_id)
        with self._mutex, self.gateway.binding._control, self.controller.store.locked() as lock:
            self._open_required()
            state, _ = self.controller._begin(lock, session_id)
            session = state["session"]
            for ticket, cancel in self.gateway.binding._active.values():
                if ticket.session_id == session_id: cancel.set()
            if not session["pod_id"] and session["create_operation"] not in session["effects"]:
                self.controller._close(state, session); self.controller._save(lock)
            else:
                self.controller.abort(session_id=session_id)
            for rid, chat in list(self._queued.items()):
                if chat.session_id == session_id:
                    self._queued.pop(rid); self._states[rid] = "cancelled"
            self.gateway.binding._permits.clear()
            self._persist()

    def drop(self, request_id):
        self._open_required(); identity(request_id)
        with self._mutex:
            self._open_required()
            chat = self._queued.pop(request_id, None)
            if chat is None: return False
            self._states[request_id] = "dropped"
            if not self._queued and chat.session_id not in self._dispatched_sessions: self.stop(chat.session_id)
            else: self._persist()
            return True

    def tick(self):
        self._open_required()
        with self._mutex:
            self._open_required()
            status = self.controller.status()
            if status["session_id"] is None: return "idle"
            state = self.controller.store.snapshot(); session = state["session"]
            now = max(state["last_now"], self.controller.clock.now())
            for rid, chat in list(self._queued.items()):
                if now >= chat.deadline:
                    self._queued.pop(rid); self._states[rid] = "expired"
            limits = self.controller.authority._records[session["id"]]["limits"]
            if status["phase"] in {"provisioning", "bootstrapping", "loading", "ready"}:
                total = self.rate.cost(session["started_at"], now) + Decimal(session["spent_usd"])
                if now >= session["deadline"] or total >= limits.max_usd - limits.cleanup_usd:
                    self.stop(session["id"]); return "budget_draining"
            phase = status["phase"]
            if phase == "failed" and session["pod_id"]:
                self.stop(session["id"]); return "startup_failed_draining"
            if phase == "ready" and self.controller._http_inflight:
                if now >= session["idle_deadline"]: self.stop(session["id"]); return "idle_draining"
                return "inference_active"
            if phase == "provisioning": self.plans.create(session["id"], session["create_operation"])
            elif phase in {"bootstrapping", "loading"}:
                self.plans.check_owner(session["id"], session["pod_id"])
                self.bootstrap.plan(session["id"], session["pod_id"])
            self._snapshot_aux()
            result = self.controller.run_one_step()
            current = self.controller.store.snapshot()["session"]
            if result == "created":
                self.plans.acknowledge(current["id"], current["create_operation"], {"id": current["pod_id"]}, account=self.plans.spec.account)
            if result == "ready" and current["phase"] == "ready":
                self.gateway.binding.bind_ready_session(current["id"])
            self._persist()
            return result

    def ready_request(self, request_id):
        self._open_required(); identity(request_id)
        with self._mutex:
            self._open_required()
            chat = self._queued.get(request_id)
            if chat is None or self.controller.status()["phase"] != "ready": raise PortError("request_not_ready")
            self._work_fence()
            self.plans.check_owner(chat.session_id, self.controller.status()["pod_id"])
            payload = json.loads(chat.payload)
            minted = self.gateway._mint({"payload": payload}, request_id)
            self._queued.pop(request_id); self._states[request_id] = "dispatch_once"
            self._dispatched_sessions.add(chat.session_id)
            self._persist()
            return payload, {"X-Request-ID": request_id, "X-Intent-Token": minted["intent_token"]}

    def retry_queued(self, context, request_id):
        """Explicit verified manual retry before dispatch; no new deadline/grant.

        Inference dispatched once is never replayed through this operation. A
        fresh manual request is required after an unknown inference outcome.
        """
        self._open_required()
        with self._mutex:
            self._open_required()
            self._consume_manual(context)
            chat = self._queued.get(request_id)
            if chat is None or self._states.get(request_id) != "waiting_ready":
                raise PortError("dispatched_or_unknown_not_retryable")
            self._work_fence()
            self._persist()
            return {"request_id": request_id, "deadline": chat.deadline, "simulated": True}

    def reconcile_create(self, session_id):
        self._open_required(); identity(session_id)
        with self._mutex:
            self._open_required()
            result = self.controller.reconcile_start(session_id=session_id)
            if result == "found":
                session = self.controller.store.snapshot()["session"]
                self.plans.acknowledge(session_id, session["create_operation"], {"id": session["pod_id"]}, account=self.plans.spec.account)
                if not self._queued: self.stop(session_id)
            self._persist(); return result

    def approve_cleanup(self, approval):
        self._open_required()
        with self._mutex:
            self._open_required()
            if not self.exports.verify(approval.receipt_sha256): raise PortError("external_readback_required")
            self.controller.submit_termination_approval(approval); self._persist()

    def confirm_cleanup(self, session_id, approval_id):
        self._open_required(); identity(session_id); identity(approval_id)
        with self._mutex:
            self._open_required()
            session = self.controller.store.snapshot()["session"]
            if not session or session["id"] != session_id or not session["approval"] or session["approval"]["id"] != approval_id:
                raise PortError("exact_cleanup_approval_required")
            if not self.exports.verify(session["receipt"]["sha256"]): raise PortError("external_readback_required")
            if session["phase"] == "termination_unconfirmed": return "unconfirmed"
            with self.controller.store.locked() as lock:
                state, now = self.controller._begin(lock, session_id)
                current = state["session"]
                approval = self.controller._approval_from(current["approval"])
                limits = self.controller.authority._records[session_id]["limits"]
                total = self.rate.cost(current["started_at"], now) + Decimal(current["spent_usd"])
                if (current["phase"] != "terminating" or now >= current["cleanup_deadline"]
                        or not self.controller._approval_matches(approval, current, now) or total > limits.max_usd):
                    raise PortError("cleanup_scope_expired")
            self.plans.terminate(session_id, session["pod_id"], session["terminate_operation"], frozenset({session["pod_id"]}))
            self._snapshot_aux()
            result = self.controller.confirm_termination(session_id=session_id, approval_id=approval_id)
            self._persist(); return result

    def close(self):
        # Same operation lock as every mutator: a worker already waiting for
        # this lock must recheck closed before reserving or performing effects.
        with self._mutex:
            if self._closed: return
            self._closed = True
            self.gateway.binding.close(); self.policy.close()
            self._persist()
