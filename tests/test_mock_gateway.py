"""Isolated ephemeral IPv4 loopback tests. No live provider or external socket."""
from concurrent.futures import ThreadPoolExecutor
import http.client
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
from qmc_runpod.ondemand import ChatAdmissionError, LifecycleController, Phase
from qmc_runpod.mock_gateway import APPROVAL_FIXTURE, BODY_LIMIT, MockGateway, SYNTHETIC_BEARER
from test_ondemand_controller import FakeClock, exact_approval, export_pending, make_controller, make_limits, ready


def chat_body(session="s"):
    return {"session_id": session, "model": "qwen-27b", "messages": [{"role": "user", "content": "synthetic prompt"}]}


def request(address, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection(*address, timeout=2)
    supplied = {"Authorization": "Bearer " + SYNTHETIC_BEARER}
    if headers: supplied.update(headers)
    if body is not None:
        supplied["Content-Type"] = "application/json"
        body = json.dumps(body).encode()
    try:
        connection.request(method, path, body=body, headers=supplied)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def raw_request(address, data, *, eof=False):
    with socket.create_connection(address, timeout=2) as sock:
        sock.sendall(data)
        if eof: sock.shutdown(socket.SHUT_WR)
        response = bytearray()
        while True:
            part = sock.recv(8192)
            if not part: break
            response.extend(part)
    header, payload = bytes(response).split(b"\r\n\r\n", 1)
    return int(header.split(b" ")[1]), json.loads(payload), header


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= deadline: raise AssertionError("synthetic wait deadline")
        threading.Event().wait(0.005)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        original_connect, original_resolve, original_bind = socket.socket.connect, socket.getaddrinfo, socket.socket.bind

        def loopback_connect(sock, address):
            if address[0] != "127.0.0.1": raise AssertionError("outbound socket denied")
            return original_connect(sock, address)

        def loopback_resolve(host, *args, **kwargs):
            if host != "127.0.0.1": raise AssertionError("outbound DNS denied")
            return original_resolve(host, *args, **kwargs)

        def loopback_bind(sock, address):
            if address[0] != "127.0.0.1": raise AssertionError("nonloopback bind denied")
            return original_bind(sock, address)

        for target, attribute, replacement in ((socket.socket, "connect", loopback_connect),
                                               (socket, "getaddrinfo", loopback_resolve),
                                               (socket.socket, "bind", loopback_bind)):
            guard = patch.object(target, attribute, replacement)
            guard.start(); self.addCleanup(guard.stop)

    def gateway_ready(self, **kwargs):
        controller = make_controller(clock=FakeClock(), idle=10)
        ready(controller, "s")
        return MockGateway(controller, **kwargs)

    def test_default_construction_is_inert_and_live_or_nonloopback_is_rejected(self):
        with patch.object(socket, "socket", side_effect=AssertionError("unexpected listen")):
            gateway = MockGateway()
            self.assertEqual(gateway.controller.provider.calls, [])
            with self.assertRaises(ValueError): MockGateway(backend="live")
            for host in ("0.0.0.0", "localhost", "::1", "example.com"):
                with self.assertRaises(ValueError):
                    with gateway.serve(host=host): pass
        with self.assertRaises(ValueError): MockGateway(controller=object())

    def test_outbound_socket_and_dns_tripwire_allows_only_ephemeral_loopback_tests(self):
        original_connect, original_resolve = socket.socket.connect, socket.getaddrinfo
        observed = []
        def connect(sock, address):
            self.assertEqual(address[0], "127.0.0.1"); observed.append(address)
            return original_connect(sock, address)
        def resolve(host, *args, **kwargs):
            self.assertEqual(host, "127.0.0.1")
            return original_resolve(host, *args, **kwargs)
        with patch.object(socket.socket, "connect", connect), patch.object(socket, "getaddrinfo", resolve):
            gateway = MockGateway()
            with gateway.serve() as address:
                self.assertEqual(address[0], "127.0.0.1")
                self.assertEqual(request(address, "GET", "/healthz", headers={"Authorization": ""})[0], 200)
                self.assertEqual(request(address, "GET", "/v1/models")[0], 200)
        self.assertEqual(len(observed), 2)

    def test_models_status_and_health_never_change_grant_idle_or_effects(self):
        gateway = self.gateway_ready()
        before = gateway.controller.store.snapshot()
        calls = list(gateway.controller.provider.calls)
        with gateway.serve() as address:
            for _ in range(8):
                for path in ("/healthz", "/v1/models", "/_mock/sessions/s"):
                    self.assertEqual(request(address, "GET", path)[0], 200)
        self.assertEqual(gateway.controller.store.snapshot(), before)
        self.assertEqual(gateway.controller.provider.calls, calls)

    def test_start_requires_fixture_and_idempotency_and_holds_no_chat_until_ready(self):
        gateway = MockGateway(startup_delay=0.1)
        body = {"session_id": "s", "approval_fixture": APPROVAL_FIXTURE}
        with gateway.serve() as address:
            self.assertEqual(request(address, "POST", "/_mock/sessions", body)[0], 400)
            first = request(address, "POST", "/_mock/sessions", body, {"Idempotency-Key": "intent-one"})
            self.assertEqual(first[0], 202)
            self.assertIn("request_id", first[1])
            self.assertEqual(request(address, "POST", "/v1/chat/completions", chat_body())[0], 503)
            self.assertEqual(gateway.controller.store.snapshot()["session"]["queue"], [])
            repeated = request(address, "POST", "/_mock/sessions", body, {"Idempotency-Key": "intent-one"})
            self.assertEqual(first, repeated)
            wait_for(lambda: gateway.controller.status()["phase"] == "ready")
            changed = dict(body, session_id="other")
            self.assertEqual(request(address, "POST", "/_mock/sessions", changed, {"Idempotency-Key": "intent-one"})[0], 409)
        self.assertEqual(sum(k == "create" for k, _ in gateway.controller.provider.calls), 1)
        self.assertFalse(any(k == "chat" for k, _ in gateway.controller.transport.calls))

    def test_concurrent_start_has_one_intent_and_control_competition_is_409(self):
        gateway = MockGateway(startup_delay=0.05)
        body = {"session_id": "s", "approval_fixture": APPROVAL_FIXTURE}
        with gateway.serve() as address:
            barrier = threading.Barrier(2)
            def start(_):
                barrier.wait()
                return request(address, "POST", "/_mock/sessions", body, {"Idempotency-Key": "same"})[0]
            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(start, range(2)))
            self.assertIn(202, statuses)
            self.assertTrue(set(statuses) <= {202, 409})
            wait_for(lambda: gateway.controller.status()["phase"] == "ready")
            with gateway._control:
                self.assertEqual(request(address, "POST", "/_mock/sessions/s/stop", {})[0], 409)
        self.assertEqual(sum(k == "create" for k, _ in gateway.controller.provider.calls), 1)

    def test_every_nonready_phase_returns_503_without_create_or_queue(self):
        for phase in Phase:
            if phase == Phase.READY: continue
            with self.subTest(phase=phase):
                controller = make_controller(clock=FakeClock(), idle=10)
                if phase != Phase.ABSENT:
                    ready(controller, "s"); export_pending(controller, controller.clock, idle=10)
                    controller.submit_termination_approval(exact_approval(controller))
                    with controller.store.locked() as lock:
                        controller._phase(lock.load(), lock.load()["session"], phase)
                        controller._save(lock)
                gateway = MockGateway(controller)
                before = list(controller.provider.calls)
                with gateway.serve() as address:
                    status, body = request(address, "POST", "/v1/chat/completions", chat_body())
                self.assertEqual((status, body["error"]["code"]), (503, "backend_not_ready"))
                self.assertEqual(controller.provider.calls, before)
                self.assertFalse(any(k == "chat" for k, _ in controller.transport.calls))
                session = controller.store.snapshot()["session"]
                if session: self.assertEqual(session["queue"], [])

    def test_auth_route_method_id_errors_have_fixed_messages_and_secret_free_logs(self):
        gateway = self.gateway_ready()
        secret = "private-body-auth-output-marker"
        with gateway.serve() as address:
            cases = [("GET", "/v1/models", None, {"Authorization": "Bearer " + secret}, 401),
                     ("GET", "/private/" + secret, None, None, 404),
                     ("DELETE", "/v1/models", None, None, 405),
                     ("GET", "/v1/models", None, {"X-Request-ID": "bad/id"}, 400),
                     ("POST", "/v1/chat/completions", dict(chat_body(), tools=[secret]), None, 400),
                     ("GET", "/_mock/sessions/wrong", None, None, 404)]
            for method, path, body, headers, status in cases:
                result = request(address, method, path, body, headers)
                self.assertEqual(result[0], status)
                self.assertEqual(result[1]["error"]["message"], result[1]["error"]["code"])
            body = chat_body(); body["messages"][0]["content"] = secret
            self.assertEqual(request(address, "POST", "/v1/chat/completions", body, {"X-Request-ID": "req-safe"})[0], 200)
        logs = gateway.logs()
        self.assertNotIn(secret, json.dumps(logs))
        self.assertNotIn(SYNTHETIC_BEARER, json.dumps(logs))
        self.assertNotIn("simulated response", json.dumps(logs))
        self.assertTrue(all(set(item) == {"id", "route", "status", "duration", "outcome"} for item in logs))

    def test_unsupported_stream_models_tools_media_urls_and_bad_types_never_infer(self):
        gateway = self.gateway_ready()
        cases = [dict(chat_body(), stream="true"),
                 dict(chat_body(), model="https://remote.example/model"), dict(chat_body(), tools=[]),
                 dict(chat_body(), audio={}), dict(chat_body(), images=[]), dict(chat_body(), remote_provider="x"),
                 dict(chat_body(), max_tokens=True), dict(chat_body(), messages=[{"role": [], "content": "x"}]),
                 dict(chat_body(), messages=[{"role": "user", "content": [{"image_url": "x"}]}]),
                 dict(chat_body(), messages=[{"role": "user", "content": "https://remote.example"}]),
                 dict(chat_body(), messages=[{"role": "user", "content": "ftp://remote.example"}])]
        with gateway.serve() as address:
            for body in cases: self.assertEqual(request(address, "POST", "/v1/chat/completions", body)[0], 400)
        self.assertFalse(any(k == "chat" for k, _ in gateway.controller.transport.calls))

    def test_body_size_exact_boundary_and_oversize(self):
        gateway = self.gateway_ready()
        content = json.dumps(chat_body()).encode()
        prefix = b"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer " + SYNTHETIC_BEARER.encode() + b"\r\nContent-Type: application/json\r\n"
        with gateway.serve() as address:
            valid = content + b" " * (BODY_LIMIT - len(content))
            self.assertEqual(raw_request(address, prefix + f"Content-Length: {len(valid)}\r\n\r\n".encode() + valid)[0], 200)
            # Oversize is rejected from its header before reading an oversized body.
            self.assertEqual(raw_request(address, prefix + f"Content-Length: {BODY_LIMIT + 1}\r\n\r\n".encode())[0], 413)

    def test_malformed_duplicate_negative_missing_chunked_and_truncated_framing_close(self):
        gateway = self.gateway_ready(body_timeout=0.05)
        prefix = b"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        with gateway.serve() as address:
            for framing in (b"", b"Content-Length: -1\r\n", b"Content-Length: +2\r\n",
                            b"Content-Length: 2\r\nContent-Length: 2\r\n", b"Transfer-Encoding: chunked\r\n",
                            b"Content-Length: 2\r\nTransfer-Encoding: chunked\r\n", b"X-Bad : x\r\n"):
                status, _, headers = raw_request(address, prefix + framing + b"\r\n")
                self.assertEqual(status, 400); self.assertIn(b"Connection: close", headers)
            self.assertEqual(raw_request(address, prefix + b"Content-Length: 10\r\n\r\n{}", eof=True)[0], 400)
            self.assertEqual(raw_request(address, prefix + b"Content-Length: 10\r\n\r\n{}")[0], 408)
        self.assertEqual(gateway.controller.http_release_count, 0)

    def test_malformed_json_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        gateway = self.gateway_ready()
        prefix = b"POST /v1/chat/completions HTTP/1.1\r\nAuthorization: Bearer " + SYNTHETIC_BEARER.encode() + b"\r\nContent-Type: application/json\r\n"
        with gateway.serve() as address:
            for data in (b"{", b'{"model":"qwen-27b","model":"other"}', b'{"max_tokens":NaN}', b"[]", b"\xff"):
                self.assertEqual(raw_request(address, prefix + f"Content-Length: {len(data)}\r\n\r\n".encode() + data)[0], 400)

    def test_slow_header_and_body_have_distinct_finite_timeouts(self):
        gateway = self.gateway_ready(header_timeout=0.04, body_timeout=0.04)
        with gateway.serve() as address:
            self.assertEqual(raw_request(address, b"GET /healthz HTTP/1.1\r\nX-Slow: ")[0], 408)
            self.assertEqual(raw_request(address, b"POST /_mock/sessions HTTP/1.1\r\nContent-Length: 10\r\n\r\n")[0], 408)
        self.assertEqual(gateway.controller.http_release_count, 0)

    def test_processing_timeout_cancels_and_releases_inflight_once(self):
        gateway = self.gateway_ready(processing_delay=0.2, processing_timeout=0.04)
        with gateway.serve() as address:
            status, body = request(address, "POST", "/v1/chat/completions", chat_body())
            self.assertEqual((status, body["error"]["code"]), (504, "processing_timeout"))
            wait_for(lambda: gateway.controller.http_release_count == 1)
        self.assertEqual(gateway.controller.http_release_count, 1)
        self.assertIsNone(gateway.controller.store.snapshot()["session"]["active"])
        self.assertFalse(any(k == "chat" for k, _ in gateway.controller.transport.calls))

    def test_processing_timeout_while_waiting_for_store_lock_never_dispatches(self):
        gateway = self.gateway_ready(processing_timeout=0.04)
        controller = gateway.controller
        entered, proceed = threading.Event(), threading.Event()
        original = controller.execute_http_chat

        def wait_for_lock(ticket, **kwargs):
            entered.set()
            self.assertTrue(proceed.wait(1))
            return original(ticket, **kwargs)

        before_activity = controller.store.snapshot()["session"]["last_activity"]
        with patch.object(controller, "execute_http_chat", side_effect=wait_for_lock), \
             gateway.serve() as address, ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(request, address, "POST", "/v1/chat/completions", chat_body())
            self.assertTrue(entered.wait(1))
            with controller.store.locked():
                proceed.set()
                threading.Event().wait(0.1)  # Finite store-lock contention exceeds processing scope.
            status, body = pending.result()
            self.assertEqual((status, body["error"]["code"]), (504, "processing_timeout"))
            wait_for(lambda: controller.http_release_count == 1)
        self.assertEqual(controller.http_release_count, 1)
        session = controller.store.snapshot()["session"]
        self.assertIsNone(session["active"])
        self.assertEqual(session["last_activity"], before_activity)
        self.assertFalse(any(k == "chat" for k, _ in controller.transport.calls))

    def test_processing_cancel_or_checkpoint_delay_is_rechecked_inside_dispatch_lock(self):
        for boundary in ("cancel", "checkpoint"):
            with self.subTest(boundary=boundary):
                gateway = self.gateway_ready()
                controller = gateway.controller
                ticket = controller.admit_http_chat("s", "guarded", "synthetic")
                cancel = threading.Event()
                original_save, original_check = controller._save, controller.authority.check_effect

                def delayed_save(lock):
                    original_save(lock)
                    if boundary == "checkpoint": threading.Event().wait(0.05)

                def cancel_after_validation(*args):
                    result = original_check(*args)
                    if boundary == "cancel": cancel.set()
                    return result

                duration = 1 if boundary == "cancel" else 0.03
                with patch.object(controller, "_save", side_effect=delayed_save), \
                     patch.object(controller.authority, "check_effect", side_effect=cancel_after_validation):
                    with self.assertRaises(ChatAdmissionError) as caught:
                        controller.execute_http_chat(ticket, processing_deadline=time.monotonic() + duration,
                                                     cancel_event=cancel)
                self.assertEqual(caught.exception.code, "processing_timeout")
                self.assertEqual(controller.http_release_count, 1)
                self.assertIsNone(controller.store.snapshot()["session"]["active"])
                self.assertFalse(any(k == "chat" for k, _ in controller.transport.calls))

    def test_disconnect_while_waiting_for_store_lock_never_dispatches(self):
        gateway = self.gateway_ready()
        controller = gateway.controller
        entered, proceed = threading.Event(), threading.Event()
        cancel_context = []
        original = controller.execute_http_chat

        def wait_for_lock(ticket, **kwargs):
            cancel_context.append(kwargs["cancel_event"])
            entered.set()
            self.assertTrue(proceed.wait(1))
            return original(ticket, **kwargs)

        content = json.dumps(chat_body()).encode()
        with patch.object(controller, "execute_http_chat", side_effect=wait_for_lock), gateway.serve() as address:
            sock = socket.create_connection(address, timeout=2)
            try:
                sock.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nAuthorization: Bearer " + SYNTHETIC_BEARER.encode() +
                             b"\r\nContent-Type: application/json\r\n" + f"Content-Length: {len(content)}\r\n\r\n".encode() + content)
                self.assertTrue(entered.wait(1))
                with controller.store.locked():
                    proceed.set()
                    sock.close()
                    self.assertTrue(cancel_context[0].wait(1))  # Visible before acquiring release lock.
                wait_for(lambda: controller.http_release_count == 1)
                wait_for(lambda: bool(gateway.logs()))
            finally:
                sock.close()
        self.assertEqual(controller.http_release_count, 1)
        self.assertIsNone(controller.store.snapshot()["session"]["active"])
        self.assertFalse(any(k == "chat" for k, _ in controller.transport.calls))
        self.assertTrue(any(item["status"] == 499 and item["outcome"] == "client_disconnected"
                            for item in gateway.logs()))

    def test_cancel_or_timeout_after_effect_withholds_success_and_idle_refresh(self):
        for boundary in ("cancel", "timeout"):
            with self.subTest(boundary=boundary):
                controller = self.gateway_ready().controller
                ticket = controller.admit_http_chat("s", "complete-guard", "synthetic")
                before = controller.store.snapshot()["session"]
                original, cancel = controller.transport.chat, threading.Event()

                def changed_completion(*args):
                    response = original(*args)
                    controller.clock.set(101)
                    if boundary == "cancel": cancel.set()
                    else: threading.Event().wait(0.04)
                    return response

                duration = 1 if boundary == "cancel" else 0.02
                with patch.object(controller.transport, "chat", side_effect=changed_completion):
                    with self.assertRaises(ChatAdmissionError) as caught:
                        controller.execute_http_chat(ticket, processing_deadline=time.monotonic() + duration,
                                                     cancel_event=cancel)
                self.assertEqual(caught.exception.code, "processing_timeout")
                self.assertEqual(controller.http_release_count, 1)
                after = controller.store.snapshot()["session"]
                self.assertEqual((after["idle_deadline"], after["last_activity"]),
                                 (before["idle_deadline"], before["last_activity"]))
                self.assertEqual(sum(k == "chat" for k, _ in controller.transport.calls), 1)

    def test_second_chat_at_idle_or_work_expiry_with_inflight_is_503_not_500(self):
        for boundary in ("idle_deadline", "deadline"):
            with self.subTest(boundary=boundary):
                gateway = self.gateway_ready(processing_delay=0.15)
                controller = gateway.controller
                with gateway.serve() as address, ThreadPoolExecutor(max_workers=1) as pool:
                    first = pool.submit(request, address, "POST", "/v1/chat/completions", chat_body())
                    wait_for(lambda: controller.store.snapshot()["session"]["active"] is not None)
                    controller.clock.set(controller.store.snapshot()["session"][boundary])
                    second = request(address, "POST", "/v1/chat/completions", chat_body(), {"X-Request-ID": "expired-second"})
                    self.assertEqual((second[0], second[1]["error"]["code"]), (503, "backend_not_ready"))
                    self.assertEqual(controller.status()["phase"], "draining")
                    self.assertIsNone(controller.store.snapshot()["session"]["active"])
                    self.assertEqual(first.result()[0], 503)
                self.assertEqual(controller.http_release_count, 1)
                self.assertEqual(controller.store.snapshot()["session"]["queue"], [])
                self.assertFalse(any(k == "chat" for k, _ in controller.transport.calls))

    def test_exact_64_header_fields_allowed_and_65_or_66_rejected(self):
        gateway = MockGateway()
        with gateway.serve() as address:
            for fields, expected in ((64, 200), (65, 431), (66, 431)):
                with self.subTest(fields=fields):
                    raw = (b"GET /healthz HTTP/1.1\r\n" +
                           b"".join(f"X-{i}: x\r\n".encode() for i in range(fields)) + b"\r\n")
                    status, _, headers = raw_request(address, raw)
                    self.assertEqual(status, expected)
                    self.assertIn(b"Connection: close", headers)

    def test_disconnect_cancels_before_effect_and_releases_once(self):
        gateway = self.gateway_ready(processing_delay=0.2)
        content = json.dumps(chat_body()).encode()
        with gateway.serve() as address:
            sock = socket.create_connection(address, timeout=2)
            sock.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nAuthorization: Bearer " + SYNTHETIC_BEARER.encode() +
                         b"\r\nContent-Type: application/json\r\n" + f"Content-Length: {len(content)}\r\n\r\n".encode() + content)
            wait_for(lambda: gateway.controller.store.snapshot()["session"]["active"] is not None)
            sock.close()
            wait_for(lambda: gateway.controller.http_release_count == 1)
        self.assertEqual(gateway.controller.http_release_count, 1)
        self.assertFalse(any(k == "chat" for k, _ in gateway.controller.transport.calls))
        self.assertTrue(any(item["outcome"] == "client_disconnected" for item in gateway.logs()))

    def test_busy_is_429_and_stop_during_admission_does_not_revive_session(self):
        gateway = self.gateway_ready(processing_delay=0.15)
        with gateway.serve() as address:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(request, address, "POST", "/v1/chat/completions", chat_body())
                wait_for(lambda: gateway.controller.store.snapshot()["session"]["active"] is not None)
                self.assertEqual(request(address, "POST", "/v1/chat/completions", chat_body())[0], 429)
                self.assertEqual(request(address, "POST", "/_mock/sessions/s/stop", {})[0], 202)
                self.assertEqual(pending.result()[0], 409)
            self.assertEqual(request(address, "POST", "/v1/chat/completions", chat_body())[0], 503)
        self.assertEqual(gateway.controller.status()["phase"], "draining")
        self.assertEqual(gateway.controller.http_release_count, 1)
        self.assertFalse(any(k == "terminate" for k, _ in gateway.controller.provider.calls))
        self.assertFalse(any(k == "chat" for k, _ in gateway.controller.transport.calls))

    def test_expiry_between_atomic_admission_and_execution_rejects_without_effect(self):
        gateway = self.gateway_ready(processing_delay=0.08)
        with gateway.serve() as address:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(request, address, "POST", "/v1/chat/completions", chat_body())
                wait_for(lambda: gateway.controller.store.snapshot()["session"]["active"] is not None)
                gateway.controller.clock.set(gateway.controller.store.snapshot()["session"]["deadline"])
                self.assertEqual(pending.result()[0], 503)
        self.assertEqual(gateway.controller.status()["phase"], "draining")
        self.assertEqual(gateway.controller.http_release_count, 1)
        self.assertFalse(any(k == "chat" for k, _ in gateway.controller.transport.calls))

    def test_stale_ticket_after_new_session_cannot_restore_or_change_new_session(self):
        controller = make_controller(clock=FakeClock(), idle=10)
        ready(controller, "old")
        ticket = controller.admit_http_chat("old", "req-old", "synthetic")
        controller.abort(session_id="old")
        controller.run_one_step(); controller.run_one_step()
        approval = exact_approval(controller)
        controller.submit_termination_approval(approval)
        controller.confirm_termination(session_id="old", approval_id=approval.approval_id)
        ready(controller, "new")
        before = controller.store.snapshot()
        with self.assertRaises(ChatAdmissionError):
            controller.execute_http_chat(ticket, processing_deadline=time.monotonic() + 1,
                                         cancel_event=threading.Event())
        after = controller.store.snapshot()
        self.assertEqual(after["session"], before["session"])
        self.assertEqual(controller.http_release_count, 1)
        self.assertFalse(any(k == "chat" for k, _ in controller.transport.calls))


if __name__ == "__main__": unittest.main()
