"""Mock-authority OWUI binding + upstream relay; no automatic provisioning.

The one-use interactive permit is internal server state, never a client header
or body claim. Production OWUI authentication/gesture middleware remains gated.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import math
import threading
import time

from .ondemand import (ChatAdmissionError, ControllerError, LifecycleController,
                      MemoryStateStore, MockArtifactStore, MockChatTicket, MockProvider,
                      MockTransport, Phase, _finite_number)
from .private_chat import PrivateChatError
from .upstream_sse import LoopbackUpstream, SSEDecoder, StreamEnd, prepare


class BindingError(RuntimeError):
    pass


@dataclass(frozen=True)
class InteractivePermit:
    request_id: str
    session_id: str
    pod_id: str
    digest: str = field(repr=False)
    sequence: int
    expires_at: float


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class MockUpstream:
    """Fixed public test output, no socket and no provider lifecycle dependency."""
    def __init__(self):
        self.calls = 0

    def json(self, payload, request_id, *, deadline, cancel, fence):
        fence(); self.calls += 1
        return {"model": "qwen-27b", "choices": [{"index": 0, "message": {
                "role": "assistant", "content": "模型🙂"}, "finish_reason": "stop"}]}

    def stream(self, payload, request_id, *, deadline, cancel, fence):
        fence(); self.calls += 1
        decoder = SSEDecoder(request_id)
        for delta, finish in (({"role": "assistant"}, None), ({"content": "模型🙂"}, None), ({}, "stop")):
            fence()
            value = {"id": "public-provider-fixture", "object": "chat.completion.chunk", "model": "qwen-27b",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            wire = b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n\n"
            for event in decoder.feed(wire): yield event
        for event in decoder.feed(b"data: [DONE]\n\n"): yield event
        decoder.finish()


class OWUIBinding:
    """Bind standard OWUI text requests to an already-granted mock session.

    Default transport is CPU mock. Explicit LoopbackUpstream is solely exercised
    against ephemeral fixtures here; no real provider grant/tunnel is installed.
    """
    def __init__(self, controller=None, upstream=None):
        self.controller = controller if controller is not None else LifecycleController()
        if (type(self.controller) is not LifecycleController or type(self.controller.store) is not MemoryStateStore
                or type(self.controller.provider) is not MockProvider or type(self.controller.transport) is not MockTransport
                or type(self.controller.artifacts) is not MockArtifactStore):
            raise BindingError("mock_authority_required")
        self.upstream = upstream if upstream is not None else MockUpstream()
        if type(self.upstream) not in (MockUpstream, LoopbackUpstream):
            raise BindingError("unsupported_upstream")
        self._control = threading.RLock()
        self._binding = None
        self._sequence = 0
        self._permits = {}
        self._active = {}
        self._streams = {}
        self._closed = False

    def catalog(self):
        return {"object": "list", "data": list(self.controller.list_models())}

    def status(self):
        return self.controller.status()

    def bind_ready_session(self, session_id):
        # Trusted local control call. Never called from ordinary OWUI chat input.
        with self._control, self.controller.store.locked():
            if self._closed or self._active: raise BindingError("binding_unavailable")
            status = self.controller.status()
            if status["phase"] != "ready" or status["session_id"] != session_id or not status["pod_id"]:
                raise BindingError("backend_not_ready")
            self._binding = (session_id, status["pod_id"])
            self._permits.clear()

    def permit_interactive(self, payload, request_id):
        """Called only by a reviewed trusted user-gesture/control integration.

        Possession of normal OWUI chat/body metadata cannot invoke this method.
        No current HTTP endpoint exposes it and no production gesture is wired.
        """
        clean = prepare(payload, request_id)
        with self._control:
            if self._closed or self._binding is None: raise BindingError("binding_unavailable")
            now = time.monotonic()
            self._permits = {k: v for k, v in self._permits.items() if v.expires_at > now}
            if len(self._permits) >= 64: raise BindingError("intent_limit")
            self._sequence += 1
            permit = InteractivePermit(request_id, *self._binding, _digest(clean), self._sequence, now + 5)
            self._permits[permit.sequence] = permit
            return permit

    def _admit(self, payload, request_id, permit, deadline, cancel):
        clean = prepare(payload, request_id)
        if (type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline - time.monotonic() > 30
                or not isinstance(cancel, threading.Event) or cancel.is_set() or time.monotonic() >= deadline):
            raise BindingError("request_deadline_or_cancel")
        digest = _digest(clean)

        def check_intent():
            now = time.monotonic()
            if cancel.is_set() or now >= deadline:
                raise BindingError("request_deadline_or_cancel")
            if (self._closed or type(permit) is not InteractivePermit
                    or self._permits.get(permit.sequence) is not permit
                    or permit.expires_at <= now or permit.request_id != request_id
                    or (permit.session_id, permit.pod_id) != self._binding or permit.digest != digest):
                raise BindingError("interactive_intent_required")

        with self._control, self.controller.store.locked() as lock:
            check_intent()
            controller = self.controller
            # The accepted controller API has no processing/intent admission hook.
            # Mirror its mock admission here, adding the gate after _begin's
            # checkpoint and directly before reservation, without changing it.
            try:
                state, now = controller._begin(lock)
            except ControllerError:
                raise BindingError("session_authority_rejected") from None
            check_intent()  # Checkpoint time is not retained permission to reserve.
            if Phase(state["phase"]) != Phase.READY:
                raise BindingError("backend_not_ready")
            session = controller._session(state)
            now = max(now, _finite_number(controller.clock.now(), "clock"))
            if (session["id"], session["pod_id"]) != self._binding:
                raise BindingError("stale_session_binding")
            if now >= min(session["deadline"], session["idle_deadline"]):
                controller.authority.observe(now)
                session["active"] = None; session["queue"].clear()
                controller._phase(state, session, Phase.DRAINING); controller._save(lock)
                raise BindingError("backend_not_ready")
            if session["active"] or session["queue"] or controller._http_inflight:
                raise BindingError("backend_busy")
            if request_id in session["completed"]:
                raise BindingError("control_conflict")
            ticket = MockChatTicket(permit.session_id, request_id, controller._owned_pod(session))
            operation = controller._chat_op(permit.session_id, request_id)
            reserve = controller.authority.reserve
            check_intent()  # All checkpoint/adapter/identity work is before this gate.
            self._permits.pop(permit.sequence)  # TTL is guaranteed at this consumption point.
            try:
                reserve(permit.session_id, operation, "chat", now)
            except ControllerError:
                controller._phase(state, session, Phase.DRAINING); controller._save(lock)
                raise BindingError("backend_not_ready") from None
            controller.authority.observe(now)
            controller._http_inflight[request_id] = ticket
            session["active"] = {"id": request_id, "prompt": "[private upstream request]"}
            controller._audit(state, "http_chat_admitted_simulated")
            try:
                controller._save(lock)
            except BaseException:
                controller._http_inflight.pop(request_id, None)
                raise
            self._active[request_id] = (ticket, cancel)
            return clean, ticket

    def _fence(self, ticket, deadline, cancel, claim=None):
        try:
            with self.controller.store.locked():
                self.controller.check_http_stream(ticket, processing_deadline=deadline,
                                                  cancel_event=cancel)
                status = self.controller.status()
                if (status["session_id"], status["pod_id"]) != (ticket.session_id, ticket.pod_id):
                    raise BindingError("stale_session_binding")
                self.controller.authority.check_effect(ticket.session_id,
                    self.controller._chat_op(ticket.session_id, ticket.request_id), "chat", self.controller.clock.now())
                if claim is not None:
                    self.controller.check_http_stream(ticket, processing_deadline=deadline,
                                                      cancel_event=cancel, claim_success=claim)
        except ChatAdmissionError as error:
            raise BindingError(error.code) from None
        except ControllerError:
            raise BindingError("session_authority_rejected") from None

    def _release(self, ticket):
        # No idle renewal for JSON, content, heartbeat, terminal or reconnect.
        with self._control:
            active = self._active.get(ticket.request_id)
            if active is not None and active[0] is ticket:
                self._active.pop(ticket.request_id)
            stream = self._streams.get(ticket.request_id)
            if stream is not None and stream[0] is ticket:
                self._streams.pop(ticket.request_id)
        self.controller.release_http_chat(ticket, successful=False)

    def json(self, payload, request_id, *, permit=None, deadline, cancel):
        if type(payload) is not dict or payload.get("stream", False): raise BindingError("wrong_response_mode")
        clean, ticket = self._admit(payload, request_id, permit, deadline, cancel)
        try:
            result = self.upstream.json(clean, request_id, deadline=deadline, cancel=cancel,
                                        fence=lambda: self._fence(ticket, deadline, cancel))
            self._fence(ticket, deadline, cancel)
            return {"id": request_id, "object": "chat.completion", **result, "simulated": True}
        finally:
            cancel.set(); self._release(ticket)

    @contextmanager
    def stream(self, payload, request_id, *, permit=None, deadline, cancel):
        if type(payload) is not dict or payload.get("stream") is not True: raise BindingError("wrong_response_mode")
        clean, ticket = self._admit(payload, request_id, permit, deadline, cancel)
        source = self.upstream.stream(clean, request_id, deadline=deadline, cancel=cancel,
                                     fence=lambda: self._fence(ticket, deadline, cancel))
        terminal = False

        def claim():
            nonlocal terminal
            if terminal: return False
            terminal = True
            return True

        def relay():
            for event in source:
                if type(event) is StreamEnd:
                    # Logical success/stop ordering is decided under controller lock.
                    self._fence(ticket, deadline, cancel, claim)
                    if cancel.is_set() or time.monotonic() >= deadline:
                        raise BindingError("processing_timeout")
                    yield event.wire
                    return
                self._fence(ticket, deadline, cancel)
                yield event
            raise PrivateChatError("incomplete_sse")

        iterator = relay()
        with self._control:
            self._streams[request_id] = (ticket, iterator, source)
        try:
            yield iterator
        finally:
            cancel.set()
            try:
                iterator.close(); source.close()
            finally:
                self._release(ticket)

    def abort(self):
        # Cancellation reaches socket watchdog before controller's mutation lock.
        with self._control:
            for _, cancel in self._active.values(): cancel.set()
            binding = self._binding
            self._permits.clear()
        if binding is not None:
            self.controller.abort(session_id=binding[0])

    def close(self):
        with self._control:
            self._closed = True
            active = list(self._active.values())
            streams = list(self._streams.values())
            self._permits.clear()
            for _, cancel in active: cancel.set()
        for _, iterator, source in streams:
            # Paused readers close synchronously. Running readers observe cancel
            # via their socket watchdog and close in the owning context's finally.
            for generator in (source, iterator):
                try: generator.close()
                except ValueError: pass  # generator currently executing in its owner
        for ticket, _ in active:
            self._release(ticket)
