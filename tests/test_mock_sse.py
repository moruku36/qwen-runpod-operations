"""Bounded synthetic SSE protocol and race matrix on isolated IPv4 loopback."""
import codecs
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

import test_mock_gateway as shared
from test_mock_gateway import chat_body, request, wait_for
from qmc_runpod.mock_gateway import MockGateway, StreamLimits, SYNTHETIC_BEARER


def body(): return dict(chat_body(), stream=True)


def open_stream(address, request_id="stream-one"):
    connection = http.client.HTTPConnection(*address, timeout=2)
    connection.request("POST", "/v1/chat/completions", json.dumps(body()).encode(),
        {"Authorization": "Bearer " + SYNTHETIC_BEARER, "Content-Type": "application/json", "X-Request-ID": request_id})
    return connection, connection.getresponse()


def read_stream(address, request_id="stream-one", read_size=8192):
    connection, response = open_stream(address, request_id)
    try:
        parts = []
        while True:
            data = response.read(read_size)
            if not data: break
            parts.append(data)
        return response.status, response.getheader("Content-Type"), b"".join(parts), parts
    finally: connection.close()


def parse_success(parts):
    decoder = codecs.getincrementaldecoder("utf-8")()
    text = "".join(decoder.decode(part) for part in parts) + decoder.decode(b"", final=True)
    assert text.endswith("\n\n"), "truncated SSE frame"
    frames = text[:-2].split("\n\n")
    assert frames[-1] == "data: [DONE]" and frames.count("data: [DONE]") == 1, "EOF is not successful completion"
    items = [json.loads(frame[6:]) for frame in frames[:-1] if frame.startswith("data: ")]
    assert len(items) == len(frames) - 1 and items[0]["choices"][0]["delta"]["role"] == "assistant"
    assert items[-1]["choices"][0]["finish_reason"] == "stop"
    assert sum(item["choices"][0]["finish_reason"] == "stop" for item in items) == 1
    assert {item["id"] for item in items} == {"stream-one"} and {item["model"] for item in items} == {"qwen-27b"}
    return "".join(item["choices"][0]["delta"].get("content", "") for item in items)


