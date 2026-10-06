"""OWUI-shaped requests -> bound mock grant -> ephemeral upstream JSON/SSE."""
from contextlib import contextmanager
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import http.server
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_mock_gateway as guards
from test_ondemand_controller import FakeClock, make_controller, make_limits, ready
from qmc_runpod.owui_binding import BindingError, MockUpstream, OWUIBinding
from qmc_runpod.private_chat import LoopbackChatTransport, PrivateChatError, TunnelEndpoint
from qmc_runpod.upstream_sse import LoopbackUpstream, SSEDecoder, StreamEnd


def body(stream=True):
    return {"model": "qwen-27b", "messages": [{"role": "user", "content": "模型🙂"}], "stream": stream}


def chunk(delta, finish=None, identity="provider-fixture", **extra):
    value = {"id": identity, "object": "chat.completion.chunk", "model": "qwen-27b",
             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
    return b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n\n"


def stream_wire():
    return chunk({"role": "assistant"}) + chunk({"content": "模型🙂\nquoted\""}) + chunk({}, "stop") + b"data: [DONE]\n\n"


def decode(wire, *, piece=4096, request_id="owui-one"):
    decoder = SSEDecoder(request_id); events = []
    for offset in range(0, len(wire), piece): events.extend(decoder.feed(wire[offset:offset + piece]))
    decoder.finish()
    return events


@contextmanager
def server(*, framing="chunked", wire=None, status=200, extra_headers=(), stall=None, raw_header=None, header_packet=None):
    seen = []
    wire = stream_wire() if wire is None else wire
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, dict(self.headers), payload))
            try:
                if header_packet is not None:
                    self.wfile.write(header_packet); return
                if raw_header is not None:
                    for byte in raw_header:
                        self.wfile.write(bytes([byte])); self.wfile.flush(); threading.Event().wait(0.002)
                    return
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream" if payload["stream"] else "application/json")
                if framing == "chunked": self.send_header("Transfer-Encoding", "chunked")
                if framing == "length": self.send_header("Content-Length", str(len(wire)))
                if framing == "truncated": self.send_header("Content-Length", str(len(wire) + 20))
                for key, value in extra_headers: self.send_header(key, value)
                self.end_headers()
                if stall is not None:
                    stall.set(); threading.Event().wait(0.15)
                if framing == "chunked":
                    # Deliberately split inside multibyte UTF-8 and every delimiter.
                    for offset in range(0, len(wire), 7):
                        part = wire[offset:offset + 7]
                        self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else: self.wfile.write(wire)
            except OSError: pass
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=lambda: service.serve_forever(poll_interval=0.01)); worker.start()
    try: yield service.server_address, seen
    finally:
        service.shutdown(); service.server_close(); worker.join(1)
        assert not worker.is_alive()


