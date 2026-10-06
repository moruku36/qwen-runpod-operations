"""Explicit ephemeral loopback HTTP ingress for the accepted mock binding.

Keys are injected in memory; defaults are PUBLIC SYNTHETIC fixtures. This is not
a deployed OWUI integration or a durable/live authority. No lifecycle creation
route exists. Inference credentials alone cannot mint an interactive intent.
"""
from contextlib import contextmanager
import hmac
import json
import re
import secrets
import select
import socket
import threading
import time
import uuid

from .mock_gateway import GatewayError, MockGateway, StreamLimits, _Stream, _identity
from .ondemand import _finite_number
from .owui_binding import BindingError, OWUIBinding
from .private_chat import PrivateChatError
from .upstream_sse import _strict_json

INFERENCE_FIXTURE = "phase4-public-inference-fixture"
CONTROL_FIXTURE = "phase4-public-control-fixture"
_KEY = re.compile(r"[A-Za-z0-9._~-]{1,256}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32}\Z")
_ERRORS = {
    "unsupported_chat": (400, "unsupported_input"),
    "wrong_response_mode": (400, "unsupported_input"),
    "request_too_large": (413, "body_too_large"),
    "backend_busy": (429, "backend_busy"),
    "control_conflict": (409, "control_conflict"),
    "interactive_intent_required": (403, "interactive_intent_required"),
    "intent_limit": (429, "intent_limit"),
    "request_deadline_or_cancel": (504, "processing_timeout"),
    "processing_timeout": (504, "processing_timeout"),
    "request_deadline": (504, "processing_timeout"),
    "request_cancelled": (499, "request_cancelled"),
    "backend_not_ready": (503, "backend_not_ready"),
    "binding_unavailable": (503, "backend_not_ready"),
}


def _safe_error(error):
    if isinstance(error, GatewayError):
        return error
    # Never copy provider exceptions, response bodies, addresses or credentials.
    code = error.args[0] if isinstance(error, (BindingError, PrivateChatError)) and error.args else None
    status, code = _ERRORS.get(code if type(code) is str else None, (502, "upstream_refused"))
    return GatewayError(status, code)


