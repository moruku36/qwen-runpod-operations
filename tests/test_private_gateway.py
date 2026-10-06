"""Mock authority; isolated ephemeral loopback only, never real OWUI/RunPod."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import http.server
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qmc_runpod.private_gateway import CONTROL_FIXTURE, INFERENCE_FIXTURE, PrivateGateway
from qmc_runpod.mock_gateway import GatewayError, StreamLimits
from qmc_runpod.private_chat import TunnelEndpoint
from qmc_runpod.upstream_sse import LoopbackUpstream
import test_mock_gateway as guards
from test_ondemand_controller import FakeClock, make_controller, ready
from test_ondemand_controller import exact_approval, export_pending
from test_owui_upstream import server, chunk, stream_wire


def body(stream=False, text="public CPU fixture"):
    return {"model": "qwen-27b", "messages": [{"role": "user", "content": text}],
            "stream": stream, "max_tokens": 32}


def request(address, method, path, payload=None, *, key=INFERENCE_FIXTURE, rid="request-a", token=None):
    connection = http.client.HTTPConnection(*address, timeout=2)
    headers = {"Authorization": "Bearer " + key, "X-Request-ID": rid}
    if token is not None: headers["X-Intent-Token"] = token
    if payload is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(payload).encode()
    try:
        connection.request(method, path, payload, headers)
        response = connection.getresponse()
        wire = response.read()
        return response.status, dict(response.getheaders()), wire
    finally:
        connection.close()


def intent(address, payload, rid="request-a"):
    status, _, wire = request(address, "POST", "/_control/intents", {"payload": payload}, key=CONTROL_FIXTURE, rid=rid)
    if status != 200: raise AssertionError((status, wire))
    return json.loads(wire)["intent_token"]


class PrivateGatewayTests(unittest.TestCase):
    def setUp(self):
        self.guards = guards.GatewayTests(); self.guards.setUp(); self.addCleanup(self.guards.doCleanups)

    def gateway(self, upstream=None, **kwargs):
        controller = make_controller(clock=FakeClock(), idle=10)
        ready(controller, "s")
        gateway = PrivateGateway(controller, upstream=upstream, **kwargs)
        gateway.binding.bind_ready_session("s")
        return gateway

    def test_default_inert_and_no_fixed_or_nonloopback_service(self):
        with patch.object(socket, "socket", side_effect=AssertionError("unexpected IO")):
            gateway = PrivateGateway()
            self.assertEqual(gateway.controller.provider.calls, [])
            for host, port in (("0.0.0.0", 0), ("localhost", 0), ("127.0.0.1", 8080), ("127.0.0.1", True)):
                with self.assertRaises(ValueError):
                    with gateway.serve(host=host, port=port): pass
        with self.assertRaises(ValueError): PrivateGateway(inference_key="same", control_key="same")
        with self.assertRaises(ValueError): PrivateGateway(control_key="bad\r\nkey")

    def test_catalog_status_health_are_read_only(self):
        gateway = self.gateway(); before = gateway.controller.store.snapshot()
        calls = list(gateway.controller.provider.calls)
        with gateway.serve() as address:
            for route in ("/healthz", "/v1/models", "/status"):
                self.assertEqual(request(address, "GET", route)[0], 200)
        self.assertEqual(before, gateway.controller.store.snapshot())
        self.assertEqual(calls, gateway.controller.provider.calls)
        self.assertEqual(gateway.binding.upstream.calls, 0)

    def test_inference_auth_cannot_mint_control_and_control_cannot_infer(self):
        gateway = self.gateway()
        with gateway.serve() as address:
            self.assertEqual(request(address, "POST", "/_control/intents", {"payload": body()})[0], 401)
            token = intent(address, body())
            self.assertEqual(request(address, "POST", "/v1/chat/completions", body(), key=CONTROL_FIXTURE, token=token)[0], 401)
            self.assertEqual(request(address, "GET", "/v1/models", key="wrong")[0], 401)
        self.assertEqual(gateway.binding.upstream.calls, 0)

    def test_chat_and_background_claim_without_intent_cannot_start(self):
        gateway = PrivateGateway()
        with gateway.serve() as address:
            for payload in (body(), dict(body(), metadata={"task": "manual_chat", "user_id": "fake"}),
                            dict(body(), title_generation=True)):
                self.assertEqual(request(address, "POST", "/v1/chat/completions", payload)[0], 403)
            self.assertEqual(request(address, "POST", "/_mock/sessions", {})[0], 404)
        self.assertEqual(gateway.controller.provider.calls, [])
        self.assertEqual(gateway.binding.upstream.calls, 0)

    def test_ready_standard_json_and_one_use_intent(self):
        gateway = self.gateway(); idle = gateway.controller.store.snapshot()["session"]["idle_deadline"]
        with gateway.serve() as address:
            token = intent(address, body())
            status, headers, wire = request(address, "POST", "/v1/chat/completions", body(), token=token)
            self.assertEqual(status, 200); value = json.loads(wire)
            self.assertEqual(value["id"], "request-a"); self.assertEqual(value["object"], "chat.completion")
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(request(address, "POST", "/v1/chat/completions", body(), token=token)[0], 403)
        self.assertEqual(gateway.binding.upstream.calls, 1)
        self.assertEqual(gateway.controller.http_release_count, 1)
        self.assertEqual(gateway.controller.store.snapshot()["session"]["idle_deadline"], idle)

    def test_changed_body_or_request_id_never_reserves(self):
        for altered, rid in ((body(text="changed"), "request-a"), (body(), "other")):
            gateway = self.gateway()
            before = gateway.controller.store.snapshot()["session"]["spent_usd"]
            with gateway.serve() as address:
                token = intent(address, body())
                self.assertEqual(request(address, "POST", "/v1/chat/completions", altered, rid=rid, token=token)[0], 403)
            self.assertEqual(gateway.binding.upstream.calls, 0)
            self.assertEqual(gateway.controller.store.snapshot()["session"]["spent_usd"], before)
            self.assertEqual(gateway.controller.http_release_count, 0)

    def test_intent_expiry_is_enforced_before_admission(self):
        gateway = self.gateway()
        with gateway.serve() as address:
            token = intent(address, body())
            with gateway._intent_lock:
                permit = gateway._intents[token]
                # Replace both trusted object registries with an expired fixture.
                from dataclasses import replace
                expired = replace(permit, expires_at=time.monotonic() - 1)
                gateway._intents[token] = expired
                gateway.binding._permits[permit.sequence] = expired
            self.assertEqual(request(address, "POST", "/v1/chat/completions", body(), token=token)[0], 403)
        self.assertEqual(gateway.binding.upstream.calls, 0)
        self.assertEqual(gateway.controller.http_release_count, 0)

    def test_sse_normalized_role_content_terminal_and_release(self):
        gateway = self.gateway()
        with gateway.serve() as address:
            token = intent(address, body(True))
            status, headers, wire = request(address, "POST", "/v1/chat/completions", body(True), token=token)
            self.assertEqual(status, 200)
            self.assertTrue(headers["Content-Type"].startswith("text/event-stream"))
            self.assertEqual(wire.count(b"data: [DONE]"), 1)
            self.assertNotIn(b"public-provider-fixture", wire)
            values = [json.loads(line[6:]) for line in wire.split(b"\n") if line.startswith(b"data: {")]
            self.assertEqual({v["id"] for v in values}, {"request-a"})
            self.assertEqual(values[0]["choices"][0]["delta"], {"role": "assistant"})
            self.assertEqual(values[-1]["choices"][0]["finish_reason"], "stop")
        self.assertEqual(gateway.controller.http_release_count, 1)
        self.assertTrue(gateway.stream_metrics()[0]["complete"])

    def test_http_client_through_gateway_to_private_json_fixture(self):
        wire = json.dumps({"id": "untrusted-provider", "model": "qwen-27b", "choices": [{"index": 0,
               "message": {"role": "assistant", "content": "private fixture"}, "finish_reason": "stop"}]}).encode()
        with server(framing="length", wire=wire) as (upstream_address, seen):
            gateway = self.gateway(LoopbackUpstream(TunnelEndpoint(upstream_address[1]), allow_loopback_io=True))
            with gateway.serve() as address:
                token = intent(address, body())
                status, _, response = request(address, "POST", "/v1/chat/completions", body(), token=token)
                self.assertEqual(status, 200); self.assertNotIn(b"untrusted-provider", response)
            self.assertEqual(seen[0][0], "/v1/chat/completions")
            self.assertEqual(seen[0][2], body())

    def test_http_client_gateway_private_chunked_sse_fixture(self):
        with server() as (upstream_address, seen):
            gateway = self.gateway(LoopbackUpstream(TunnelEndpoint(upstream_address[1]), allow_loopback_io=True))
            with gateway.serve() as address:
                token = intent(address, body(True))
                status, _, wire = request(address, "POST", "/v1/chat/completions", body(True), token=token)
                self.assertEqual(status, 200); self.assertEqual(wire.count(b"data: [DONE]"), 1)
                self.assertNotIn(b"provider-fixture", wire)
            self.assertEqual(len(seen), 1)

    def test_preheader_private_error_is_sanitized_json(self):
        with server(status=403, wire=b"SECRET_SENTINEL") as (upstream_address, _):
            gateway = self.gateway(LoopbackUpstream(TunnelEndpoint(upstream_address[1]), allow_loopback_io=True))
            with gateway.serve() as address:
                token = intent(address, body(True))
                status, headers, wire = request(address, "POST", "/v1/chat/completions", body(True), token=token)
                self.assertEqual(status, 502); self.assertEqual(headers["Content-Type"], "application/json")
                self.assertNotIn(b"SECRET_SENTINEL", wire)
        self.assertEqual(gateway.controller.http_release_count, 1)

    def test_postheader_incomplete_sse_has_no_success_terminal(self):
        incomplete = chunk({"role": "assistant"}) + chunk({"content": "partial"})
        with server(framing="length", wire=incomplete) as (upstream_address, _):
            gateway = self.gateway(LoopbackUpstream(TunnelEndpoint(upstream_address[1]), allow_loopback_io=True))
            with gateway.serve() as address:
                token = intent(address, body(True))
                status, headers, wire = request(address, "POST", "/v1/chat/completions", body(True), token=token)
                self.assertEqual(status, 200); self.assertTrue(headers["Content-Type"].startswith("text/event-stream"))
                self.assertNotIn(b"data: [DONE]", wire)
                self.assertNotIn(b'"finish_reason":"stop"', wire)
                self.assertEqual(wire.count(b"HTTP/1.1"), 0)
        self.assertEqual(gateway.controller.http_release_count, 1)

    def test_deadline_closes_stalled_upstream_before_header(self):
        stalled = threading.Event()
        with server(stall=stalled) as (upstream_address, seen):
            gateway = self.gateway(LoopbackUpstream(TunnelEndpoint(upstream_address[1]), allow_loopback_io=True), processing_timeout=.04)
            with gateway.serve() as address:
                token = intent(address, body(True))
                start = time.monotonic()
                status, headers, wire = request(address, "POST", "/v1/chat/completions", body(True), token=token)
                self.assertEqual(status, 504)
                self.assertLess(time.monotonic() - start, .5)
        self.assertTrue(stalled.is_set()); self.assertEqual(len(seen), 1)
        self.assertEqual(gateway.controller.http_release_count, 1)

    def test_control_stop_cancels_wait_and_no_terminal(self):
        stalled = threading.Event()
        with server(stall=stalled) as (upstream_address, _):
            gateway = self.gateway(LoopbackUpstream(TunnelEndpoint(upstream_address[1]), allow_loopback_io=True))
            with gateway.serve() as address, ThreadPoolExecutor(max_workers=1) as pool:
                token = intent(address, body(True))
                pending = pool.submit(request, address, "POST", "/v1/chat/completions", body(True), token=token)
                self.assertTrue(stalled.wait(1))
                self.assertEqual(request(address, "POST", "/_control/stop", {"session_id": "s"}, key=CONTROL_FIXTURE)[0], 200)
                try:
                    status, _, wire = pending.result(1)
                    self.assertNotEqual(status, 200); self.assertNotIn(b"data: [DONE]", wire)
                except http.client.RemoteDisconnected:
                    pass
        self.assertEqual(gateway.controller.status()["phase"], "draining")
        self.assertEqual(gateway.controller.http_release_count, 1)

    def test_strict_framing_duplicate_json_nonfinite_and_unknown_routes(self):
        gateway = self.gateway()
        with gateway.serve() as address:
            for payload in (b'{"payload":1,"payload":2}', b'{"payload":{"x":1e400}}', b'{"payload":NaN}'):
                raw = (b"POST /_control/intents HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer " + CONTROL_FIXTURE.encode()
                    + b"\r\nX-Request-ID: r\r\nContent-Type: application/json\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
                self.assertEqual(guards.raw_request(address, raw)[0], 400)
            raw = b"POST /v1/chat/completions HTTP/1.1\r\nContent-Length: 0\r\nTransfer-Encoding: chunked\r\n\r\n"
            self.assertEqual(guards.raw_request(address, raw)[0], 400)
            self.assertEqual(request(address, "GET", "/status?token=SECRET_SENTINEL")[0], 404)
        self.assertEqual(gateway.binding.upstream.calls, 0)
        self.assertNotIn("SECRET_SENTINEL", repr(gateway.logs()))

    def test_logs_never_contain_keys_intent_or_prompt(self):
        gateway = self.gateway()
        with gateway.serve() as address:
            payload = body(text="SECRET_SENTINEL")
            token = intent(address, payload)
            self.assertEqual(request(address, "POST", "/v1/chat/completions", payload, token=token)[0], 200)
        logs = repr(gateway.logs())
        for value in (CONTROL_FIXTURE, INFERENCE_FIXTURE, token, "SECRET_SENTINEL"):
            self.assertNotIn(value, logs)
        self.assertEqual(set(gateway.logs()[0]), {"id", "route", "status", "duration", "outcome"})

    def test_checkpoint_wait_past_processing_deadline_reserves_nothing(self):
        gateway = self.gateway(processing_timeout=.02)
        original = gateway.controller._begin
        def late(lock, *args):
            result = original(lock, *args)
            threading.Event().wait(.04)
            return result
        with gateway.serve() as address:
            token = intent(address, body())
            with patch.object(gateway.controller, "_begin", side_effect=late):
                self.assertEqual(request(address, "POST", "/v1/chat/completions", body(), token=token)[0], 504)
        self.assertEqual(gateway.controller.store.snapshot()["session"]["spent_usd"], "0")
        self.assertEqual(gateway.binding.upstream.calls, 0)
        self.assertEqual(gateway.controller.http_release_count, 0)

    def test_concurrent_request_is_busy_without_second_upstream_call(self):
        stalled = threading.Event()
        wire = json.dumps({"model": "qwen-27b", "choices": [{"index": 0,
               "message": {"role": "assistant", "content": "fixture"}, "finish_reason": "stop"}]}).encode()
        with server(framing="length", wire=wire, stall=stalled) as (upstream_address, seen):
            gateway = self.gateway(LoopbackUpstream(TunnelEndpoint(upstream_address[1]), allow_loopback_io=True))
            with gateway.serve() as address, ThreadPoolExecutor(max_workers=1) as pool:
                first = intent(address, body(), "first"); second = intent(address, body(), "second")
                pending = pool.submit(request, address, "POST", "/v1/chat/completions", body(), rid="first", token=first)
                self.assertTrue(stalled.wait(1))
                self.assertEqual(request(address, "POST", "/v1/chat/completions", body(), rid="second", token=second)[0], 429)
                self.assertEqual(pending.result(1)[0], 200)
            self.assertEqual(len(seen), 1)
        self.assertEqual(gateway.controller.http_release_count, 1)

    def test_wrong_stop_scope_does_not_drain_or_cancel(self):
        gateway = self.gateway()
        with gateway.serve() as address:
            self.assertEqual(request(address, "POST", "/_control/stop", {"session_id": "s"})[0], 401)
            self.assertEqual(request(address, "POST", "/_control/stop", {"session_id": "other"}, key=CONTROL_FIXTURE)[0], 409)
        self.assertEqual(gateway.controller.status()["phase"], "ready")

    def test_output_cap_closes_without_done_and_releases(self):
        limits = StreamLimits(content_bytes=8192, output_bytes=300, events=128, frame_bytes=1024,
                              piece_chars=8, total_seconds=1, write_seconds=.1)
        gateway = self.gateway(stream_limits=limits)
        with gateway.serve() as address:
            token = intent(address, body(True))
            status, _, wire = request(address, "POST", "/v1/chat/completions", body(True), token=token)
            self.assertEqual(status, 200); self.assertNotIn(b"data: [DONE]", wire)
        metric = gateway.stream_metrics()[0]
        self.assertFalse(metric["complete"]); self.assertLessEqual(metric["bytes"], 300)
        self.assertEqual(gateway.controller.http_release_count, 1)

    def test_disconnect_during_upstream_read_cancels_and_releases_once(self):
        stalled = threading.Event()
        with server(stall=stalled) as (upstream_address, seen):
            gateway = self.gateway(LoopbackUpstream(TunnelEndpoint(upstream_address[1]), allow_loopback_io=True))
            with gateway.serve() as address:
                token = intent(address, body(True))
                payload = json.dumps(body(True)).encode()
                wire = (b"POST /v1/chat/completions HTTP/1.1\r\nHost: fixture\r\nAuthorization: Bearer " + INFERENCE_FIXTURE.encode()
                        + b"\r\nX-Request-ID: request-a\r\nX-Intent-Token: " + token.encode()
                        + b"\r\nContent-Type: application/json\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
                client = socket.create_connection(address, timeout=1)
                client.sendall(wire); self.assertTrue(stalled.wait(1)); client.close()
                guards.wait_for(lambda: gateway.controller.http_release_count == 1)
            self.assertEqual(len(seen), 1)
        self.assertFalse(any(thread.name == "mock-private-client-watch" for thread in threading.enumerate()))

    def test_shutdown_during_upstream_read_is_bounded_and_joins_watchers(self):
        stalled = threading.Event()
        with server(stall=stalled) as (upstream_address, _):
            gateway = self.gateway(LoopbackUpstream(TunnelEndpoint(upstream_address[1]), allow_loopback_io=True))
            with ThreadPoolExecutor(max_workers=1) as pool:
                with gateway.serve() as address:
                    token = intent(address, body(True))
                    pending = pool.submit(request, address, "POST", "/v1/chat/completions", body(True), token=token)
                    self.assertTrue(stalled.wait(1)); start = time.monotonic()
                self.assertLess(time.monotonic() - start, .5)
                try: pending.result(1)
                except (http.client.RemoteDisconnected, ConnectionResetError): pass
        self.assertEqual(gateway.controller.http_release_count, 1)
        self.assertFalse(any(thread.name == "mock-private-client-watch" for thread in threading.enumerate()))

    def test_json_write_rechecks_deadline_after_fence_and_tracks_commit(self):
        class Socket:
            def __init__(self): self.sent = bytearray()
            def setblocking(self, value): pass
            def send(self, value):
                size = min(len(value), 8)
                self.sent.extend(value[:size]); return size
        gateway = self.gateway(); sock = Socket(); committed = [False]
        def late(): threading.Event().wait(.03)
        with patch("qmc_runpod.private_gateway.select.select", return_value=([], [sock], [])):
            with self.assertRaises(GatewayError):
                gateway._json_write(sock, 200, {}, deadline=time.monotonic() + .01, committed=committed, fence=late)
            self.assertEqual(sock.sent, b""); self.assertFalse(committed[0])
            calls = [0]
            def stop_after_first():
                calls[0] += 1
                if calls[0] > 1: raise GatewayError(503, "backend_not_ready")
            with self.assertRaises(GatewayError):
                gateway._json_write(sock, 200, {}, committed=committed, fence=stop_after_first)
        self.assertTrue(committed[0]); self.assertEqual(bytes(sock.sent), b"HTTP/1.1")

    def test_json_response_fence_rejects_stale_session_or_elapsed_idle(self):
        gateway = self.gateway()
        with self.assertRaises(GatewayError):
            gateway._json_response_fence(time.monotonic() + 1, ("other", "foreign-pod"))
        expected = gateway.binding._binding
        gateway.controller.clock.set(110)
        with self.assertRaises(GatewayError):
            gateway._json_response_fence(time.monotonic() + 1, expected)

    def test_stale_stop_after_rebind_cannot_drain_successor(self):
        gateway = self.gateway(); entered = threading.Event(); go = threading.Event()
        original = gateway._stop
        def delayed(body):
            entered.set(); self.assertTrue(go.wait(1)); return original(body)
        with gateway.serve() as address, patch.object(gateway, "_stop", side_effect=delayed), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(request, address, "POST", "/_control/stop", {"session_id": "s"}, key=CONTROL_FIXTURE)
            self.assertTrue(entered.wait(1))
            export_pending(gateway.controller, gateway.controller.clock, idle=10)
            approval = exact_approval(gateway.controller)
            gateway.controller.submit_termination_approval(approval)
            self.assertEqual(gateway.controller.confirm_termination(session_id="s", approval_id=approval.approval_id), "absent")
            ready(gateway.controller, "replacement-session"); gateway.binding.bind_ready_session("replacement-session")
            token = intent(address, body(), "new-chat")
            go.set(); self.assertEqual(pending.result(1)[0], 409)
            self.assertEqual(gateway.controller.status()["phase"], "ready")
            self.assertEqual(request(address, "POST", "/v1/chat/completions", body(), rid="new-chat", token=token)[0], 200)

    def test_validated_stop_and_exact_action_are_atomic_against_replacement(self):
        gateway = self.gateway(); entered = threading.Event(); go = threading.Event(); replacing = threading.Event(); replaced = threading.Event()
        original = gateway.controller.abort; arguments = []
        def delayed(*, session_id):
            arguments.append(session_id); entered.set(); self.assertTrue(go.wait(1))
            return original(session_id=session_id)
        def replace_session():
            replacing.set()
            self.assertEqual(gateway.controller.run_one_step(), "exporting")
            self.assertEqual(gateway.controller.run_one_step(), "approval_pending")
            approval = exact_approval(gateway.controller)
            gateway.controller.submit_termination_approval(approval)
            self.assertEqual(gateway.controller.confirm_termination(session_id="s", approval_id=approval.approval_id), "absent")
            ready(gateway.controller, "replacement-session"); gateway.binding.bind_ready_session("replacement-session")
            replaced.set()
        with gateway.serve() as address, patch.object(gateway.binding, "abort", side_effect=AssertionError("unscoped abort forbidden")), patch.object(gateway.controller, "abort", side_effect=delayed), ThreadPoolExecutor(max_workers=2) as pool:
            stop = pool.submit(request, address, "POST", "/_control/stop", {"session_id": "s"}, key=CONTROL_FIXTURE)
            self.assertTrue(entered.wait(1))
            replacement = pool.submit(replace_session); self.assertTrue(replacing.wait(1))
            self.assertFalse(replaced.wait(.03)); go.set()
            self.assertEqual(stop.result(1)[0], 200); replacement.result(1)
        self.assertEqual(arguments, ["s"])
        self.assertEqual(gateway.controller.status()["session_id"], "replacement-session")
        self.assertEqual(gateway.controller.status()["phase"], "ready")

    def test_content_cap_enforces_utf8_bytes_and_cumulative_events(self):
        for limit, upstream in ((1, None), (5, "fixture")):
            limits = StreamLimits(content_bytes=limit, output_bytes=65536, events=1024, frame_bytes=2048,
                                  piece_chars=64, total_seconds=1, write_seconds=.1)
            if upstream is None:
                gateway = self.gateway(stream_limits=limits)
                with gateway.serve() as address:
                    token = intent(address, body(True))
                    status, _, wire = request(address, "POST", "/v1/chat/completions", body(True), token=token)
            else:
                upstream_wire = chunk({"role": "assistant"}) + chunk({"content": "\u00e9"}) + chunk({"content": "\U0001f642"}) + chunk({}, "stop") + b"data: [DONE]\n\n"
                with server(framing="length", wire=upstream_wire) as (upstream_address, _):
                    gateway = self.gateway(LoopbackUpstream(TunnelEndpoint(upstream_address[1]), allow_loopback_io=True), stream_limits=limits)
                    with gateway.serve() as address:
                        token = intent(address, body(True))
                        status, _, wire = request(address, "POST", "/v1/chat/completions", body(True), token=token)
            self.assertEqual(status, 200)
            values = [json.loads(line[6:]) for line in wire.split(b"\n") if line.startswith(b"data: {")]
            size = sum(len(v.get("choices", [{}])[0].get("delta", {}).get("content", "").encode()) for v in values)
            self.assertLessEqual(size, limit); self.assertNotIn(b"data: [DONE]", wire)
            self.assertFalse(gateway.stream_metrics()[0]["complete"])
            self.assertEqual(gateway.controller.http_release_count, 1)

    def test_overall_stream_deadline_cancels_upstream_wait_after_role(self):
        first = chunk({"role": "assistant"}); remaining = chunk({"content": "late"}) + chunk({}, "stop") + b"data: [DONE]\n\n"
        first_sent = threading.Event()
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                try:
                    self.send_response(200); self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(first + remaining))); self.end_headers()
                    self.wfile.write(first); self.wfile.flush(); first_sent.set()
                    threading.Event().wait(.3)
                    self.wfile.write(remaining); self.wfile.flush()
                except OSError: pass
        service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=lambda: service.serve_forever(poll_interval=.01)); worker.start()
        try:
            limits = StreamLimits(content_bytes=8192, output_bytes=65536, events=1024, frame_bytes=2048,
                                  piece_chars=64, total_seconds=.04, write_seconds=.02)
            gateway = self.gateway(LoopbackUpstream(TunnelEndpoint(service.server_address[1]), allow_loopback_io=True),
                                   processing_timeout=.8, stream_limits=limits)
            with gateway.serve() as address:
                token = intent(address, body(True)); started = time.monotonic()
                status, _, wire = request(address, "POST", "/v1/chat/completions", body(True), token=token)
                self.assertLess(time.monotonic() - started, .18)
                self.assertEqual(gateway.controller.http_release_count, 1)
                self.assertNotIn(b"data: [DONE]", wire); self.assertNotIn(b"late", wire)
                self.assertEqual(status, 200); self.assertTrue(first_sent.is_set())
        finally:
            service.shutdown(); service.server_close(); worker.join(1)


if __name__ == "__main__": unittest.main()
