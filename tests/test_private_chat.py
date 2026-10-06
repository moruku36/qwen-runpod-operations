"""Auth-free / public-fixture-only loopback tests, never a real tunnel or Pod."""
from contextlib import contextmanager
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

import test_mock_gateway as shared
from qmc_runpod.mock_gateway import SYNTHETIC_BEARER
from qmc_runpod.private_chat import (LoopbackChatTransport, PrivateChatError,
                                    TunnelEndpoint, build_chat_request)


def payload():
    return {"model": "qwen-27b", "messages": [{"role": "user", "content": "模型🙂"}], "stream": False}


def completion():
    return {"model": "qwen-27b", "choices": [{"index": 0, "message": {"role": "assistant", "content": "回答🙂"}, "finish_reason": "stop"}]}


@contextmanager
def fixture(*, status=200, body=None, extra_headers=(), delay=False, drip=False, after_headers=None):
    seen = []
    encoded = json.dumps(completion() if body is None else body, ensure_ascii=False).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, dict(self.headers), json.loads(data)))
            try:
                if delay:
                    threading.Event().wait(0.15)
                if drip:
                    # Continually arriving header bytes defeat per-recv timeouts.
                    for byte in b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n":
                        self.wfile.write(bytes([byte])); self.wfile.flush()
                        threading.Event().wait(0.003)
                    return
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                for key, value in extra_headers:
                    self.send_header(key, value)
                self.end_headers()
                if after_headers is not None:
                    after_headers.set()
                    threading.Event().wait(0.15)
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    worker.start()
    try:
        yield server.server_address, seen
    finally:
        server.shutdown(); server.server_close(); worker.join(1)
        assert not worker.is_alive()