class PrivateGateway(MockGateway):
    """No socket on construction. Only explicit ``serve(port=0)`` is supported.

    ``/_control/intents`` is a fixture of a trusted server control channel,
    never an OWUI user gesture by itself. Production user verification and key
    storage remain separate workstreams. It grants no create/terminate action.
    """
    def __init__(self, controller=None, *, upstream=None, inference_key=INFERENCE_FIXTURE,
                 control_key=CONTROL_FIXTURE, **kwargs):
        for key in (inference_key, control_key):
            if type(key) is not str or not _KEY.fullmatch(key):
                raise ValueError("invalid_in_memory_key")
        if hmac.compare_digest(inference_key, control_key):
            raise ValueError("distinct_role_keys_required")
        kwargs.setdefault("stream_limits", StreamLimits(content_bytes=8192, output_bytes=65536,
                         events=1024, frame_bytes=2048, piece_chars=64, total_seconds=1, write_seconds=.1))
        super().__init__(controller, **kwargs)
        self.binding = OWUIBinding(self.controller, upstream)
        self._inference_key, self._control_key = inference_key, control_key
        self._intents = {}
        self._intent_lock = threading.Lock()

    @contextmanager
    def serve(self, *, host="127.0.0.1", port=0):
        if host != "127.0.0.1" or type(port) is not int or port != 0:
            raise ValueError("ephemeral_loopback_fixture_only")
        try:
            with super().serve(host=host, port=port) as address:
                yield address
        finally:
            self.binding.close()
            with self._intent_lock:
                self._intents.clear()

    @staticmethod
    def _route(path):
        if path not in {"/healthz", "/v1/models", "/status", "/v1/chat/completions",
                        "/_control/intents", "/_control/stop"}:
            raise GatewayError(404, "not_found")
        return path, None

    def _authorize(self, route, headers):
        if route == "/healthz":
            return
        key = self._control_key if route.startswith("/_control/") else self._inference_key
        if not hmac.compare_digest(headers.get("authorization", ""), "Bearer " + key):
            raise GatewayError(401, "unauthorized")

    @staticmethod
    def _decode(data, headers):
        if headers.get("content-type", "").lower() != "application/json":
            raise GatewayError(415, "unsupported_media_type")
        try:
            value = _strict_json(data.decode("utf-8"))
        except (PrivateChatError, UnicodeError, ValueError, RecursionError):
            raise GatewayError(400, "invalid_json") from None
        if type(value) is not dict:
            raise GatewayError(400, "invalid_json")
        return value

    def _mint(self, body, request_id):
        if set(body) != {"payload"}:
            raise GatewayError(400, "unsupported_input")
        with self._intent_lock:
            now = time.monotonic()
            self._intents = {k: v for k, v in self._intents.items() if v.expires_at > now}
            if len(self._intents) >= 64:
                raise GatewayError(429, "intent_limit")
            try:
                permit = self.binding.permit_interactive(body["payload"], request_id)
            except PrivateChatError:
                raise GatewayError(400, "unsupported_input") from None
            token = secrets.token_urlsafe(24)
            if token in self._intents:
                raise GatewayError(503, "intent_unavailable")
            self._intents[token] = permit
        return {"request_id": request_id, "intent_token": token, "expires_in_seconds": 5, "simulated": True}

    def _take(self, headers):
        token = headers.get("x-intent-token", "")
        if not _TOKEN.fullmatch(token):
            raise GatewayError(403, "interactive_intent_required")
        with self._intent_lock:
            permit = self._intents.pop(token, None)
        if permit is None:
            raise GatewayError(403, "interactive_intent_required")
        return permit

    def _stop(self, body):
        if set(body) != {"session_id"}:
            raise GatewayError(409, "control_conflict")
        session_id = body["session_id"]
        # Same lock order as bind/admit. Scope validation and exact action are
        # indivisible; never call the old unscoped binding.abort() here.
        with self.binding._control, self.controller.store.locked():
            if (type(session_id) is not str or self.binding._binding is None
                    or self.binding._binding[0] != session_id
                    or self.binding.status()["session_id"] != session_id):
                raise GatewayError(409, "control_conflict")
            for ticket, cancel in self.binding._active.values():
                if ticket.session_id == session_id:
                    cancel.set()
            self.controller.abort(session_id=session_id)
            self.binding._permits.clear()
        # Do not invert mint's intent→binding lock order or clear successor tokens.
        with self._intent_lock:
            self._intents = {k: p for k, p in self._intents.items() if p.session_id != session_id}
        return {"outcome": "mock_draining", "simulated": True}

    @contextmanager
    def _watch_client(self, sock, cancel, deadline):
        done = threading.Event()
        def watch():
            while not done.wait(.005):
                if self._closing.is_set() or time.monotonic() >= deadline:
                    cancel.set(); return
                try:
                    readable, _, _ = select.select([sock], [], [], 0)
                    if readable:
                        # EOF or unsolicited pipelining is not another admitted request.
                        cancel.set(); return
                except (OSError, ValueError):
                    cancel.set(); return
        worker = threading.Thread(target=watch, name="mock-private-client-watch", daemon=False)
        worker.start()
        try:
            yield
        finally:
            done.set(); worker.join(1)

    def _json_response_fence(self, deadline, expected_binding):
        with self.controller.store.locked():
            status = self.binding.status()
            state = self.controller.store.snapshot()
            session = state["session"]
            if (status["phase"] != "ready" or (status["session_id"], status["pod_id"]) != expected_binding
                    or self.binding._binding != expected_binding
                    or max(state["last_now"], _finite_number(self.controller.clock.now(), "clock"))
                       >= min(session["deadline"], session["idle_deadline"])):
                raise GatewayError(503, "backend_not_ready")
        if time.monotonic() >= deadline:
            raise GatewayError(504, "processing_timeout")

    def _json_write(self, sock, status, value, *, deadline=None, committed=None, fence=None):
        payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
        if len(payload) > 65536:
            raise GatewayError(413, "response_too_large")
        wire = (f"HTTP/1.1 {status} Mock\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n").encode() + payload
        until = time.monotonic() + self.socket_timeout
        if deadline is not None:
            until = min(until, deadline)
        sock.setblocking(False)
        pending = memoryview(wire)
        while pending:
            remaining = until - time.monotonic()
            if remaining <= 0 or self._closing.is_set():
                raise GatewayError(504, "response_write_timeout")
            _, writable, _ = select.select([], [sock], [], min(.01, remaining))
            if not writable:
                continue
            if time.monotonic() >= until or self._closing.is_set():
                raise GatewayError(504, "response_write_timeout")
            if fence is not None:
                fence()
            if time.monotonic() >= until or self._closing.is_set():
                raise GatewayError(504, "response_write_timeout")
            try:
                sent = sock.send(pending)
            except BlockingIOError:
                continue
            if sent <= 0:
                raise GatewayError(499, "client_disconnected")
            if committed is not None:
                committed[0] = True
            pending = pending[sent:]

    def _frames(self, stream, event):
        terminal = event.endswith(b"data: [DONE]\n\n")
        if terminal:
            # Binding already linearized this terminal against stop under its lock.
            yield self._stream_frame(stream, {}, "stop") + b"data: [DONE]\n\n", True
            return
        value = _strict_json(event.removeprefix(b"data: ").removesuffix(b"\n\n").decode("utf-8"))
        delta = value["choices"][0]["delta"]
        content = delta.get("content", "")
        size = getattr(stream, "content_bytes", 0) + len(content.encode("utf-8"))
        if size > stream.limits.content_bytes:
            raise GatewayError(413, "stream_output_limit")
        stream.content_bytes = size
        if "role" in delta:
            yield self._stream_frame(stream, {"role": "assistant"}), False
        for offset in range(0, len(content), stream.limits.piece_chars):
            yield self._stream_frame(stream, {"content": content[offset:offset + stream.limits.piece_chars]}), False

    def _relay(self, sock, body, request_id, permit, deadline, cancel, committed):
        # The same overall deadline governs admission, upstream waits, watchdog
        # cancellation, controller fences and downstream writes, before first read.
        deadline = min(deadline, time.monotonic() + self.stream_limits.total_seconds)
        with self.binding.stream(body, request_id, permit=permit, deadline=deadline, cancel=cancel) as events:
            # Obtain/validate the first upstream frame before committing HTTP 200.
            first = next(events)
            with self.binding._control:
                ticket = self.binding._active[request_id][0]
            stream = _Stream(ticket, cancel, "", deadline, self.stream_limits)
            complete = False
            try:
                sock.setblocking(False)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
                self._stream_write(sock, stream, b"HTTP/1.1 200 Mock\r\nContent-Type: text/event-stream; charset=utf-8\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n", header=True)
                committed[0] = stream.header_started
                event = first
                while True:
                    for frame, terminal in self._frames(stream, event):
                        self._stream_budget(stream, frame, events=2 if terminal else 1)
                        if terminal:
                            stream.claim("success")
                        self._stream_write(sock, stream, frame)
                        if terminal:
                            complete = True
                            return
                    event = next(events)
            except Exception:
                committed[0] = stream.header_started
                stream.claim("error")
                if stream.header_started and stream.winner != "success":
                    frame = b'event: error\ndata: {"error":{"code":"stream_error","message":"stream_error"}}\n\n'
                    try:
                        self._stream_budget(stream, frame)
                        self._stream_write(sock, stream, frame, error_write=True)
                    except Exception:
                        pass
                raise
            finally:
                with self._log_lock:
                    self._stream_metrics.append({"id": request_id, "winner": stream.winner,
                        "claims": stream.claims, "complete": complete, "events": stream.events,
                        "bytes": stream.bytes, "pending_peak": stream.pending_peak,
                        "content_bytes": getattr(stream, "content_bytes", 0),
                        "header_started": stream.header_started})
                    del self._stream_metrics[:-64]

    def _handle_request(self, sock):
        started = time.monotonic()
        request_id, route, status, outcome = uuid.uuid4().hex, "unknown", 500, "internal_error"
        committed = [False]
        processing_deadline = None
        try:
            method, path, headers, data = self._read(sock)
            if "x-request-id" in headers:
                request_id = _identity(headers["x-request-id"])
            route, _ = self._route(path)
            if self._closing.is_set():
                raise GatewayError(503, "backend_not_ready")
            self._authorize(route, headers)
            expected = "POST" if route in {"/v1/chat/completions", "/_control/intents", "/_control/stop"} else "GET"
            if method != expected:
                raise GatewayError(405, "method_not_allowed")
            if route == "/healthz":
                response = {"alive": True, "simulated": True}
            elif route == "/v1/models":
                response = self.binding.catalog()
            elif route == "/status":
                response = self.binding.status()
            else:
                body = self._decode(data, headers)
                if route == "/_control/intents":
                    if "x-request-id" not in headers:
                        raise GatewayError(400, "request_id_required")
                    response = self._mint(body, request_id)
                elif route == "/_control/stop":
                    response = self._stop(body)
                else:
                    if "x-request-id" not in headers:
                        raise GatewayError(400, "request_id_required")
                    permit = self._take(headers)
                    deadline = time.monotonic() + self.processing_timeout
                    if body.get("stream") is True:
                        deadline = min(deadline, time.monotonic() + self.stream_limits.total_seconds)
                    processing_deadline = deadline
                    cancel = threading.Event()
                    with self._watch_client(sock, cancel, deadline):
                        if body.get("stream") is True:
                            self._relay(sock, body, request_id, permit, deadline, cancel, committed)
                            status, outcome = 200, "simulated_stream_success"
                            return
                        response = self.binding.json(body, request_id, permit=permit, deadline=deadline, cancel=cancel)
                    # Binding sets cancel on release; that signal is not a response grant.
                    self._json_write(sock, 200, response, deadline=deadline, committed=committed,
                                     fence=lambda: self._json_response_fence(deadline, (permit.session_id, permit.pod_id)))
                    status, outcome = 200, "simulated_success"
                    return
            self._json_write(sock, 200, response, committed=committed)
            status, outcome = 200, "simulated_success"
        except (GatewayError, BindingError, PrivateChatError) as error:
            safe = _safe_error(error)
            status, outcome = safe.status, safe.code
            if status == 499 and processing_deadline is not None and time.monotonic() >= processing_deadline:
                status, outcome = 504, "processing_timeout"
        except OSError:
            status, outcome = 499, "client_disconnected"
        except Exception:
            status, outcome = 500, "internal_error"
        finally:
            if status >= 400 and status != 499 and not committed[0]:
                try:
                    self._json_write(sock, status, {"error": {"code": outcome, "message": outcome,
                                     "type": "mock_private_gateway_error"}, "request_id": request_id})
                except Exception:
                    pass
            self._log(request_id, route, status, started, outcome)
