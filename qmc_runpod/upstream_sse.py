"""Bounded private-loopback upstream JSON/SSE; construction never connects."""
from contextlib import contextmanager
from dataclasses import dataclass
import http.client
import json
import math
import re
import socket
import threading
import time

from .private_chat import (MAX_BODY, LoopbackChatTransport, PrivateChatError,
                           TunnelEndpoint, _invalid_constant, _pairs, build_chat_request)


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result): raise PrivateChatError("invalid_upstream_json")
    return result


def _strict_json(value):
    return json.loads(value, object_pairs_hook=_pairs, parse_constant=_invalid_constant, parse_float=_finite_float)


def prepare(payload, request_id):
    if type(payload) is not dict or type(payload.get("stream", False)) is not bool:
        raise PrivateChatError("unsupported_chat")
    raw = json.loads(build_chat_request(dict(payload, stream=False), request_id).body)
    raw["stream"] = payload.get("stream", False)
    return raw


@dataclass(frozen=True)
class StreamEnd:
    wire: bytes


class SSEDecoder:
    """One incremental bounded line/frame; provider metadata never forwarded."""
    def __init__(self, request_id):
        build_chat_request({"model": "qwen-27b", "messages": [{"role": "user", "content": "fixture"}]}, request_id)
        self.request_id = request_id
        self.pending = bytearray()
        self.data = []
        self.frame_bytes = self.input_bytes = self.output_bytes = self.events = self.content_bytes = 0
        self.provider_id = None
        self.role = self.stopped = self.done = False

    def _encode(self, delta, finish=None):
        value = {"id": self.request_id, "object": "chat.completion.chunk", "model": "qwen-27b",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        wire = b"data: " + json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n\n"
        self.output_bytes += len(wire)
        if len(wire) > 8192 or self.output_bytes > 65536:
            raise PrivateChatError("sse_output_limit")
        return wire

    def _frame(self):
        if not self.data:
            return []
        self.events += 1
        if self.events > 256:
            raise PrivateChatError("sse_event_limit")
        raw = b"\n".join(self.data)
        self.data.clear()
        if raw == b"[DONE]":
            if not self.role or not self.stopped or self.done:
                raise PrivateChatError("invalid_sse_terminal")
            self.done = True
            wire = self._encode({}, "stop") + b"data: [DONE]\n\n"
            self.output_bytes += len(b"data: [DONE]\n\n")
            if self.output_bytes > 65536:
                raise PrivateChatError("sse_output_limit")
            return [StreamEnd(wire)]
        if self.stopped or self.done:
            raise PrivateChatError("invalid_sse_order")
        try:
            value = _strict_json(raw.decode("utf-8"))
            if type(value) is not dict or "error" in value:
                raise PrivateChatError("upstream_stream_error")
            identity = value.get("id")
            if (type(identity) is not str or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", identity)
                    or value.get("model") != "qwen-27b" or value.get("object") != "chat.completion.chunk"):
                raise PrivateChatError("invalid_sse_chunk")
            if self.provider_id is None:
                self.provider_id = identity
            if identity != self.provider_id:
                raise PrivateChatError("invalid_sse_identity")
            choices = value["choices"]
            if type(choices) is not list or len(choices) != 1:
                raise PrivateChatError("invalid_sse_chunk")
            choice = choices[0]
            delta, finish = choice["delta"], choice["finish_reason"]
            if (type(choice.get("index")) is not int or choice["index"] != 0
                    or type(delta) is not dict or set(delta) - {"role", "content"}
                    or finish not in (None, "stop")):
                raise PrivateChatError("invalid_sse_chunk")
            output = []
            if not self.role:
                if delta.get("role") != "assistant":
                    raise PrivateChatError("invalid_sse_role")
                self.role = True
                output.append(self._encode({"role": "assistant"}))
            elif "role" in delta:
                raise PrivateChatError("invalid_sse_role")
            if "content" in delta:
                content = delta["content"]
                if type(content) is not str:
                    raise PrivateChatError("invalid_sse_content")
                self.content_bytes += len(content.encode("utf-8"))
                if self.content_bytes > 8192:
                    raise PrivateChatError("sse_content_limit")
                if content:
                    output.append(self._encode({"content": content}))
            if finish == "stop":
                self.stopped = True
            return output
        except PrivateChatError:
            raise
        except (ValueError, UnicodeError, TypeError, KeyError, IndexError, RecursionError):
            raise PrivateChatError("invalid_sse_chunk") from None

    def feed(self, chunk):
        if type(chunk) is not bytes or len(chunk) > 4096:
            raise PrivateChatError("sse_read_limit")
        self.input_bytes += len(chunk)
        if self.input_bytes > MAX_BODY:
            raise PrivateChatError("sse_input_limit")
        output = []
        for byte in chunk:
            if self.done:
                raise PrivateChatError("sse_trailing_data")
            self.pending.append(byte)
            self.frame_bytes += 1
            if len(self.pending) > 8192 or self.frame_bytes > 8192:
                raise PrivateChatError("sse_frame_limit")
            if byte != 10:
                continue
            line = bytes(self.pending[:-1]).removesuffix(b"\r")
            self.pending.clear()
            try: line.decode("utf-8")
            except UnicodeError: raise PrivateChatError("invalid_sse_utf8") from None
            if not line:
                output.extend(self._frame())
                self.frame_bytes = 0
            elif line.startswith(b":"):
                pass
            elif line.startswith(b"data:"):
                self.data.append(line[5:].removeprefix(b" "))
            elif line == b"event: message":
                pass
            else:
                raise PrivateChatError("unsupported_sse_field")
        return output

    def finish(self):
        if not self.done or self.pending or self.data:
            raise PrivateChatError("incomplete_sse")


class _BoundedLines:
    def __init__(self, file):
        self.file, self.headers, self.bytes, self.lines = file, True, 0, 0

    def readline(self, limit=-1):
        cap = 16384 if self.headers else 32768
        line = self.file.readline(min(limit if limit >= 0 else cap + 1, cap - self.bytes + 1))
        self.bytes += len(line); self.lines += 1
        if self.bytes > cap or self.lines > (68 if self.headers else 4096):
            raise PrivateChatError("upstream_header_or_framing_limit")
        return line

    def __getattr__(self, name):
        return getattr(self.file, name)


class _Response(http.client.HTTPResponse):
    def begin(self):
        self.fp = _BoundedLines(self.fp)
        super().begin()
        self.fp.headers, self.fp.bytes, self.fp.lines = False, 0, 0


class LoopbackUpstream:
    def __init__(self, endpoint, *, allow_loopback_io=False, bearer=None):
        # Reuse Phase3a's inert endpoint/auth validation without changing it.
        validated = LoopbackChatTransport(endpoint, allow_loopback_io=allow_loopback_io, bearer=bearer)
        self.endpoint, self.enabled, self._bearer = endpoint, validated._enabled, bearer

    @contextmanager
    def _open(self, payload, request_id, deadline, cancel, fence):
        if not self.enabled:
            raise PrivateChatError("connection_disabled")
        if (type(deadline) not in (float, int) or not math.isfinite(deadline)
                or deadline - time.monotonic() > 30 or not isinstance(cancel, threading.Event)):
            raise PrivateChatError("invalid_deadline")

        def check():
            if cancel.is_set(): raise PrivateChatError("request_cancelled")
            if time.monotonic() >= deadline: raise PrivateChatError("request_deadline")
            fence()
            if cancel.is_set(): raise PrivateChatError("request_cancelled")
            if time.monotonic() >= deadline: raise PrivateChatError("request_deadline")

        check()
        raw = json.dumps(prepare(payload, request_id), ensure_ascii=False).encode()
        if len(raw) > MAX_BODY: raise PrivateChatError("request_too_large")
        connection = http.client.HTTPConnection("127.0.0.1", self.endpoint.port, timeout=deadline - time.monotonic())
        connection.response_class = _Response
        response = watchdog = None
        finished = threading.Event()
        try:
            connection.connect()
            sock = connection.sock
            check()

            def watch():
                while not finished.wait(0.01):
                    if cancel.is_set() or time.monotonic() >= deadline:
                        try: sock.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                        return

            watchdog = threading.Thread(target=watch, name="upstream-fixture-watchdog")
            watchdog.start()
            headers = {"Content-Type": "application/json", "Connection": "close", "X-Request-ID": request_id,
                       "Accept": "text/event-stream" if payload.get("stream", False) else "application/json"}
            if self._bearer is not None: headers["Authorization"] = "Bearer " + self._bearer
            check()  # Final post-connect/lookup fence; no controller lock crosses I/O.
            sock.settimeout(deadline - time.monotonic())
            connection.request("POST", "/v1/chat/completions", raw, headers)
            response = connection.getresponse()
            check()
            if response.status != 200: raise PrivateChatError("upstream_http_error")
            expected_type = "text/event-stream" if payload.get("stream", False) else "application/json"
            if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != expected_type:
                raise PrivateChatError("invalid_upstream_type")
            lengths, transfers = response.headers.get_all("Content-Length", []), response.headers.get_all("Transfer-Encoding", [])
            if (len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers)
                    or (transfers and transfers != ["chunked"])
                    or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= MAX_BODY))):
                raise PrivateChatError("invalid_upstream_framing")

            def read():
                check()
                if response.isclosed():
                    if response.length not in (None, 0):
                        raise PrivateChatError("truncated_upstream_body")
                    return b""
                sock.settimeout(deadline - time.monotonic())
                chunk = response.read1(4096)
                check()
                if not chunk and response.length not in (None, 0):
                    raise PrivateChatError("truncated_upstream_body")
                return chunk

            yield read
        except PrivateChatError:
            raise
        except (OSError, http.client.HTTPException, UnicodeError, ValueError, TypeError, RecursionError):
            if cancel.is_set(): raise PrivateChatError("request_cancelled") from None
            if time.monotonic() >= deadline: raise PrivateChatError("request_deadline") from None
            raise PrivateChatError("upstream_protocol_error") from None
        finally:
            finished.set()
            if response is not None: response.close()
            connection.close()
            if watchdog is not None: watchdog.join()

    def json(self, payload, request_id, *, deadline, cancel, fence):
        with self._open(dict(payload, stream=False), request_id, deadline, cancel, fence) as read:
            data = bytearray()
            while True:
                chunk = read()
                if not chunk: break
                data.extend(chunk)
                if len(data) > MAX_BODY: raise PrivateChatError("upstream_body_limit")
            _strict_json(data.decode("utf-8"))  # Also reject nested 1e400 overflow before metadata stripping.
            completion = LoopbackChatTransport._completion(data)
            if cancel.is_set(): raise PrivateChatError("request_cancelled")
            if time.monotonic() >= deadline: raise PrivateChatError("request_deadline")
            fence()
            if cancel.is_set(): raise PrivateChatError("request_cancelled")
            if time.monotonic() >= deadline: raise PrivateChatError("request_deadline")
            return completion

    def stream(self, payload, request_id, *, deadline, cancel, fence):
        decoder = SSEDecoder(request_id)
        with self._open(dict(payload, stream=True), request_id, deadline, cancel, fence) as read:
            while not decoder.done:
                chunk = read()
                if not chunk: break
                for event in decoder.feed(chunk):
                    # Consumer may pause after a prior buffered event. Its fence
                    # is additional policy, not this transport's deadline/cancel.
                    if cancel.is_set(): raise PrivateChatError("request_cancelled")
                    if time.monotonic() >= deadline: raise PrivateChatError("request_deadline")
                    fence()
                    if cancel.is_set(): raise PrivateChatError("request_cancelled")
                    if time.monotonic() >= deadline: raise PrivateChatError("request_deadline")
                    yield event
            decoder.finish()
