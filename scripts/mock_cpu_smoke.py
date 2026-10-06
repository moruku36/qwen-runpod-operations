"""One isolated synthetic smoke, explicit ephemeral loopback, then shutdown."""
import http.client
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qmc_runpod.mock_gateway import APPROVAL_FIXTURE, MockGateway, SYNTHETIC_BEARER


def run():
    gateway, checks = MockGateway(), []
    with gateway.serve(host="127.0.0.1", port=0) as address:
        def call(name, method, route, payload=None, expected=200, extra=None):
            connection = http.client.HTTPConnection(*address, timeout=2)
            headers = {"Authorization": "Bearer " + SYNTHETIC_BEARER}
            if extra: headers.update(extra)
            data = None if payload is None else json.dumps(payload).encode()
            if data is not None: headers["Content-Type"] = "application/json"
            try:
                connection.request(method, route, data, headers)
                response = connection.getresponse(); raw = response.read()
                assert response.status == expected, name
                checks.append({"check": name, "status": response.status})
                return raw
            finally: connection.close()

        chat = {"session_id": "smoke", "model": "qwen-27b", "messages": [{"role": "user", "content": "synthetic smoke"}]}
        call("liveness", "GET", "/healthz")
        call("fixed catalog", "GET", "/v1/models")
        call("nonREADY does not allocate", "POST", "/v1/chat/completions", chat, 503)
        call("unauthorized", "GET", "/v1/models", expected=401, extra={"Authorization": "Bearer public-invalid-fixture"})
        call("unknown route", "GET", "/unknown", expected=404)
        call("explicit fixture start", "POST", "/_mock/sessions", {"session_id": "smoke", "approval_fixture": APPROVAL_FIXTURE}, 202, {"Idempotency-Key": "smoke-start"})
        deadline = time.monotonic() + 1
        while gateway.controller.status()["phase"] != "ready":
            assert time.monotonic() < deadline, "bounded synthetic startup"
            time.sleep(0.005)
        call("JSON completion", "POST", "/v1/chat/completions", chat)
        stream = call("SSE completion", "POST", "/v1/chat/completions", dict(chat, stream=True))
        assert stream.endswith(b"data: [DONE]\n\n") and stream.count(b"data: [DONE]") == 1
        assert stream.count(b'"finish_reason":"stop"') == 1
        call("unsupported media", "POST", "/v1/chat/completions", dict(chat, images=[]), 400)
        call("mock drain only", "POST", "/_mock/sessions/smoke/stop", {}, 202)
        call("drained rejects stream", "POST", "/v1/chat/completions", dict(chat, stream=True), 503)
    assert not gateway._active_sockets and all(not worker.is_alive() for worker in gateway._workers)
    assert sum(kind == "create" for kind, _ in gateway.controller.provider.calls) == 1
    assert not any(kind == "terminate" for kind, _ in gateway.controller.provider.calls)
    return {"simulated": True, "checks": checks, "logs": gateway.logs(), "streams": gateway.stream_metrics()}


if __name__ == "__main__": print(json.dumps(run(), ensure_ascii=True, indent=2))