class SSETests(unittest.TestCase):
    def setUp(self):
        # Reuse only the established outbound/bind guards, not inherited test cases.
        self.guards = shared.GatewayTests(); self.guards.setUp()
        self.addCleanup(self.guards.doCleanups)

    def gateway(self, **kwargs): return self.guards.gateway_ready(processing_timeout=1, **kwargs)

    def assert_released(self, gateway):
        wait_for(lambda: gateway.controller.http_release_count == 1)
        self.assertEqual(gateway.controller.http_release_count, 1)
        self.assertIsNone(gateway.controller.store.snapshot()["session"]["active"])

    def test_utf8_json_blankline_reconstruction_and_normal_nonstream(self):
        gateway = self.gateway(stream_fixture="utf8")
        before = gateway.controller.store.snapshot()["session"]
        with gateway.serve() as address:
            status, kind, raw, parts = read_stream(address, read_size=1)
            self.assertEqual(status, 200); self.assertIn("text/event-stream", kind)
            self.assertEqual(parse_success(parts), '模型🙂\n\n"quoted" café')
            self.assert_released(gateway)
            after = gateway.controller.store.snapshot()["session"]
            self.assertEqual((after["idle_deadline"], after["last_activity"]), (before["idle_deadline"], before["last_activity"]))
            self.assertEqual(request(address, "POST", "/v1/chat/completions", chat_body())[0], 200)
        metric = gateway.stream_metrics()[0]
        self.assertEqual((metric["winner"], metric["claims"], metric["complete"]), ("success", 1, True))
        self.assertEqual(metric["bytes"], len(raw))
        self.assertNotIn("模型", json.dumps(gateway.logs()))

    def test_header_gate_deadline_cancel_stop_expiry_returns_json_without_200(self):
        for boundary, expected in (("deadline", 504), ("cancel", 504), ("stop", 409), ("expiry", 503)):
            with self.subTest(boundary=boundary):
                gateway = self.gateway()
                entered, proceed = threading.Event(), threading.Event(); captured = []
                original = gateway._emit_stream
                def before_header(sock, stream):
                    captured.append(stream); entered.set(); self.assertTrue(proceed.wait(1))
                    return original(sock, stream)
                with patch.object(gateway, "_emit_stream", side_effect=before_header), gateway.serve() as address, ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(request, address, "POST", "/v1/chat/completions", body())
                    self.assertTrue(entered.wait(1))
                    if boundary == "deadline": captured[0].deadline = time.monotonic() - 1
                    if boundary == "cancel": captured[0].cancel.set()
                    if boundary == "stop": gateway.controller.abort(session_id="s")
                    if boundary == "expiry": gateway.controller.clock.set(110)
                    proceed.set(); status, result = pending.result()
                    self.assertEqual(status, expected); self.assertIn("error", result)
                    self.assert_released(gateway)
                self.assertFalse(gateway.stream_metrics()[0]["header_started"])

    def test_stream_store_contention_cancel_or_deadline_prevents_dispatch(self):
        for boundary in ("timeout", "disconnect"):
            with self.subTest(boundary=boundary):
                gateway = self.guards.gateway_ready(processing_timeout=0.04 if boundary == "timeout" else 1)
                controller = gateway.controller
                entered, proceed = threading.Event(), threading.Event(); context = []
                original = controller.execute_http_chat
                def execution(ticket, **kwargs):
                    context.append(kwargs); entered.set(); self.assertTrue(proceed.wait(1))
                    return original(ticket, **kwargs)
                with patch.object(controller, "execute_http_chat", side_effect=execution), gateway.serve() as address, ThreadPoolExecutor(max_workers=1) as pool:
                    if boundary == "timeout":
                        pending = pool.submit(request, address, "POST", "/v1/chat/completions", body())
                    else:
                        data = json.dumps(body()).encode(); sock = socket.create_connection(address, timeout=2)
                        sock.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nAuthorization: Bearer " + SYNTHETIC_BEARER.encode() + b"\r\nContent-Type: application/json\r\n" + f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
                    self.assertTrue(entered.wait(1)); self.assertTrue(context[0]["defer_release"])
                    with controller.store.locked():
                        proceed.set()
                        if boundary == "disconnect": sock.close()
                        self.assertTrue(context[0]["cancel_event"].wait(1))
                    if boundary == "timeout": self.assertEqual(pending.result()[0], 504)
                    self.assert_released(gateway)
                self.assertFalse(any(k == "chat" for k, _ in controller.transport.calls))

    def test_last_header_scope_check_after_checkpoint_cannot_leak_200(self):
        gateway = self.gateway()
        controller, armed = gateway.controller, threading.Event()
        original_save, original_select = controller._save, __import__("select").select
        def writable_then_checkpoint(readers, writers, errors, timeout=0):
            result = original_select(readers, writers, errors, timeout)
            if writers: armed.set()
            return result
        def checkpoint_advances_scope(lock):
            original_save(lock)
            if armed.is_set(): controller.clock.set(110); armed.clear()
        with patch.object(controller, "_save", side_effect=checkpoint_advances_scope), \
             patch("qmc_runpod.mock_gateway.select.select", side_effect=writable_then_checkpoint), gateway.serve() as address:
            status, result = request(address, "POST", "/v1/chat/completions", body())
            self.assertEqual(status, 503); self.assertEqual(result["error"]["code"], "backend_not_ready")
            self.assert_released(gateway)
        self.assertFalse(gateway.stream_metrics()[0]["header_started"])

    def _write_deadline_after_checkpoint(self, *, header):
        gateway = self.gateway(stream_limits=StreamLimits(write_seconds=0.02, total_seconds=0.5))
        controller = gateway.controller
        original_save, original_select, original_write = controller._save, __import__("select").select, gateway._stream_write
        current = [1.0]
        target, armed, delayed = threading.Event(), threading.Event(), threading.Event()

        def writing(sock, stream, payload, **kwargs):
            if header == kwargs.get("header", False) and (header or stream.events > 1): target.set()
            return original_write(sock, stream, payload, **kwargs)

        def writable(readers, writers, errors, timeout=0):
            result = original_select(readers, writers, errors, timeout)
            if writers and target.is_set() and not delayed.is_set(): armed.set()
            return result

        def checkpoint(lock):
            original_save(lock)
            if armed.is_set():
                current[0] += 0.06  # Finite checkpoint time after writable select, inside guard.
                armed.clear(); delayed.set()

        with patch("qmc_runpod.mock_gateway.time.monotonic", side_effect=lambda: current[0]), \
             patch.object(controller, "_save", side_effect=checkpoint), \
             patch.object(gateway, "_stream_write", side_effect=writing), \
             patch("qmc_runpod.mock_gateway.select.select", side_effect=writable), gateway.serve() as address:
            if header:
                status, result = request(address, "POST", "/v1/chat/completions", body())
                self.assertEqual((status, result["error"]["code"]), (504, "stream_write_timeout"))
            else:
                status, kind, raw, _ = read_stream(address)
                self.assertEqual(status, 200); self.assertIn("text/event-stream", kind)
                self.assertIn(b"event: error", raw)
                self.assertNotIn(b'"finish_reason":"stop"', raw); self.assertNotIn(b"[DONE]", raw)
                with self.assertRaises(AssertionError): parse_success([raw])
            self.assertTrue(delayed.is_set())
            self.assert_released(gateway)
        metric = gateway.stream_metrics()[0]
        self.assertEqual(metric["header_started"], not header)
        self.assertEqual((metric["complete"], metric["claims"]), (False, 1))
        self.assertEqual(gateway.logs()[0]["outcome"], "stream_write_timeout")

    def test_preheader_each_write_deadline_rechecked_after_writable_guard_checkpoint(self):
        self._write_deadline_after_checkpoint(header=True)

    def test_postheader_each_write_deadline_rechecked_after_writable_guard_checkpoint(self):
        self._write_deadline_after_checkpoint(header=False)

    def test_error_after_header_has_safe_event_no_finish_done_or_secret(self):
        gateway = self.gateway()
        original = gateway._stream_write
        def failure(sock, stream, payload, **kwargs):
            if not kwargs.get("header") and not kwargs.get("error_write") and stream.events > 1:
                raise RuntimeError("private-output-auth-secret")
            return original(sock, stream, payload, **kwargs)
        with patch.object(gateway, "_stream_write", side_effect=failure), gateway.serve() as address:
            status, kind, raw, _ = read_stream(address)
            self.assertEqual(status, 200); self.assertIn("text/event-stream", kind)
            self.assertIn(b"event: error", raw)
            self.assertNotIn(b'"finish_reason":"stop"', raw); self.assertNotIn(b"[DONE]", raw)
            self.assertNotIn(b"private-output-auth-secret", raw)
            with self.assertRaises(AssertionError): parse_success([raw])
            self.assert_released(gateway)
        self.assertNotIn("private-output-auth-secret", json.dumps(gateway.logs()))
        self.assertEqual(gateway.stream_metrics()[0]["claims"], 1)

    def test_absent_stream_and_title_like_calls_never_start_or_change_model_catalog_state(self):
        gateway = MockGateway()
        before = gateway.controller.store.snapshot()
        with gateway.serve() as address:
            for _ in range(3):
                self.assertEqual(request(address, "GET", "/v1/models")[0], 200)
                self.assertEqual(request(address, "GET", "/_mock/sessions/s")[0], 404)
            self.assertEqual(gateway.controller.store.snapshot(), before)
            self.assertEqual(request(address, "POST", "/v1/chat/completions", body())[0], 503)
            title = body(); title["messages"][0]["content"] = "Generate a short title"
            self.assertEqual(request(address, "POST", "/v1/chat/completions", title)[0], 503)
        self.assertIsNone(gateway.controller.store.snapshot()["session"])
        self.assertEqual(gateway.controller.provider.calls, [])
        self.assertEqual(gateway.controller.transport.calls, [])

    def test_midstream_disconnect_and_reconnect_cannot_reuse_old_request_or_revive(self):
        gateway = self.gateway(stream_chunk_delay=0.03)
        with gateway.serve() as address:
            connection, response = open_stream(address, "old-stream")
            self.assertEqual(response.status, 200)
            self.assertTrue(response.read(1)); response.close(); connection.close()
            self.assert_released(gateway)
            self.assertEqual(request(address, "POST", "/v1/chat/completions", body(), {"X-Request-ID": "old-stream"})[0], 409)
            self.assertEqual(request(address, "POST", "/_mock/sessions/s/stop", {})[0], 202)
            self.assertEqual(request(address, "POST", "/v1/chat/completions", body())[0], 503)
        self.assertEqual(gateway.stream_metrics()[0]["claims"], 1)
        self.assertFalse(gateway.stream_metrics()[0]["complete"])
        self.assertEqual(sum(k == "create" for k, _ in gateway.controller.provider.calls), 1)

    def test_content_events_bytes_and_frame_limits_independently_bounded(self):
        cases = [(StreamLimits(content_bytes=1), "transport", 413),
                 (StreamLimits(events=1), "transport", 200),
                 (StreamLimits(output_bytes=200), "transport", 200),
                 (StreamLimits(frame_bytes=240, piece_chars=64), "long", 200)]
        for limits, fixture, expected in cases:
            with self.subTest(limits=limits):
                gateway = self.gateway(stream_limits=limits, stream_fixture=fixture)
                with gateway.serve() as address:
                    status, _, raw, _ = read_stream(address, "limit")
                    self.assertEqual(status, expected); self.assertNotIn(b"[DONE]", raw)
                    self.assert_released(gateway)
                metric = gateway.stream_metrics()[0]
                self.assertLessEqual(metric["events"], limits.events)
                self.assertLessEqual(metric["bytes"], limits.output_bytes)
                self.assertLessEqual(metric["pending_peak"], limits.frame_bytes)
                self.assertLessEqual(len(raw), limits.output_bytes if expected == 200 else 512)

    def test_total_timeout_and_slow_write_watchdog_close_without_success(self):
        for boundary in ("total", "write"):
            with self.subTest(boundary=boundary):
                limits = StreamLimits(total_seconds=0.03 if boundary == "total" else 0.5, write_seconds=0.03)
                gateway = self.gateway(stream_limits=limits, stream_chunk_delay=0.02 if boundary == "total" else 0)
                original_write = gateway._stream_write
                original_select = __import__("select").select
                stalled = threading.Event()
                def writer(sock, stream, payload, **kwargs):
                    if boundary == "write" and stream.events > 1: stalled.set()
                    return original_write(sock, stream, payload, **kwargs)
                def nonwritable(readers, writers, errors, timeout=0):
                    if writers and stalled.is_set():
                        threading.Event().wait(min(timeout, 0.001)); return [], [], []
                    return original_select(readers, writers, errors, timeout)
                started = time.monotonic()
                with patch.object(gateway, "_stream_write", side_effect=writer), patch("qmc_runpod.mock_gateway.select.select", side_effect=nonwritable), gateway.serve() as address:
                    status, _, raw, _ = read_stream(address)
                    self.assertEqual(status, 200); self.assertNotIn(b"[DONE]", raw)
                    self.assert_released(gateway)
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertFalse(gateway.stream_metrics()[0]["complete"])
                self.assertEqual(gateway.stream_metrics()[0]["claims"], 1)

    def test_stop_or_expiry_and_terminal_success_have_one_linear_winner(self):
        for boundary in ("stop", "expiry"):
            for first in ("control", "success"):
                with self.subTest(boundary=boundary, first=first):
                    gateway = self.gateway()
                    entered, proceed = threading.Event(), threading.Event()
                    original_guard, original_write = gateway._stream_guard, gateway._stream_write
                    def guard(sock, stream, **kwargs):
                        if first == "control" and kwargs.get("claim_success"):
                            entered.set(); self.assertTrue(proceed.wait(1))
                        return original_guard(sock, stream, **kwargs)
                    def writer(sock, stream, payload, **kwargs):
                        if first == "success" and b'"finish_reason":"stop"' in payload:
                            entered.set(); self.assertTrue(proceed.wait(1))
                        return original_write(sock, stream, payload, **kwargs)
                    with patch.object(gateway, "_stream_guard", side_effect=guard), patch.object(gateway, "_stream_write", side_effect=writer), gateway.serve() as address, ThreadPoolExecutor(max_workers=1) as pool:
                        pending = pool.submit(read_stream, address)
                        self.assertTrue(entered.wait(1))
                        if boundary == "stop": gateway.controller.abort(session_id="s")
                        else: gateway.controller.clock.set(110)
                        proceed.set(); status, _, raw, _ = pending.result()
                        self.assertEqual(status, 200)
                        if first == "success": self.assertEqual(parse_success([raw]), "simulated response")
                        else: self.assertNotIn(b"[DONE]", raw); self.assertNotIn(b'"finish_reason":"stop"', raw)
                        self.assert_released(gateway)
                    self.assertEqual(gateway.stream_metrics()[0]["claims"], 1)
                    self.assertEqual(gateway.stream_metrics()[0]["winner"], "success" if first == "success" else "error")

    def test_shutdown_cancels_stream_and_leaves_no_success_or_orphan(self):
        gateway = self.gateway(stream_chunk_delay=0.05)
        with gateway.serve() as address:
            connection, response = open_stream(address)
            self.assertEqual(response.status, 200); self.assertTrue(response.read(1))
        raw = response.read(); response.close(); connection.close()
        self.assertNotIn(b"[DONE]", raw)
        self.assert_released(gateway)
        self.assertTrue(all(not worker.is_alive() for worker in gateway._workers))
        self.assertFalse(gateway._active_sockets)
        self.assertEqual(gateway.stream_metrics()[0]["claims"], 1)

    def test_error_and_disconnect_race_keeps_one_terminal_winner_in_both_orders(self):
        for first in ("error", "disconnect"):
            with self.subTest(first=first):
                gateway = self.gateway()
                entered, proceed = threading.Event(), threading.Event()
                original = gateway._stream_write
                def writer(sock, stream, payload, **kwargs):
                    if first == "error" and kwargs.get("error_write"):
                        entered.set(); self.assertTrue(proceed.wait(1))
                    if not kwargs.get("header") and not kwargs.get("error_write") and stream.events > 1:
                        if first == "error": raise RuntimeError("private-stream-fault")
                        entered.set(); self.assertTrue(proceed.wait(1))
                    return original(sock, stream, payload, **kwargs)
                with patch.object(gateway, "_stream_write", side_effect=writer), gateway.serve() as address:
                    connection, response = open_stream(address)
                    self.assertEqual(response.status, 200)
                    self.assertTrue(entered.wait(1))
                    response.close(); connection.close(); proceed.set()
                    self.assert_released(gateway)
                    wait_for(lambda: bool(gateway.stream_metrics()))
                metric = gateway.stream_metrics()[0]
                self.assertEqual(metric["winner"], first)
                self.assertEqual(metric["claims"], 1); self.assertFalse(metric["complete"])
                self.assertNotIn("private-stream-fault", json.dumps(gateway.logs()))


if __name__ == "__main__": unittest.main()