class OWUIUpstreamTests(unittest.TestCase):
    def setUp(self):
        self.guards = guards.GatewayTests(); self.guards.setUp(); self.addCleanup(self.guards.doCleanups)

    def bridge(self, upstream=None, *, usd="1.00"):
        controller = make_controller(clock=FakeClock(), idle=10)
        controller.start("session-a", make_limits("session-a", usd=usd))
        for expected in ("created", "bootstrapped", "ready"): self.assertEqual(controller.run_one_step(), expected)
        result = OWUIBinding(controller, upstream); result.bind_ready_session("session-a")
        return result

    def consume(self, bridge, *, payload=None, request_id="owui-one", permit=None, timeout=1):
        payload = body() if payload is None else payload
        permit = bridge.permit_interactive(payload, request_id) if permit is None else permit
        cancel = threading.Event()
        with bridge.stream(payload, request_id, permit=permit, deadline=time.monotonic() + timeout, cancel=cancel) as output:
            return b"".join(output)

    def test_parser_fragments_utf8_crlf_and_normalizes_provider_identity(self):
        events = decode(stream_wire().replace(b"\n\n", b"\r\n\r\n"), piece=1)
        self.assertEqual(sum(type(e) is StreamEnd for e in events), 1)
        wire = b"".join(e.wire if type(e) is StreamEnd else e for e in events)
        self.assertNotIn(b"provider-fixture", wire)
        self.assertEqual(wire.count(b"data: [DONE]"), 1)
        self.assertIn("模型🙂".encode(), wire)
        values = [json.loads(line[6:]) for line in wire.split(b"\n") if line.startswith(b"data: {")]
        self.assertEqual({v["id"] for v in values}, {"owui-one"})

    def test_parser_requires_done_and_holds_finish_until_verified_terminal(self):
        decoder = SSEDecoder("owui-one")
        events = decoder.feed(chunk({"role": "assistant"}) + chunk({}, "stop"))
        self.assertNotIn(b'"finish_reason":"stop"', b"".join(events))
        with self.assertRaisesRegex(PrivateChatError, "incomplete_sse"): decoder.finish()
        for wire in (b"data: [DONE]\n\n", chunk({"role": "assistant"}) + b"data: [DONE]\n\n",
                     stream_wire() + b"data: [DONE]\n\n"):
            with self.subTest(wire=wire), self.assertRaises(PrivateChatError): decode(wire)

    def test_parser_refuses_executable_fields_error_and_invalid_utf8(self):
        for wire in (b"event: execute\n\ndata: {}\n\n", b"id: replay\n\n", b"retry: 5\n\n",
                     b"data: {\"error\":\"SECRET\"}\n\n", b":\xff\n\n"):
            with self.subTest(wire=wire), self.assertRaises(PrivateChatError) as error: decode(wire)
            self.assertNotIn("SECRET", str(error.exception))

    def test_parser_refuses_changed_model_id_role_tool_nonfinite_duplicate(self):
        values = [chunk({"content": "no role"}), chunk({"role": "assistant"}, model="other"),
                  chunk({"role": "assistant"}) + chunk({"content": "x"}, identity="changed"),
                  chunk({"role": "assistant"}) + chunk({"role": "assistant"}),
                  chunk({"role": "assistant", "tool_calls": []}), b'data: {"x":NaN}\n\n', b'data: {"x":1,"x":2}\n\n']
        for wire in values:
            with self.subTest(wire=wire), self.assertRaises(PrivateChatError): decode(wire)

    def test_overflowed_float_in_unknown_nested_metadata_is_rejected_before_strip(self):
        wire = chunk({"role": "assistant"}, metadata={"value": 0}).replace(b'"value": 0', b'"value": 1e400')
        with self.assertRaisesRegex(PrivateChatError, "invalid_upstream_json"): decode(wire)
        value = {"model": "qwen-27b", "metadata": {"value": 0}, "choices": [{"index": 0,
                 "message": {"role": "assistant", "content": "fixture"}, "finish_reason": "stop"}]}
        wire = json.dumps(value).encode().replace(b'"value": 0', b'"value": 1e400')
        with server(framing="length", wire=wire) as (address, _):
            bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True))
            permit = bridge.permit_interactive(body(False), "r")
            with self.assertRaises(PrivateChatError):
                bridge.json(body(False), "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event())

    def test_parser_limits_read_frame_input_event_and_cumulative_content(self):
        with self.assertRaisesRegex(PrivateChatError, "sse_read_limit"): SSEDecoder("r").feed(b"x" * 4097)
        with self.assertRaisesRegex(PrivateChatError, "sse_frame_limit"): decode(b"data: " + b"x" * 8200)
        with self.assertRaisesRegex(PrivateChatError, "sse_input_limit"): decode(b":heartbeat\n\n" * 6000)
        with self.assertRaisesRegex(PrivateChatError, "sse_event_limit"):
            decode(chunk({"role": "assistant"}) + chunk({"content": ""}) * 256)
        with self.assertRaisesRegex(PrivateChatError, "sse_content_limit"):
            decode(chunk({"role": "assistant"}) + chunk({"content": "界" * 1000}) * 3)

    def test_parser_output_limit_is_independent_of_input_and_content(self):
        wire = chunk({"role": "assistant"}, identity="p") + chunk({"content": "\x01" * 1000}, identity="p") * 3
        wire += chunk({"content": "x"}, identity="p") * 250
        self.assertLess(len(wire), 65536)
        with self.assertRaisesRegex(PrivateChatError, "sse_output_limit"): decode(wire, request_id="r" * 64)

    def test_default_absent_binding_status_catalog_are_allocation_free(self):
        with patch.object(socket, "socket", side_effect=AssertionError("socket")):
            bridge = OWUIBinding(); before = bridge.controller.store.snapshot()
            for _ in range(3): bridge.catalog(); bridge.status()
            self.assertEqual(bridge.controller.store.snapshot(), before)
            with self.assertRaises(BindingError): bridge.bind_ready_session("missing")
            self.assertEqual(bridge.controller.provider.calls, [])

    def test_isolated_new_module_imports_and_defaults_do_not_read_environment_or_execute(self):
        class NoEnvironment(dict):
            def __getitem__(self, key): raise AssertionError("environment read")
            def get(self, *args): raise AssertionError("environment read")
            def __iter__(self): raise AssertionError("environment enumeration")
        for name in ("upstream_sse", "owui_binding"):
            path = Path(__file__).resolve().parents[1] / "qmc_runpod" / (name + ".py")
            spec = importlib.util.spec_from_file_location("qmc_runpod._inert_" + name, path)
            module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
            try:
                with patch.object(socket, "socket", side_effect=AssertionError("socket")), \
                     patch.object(subprocess, "Popen", side_effect=AssertionError("process")), \
                     patch.object(os, "getenv", side_effect=AssertionError("environment read")), \
                     patch.object(os, "environ", NoEnvironment()):
                    spec.loader.exec_module(module)
                    if name == "owui_binding":
                        bridge = module.OWUIBinding(); bridge.status(); bridge.catalog(); bridge.close()
                    else:
                        self.assertFalse(module.LoopbackUpstream(TunnelEndpoint(19181)).enabled)
            finally: del sys.modules[spec.name]

    def test_missing_background_or_forged_permit_cannot_admit(self):
        bridge = self.bridge(); calls = list(bridge.controller.provider.calls)
        permit = bridge.permit_interactive(body(False), "r")
        for suspect in (None, replace(permit), "interactive", {"task": "title_generation"}):
            with self.subTest(suspect=suspect), self.assertRaisesRegex(BindingError, "interactive_intent_required"):
                bridge.json(body(False), "r", permit=suspect, deadline=time.monotonic() + 1, cancel=threading.Event())
        self.assertEqual(bridge.upstream.calls, 0); self.assertEqual(bridge.controller.provider.calls, calls)
        self.assertEqual(bridge.controller.http_release_count, 0)

    def test_exact_payload_intent_is_consumed_once_and_id_not_replayed(self):
        bridge = self.bridge(); payload = body(False); permit = bridge.permit_interactive(payload, "r")
        altered = dict(payload, max_tokens=12)
        with self.assertRaises(BindingError): bridge.json(altered, "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event())
        self.assertEqual(bridge.upstream.calls, 0)
        result = bridge.json(payload, "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event())
        self.assertEqual(result["id"], "r")
        with self.assertRaises(BindingError): bridge.json(payload, "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event())
        again = bridge.permit_interactive(payload, "r")
        with self.assertRaises(BindingError): bridge.json(payload, "r", permit=again, deadline=time.monotonic() + 1, cancel=threading.Event())
        self.assertEqual(bridge.upstream.calls, 1); self.assertEqual(bridge.controller.http_release_count, 1)

    def test_client_session_and_task_claims_are_rejected_not_trusted(self):
        bridge = self.bridge()
        for claim in ({"session_id": "other"}, {"metadata": {"task": "title_generation"}}, {"interactive": True}):
            with self.assertRaises(PrivateChatError): bridge.permit_interactive(dict(body(), **claim), "r")
        self.assertEqual(bridge.upstream.calls, 0)

    def test_mock_json_and_sse_leave_idle_grant_and_provider_unchanged(self):
        for streaming in (False, True):
            bridge = self.bridge(); before = bridge.controller.store.snapshot()["session"]
            calls = list(bridge.controller.provider.calls)
            if streaming: self.assertIn(b"data: [DONE]", self.consume(bridge))
            else:
                permit = bridge.permit_interactive(body(False), "r")
                bridge.json(body(False), "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event())
            after = bridge.controller.store.snapshot()["session"]
            self.assertEqual(before["idle_deadline"], after["idle_deadline"])
            self.assertEqual(before["deadline"], after["deadline"])
            self.assertEqual(bridge.controller.provider.calls, calls)
            self.assertEqual(bridge.controller.http_release_count, 1)

    def test_real_loopback_chunked_length_and_eof_sse_complete_without_private_fields(self):
        for framing in ("chunked", "length", "eof"):
            with self.subTest(framing=framing), server(framing=framing) as (address, seen):
                bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True))
                wire = self.consume(bridge)
                self.assertEqual(wire.count(b"data: [DONE]"), 1)
                self.assertEqual(seen[0][0], "/v1/chat/completions")
                self.assertNotIn("Authorization", seen[0][1]); self.assertNotIn("session_id", seen[0][2])
                self.assertEqual(bridge.controller.http_release_count, 1)

    def test_real_loopback_json_normalizes_owned_request_id(self):
        wire = json.dumps({"model": "qwen-27b", "id": "private-provider-id", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "回答"}, "finish_reason": "stop"}]}).encode()
        with server(framing="length", wire=wire) as (address, _):
            bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True))
            permit = bridge.permit_interactive(body(False), "r")
            result = bridge.json(body(False), "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event())
        self.assertEqual(result["id"], "r"); self.assertNotIn("private-provider-id", str(result))

    def test_eof_without_done_and_declared_truncated_json_never_succeed(self):
        with server(framing="eof", wire=chunk({"role": "assistant"}) + chunk({}, "stop")) as (address, _):
            bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True))
            with self.assertRaisesRegex(PrivateChatError, "incomplete_sse"): self.consume(bridge)
            self.assertEqual(bridge.controller.http_release_count, 1)
        with server(framing="truncated", wire=b"{}") as (address, _):
            bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True))
            permit = bridge.permit_interactive(body(False), "r")
            with self.assertRaises(PrivateChatError): bridge.json(body(False), "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event())

    def test_nonready_stale_binding_budget_and_reconnect_do_not_start_provider(self):
        bridge = self.bridge(usd="0.0102"); self.consume(bridge)
        calls = list(bridge.controller.provider.calls)
        with self.assertRaises(BindingError): self.consume(bridge, request_id="second")
        self.assertEqual(bridge.controller.provider.calls, calls); self.assertEqual(bridge.upstream.calls, 1)
        bridge = self.bridge(); permit = bridge.permit_interactive(body(), "r"); bridge.abort()
        with self.assertRaises(BindingError): self.consume(bridge, request_id="r", permit=permit)
        self.assertEqual(bridge.upstream.calls, 0)

    def test_json_late_stop_and_stream_midway_stop_never_emit_done(self):
        bridge = self.bridge(); original = bridge.upstream.json
        def stop(*args, **kwargs):
            result = original(*args, **kwargs); bridge.controller.abort(session_id="session-a"); return result
        permit = bridge.permit_interactive(body(False), "r")
        with patch.object(bridge.upstream, "json", stop), self.assertRaises(BindingError):
            bridge.json(body(False), "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event())
        self.assertEqual(bridge.controller.http_release_count, 1)
        bridge = self.bridge(); permit = bridge.permit_interactive(body(), "r")
        with bridge.stream(body(), "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event()) as output:
            first = next(output); bridge.abort()
            with self.assertRaises(BindingError): next(output)
        self.assertNotIn(b"[DONE]", first); self.assertEqual(bridge.controller.http_release_count, 1)

    def test_terminal_success_then_stop_preserves_one_logical_terminal(self):
        bridge = self.bridge(); permit = bridge.permit_interactive(body(), "r")
        with bridge.stream(body(), "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event()) as output:
            next(output); next(output); terminal = next(output); bridge.abort()
            self.assertIn(b"[DONE]", terminal)
            with self.assertRaises(StopIteration): next(output)
        self.assertEqual(bridge.controller.http_release_count, 1)

    def test_scope_expiry_before_terminal_and_close_without_consumption_release_once(self):
        bridge = self.bridge(); permit = bridge.permit_interactive(body(), "r")
        with bridge.stream(body(), "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event()) as output:
            first = next(output); bridge.controller.clock.set(111)
            with self.assertRaises(BindingError): next(output)
        self.assertNotIn(b"[DONE]", first); self.assertEqual(bridge.controller.http_release_count, 1)
        bridge = self.bridge(); permit = bridge.permit_interactive(body(), "r")
        with bridge.stream(body(), "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event()): pass
        self.assertEqual(bridge.upstream.calls, 0); self.assertEqual(bridge.controller.http_release_count, 1)

    def test_busy_concurrent_request_is_denied_and_one_release(self):
        bridge = self.bridge(); entered, go = threading.Event(), threading.Event()
        original = bridge.upstream.json
        def wait(*args, **kwargs): entered.set(); go.wait(1); return original(*args, **kwargs)
        first = bridge.permit_interactive(body(False), "first"); second = bridge.permit_interactive(body(False), "second")
        with patch.object(bridge.upstream, "json", wait), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(bridge.json, body(False), "first", permit=first, deadline=time.monotonic() + 1, cancel=threading.Event())
            self.assertTrue(entered.wait(1))
            try:
                with self.assertRaisesRegex(BindingError, "backend_busy"):
                    bridge.json(body(False), "second", permit=second, deadline=time.monotonic() + 1, cancel=threading.Event())
            finally: go.set()
            future.result(1)
        self.assertEqual(bridge.controller.http_release_count, 1); self.assertEqual(bridge.upstream.calls, 1)

    def test_store_wait_expiry_does_not_admit_or_reserve_or_dispatch(self):
        bridge = self.bridge(); permit = bridge.permit_interactive(body(), "r")
        before = bridge.controller.store.snapshot()["session"]["spent_usd"]
        locked, go, started = threading.Event(), threading.Event(), threading.Event()
        def hold():
            with bridge.controller.store.locked(): locked.set(); go.wait(1)
        holder = threading.Thread(target=hold); holder.start(); self.assertTrue(locked.wait(1))
        def run():
            started.set()
            return self.consume(bridge, request_id="r", permit=permit, timeout=0.02)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(run); self.assertTrue(started.wait(1)); threading.Event().wait(0.05); go.set()
            with self.assertRaisesRegex(BindingError, "request_deadline_or_cancel"): result.result(1)
        holder.join(1)
        self.assertEqual(bridge.upstream.calls, 0); self.assertEqual(bridge.controller.http_release_count, 0)
        self.assertEqual(bridge.controller.store.snapshot()["session"]["spent_usd"], before)

    def test_checkpoint_elapsed_deadline_rejects_before_reservation_and_admission(self):
        bridge = self.bridge(); clock = [100.0]
        with patch.object(time, "monotonic", side_effect=lambda: clock[0]):
            permit = bridge.permit_interactive(body(False), "r")
            save = bridge.controller._save
            def delayed(lock):
                save(lock); clock[0] += 0.06
            with patch.object(bridge.controller, "_save", delayed), self.assertRaisesRegex(BindingError, "request_deadline_or_cancel"):
                bridge.json(body(False), "r", permit=permit, deadline=100.02, cancel=threading.Event())
        self.assertEqual(bridge.controller.store.snapshot()["session"]["spent_usd"], "0")
        self.assertEqual(bridge.upstream.calls, 0); self.assertEqual(bridge.controller.http_release_count, 0)
        self.assertIsNone(bridge.controller.store.snapshot()["session"]["active"])

    def test_permit_ttl_is_checked_at_consumption_after_checkpoint(self):
        bridge = self.bridge(); clock = [100.0]
        with patch.object(time, "monotonic", side_effect=lambda: clock[0]):
            permit = bridge.permit_interactive(body(False), "r")
            save = bridge.controller._save
            def delayed(lock):
                save(lock); clock[0] += 5.05
            with patch.object(bridge.controller, "_save", delayed), self.assertRaisesRegex(BindingError, "interactive_intent_required"):
                bridge.json(body(False), "r", permit=permit, deadline=130.0, cancel=threading.Event())
        self.assertEqual(bridge.controller.store.snapshot()["session"]["spent_usd"], "0")
        self.assertEqual(bridge.upstream.calls, 0); self.assertEqual(bridge.controller.http_release_count, 0)

    def test_standalone_buffered_events_enforce_own_cancel_without_policy_fence(self):
        with server(framing="length") as (address, _):
            transport = LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True)
            cancel = threading.Event()
            source = transport.stream(body(), "r", deadline=time.monotonic() + 1, cancel=cancel, fence=lambda: None)
            try:
                self.assertIn(b'"role":"assistant"', next(source)); cancel.set()
                with self.assertRaisesRegex(PrivateChatError, "request_cancelled"): next(source)
            finally: source.close()

    def test_standalone_buffered_events_enforce_own_deadline_without_policy_fence(self):
        with server(framing="length") as (address, _):
            transport = LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True)
            deadline = time.monotonic() + 1
            source = transport.stream(body(), "r", deadline=deadline, cancel=threading.Event(), fence=lambda: None)
            try:
                self.assertIn(b'"role":"assistant"', next(source))
                with patch.object(time, "monotonic", return_value=deadline + 0.01), self.assertRaisesRegex(PrivateChatError, "request_deadline"):
                    next(source)
            finally: source.close()

    def test_standalone_json_checks_own_boundary_after_parser_and_policy_fence(self):
        wire = json.dumps({"model": "qwen-27b", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "fixture"}, "finish_reason": "stop"}]}).encode()
        original_parse, original_time = LoopbackChatTransport._completion, time.monotonic
        for phase in ("parser", "fence"):
            for boundary in ("cancel", "deadline"):
                with self.subTest(phase=phase, boundary=boundary), server(framing="length", wire=wire) as (address, _):
                    transport = LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True)
                    deadline = original_time() + 1; cancel = threading.Event(); flags = {"parsed": False, "expired": False}
                    def trip():
                        if boundary == "cancel": cancel.set()
                        else: flags["expired"] = True
                    def parse(value):
                        result = original_parse(value); flags["parsed"] = True
                        if phase == "parser": trip()
                        return result
                    def fence():
                        if phase == "fence" and flags["parsed"]: trip()
                    code = "request_cancelled" if boundary == "cancel" else "request_deadline"
                    with patch.object(LoopbackChatTransport, "_completion", parse), \
                         patch.object(time, "monotonic", side_effect=lambda: deadline + 0.01 if flags["expired"] else original_time()), \
                         self.assertRaisesRegex(PrivateChatError, code):
                        transport.json(body(False), "r", deadline=deadline, cancel=cancel, fence=fence)

    def test_connect_time_stop_prevents_request_bytes_and_close_is_idempotent(self):
        with server() as (address, seen):
            bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True))
            original = guards.http.client.HTTPConnection.connect
            def connect(connection): original(connection); bridge.abort()
            with patch.object(guards.http.client.HTTPConnection, "connect", connect), self.assertRaises(PrivateChatError):
                self.consume(bridge)
            self.assertEqual(seen, []); self.assertEqual(bridge.controller.http_release_count, 1)
        bridge = self.bridge(); permit = bridge.permit_interactive(body(), "r")
        with bridge.stream(body(), "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event()) as output:
            bridge.close(); bridge.close()
            with self.assertRaises(StopIteration): next(output)
        self.assertEqual(bridge.controller.http_release_count, 1); self.assertEqual(bridge.upstream.calls, 0)

    def test_header_budget_rejects_large_line_before_body_parsing(self):
        header = b"HTTP/1.1 200 OK\r\nX-Foo: " + b"x" * 17000 + b"\r\n\r\n"
        with server(header_packet=header) as (address, seen):
            bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True))
            with self.assertRaisesRegex(PrivateChatError, "upstream_header_or_framing_limit"): self.consume(bridge)
            self.assertEqual(len(seen), 1); self.assertEqual(bridge.controller.http_release_count, 1)

    def test_close_paused_network_stream_releases_socket_watchdog_before_context_exit(self):
        with server() as (address, _):
            bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True))
            permit = bridge.permit_interactive(body(), "r")
            with bridge.stream(body(), "r", permit=permit, deadline=time.monotonic() + 1, cancel=threading.Event()) as output:
                next(output); bridge.close()
                self.assertFalse(any(t.name == "upstream-fixture-watchdog" for t in threading.enumerate()))
                self.assertEqual(bridge.controller.http_release_count, 1)
                with self.assertRaises(StopIteration): next(output)
            self.assertEqual(bridge.controller.http_release_count, 1)

    def test_upstream_headers_absolute_deadline_and_cancel_close_no_watchdog_orphan(self):
        with server(raw_header=b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n") as (address, _):
            bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True))
            with self.assertRaisesRegex(PrivateChatError, "request_deadline"): self.consume(bridge, timeout=0.04)
        stalled = threading.Event()
        with server(stall=stalled) as (address, _):
            bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True))
            permit = bridge.permit_interactive(body(), "r")
            worker = threading.Thread(target=lambda: (stalled.wait(1), bridge.abort())); worker.start()
            try:
                with self.assertRaises((BindingError, PrivateChatError)):
                    self.consume(bridge, request_id="r", permit=permit)
            finally: worker.join(1)
            self.assertEqual(bridge.controller.http_release_count, 1)
        self.assertFalse(any(t.name == "upstream-fixture-watchdog" for t in threading.enumerate()))

    def test_redirect_bad_framing_and_disabled_transport_never_fallback(self):
        for kwargs in ({"status": 301, "extra_headers": (("Location", "https://api.openai.com/v1"),)},
                       {"extra_headers": (("Content-Length", "5"),)},
                       {"extra_headers": (("Transfer-Encoding", "gzip"),)}):
            with server(**kwargs) as (address, seen):
                bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(address[1]), allow_loopback_io=True))
                with self.assertRaises(PrivateChatError): self.consume(bridge)
                self.assertEqual(len(seen), 1); self.assertEqual(bridge.controller.http_release_count, 1)
        bridge = self.bridge(LoopbackUpstream(TunnelEndpoint(19181)))
        with patch.object(socket, "socket", side_effect=AssertionError("socket")), self.assertRaisesRegex(PrivateChatError, "connection_disabled"):
            self.consume(bridge)


if __name__ == "__main__": unittest.main()
