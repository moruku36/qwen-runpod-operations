"""Inactive-by-default private-tunnel HTTP seam; no provider or lifecycle actions.

This module is NOT wired into the accepted mock controller/gateway. Explicit I/O
is exercised only against isolated loopback fixtures in this phase. A loopback
address alone is not proof of Pod ownership, tunnel identity or authorization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
import math
import re
import socket
import threading
import time

MAX_BODY = 65536
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
REMOTE = re.compile(r"(?:[a-z][a-z0-9+.-]*://|data:)", re.I)


class PrivateChatError(RuntimeError):
    """Only fixed codes are exposed; never propagate upstream text or headers."""


@dataclass(frozen=True)
class TunnelEndpoint:
    port: int
    host: str = "127.0.0.1"

    def __post_init__(self):
        if type(self.host) is not str or self.host != "127.0.0.1" or type(self.port) is not int or not 1 <= self.port <= 65535 or self.port == 3001:
            raise PrivateChatError("invalid_tunnel_endpoint")

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"


@dataclass(frozen=True)
class ChatRequest:
    request_id: str
    body: bytes = field(repr=False)
    method: str = field(default="POST", init=False)
    path: str = field(default="/v1/chat/completions", init=False)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PrivateChatError("invalid_upstream_json")
        result[key] = value
    return result


def _invalid_constant(_):
    raise PrivateChatError("invalid_upstream_json")


def build_chat_request(payload, request_id):
    """Validate the Phase 3a text-only, nonstream contract without any I/O."""
    if type(request_id) is not str or not IDENTITY.fullmatch(request_id):
        raise PrivateChatError("invalid_request_id")
    if type(payload) is not dict or not set(payload) <= {"model", "messages", "stream", "max_tokens"}:
        raise PrivateChatError("unsupported_chat")
    if payload.get("model") != "qwen-27b" or payload.get("stream", False) is not False:
        raise PrivateChatError("unsupported_chat")
    messages = payload.get("messages")
    if type(messages) is not list or not 1 <= len(messages) <= 16:
        raise PrivateChatError("unsupported_chat")
    clean = []
    for message in messages:
        if type(message) is not dict or set(message) != {"role", "content"}:
            raise PrivateChatError("unsupported_chat")
        if (message["role"] not in ("system", "user", "assistant")
                or type(message["content"]) is not str or not 1 <= len(message["content"]) <= 8192
                or REMOTE.search(message["content"])):
            raise PrivateChatError("unsupported_chat")
        clean.append(dict(message))
    tokens = payload.get("max_tokens", 128)
    if type(tokens) is not int or not 1 <= tokens <= 256:
        raise PrivateChatError("unsupported_chat")
    try:
        body = json.dumps({"model": "qwen-27b", "messages": clean, "stream": False,
                           "max_tokens": tokens}, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError):
        raise PrivateChatError("unsupported_chat") from None
    if len(body) > MAX_BODY:
        raise PrivateChatError("request_too_large")
    return ChatRequest(request_id, body)


class LoopbackChatTransport:
    """One bounded request, no redirects/proxies/DNS names/retry/fallback.

    ``allow_loopback_io`` is an explicit test opt-in, NOT a usage grant. Live
    controller integration, tunnel identity and real authorization are pending.
    No environment, credential file or key store is consulted. No logger exists.
    """

    def __init__(self, endpoint, *, allow_loopback_io=False, bearer=None):
        if type(endpoint) is not TunnelEndpoint or type(allow_loopback_io) is not bool:
            raise PrivateChatError("invalid_transport")
        if bearer is not None and (type(bearer) is not str or not 1 <= len(bearer) <= 256
                                   or not re.fullmatch(r"[A-Za-z0-9._~-]+", bearer)):
            raise PrivateChatError("invalid_auth_header")
        self._endpoint, self._enabled, self._bearer = endpoint, allow_loopback_io, bearer

    def _remaining(self, deadline, cancel):
        if cancel.is_set():
            raise PrivateChatError("request_cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PrivateChatError("request_deadline")
        return remaining

    def chat(self, payload, request_id, *, deadline, cancel):
        request = build_chat_request(payload, request_id)
        if not self._enabled:
            raise PrivateChatError("connection_disabled")
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or deadline - time.monotonic() > 30 or not callable(getattr(cancel, "is_set", None))):
            raise PrivateChatError("invalid_deadline")
        timeout = self._remaining(deadline, cancel)
        connection = http.client.HTTPConnection(self._endpoint.host, self._endpoint.port, timeout=timeout)
        response = watchdog = None
        finished = threading.Event()
        try:
            # Connect separately so cancellation/deadline is checked after connection
            # setup, immediately before any request bytes can be dispatched.
            connection.connect()
            active_socket = connection.sock
            active_socket.settimeout(self._remaining(deadline, cancel))

            def watch():
                # Covers header drip as well as body waits: socket read timeouts
                # alone reset per recv and are not an absolute HTTP deadline.
                while not finished.wait(0.01):
                    if cancel.is_set() or time.monotonic() >= deadline:
                        try:
                            active_socket.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        return

            watchdog = threading.Thread(target=watch, name="private-chat-fixture-watchdog")
            watchdog.start()
            headers = {"Content-Type": "application/json", "Accept": "application/json",
                       "X-Request-ID": request.request_id, "Connection": "close"}
            if self._bearer is not None:
                headers["Authorization"] = "Bearer " + self._bearer
            self._remaining(deadline, cancel)
            connection.request(request.method, request.path, request.body, headers)
            active_socket.settimeout(self._remaining(deadline, cancel))
            response = connection.getresponse()
            self._remaining(deadline, cancel)
            if response.status != 200:
                # 3xx Location is never followed; no response error text is read.
                raise PrivateChatError("upstream_http_error")
            if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise PrivateChatError("invalid_upstream_type")
            lengths = response.headers.get_all("Content-Length", [])
            if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
                    or not 0 < int(lengths[0]) <= MAX_BODY or response.getheader("Transfer-Encoding") is not None):
                raise PrivateChatError("invalid_upstream_framing")
            # Bounded length-delimited JSON only. Streaming/chunked responses need
            # a separately reviewed reader; do not infer success from bare EOF.
            expected, body = int(lengths[0]), bytearray()
            while len(body) < expected:
                active_socket.settimeout(self._remaining(deadline, cancel))
                chunk = response.read1(min(4096, expected - len(body)))
                self._remaining(deadline, cancel)
                if not chunk:
                    raise PrivateChatError("truncated_upstream_body")
                body.extend(chunk)
            completion = self._completion(body)
            self._remaining(deadline, cancel)
            return completion
        except PrivateChatError:
            raise
        except (socket.timeout, TimeoutError):
            if cancel.is_set():
                raise PrivateChatError("request_cancelled") from None
            if time.monotonic() >= deadline:
                raise PrivateChatError("request_deadline") from None
            raise PrivateChatError("upstream_timeout") from None
        except (OSError, http.client.HTTPException, ValueError, UnicodeError, KeyError, TypeError, AttributeError, RecursionError):
            if cancel.is_set():
                raise PrivateChatError("request_cancelled") from None
            if time.monotonic() >= deadline:
                raise PrivateChatError("request_deadline") from None
            raise PrivateChatError("upstream_protocol_error") from None
        finally:
            finished.set()
            if response is not None:
                response.close()
            connection.close()
            if watchdog is not None:
                watchdog.join()

    @staticmethod
    def _completion(body):
        try:
            value = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs,
                               parse_constant=_invalid_constant)
            choices = value["choices"]
            choice = choices[0]
            message = choice["message"]
            if (type(value) is not dict or value.get("model") != "qwen-27b"
                    or type(choices) is not list or len(choices) != 1
                    or type(choice.get("index")) is not int or choice["index"] != 0 or choice.get("finish_reason") != "stop"
                    or type(message) is not dict or set(message) != {"role", "content"}
                    or message["role"] != "assistant" or type(message["content"]) is not str
                    or len(message["content"].encode("utf-8")) > 8192):
                raise PrivateChatError("invalid_upstream_completion")
            # Discard unknown provider metadata instead of forwarding it into OWUI.
            return {"model": "qwen-27b", "choices": [{"index": 0, "message": dict(message), "finish_reason": "stop"}]}
        except PrivateChatError:
            raise
        except (ValueError, UnicodeError, KeyError, TypeError, IndexError, AttributeError, RecursionError):
            raise PrivateChatError("invalid_upstream_completion") from None