class PrivateChatTests(unittest.TestCase):
    def setUp(self):
        self.guards = shared.GatewayTests(); self.guards.setUp()
        self.addCleanup(self.guards.doCleanups)

    def call(self, address, *, timeout=1, cancel=None, **kwargs):
        transport = LoopbackChatTransport(TunnelEndpoint(address[1]), allow_loopback_io=True, **kwargs)
        return transport.chat(payload(), "private-one", deadline=time.monotonic() + timeout,
                              cancel=cancel if cancel is not None else threading.Event())

    def test_default_disabled_construction_and_call_do_not_open_socket(self):
        with patch.object(socket, "socket", side_effect=AssertionError("unexpected socket")):
            transport = LoopbackChatTransport(TunnelEndpoint(19181))
            with self.assertRaisesRegex(PrivateChatError, "connection_disabled"):
                transport.chat(payload(), "private-one", deadline=time.monotonic() + 1, cancel=threading.Event())

    def test_isolated_module_import_and_defaults_never_read_env_or_execute(self):
        class NoEnvironment(dict):
            def __getitem__(self, key): raise AssertionError("environment read")
            def get(self, *args): raise AssertionError("environment read")
            def __iter__(self): raise AssertionError("environment enumeration")
        path = Path(__file__).resolve().parents[1] / "qmc_runpod" / "private_chat.py"
        spec = importlib.util.spec_from_file_location("_private_chat_inert_guard", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            with patch.object(socket, "socket", side_effect=AssertionError("socket")), \
                 patch.object(subprocess, "Popen", side_effect=AssertionError("process")), \
                 patch.object(os, "getenv", side_effect=AssertionError("environment read")), \
                 patch.object(os, "environ", NoEnvironment()):
                spec.loader.exec_module(module)
                transport = module.LoopbackChatTransport(module.TunnelEndpoint(19181))
                with self.assertRaisesRegex(module.PrivateChatError, "connection_disabled"):
                    transport.chat(payload(), "private-one", deadline=time.monotonic() + 1, cancel=threading.Event())
        finally:
            del sys.modules[spec.name]

    def test_endpoint_cannot_select_cloud_dns_public_loopback_alias_or_voice_port(self):
        for host in ("api.openai.com", "api.runpod.io", "localhost", "::1", "127.0.0.2", "0.0.0.0", "127.0.0.1@evil"):
            with self.subTest(host=host), self.assertRaises(PrivateChatError):
                TunnelEndpoint(19181, host)
        for port in (True, 0, -1, 65536, "19181", 3001):
            with self.subTest(port=port), self.assertRaises(PrivateChatError):
                TunnelEndpoint(port)

    def test_request_contract_is_pure_utf8_and_repr_hides_body(self):
        request = build_chat_request(payload(), "private-one")
        self.assertEqual(request.path, "/v1/chat/completions")
        self.assertEqual(json.loads(request.body)["messages"][0]["content"], "模型🙂")
        self.assertNotIn("模型", repr(request))

    def test_unsupported_model_stream_tools_media_and_remote_inputs_rejected_before_io(self):
        cases = [dict(payload(), model="gpt-5"), dict(payload(), stream=True), dict(payload(), stream=0),
                 dict(payload(), tools=[]), dict(payload(), max_tokens=True), dict(payload(), max_tokens=257),
                 dict(payload(), messages=[]), dict(payload(), messages=[{"role": "user", "content": []}]),
                 dict(payload(), messages=[{"role": "user", "content": "https://example.com"}]),
                 dict(payload(), messages=[{"role": "user", "content": "data:image/png;xx"}])]
        with patch.object(socket, "socket", side_effect=AssertionError("unexpected socket")):
            for case in cases:
                with self.subTest(case=case), self.assertRaises(PrivateChatError):
                    build_chat_request(case, "private-one")

    def test_body_limit_counts_utf8_bytes(self):
        large = dict(payload(), messages=[{"role": "user", "content": "界" * 8192}] * 3)
        with self.assertRaisesRegex(PrivateChatError, "request_too_large"):
            build_chat_request(large, "private-one")

    def test_header_injection_and_request_id_are_rejected(self):
        for bearer in ("hello\r\nX-Foo: secret", "a b", "", 12):
            with self.assertRaises(PrivateChatError):
                LoopbackChatTransport(TunnelEndpoint(19181), bearer=bearer)
        for identity in ("x\r\nAuthorization: secret", "", "x" * 65, 1):
            with self.assertRaises(PrivateChatError):
                build_chat_request(payload(), identity)

    def test_auth_free_roundtrip_has_no_authorization_or_provider_metadata(self):
        body = dict(completion(), private_metadata="sensitive-provider-metadata")
        with fixture(body=body) as (address, seen):
            result = self.call(address)
        self.assertEqual(result, completion())
        self.assertEqual(seen[0][0], "/v1/chat/completions")
        self.assertNotIn("Authorization", seen[0][1])
        self.assertEqual(seen[0][1]["X-Request-ID"], "private-one")

    def test_accepted_gateway_requires_session_binding_absent_from_provider_wire(self):
        gateway = self.guards.gateway_ready()
        before = list(gateway.controller.provider.calls)
        with gateway.serve() as address:
            with self.assertRaisesRegex(PrivateChatError, "upstream_http_error"):
                self.call(address, bearer=SYNTHETIC_BEARER)
        self.assertEqual(gateway.controller.provider.calls, before)
        self.assertEqual(gateway.controller.http_release_count, 0)

    def test_redirect_error_has_no_retry_or_fallback_or_error_text(self):
        for status in (301, 401, 429, 500, 503):
            with self.subTest(status=status), fixture(status=status, body={"error": "SECRET"},
                    extra_headers=(("Location", "https://api.openai.com/v1"),)) as (address, seen):
                with self.assertRaisesRegex(PrivateChatError, "^upstream_http_error$"):
                    self.call(address)
                self.assertEqual(len(seen), 1)

    def test_invalid_framing_and_type_are_rejected(self):
        for headers in ((("Content-Length", "2"),), (("Transfer-Encoding", "chunked"),),
                        (("Content-Type", "text/html"),)):
            with self.subTest(headers=headers), fixture(extra_headers=headers) as (address, seen):
                with self.assertRaises(PrivateChatError):
                    self.call(address)
                self.assertEqual(len(seen), 1)

    def test_oversized_response_is_rejected_before_reading_body(self):
        with fixture(body={"content": "x" * 65536}) as (address, _):
            with self.assertRaisesRegex(PrivateChatError, "invalid_upstream_framing"):
                self.call(address)

    def test_invalid_completion_role_model_finish_tool_and_metadata_shapes(self):
        cases = [dict(completion(), model="other"), dict(completion(), choices=[])]
        for field, value in (("finish_reason", "length"), ("index", 1), ("index", True), ("message", {"role": "tool", "content": "SECRET"}),
                             ("message", {"role": "assistant", "content": "hello", "tool_calls": []})):
            item = completion(); item["choices"][0][field] = value; cases.append(item)
        for body in cases:
            with self.subTest(body=body), fixture(body=body) as (address, _):
                with self.assertRaisesRegex(PrivateChatError, "invalid_upstream_completion"):
                    self.call(address)

    def test_duplicate_json_nonfinite_and_truncated_shapes_rejected(self):
        for body in (b'{"model":"qwen-27b","model":"other"}', b'{"x":NaN}', b'\xff', b'{'):
            with self.subTest(body=body), self.assertRaises(PrivateChatError):
                LoopbackChatTransport._completion(body)

    def test_expired_deadline_or_cancel_before_connection_dispatches_nothing(self):
        cancel = threading.Event(); cancel.set()
        transport = LoopbackChatTransport(TunnelEndpoint(19181), allow_loopback_io=True)
        with patch.object(socket, "socket", side_effect=AssertionError("unexpected socket")):
            with self.assertRaisesRegex(PrivateChatError, "request_cancelled"):
                transport.chat(payload(), "private-one", deadline=time.monotonic() + 1, cancel=cancel)
            with self.assertRaisesRegex(PrivateChatError, "request_deadline"):
                transport.chat(payload(), "private-one", deadline=time.monotonic() - 1, cancel=threading.Event())

    def test_invalid_deadline_is_not_a_long_or_unbounded_connection(self):
        transport = LoopbackChatTransport(TunnelEndpoint(19181), allow_loopback_io=True)
        for deadline in (True, float("nan"), float("inf"), time.monotonic() + 60):
            with self.subTest(deadline=deadline), self.assertRaisesRegex(PrivateChatError, "invalid_deadline"):
                transport.chat(payload(), "private-one", deadline=deadline, cancel=threading.Event())

    def test_cancel_after_connect_is_rechecked_before_dispatch(self):
        cancel = threading.Event()
        original = shared.http.client.HTTPConnection.connect
        def connect(connection):
            original(connection)
            cancel.set()
        with fixture() as (address, seen), patch.object(shared.http.client.HTTPConnection, "connect", connect):
            with self.assertRaisesRegex(PrivateChatError, "request_cancelled"):
                self.call(address, cancel=cancel)
            self.assertEqual(seen, [])

    def test_absolute_header_deadline_closes_dripping_response(self):
        with fixture(drip=True) as (address, seen):
            started = time.monotonic()
            with self.assertRaisesRegex(PrivateChatError, "request_deadline"):
                self.call(address, timeout=0.04)
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertEqual(len(seen), 1)

    def test_cancel_during_body_wait_closes_socket_and_watchdog(self):
        cancel, headers = threading.Event(), threading.Event()
        with fixture(after_headers=headers) as (address, _):
            worker = threading.Thread(target=lambda: (headers.wait(1), cancel.set()))
            worker.start()
            try:
                with self.assertRaisesRegex(PrivateChatError, "request_cancelled"):
                    self.call(address, cancel=cancel)
            finally:
                worker.join(1)
        self.assertFalse(any(t.name == "private-chat-fixture-watchdog" for t in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()
