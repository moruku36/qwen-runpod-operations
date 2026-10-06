"""Explicitly served loopback-only synthetic JSON gateway; no live adapters."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hmac
import json
import math
import re
import select
import socket
import socketserver
import threading
import time
import uuid

from .ondemand import (ChatAdmissionError, ControllerError, LifecycleController,
                      MemoryStateStore, MockArtifactStore, MockProvider,
                      MockTransport, REQUIRED_ACTIONS, UsageLimits, UsageScope)

SYNTHETIC_BEARER = "phase2a-public-synthetic-fixture"
APPROVAL_FIXTURE = "phase2a-synthetic-start-v1"
BODY_LIMIT = 65536
HEADER_LIMIT = 16384
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
FIELD = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
REMOTE = re.compile(r"(?:[a-z][a-z0-9+.-]*://|data:)", re.I)
ERROR_STATUS = {"backend_not_ready": 503, "backend_busy": 429,
                "control_conflict": 409, "processing_timeout": 504}


class GatewayError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


@dataclass(frozen=True)
class StreamLimits:
    content_bytes: int = 8192
    output_bytes: int = 32768
    events: int = 128
    frame_bytes: int = 1024
    piece_chars: int = 8
    total_seconds: float = 0.5
    write_seconds: float = 0.1

    def __post_init__(self):
        for value, maximum in ((self.content_bytes, 16384), (self.output_bytes, 65536),
                               (self.events, 1024), (self.frame_bytes, 2048), (self.piece_chars, 64)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("invalid bounded synthetic stream limit")
        _duration(self.total_seconds); _duration(self.write_seconds)


class _Stream:
    def __init__(self, ticket, cancel, text, deadline, limits):
        self.ticket, self.cancel, self.text, self.limits = ticket, cancel, text, limits
        self.deadline = min(deadline, time.monotonic() + limits.total_seconds)
        self.header_started = False
        self.winner = None
        self.claims = self.events = self.bytes = self.pending_peak = 0
        self._terminal_lock = threading.Lock()

    def claim(self, outcome):
        with self._terminal_lock:
            if self.winner is not None: return False
            self.winner = outcome; self.claims += 1
            return True


def _duration(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 10:
        raise ValueError("invalid synthetic timeout")
    return float(value)


def _identity(value):
    if type(value) is not str or not ID.fullmatch(value):
        raise GatewayError(400, "invalid_id")
    return value


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GatewayError(400, "invalid_json")
        result[key] = value
    return result


class MockGateway:
    """Construction is inert. Use ``with gateway.serve(port=0)`` explicitly."""

    def __init__(self, controller=None, *, backend="mock", header_timeout=0.5,
                 body_timeout=0.5, socket_timeout=0.5, processing_timeout=0.5,
                 processing_delay=0.0, startup_delay=0.0, stream_limits=None,
                 stream_fixture="transport", stream_chunk_delay=0.0):
        if backend != "mock":
            raise ValueError("only synthetic mock backend is allowed")
        self.controller = controller if controller is not None else LifecycleController()
        if (type(self.controller) is not LifecycleController
                or type(self.controller.store) is not MemoryStateStore
                or type(self.controller.provider) is not MockProvider
                or type(self.controller.transport) is not MockTransport
                or type(self.controller.artifacts) is not MockArtifactStore):
            raise ValueError("only in-memory synthetic ports are allowed")
        self.header_timeout, self.body_timeout = _duration(header_timeout), _duration(body_timeout)
        self.socket_timeout, self.processing_timeout = _duration(socket_timeout), _duration(processing_timeout)
        for delay in (processing_delay, startup_delay, stream_chunk_delay):
            if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or not 0 <= delay <= 10:
                raise ValueError("invalid synthetic delay")
        self.processing_delay, self.startup_delay = processing_delay, startup_delay
        self.stream_limits = stream_limits if stream_limits is not None else StreamLimits()
        if type(self.stream_limits) is not StreamLimits or stream_fixture not in {"transport", "utf8", "long"}:
            raise ValueError("only bounded synthetic stream fixtures are allowed")
        self.stream_fixture, self.stream_chunk_delay = stream_fixture, stream_chunk_delay
        self._stream_metrics = []
        self._control = threading.Lock()
        self._log_lock = threading.Lock()
        self._logs = []
        self._idempotency = {}
        self._closing = threading.Event()
        self._workers = []
        self._serving = False
        self._connections = threading.BoundedSemaphore(16)
        self._socket_lock = threading.Lock()
        self._active_sockets = set()

    def logs(self):
        with self._log_lock:
            return [dict(item) for item in self._logs]

    def stream_metrics(self):
        with self._log_lock:
            return [dict(item) for item in self._stream_metrics]

    def _log(self, request_id, route, status, started, outcome):
        with self._log_lock:
            self._logs.append({"id": request_id, "route": route, "status": status,
                               "duration": round(time.monotonic() - started, 6), "outcome": outcome})
            del self._logs[:-256]

    @contextmanager
    def serve(self, *, host="127.0.0.1", port=0):
        if host != "127.0.0.1" or type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("only explicit IPv4 loopback listen is allowed")
        if self._serving:
            raise ValueError("already serving")
        gateway = self

        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = False
            block_on_close = True
            allow_reuse_address = False

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                with gateway._socket_lock:
                    if gateway._closing.is_set(): return
                    gateway._active_sockets.add(self.request)
                try:
                    gateway._handle(self.request)
                finally:
                    with gateway._socket_lock:
                        gateway._active_sockets.discard(self.request)

        self._closing.clear()
        server = Server((host, port), Handler)
        self._serving = True
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            self._closing.set()
            with self._socket_lock:
                active = tuple(self._active_sockets)
            for client in active:
                try: client.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            server.shutdown()
            server.server_close()
            thread.join(1)
            for worker in self._workers:
                worker.join(0.1)
            self._serving = False

    @staticmethod
    def _receive(sock, size, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GatewayError(408, "request_timeout")
        sock.settimeout(remaining)
        try:
            data = sock.recv(size)
        except socket.timeout:
            raise GatewayError(408, "request_timeout") from None
        if not data:
            raise GatewayError(400, "invalid_framing")
        if time.monotonic() > deadline:
            raise GatewayError(408, "request_timeout")
        return data

    def _read(self, sock):
        deadline = time.monotonic() + self.header_timeout
        raw = bytearray()
        while not raw.endswith(b"\r\n\r\n"):
            raw.extend(self._receive(sock, 1, deadline))
            if len(raw) > HEADER_LIMIT:
                raise GatewayError(431, "headers_too_large")
        try:
            lines = raw.decode("ascii").split("\r\n")
            method, path, version = lines[0].split(" ")
        except (UnicodeError, ValueError):
            raise GatewayError(400, "invalid_framing") from None
        if version not in {"HTTP/1.0", "HTTP/1.1"} or len(lines[0]) > 4096:
            raise GatewayError(400, "invalid_framing")
        headers = {}
        if len(lines[1:-2]) > 64:
            raise GatewayError(431, "headers_too_large")
        for line in lines[1:-2]:
            if ":" not in line:
                raise GatewayError(400, "invalid_framing")
            key, value = line.split(":", 1)
            if not FIELD.fullmatch(key) or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise GatewayError(400, "invalid_framing")
            key = key.lower()
            if key in headers:
                raise GatewayError(400, "invalid_framing")
            headers[key] = value.strip()
        if "transfer-encoding" in headers:
            raise GatewayError(400, "invalid_framing")
        length = headers.get("content-length")
        if (method == "POST" and length is None) or (length is not None and not re.fullmatch(r"[0-9]{1,8}", length)):
            raise GatewayError(400, "invalid_framing")
        size = int(length or "0")
        if size > BODY_LIMIT:
            raise GatewayError(413, "body_too_large")
        if method != "POST" and size:
            raise GatewayError(400, "invalid_framing")
        data = bytearray()
        deadline = time.monotonic() + self.body_timeout
        while len(data) < size:
            data.extend(self._receive(sock, min(8192, size - len(data)), deadline))
        return method, path, headers, bytes(data)

    @staticmethod
    def _route(path):
        if path in {"/healthz", "/v1/models", "/_mock/sessions", "/v1/chat/completions"}:
            return path, None
        match = re.fullmatch(r"/_mock/sessions/([A-Za-z0-9][A-Za-z0-9_-]{0,63})(/stop)?", path)
        if match:
            return "/_mock/sessions/{id}" + ("/stop" if match[2] else ""), match[1]
        raise GatewayError(404, "not_found")

    @staticmethod
    def _decode(data, headers):
        if headers.get("content-type", "").lower() != "application/json":
            raise GatewayError(415, "unsupported_media_type")
        try:
            result = json.loads(data.decode("utf-8"), object_pairs_hook=_json_object,
                                parse_constant=lambda value: (_ for _ in ()).throw(GatewayError(400, "invalid_json")))
        except (UnicodeError, ValueError, RecursionError):
            raise GatewayError(400, "invalid_json") from None
        if type(result) is not dict:
            raise GatewayError(400, "invalid_json")
        return result

    def _start(self, body, headers, request_id):
        if set(body) != {"session_id", "approval_fixture"} or body["approval_fixture"] != APPROVAL_FIXTURE:
            raise GatewayError(400, "invalid_start_fixture")
        session_id = _identity(body["session_id"])
        key = _identity(headers.get("idempotency-key"))
        if not self._control.acquire(False):
            raise GatewayError(409, "control_conflict")
        try:
            if key in self._idempotency:
                previous, response = self._idempotency[key]
                if previous != session_id:
                    raise GatewayError(409, "control_conflict")
                return 202, dict(response)
            if len(self._idempotency) >= 128:
                raise GatewayError(409, "control_conflict")
            now = self.controller.clock.now()
            limits = UsageLimits.create(max_runtime_seconds=120, max_usd="0.1002", expires_at=now + 150,
                        scope=UsageScope(session_id, "new-pod", REQUIRED_ACTIONS),
                        cleanup_runtime_seconds=30, cleanup_usd="0.0002")
            self.controller.start(session_id, limits)
            response = {"session_id": session_id, "request_id": request_id, "simulated": True}
            self._idempotency[key] = (session_id, response)
            worker = threading.Thread(target=self._bootstrap, args=(session_id,), daemon=True)
            self._workers.append(worker)
            worker.start()
            return 202, response
        except ControllerError:
            raise GatewayError(409, "control_conflict") from None
        finally:
            self._control.release()

    def _bootstrap(self, session_id):
        if self._closing.wait(self.startup_delay):
            return
        try:
            for _ in range(3):
                if self._closing.is_set() or self.controller.advance_mock_start(session_id) == "stopped":
                    return
        except ControllerError:
            return

    @staticmethod
    def _chat_body(body):
        if set(body) - {"session_id", "model", "messages", "stream", "max_tokens"}:
            raise GatewayError(400, "unsupported_input")
        if type(body.get("stream", False)) is not bool:
            raise GatewayError(400, "invalid_request")
        if body.get("model") != "qwen-27b":
            raise GatewayError(400, "unsupported_model")
        session_id = _identity(body.get("session_id"))
        messages = body.get("messages")
        if type(messages) is not list or not 1 <= len(messages) <= 16:
            raise GatewayError(400, "unsupported_input")
        parts = []
        for message in messages:
            if (type(message) is not dict or set(message) != {"role", "content"}
                    or type(message["role"]) is not str or message["role"] not in {"system", "user", "assistant"}
                    or type(message["content"]) is not str or not 1 <= len(message["content"]) <= 8192
                    or REMOTE.search(message["content"])):
                raise GatewayError(400, "unsupported_input")
            parts.append(message["role"] + ": " + message["content"])
        if "max_tokens" in body and (type(body["max_tokens"]) is not int or not 1 <= body["max_tokens"] <= 256):
            raise GatewayError(400, "unsupported_input")
        return session_id, "\n".join(parts)

    def _chat(self, body, request_id, sock):
        session_id, prompt = self._chat_body(body)
        streaming = body.get("stream", False)
        handed_off = False
        try:
            ticket = self.controller.admit_http_chat(session_id, request_id, prompt)
        except ChatAdmissionError as error:
            raise GatewayError(ERROR_STATUS[error.code], error.code) from None
        cancel, done = threading.Event(), threading.Event()
        result = {}
        deadline = time.monotonic() + self.processing_timeout

        def work():
            try:
                if (not cancel.wait(self.processing_delay) and not self._closing.is_set()
                        and time.monotonic() < deadline):
                    result["response"] = self.controller.execute_http_chat(
                        ticket, processing_deadline=deadline, cancel_event=cancel, defer_release=streaming)
                else:
                    result["error"] = GatewayError(504, "processing_timeout")
            except ChatAdmissionError as error:
                result["error"] = GatewayError(ERROR_STATUS[error.code], error.code)
            except Exception:
                result["error"] = GatewayError(503, "backend_not_ready")
            finally:
                try:
                    if not streaming or "response" not in result:
                        self.controller.release_http_chat(ticket)
                except Exception:
                    result["error"] = GatewayError(503, "backend_not_ready")
                finally:
                    done.set()

        worker = threading.Thread(target=work, daemon=True)
        self._workers = [previous for previous in self._workers if previous.is_alive()]
        self._workers.append(worker)
        worker.start()
        try:
            while not done.wait(0.01):
                if time.monotonic() >= deadline or self._closing.is_set():
                    raise GatewayError(504, "processing_timeout")
                readable, _, _ = select.select([sock], [], [], 0)
                if readable:
                    try:
                        data = sock.recv(1, socket.MSG_PEEK)
                    except OSError:
                        raise GatewayError(499, "client_disconnected") from None
                    raise GatewayError(400 if data else 499, "invalid_framing" if data else "client_disconnected")
            if time.monotonic() >= deadline:
                raise GatewayError(504, "processing_timeout")
            if "error" in result:
                raise result["error"]
            if "response" not in result:
                raise GatewayError(504, "processing_timeout")
            if streaming:
                text = {"utf8": '模型🙂\n\n"quoted" café', "long": "界" * 2048}.get(self.stream_fixture, result["response"])
                stream = _Stream(ticket, cancel, text, deadline, self.stream_limits)
                handed_off = True
                return 200, stream
            return 200, {"id": request_id, "object": "chat.completion", "model": body["model"],
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": result["response"]},
                                      "finish_reason": "stop"}], "simulated": True}
        finally:
            if not handed_off:
                cancel.set()
                self.controller.release_http_chat(ticket)

    def _dispatch(self, method, route, session_id, headers, data, request_id, sock):
        expected = "POST" if route in {"/_mock/sessions", "/v1/chat/completions", "/_mock/sessions/{id}/stop"} else "GET"
        if method != expected:
            raise GatewayError(405, "method_not_allowed")
        if route == "/healthz":
            return 200, {"alive": True, "simulated": True}
        if route == "/v1/models":
            return 200, {"object": "list", "data": list(self.controller.list_models())}
        if route == "/_mock/sessions/{id}":
            with self.controller.store.locked():
                status = self.controller.status()
                if status["session_id"] != session_id:
                    raise GatewayError(404, "not_found")
                return 200, status
        body = self._decode(data, headers)
        if route == "/_mock/sessions":
            return self._start(body, headers, request_id)
        if route == "/v1/chat/completions":
            return self._chat(body, request_id, sock)
        if body:
            raise GatewayError(400, "unsupported_input")
        if not self._control.acquire(False):
            raise GatewayError(409, "control_conflict")
        try:
            self.controller.abort(session_id=session_id)
            return 202, {"session_id": session_id, "request_id": request_id, "outcome": "mock_draining", "simulated": True}
        except ControllerError:
            raise GatewayError(409, "control_conflict") from None
        finally:
            self._control.release()

    def _stream_guard(self, sock, stream, *, claim_success=False, error_write=False):
        if self._closing.is_set():
            stream.cancel.set()
            raise GatewayError(503, "stream_shutdown")
        if stream.cancel.is_set() or time.monotonic() >= stream.deadline:
            raise GatewayError(504, "processing_timeout")
        readable, _, _ = select.select([sock], [], [], 0)
        if readable:
            try: data = sock.recv(1, socket.MSG_PEEK)
            except OSError: data = b""
            stream.cancel.set()
            raise GatewayError(499 if not data else 400, "client_disconnected" if not data else "invalid_framing")
        if not error_write and stream.winner != "success":
            try:
                self.controller.check_http_stream(stream.ticket, processing_deadline=stream.deadline,
                    cancel_event=stream.cancel,
                    claim_success=(lambda: stream.claim("success")) if claim_success else None)
            except ChatAdmissionError as error:
                raise GatewayError(ERROR_STATUS[error.code], error.code) from None
        # A controller lock/checkpoint wait must not leak a late 200 or write.
        if stream.cancel.is_set() or self._closing.is_set() or time.monotonic() >= stream.deadline:
            raise GatewayError(504, "processing_timeout")

    def _stream_write(self, sock, stream, payload, *, header=False, error_write=False):
        deadline = min(stream.deadline, time.monotonic() + stream.limits.write_seconds)
        pending = memoryview(payload)
        while pending:
            self._stream_guard(sock, stream, error_write=error_write)
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise GatewayError(504, "stream_write_timeout")
            _, writable, _ = select.select([], [sock], [], min(0.01, remaining))
            if not writable: continue
            self._stream_guard(sock, stream, error_write=error_write)
            if time.monotonic() >= deadline:
                raise GatewayError(504, "stream_write_timeout")
            try: sent = sock.send(pending)
            except BlockingIOError: continue
            except OSError: raise GatewayError(499, "client_disconnected") from None
            if sent <= 0: raise GatewayError(499, "client_disconnected")
            if header: stream.header_started = True  # First accepted byte commits HTTP status.
            pending = pending[sent:]

    @staticmethod
    def _stream_frame(stream, delta, finish=None):
        item = {"id": stream.ticket.request_id, "object": "chat.completion.chunk", "model": "qwen-27b",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return b"data: " + json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n\n"

    @staticmethod
    def _stream_budget(stream, payload, *, events=1):
        if (len(payload) > stream.limits.frame_bytes or stream.events + events > stream.limits.events
                or stream.bytes + len(payload) > stream.limits.output_bytes):
            raise GatewayError(413, "stream_output_limit")
        stream.events += events; stream.bytes += len(payload)
        stream.pending_peak = max(stream.pending_peak, len(payload))

    def _stream_pause(self, sock, stream):
        until = time.monotonic() + self.stream_chunk_delay
        while time.monotonic() < until:
            self._stream_guard(sock, stream)
            stream.cancel.wait(min(0.01, max(0, until - time.monotonic())))

    def _emit_stream(self, sock, stream):
        complete = False
        outcome, status = "stream_error", 500
        try:
            if len(stream.text.encode("utf-8")) > stream.limits.content_bytes:
                raise GatewayError(413, "stream_output_limit")
            role = self._stream_frame(stream, {"role": "assistant", "content": ""})
            self._stream_budget(stream, role)
            sock.setblocking(False)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            self._stream_guard(sock, stream)
            self._stream_write(sock, stream, b"HTTP/1.1 200 Mock\r\nContent-Type: text/event-stream; charset=utf-8\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n", header=True)
            self._stream_write(sock, stream, role)
            for offset in range(0, len(stream.text), stream.limits.piece_chars):
                self._stream_pause(sock, stream)
                payload = self._stream_frame(stream, {"content": stream.text[offset:offset + stream.limits.piece_chars]})
                self._stream_budget(stream, payload)
                self._stream_write(sock, stream, payload)
            self._stream_pause(sock, stream)
            terminal = self._stream_frame(stream, {}, "stop") + b"data: [DONE]\n\n"
            self._stream_budget(stream, terminal, events=2)
            # Linearize success against stop/expiry under the controller lock.
            self._stream_guard(sock, stream, claim_success=True)
            self._stream_write(sock, stream, terminal)
            complete = True
            status, outcome = 200, "simulated_stream_success"
        except Exception as error:
            if isinstance(error, GatewayError): status, outcome = error.status, error.code
            else: status, outcome = 500, "stream_error"
            stream.claim("disconnect" if status == 499 else "error")
            if stream.header_started and status != 499 and stream.winner != "success":
                payload = b'event: error\ndata: {"error":{"code":"stream_error","message":"stream_error","type":"mock_gateway_error"}}\n\n'
                try:
                    self._stream_budget(stream, payload)
                    self._stream_write(sock, stream, payload, error_write=True)
                except Exception:
                    pass  # A bounded disconnect/close is the only fallback; no success terminator.
            if stream.header_started: status = 200
        finally:
            stream.cancel.set()
            try:
                self.controller.release_http_chat(stream.ticket)
            except Exception:
                complete, outcome = False, "stream_error"
            with self._log_lock:
                self._stream_metrics.append({"id": stream.ticket.request_id, "winner": stream.winner,
                    "claims": stream.claims, "complete": complete, "events": stream.events,
                    "bytes": stream.bytes, "pending_peak": stream.pending_peak,
                    "header_started": stream.header_started})
                del self._stream_metrics[:-64]
        return status, outcome

    def _handle(self, sock):
        if not self._connections.acquire(False):
            started = time.monotonic()
            try:
                sock.settimeout(self.socket_timeout)
                payload = b'{"error":{"code":"backend_busy","message":"backend_busy","type":"mock_gateway_error"}}'
                sock.sendall((f"HTTP/1.1 429 Mock\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n").encode("ascii") + payload)
            except OSError:
                pass
            self._log(uuid.uuid4().hex, "unknown", 429, started, "backend_busy")
            return
        try:
            self._handle_request(sock)
        finally:
            self._connections.release()

    def _handle_request(self, sock):
        started = time.monotonic()
        request_id, route, status, outcome = uuid.uuid4().hex, "unknown", 500, "internal_error"
        response = None
        try:
            method, path, headers, data = self._read(sock)
            if "x-request-id" in headers:
                request_id = _identity(headers["x-request-id"])
            route, session_id = self._route(path)
            if self._closing.is_set():
                raise GatewayError(503, "backend_not_ready")
            if route != "/healthz" and not hmac.compare_digest(headers.get("authorization", ""), "Bearer " + SYNTHETIC_BEARER):
                raise GatewayError(401, "unauthorized")
            status, response = self._dispatch(method, route, session_id, headers, data, request_id, sock)
            if type(response) is _Stream:
                stream, response = response, None
                status, outcome = self._emit_stream(sock, stream)
                if stream.header_started:
                    self._log(request_id, route, status, started, outcome)
                    return
            else:
                outcome = "simulated_success"
        except GatewayError as error:
            status, outcome = error.status, error.code
        except OSError:
            status, outcome = 499, "client_disconnected"
        except Exception:
            status, outcome = 500, "internal_error"
        try:
            if status != 499:
                if response is None:
                    response = {"error": {"message": outcome, "type": "mock_gateway_error", "code": outcome},
                                "request_id": request_id}
                payload = json.dumps(response, ensure_ascii=True, separators=(",", ":")).encode("ascii")
                sock.settimeout(self.socket_timeout)
                sock.sendall((f"HTTP/1.1 {status} Mock\r\nContent-Type: application/json\r\n"
                              f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n").encode("ascii") + payload)
        except OSError:
            outcome = "client_disconnected"
        finally:
            self._log(request_id, route, status, started, outcome)
