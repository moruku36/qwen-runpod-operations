import ast
import json
import re
import socket
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from conftest import ROOT, UPSTREAM, needs_upstream

from qmc_runpod import launch, layout, models, pins, podapi, report

PKG = ROOT / "qmc_runpod"


# ---------------------------------------------------------------- pins / no Colab dependency
def test_pins_match_baseline_json():
    b = json.loads((ROOT / "baseline.json").read_text())
    assert pins.UPSTREAM_SHA == b["upstream"]["commit"]
    assert pins.UPSTREAM_NOTEBOOK_BLOB == b["upstream"]["notebook_git_blob"]
    assert pins.LLAMA_CPP_COMMIT == b["runtime"]["commit"]
    for role, m in zip(("chat", "mmproj"), b["models"]):
        assert pins.MODELS[role]["repo"] == m["repo"] and pins.MODELS[role]["file"] == m["file"]
        assert pins.MODELS[role]["revision"] == m["file_change_commit"]
        assert pins.MODELS[role]["sha256"] == m["sha256_publisher"]
    assert pins.MODELS["mmproj"]["size_bytes"] == b["models"][1]["size_bytes"]


@needs_upstream
def test_upstream_checkout_is_the_pinned_commit():
    head = subprocess.run(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    assert head == pins.UPSTREAM_SHA
    from qmc import colab
    assert colab.LLAMA_CPP_COMMIT == pins.LLAMA_CPP_COMMIT


def test_no_colab_or_drive_dependency_in_package():
    for f in list(PKG.glob("*.py")) + [ROOT / "scripts" / "pod.py"]:
        src = f.read_text()
        assert "/content" not in src, f
        assert "google.colab" not in src and "mount_drive" not in src and "drive.mount" not in src, f
        assert "MyDrive" not in src, f


def test_layout_env_is_workspace_and_non_secret(clean_env, monkeypatch):
    env = layout.apply_env(root=clean_env / "ws", ctx=8192)
    assert all(str(clean_env / "ws") in v or k in {"QMC_LOCAL_DB"} or not v.startswith("/") for k, v in env.items())
    assert env["QMC_SHARE"] == "false" and env["QMC_TTS"] == "false" and env["QMC_CHAT_CTX"] == "8192"
    assert not any(re.search(r"AUTH|KEY|PASS|TOKEN", k) for k in env)


# ---------------------------------------------------------------- launch preflight
@needs_upstream
def test_preflight_requires_both_auth_values(clean_env, monkeypatch):
    cfg = launch.make_config(mock=True, port=17861)
    with pytest.raises(launch.LaunchError, match="両方"):
        launch.preflight(cfg)
    cfg.auth_user = "u"
    with pytest.raises(launch.LaunchError, match="両方"):
        launch.preflight(cfg)
    cfg.auth_user, cfg.auth_password = None, "p"
    with pytest.raises(launch.LaunchError, match="両方"):
        launch.preflight(cfg)
    cfg.auth_user = "u"
    launch.preflight(cfg)  # both present, free port: ok


@needs_upstream
def test_preflight_refuses_busy_port_without_fallback(clean_env):
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", 0))
        s.listen()
        port = s.getsockname()[1]
        cfg = launch.make_config(mock=True, port=port)
        cfg.auth_user, cfg.auth_password = "u", "p"
        with pytest.raises(launch.LaunchError, match="使用中"):
            launch.preflight(cfg)
        assert cfg.server_port == port  # untouched: no silent move to another port


@needs_upstream
def test_preflight_llama_host_share_remote(clean_env):
    cfg = launch.make_config(mock=False, port=17862)
    cfg.auth_user, cfg.auth_password = "u", "p"
    launch.preflight(cfg)
    for attr, value, msg in (("host", "0.0.0.0", "127.0.0.1"),):
        setattr(cfg.chat, attr, value)
        with pytest.raises(launch.LaunchError, match=msg):
            launch.preflight(cfg)
    cfg.chat.host = "127.0.0.1"
    cfg.chat.remote_base_url = "http://example.invalid/v1"
    with pytest.raises(launch.LaunchError, match="外部"):
        launch.preflight(cfg)
    cfg.chat.remote_base_url = None
    cfg.share = True
    with pytest.raises(launch.LaunchError, match="share"):
        launch.preflight(cfg)


@needs_upstream
def test_llama_command_binds_loopback(clean_env):
    from qmc.backends.llama_server import build_command
    cfg = launch.make_config(mock=False)
    cmd = build_command(Path("/x/llama-server"), Path("/m.gguf"), Path("/p.gguf"), host=cfg.chat.host,
                        port=cfg.chat.port, ctx_size=8192, fit_target_mib=2048, api_key="k")
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1" and "--no-webui" in cmd


# ---------------------------------------------------------------- Gradio 6 chat-only UI (mock, CPU)
@needs_upstream
def test_upstream_ui_chat_is_incompatible_but_adapter_builds(clean_env):
    import gradio as gr
    from qmc import ui_chat
    from qmc.app import build_app

    from qmc_runpod import chat_ui
    assert gr.__version__ == "6.29.0"
    app = build_app(launch.make_config(mock=True))
    with pytest.raises(TypeError, match="type"):
        ui_chat.build_ui(app)  # documents the upstream incompatibility being worked around
    demo = chat_ui.build_ui(app)
    assert demo is not None
    assert gr.Chatbot.__name__ == "Chatbot"  # patch was restored


@needs_upstream
def test_mock_chat_only_server_requires_login_and_serves(clean_env, monkeypatch):
    import requests
    monkeypatch.setenv("QMC_AUTH_USER", "tester")
    monkeypatch.setenv("QMC_AUTH_PASSWORD", "pw-for-test")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    app = launch.launch(mock=True, host="127.0.0.1", port=port)
    try:
        assert app.cfg.chat_only
        with pytest.raises(KeyError):
            app.manager.get("image")  # no image-generation backend is registered
        base = f"http://127.0.0.1:{port}"
        assert requests.get(base + "/config", timeout=10).status_code in (401, 403)
        sess = requests.Session()
        r = sess.post(base + "/login", data={"username": "tester", "password": "pw-for-test"}, timeout=10)
        assert r.status_code == 200
        assert sess.get(base + "/config", timeout=10).status_code == 200
        from qmc_runpod import smoke
        turn = smoke.run_turn(app, "ping")
        assert turn["error"] is False and turn["chars"] > 0
    finally:
        launch.stop_app(app)


# ---------------------------------------------------------------- models
def test_models_verify_ok_and_mismatch(tmp_path):
    import hashlib
    data = b"x" * 1000
    f = tmp_path / "m.gguf"; f.write_bytes(data)
    spec = {"file": "m.gguf", "revision": "r", "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": 1000}
    assert models.verify(f, spec)["sha256_verified"] is True
    with pytest.raises(models.VerifyError):
        models.verify(f, {**spec, "sha256": "0" * 64})
    with pytest.raises(models.VerifyError):
        models.verify(f, {**spec, "size_bytes": 999})


def test_fetch_pinned_uses_revisions_and_two_files_only(tmp_path):
    import hashlib
    calls, specs = [], {}
    for role in ("chat", "mmproj"):
        data = role.encode() * 10
        specs[role] = {"repo": f"o/{role}", "file": f"{role}.gguf", "revision": "a" * 40,
                       "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}

    def fake(repo, filename, revision, cache_dir):
        calls.append((repo, filename, revision))
        p = tmp_path / filename; p.write_bytes(filename.split(".")[0].encode() * 10); return str(p)
    (chat, _mm), recs = models.fetch_pinned(tmp_path, download=fake, specs=specs)
    assert [c[2] for c in calls] == ["a" * 40, "a" * 40] and len(calls) == 2
    assert all(r["sha256_verified"] for r in recs) and chat.name == "chat.gguf"


# ---------------------------------------------------------------- report allowlist
def good_report():
    return {"trial_id": "t1", "created_at_jst": "2026-10-02T15:00:00+09:00",
            "source": {"ops_commit": "a" * 40, "ops_tree_clean": True, "lock_sha256": "b" * 64},
            "upstream": {"sha": pins.UPSTREAM_SHA, "notebook_blob": pins.UPSTREAM_NOTEBOOK_BLOB},
            "models": [{"role": "chat", "file": "a.gguf", "revision": "r", "sha256_verified": True, "size_bytes": 1}],
            "settings": {"ctx": 8192, "thinking": False, "share": False},
            "metrics": {"first_first_delta_s": 1.2}, "checks": report.checks_template(), "notes": ["ok"]}


def test_report_accepts_allowlisted_and_rejects_everything_else(tmp_path):
    assert report.validate(good_report()) == []
    assert report.write_report(good_report(), tmp_path).exists()
    bad = good_report(); bad["environment_dump"] = {"A": "b"}
    assert any("allowlist" in e for e in report.validate(bad))
    bad = good_report(); bad["pod"] = {"id": "abc", "env": {"JUPYTER_PASSWORD": "x"}}
    assert any("pod.env" in e for e in report.validate(bad))
    for secretish in ("Bearer abcdefghij", "my api_key is 1", "password=hunter2", "A" * 45, "JUPYTER_PASSWORD"):
        bad = good_report(); bad["notes"] = [secretish]
        assert report.validate(bad), secretish
    bad = good_report(); bad["metrics"] = {"Bad Key": 1}
    assert report.validate(bad)
    with pytest.raises(ValueError):
        bad = good_report(); bad["foo"] = 1; report.write_report(bad, tmp_path)


def test_bundle_roundtrip_and_tamper_detection(tmp_path):
    p = report.write_report(good_report(), tmp_path)
    tgz = tmp_path / "b.tar.gz"
    report.make_bundle([p], tgz)
    assert report.verify_bundle(tgz) == []
    with tarfile.open(tgz) as t:
        names = t.getnames()
    assert sorted(names) == ["MANIFEST.json", p.name]
    # tamper: rebuild with altered report but old manifest
    import io
    with tarfile.open(tgz) as t:
        manifest = t.extractfile("MANIFEST.json").read()
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as t:
        for name, data in ((p.name, b'{"trial_id":"t1"}'), ("MANIFEST.json", manifest)):
            i = tarfile.TarInfo(name); i.size = len(data); t.addfile(i, io.BytesIO(data))
    assert report.verify_bundle(evil)


# ---------------------------------------------------------------- pod API (fake HTTP; nothing leaves the machine)
class FakeResp:
    def __init__(self, code, body=None):
        self.status_code, self._b = code, body
        self.content = b"x" if body is not None else b""

    def json(self):
        return self._b


class FakeSession:
    """Plays a script of FakeResp or Exception objects, then repeats the last item."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        assert "headers" not in kw, "client must not set auth headers"
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def api_with(*responses):
    s = FakeSession(responses)
    return podapi.PodApi(session=s), s


NAME = "qwen-trial-20261002-143000-ab12"
JST = podapi.JST


def est(total=5.0):
    return {"total_usd": total}


def test_create_body_matches_plan():
    b = podapi.build_create_body(name=NAME, gpu_id="NVIDIA A100-SXM4-80GB", data_center_ids=["US-MD-1"])
    assert b["gpu"] == {"id": "NVIDIA A100-SXM4-80GB", "count": 1}
    assert b["disk"] == 20 and b["mounts"] == {"persistent": {"size": 80, "path": "/workspace"}}
    assert b["cloud"] == "SECURE" and b["startSsh"] is False and "env" not in b
    assert b["ports"] == ["8888/http", "7860/http"] and b["name"] == NAME
    with pytest.raises(podapi.PodApiError):
        podapi.build_create_body(name="my-pod", gpu_id="g")  # names must be the unique, searchable kind


def test_trial_names_are_unique_and_match_pattern():
    names = {podapi.make_trial_name() for _ in range(50)}
    assert len(names) == 50 and all(podapi.NAME_RE.match(n) for n in names)


def test_session_estimate_includes_runtime_to_stop_target_storage_after_stop_and_margin():
    launch_at = __import__("datetime").datetime(2026, 10, 2, 14, 30, tzinfo=JST)
    stop_at = __import__("datetime").datetime(2026, 10, 2, 16, 45, tzinfo=JST)
    e = podapi.estimate_session(1.59, launch_at, stop_at, stop_lag_min=10, hold_hours_after_stop=24, margin=0.20)
    assert e["run_hours"] == pytest.approx(2.25 + 10 / 60, abs=1e-3)           # 14:30 → 16:45 plus the stop lag
    assert e["gpu_usd"] == pytest.approx(1.59 * e["run_hours"], abs=1e-3)
    assert e["storage_after_stop_usd"] == pytest.approx(80 * 0.20 * 24 / 720, abs=1e-3)   # Pod Volume kept after stop
    assert e["total_usd"] == pytest.approx(e["subtotal_usd"] * 1.2, abs=1e-3)           # margin on top
    assert "保証するものではありません" in e["disclaimer"] and "自動停止" in e["disclaimer"]
    longer = podapi.estimate_session(1.59, launch_at, stop_at, hold_hours_after_stop=72)
    assert longer["total_usd"] > e["total_usd"]
    with pytest.raises(podapi.PodApiError):
        podapi.estimate_session(1.59, stop_at, launch_at)


def test_plan_numbers_still_match_the_operations_plan():
    assert podapi.estimate_cost(1.59, 2) == pytest.approx(3.2078, abs=1e-3)


def test_create_is_guarded_and_estimate_is_not_called_a_cap():
    body = podapi.build_create_body(name=NAME, gpu_id="g")
    api, s = api_with(FakeResp(201, {"id": "x"}))
    with pytest.raises(podapi.PodApiError, match="not approved"):
        api.create_pod(body, approval="yes", estimate=est())
    with pytest.raises(podapi.PodApiError, match="not a spending cap"):
        api.create_pod(body, approval=podapi.APPROVAL, estimate=est(10.5), limit_usd=10)
    assert s.calls == []  # nothing was sent


def test_create_returns_only_allowlisted_fields():
    api, s = api_with(FakeResp(201, {"id": "abc123xyz", "status": "PROVISIONING", "env": {"JUPYTER_PASSWORD": "s3cr3t"},
                                      "ssh": {"x": 1}, "cost": 1.59, "name": NAME}))
    out = api.create_pod(podapi.build_create_body(name=NAME, gpu_id="g"), approval=podapi.APPROVAL, estimate=est())
    assert "env" not in out and "ssh" not in out and out["id"] == "abc123xyz"
    assert s.calls[0][0] == "POST" and s.calls[0][1].endswith("/v2/pods")


@pytest.mark.parametrize("failure", [TimeoutError("t"), ConnectionError("c"), FakeResp(502, {"e": 1}), FakeResp(504)])
def test_create_timeout_or_5xx_is_reported_as_possibly_billed_and_never_retried(failure):
    api, s = api_with(failure)
    with pytest.raises(podapi.CreateUncertain) as ei:
        api.create_pod(podapi.build_create_body(name=NAME, gpu_id="g"), approval=podapi.APPROVAL, estimate=est())
    msg = str(ei.value)
    assert "MAY have been created" in msg and NAME in msg and "console" in msg and "scripts/pod.py find" in msg
    assert "Do NOT run create again" in msg
    assert len(s.calls) == 1  # exactly one attempt


def test_create_4xx_is_a_definite_failure_not_uncertain():
    api, _s = api_with(FakeResp(402, {"error": "balance"}))
    with pytest.raises(podapi.PodApiError) as ei:
        api.create_pod(podapi.build_create_body(name=NAME, gpu_id="g"), approval=podapi.APPROVAL, estimate=est())
    assert not isinstance(ei.value, podapi.CreateUncertain) and "not created" in str(ei.value)


def test_find_pods_by_name_is_exact_paged_and_allowlisted():
    page1 = {"pods": [{"id": "p1", "name": NAME + "x", "status": "RUNNING"}],
             "pagination": {"hasNextPage": True, "nextCursor": "c2"}}
    page2 = {"pods": [{"id": "p2", "name": NAME, "status": "RUNNING", "env": {"A": "b"}, "cost": 1.59}],
             "pagination": {"hasNextPage": False, "nextCursor": None}}
    api, s = api_with(FakeResp(200, page1), FakeResp(200, page2))
    found = api.find_pods_by_name(NAME)
    assert [p["id"] for p in found] == ["p2"] and "env" not in found[0]
    assert s.calls[1][2]["params"]["cursor"] == "c2" and all(c[0] == "GET" for c in s.calls)


def run_stop(responses, **kw):
    api, s = api_with(*responses)
    clock = FakeClock()
    try:
        return api.stop_and_confirm("abc123", sleep=clock.sleep, clock=clock.now, **kw), s, clock
    except podapi.StopFailed as exc:
        return exc, s, clock


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def pod(status, **extra):
    return FakeResp(200, {"id": "abc123", "status": status, **extra})


def test_stop_confirms_only_exited_with_numeric_zero_cost():
    out, s, _ = run_stop([FakeResp(200, {"status": "STARTING"}), pod("RUNNING", cost=1.59), pod("EXITED", cost=0.0)])
    assert not isinstance(out, Exception) and out["status"] == "EXITED"
    assert s.calls[0][0] == "POST" and s.calls[0][1].endswith("/v2/pods/abc123/action") and s.calls[0][2]["json"] == {"action": "stop"}
    out, _, _ = run_stop([FakeResp(200, {}), pod("EXITED", cost=0)])  # integer 0 is fine
    assert not isinstance(out, Exception)


@pytest.mark.parametrize("cost_fields", [{}, {"cost": None}, {"cost": False}, {"cost": "0"}, {"cost": 0.01}, {"cost": 1.59}])
def test_stop_api_exited_is_separate_from_billing(cost_fields):
    out, _s, clock = run_stop([FakeResp(200, {}), pod("EXITED", **cost_fields)], timeout_s=60, interval_s=10)
    assert out["compute_api_exited"] is True
    assert out["stop_corroborated"] is False and out["billing_reconciled"] is False
    assert out["billing_state"] == "pending"


def test_stop_survives_communication_errors_until_the_deadline():
    # POST times out, GET errors, then everything works before the deadline -> confirmed
    out, s, _ = run_stop([TimeoutError("x"), ConnectionError("y"), FakeResp(200, {}), pod("RUNNING", cost=1.59),
                          pod("EXITED", cost=0.0)], timeout_s=300, interval_s=10)
    assert not isinstance(out, Exception)
    # permanent errors: loop runs to the deadline, then fails with instructions
    out, s, clock = run_stop([ConnectionError("down")], timeout_s=300, interval_s=10)
    assert isinstance(out, podapi.StopFailed) and clock.t >= 300 and len(s.calls) > 5


def test_stop_failure_always_has_pod_id_console_steps_and_never_terminates():
    for responses in ([FakeResp(409, {"e": 1})], [ConnectionError("down")], [FakeResp(200, {}), pod("RUNNING", cost=1.59)]):
        out, s, _ = run_stop(responses, timeout_s=30, interval_s=10)
        msg = str(out)
        assert isinstance(out, podapi.StopFailed)
        assert "abc123" in msg and "Stop (not Terminate)" in msg and "console/pods" in msg and "Billing may still be running" in msg
        assert not any(c[2].get("json") == {"action": "terminate"} for c in s.calls)


def test_stop_cli_prints_instructions_on_failure(capsys, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import pod as pod_cli
    api, _ = api_with(ConnectionError("down"))
    monkeypatch.setattr(podapi.time, "sleep", lambda _: None)
    ticks = iter(range(0, 10_000, 100))
    monkeypatch.setattr(podapi.time, "monotonic", lambda: next(ticks))
    assert pod_cli.main(["stop", "abc123"], api=api) == 2
    err = capsys.readouterr().err
    assert "abc123" in err and "Stop (not Terminate)" in err


def test_cli_create_requires_the_planned_name_and_reports_uncertain_create(capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import pod as pod_cli

    class Api:
        def gpu_availability(self, g):
            return {"id": g, "secure_price_per_hr": 1.59, "availability": "MEDIUM", "max_count": 8}

        def datacenters_with_gpu(self, g):
            return [{"id": "US-MD-1", "availability": "MEDIUM"}]

        def create_pod(self, body, **kw):
            raise podapi.CreateUncertain(body["name"], "TimeoutError")
    now = __import__("datetime").datetime(2026, 10, 2, 14, 0, tzinfo=JST)
    assert pod_cli.main(["create", "--approve", podapi.APPROVAL], api=Api(), now=now) == 2          # no --name
    code = pod_cli.main(["create", "--approve", podapi.APPROVAL, "--name", NAME, "--launch-at", "14:30"], api=Api(), now=now)
    cap = capsys.readouterr()
    assert code == 3 and NAME in cap.err and "MAY have been created" in cap.err


def test_cli_plan_prints_estimate_breakdown_and_limit(capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import pod as pod_cli

    class Api:
        def gpu_availability(self, g):
            return {"id": g, "secure_price_per_hr": 1.59, "availability": "LOW" if "PCIe" in g else "MEDIUM", "max_count": 8}

        def datacenters_with_gpu(self, g):
            return [{"id": "US-MD-1", "availability": "MEDIUM"}]
    now = __import__("datetime").datetime(2026, 10, 2, 14, 0, tzinfo=JST)
    assert pod_cli.main(["plan", "--launch-at", "14:30"], api=Api(), now=now) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["body"]["gpu"]["id"] == "NVIDIA A100-SXM4-80GB"  # better stock wins
    e = out["estimate"]
    assert {"run_hours", "gpu_usd", "storage_running_usd", "storage_after_stop_usd", "margin_pct", "total_usd"} <= set(e)
    assert out["within_limit"] is True and podapi.NAME_RE.match(out["body"]["name"])


def test_pod_id_is_validated():
    api, _ = api_with(FakeResp(200, {}))
    for bad in ("", "../x", "a b", "x" * 50):
        with pytest.raises(podapi.PodApiError):
            api.stop_pod(bad)


def test_no_terminate_or_delete_in_podapi():
    src = (PKG / "podapi.py").read_text()
    tree = ast.parse(src)
    assert not [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and re.search("terminate|delete", n.name)]
    assert '"DELETE"' not in src and '{"action": "terminate"}' not in src


def test_no_key_handling_in_code():
    for f in list(PKG.glob("*.py")) + [ROOT / "scripts" / "pod.py"]:
        code = "\n".join(line for line in f.read_text().splitlines() if not line.lstrip().startswith(("#", '"""')))
        assert "RUNPOD_API_KEY" not in code, f
        assert not re.search(r"headers\s*=|Bearer|os\.environ\[.*KEY", code) or f.name in {"report.py", "features.py"}, f
        if f.name == "features.py":
            assert 'base.startswith("http://127.0.0.1:")' in code
            assert 'model.client._headers()' in code  # existing ephemeral localhost authentication only


# ---------------------------------------------------------------- notebook
def test_notebook_is_valid_and_secret_free():
    nb = json.loads((ROOT / "notebooks" / "Qwen-Q8-Chat-RunPod.ipynb").read_text())
    assert nb["nbformat"] == 4
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    for c in code:
        src = "".join(c["source"])
        ast.parse("\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("%", "!"))))
        assert c["outputs"] == [] and c["execution_count"] is None
        assert "/content" not in src and "google.colab" not in src and "drive.mount" not in src and "RUNPOD_API_KEY" not in src
    text = json.dumps(nb)
    assert "getpass" in text and "launch.launch" in text and "git pull" not in text
    assert "pip\", \"install" not in text and "pip install" not in text.replace("NOT here: installing into the Pod's system Python", "")
    assert "running_in_venv" in text and "qwen-venv" in text


def test_lock_file_is_hashed_and_pins_gradio():
    lock = (ROOT / "requirements-runpod.lock.txt").read_text()
    assert "gradio==6.29.0" in lock and "--hash=sha256:" in lock
    assert not any(l.startswith("torch") for l in lock.splitlines())


# ---------------------------------------------------------------- llama.cpp build plan (commands only, nothing is built)
@needs_upstream
def test_llama_install_builds_pinned_commit_under_workspace(tmp_path, monkeypatch):
    import shutil

    from qmc import colab

    from qmc_runpod import llama
    monkeypatch.delenv("QMC_CUDA_ARCHS", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/cuda/bin/nvcc" if name == "nvcc" else None)
    monkeypatch.setattr(colab, "detect_cuda_arch", lambda: "80")
    cmds = []

    def fake_run(cmd):
        cmds.append([str(c) for c in cmd])
        if cmd[0] == "cmake" and "--build" in cmd:
            out = tmp_path / "llama.cpp" / "build" / "bin"
            out.mkdir(parents=True, exist_ok=True)
            (out / "llama-server").write_text("bin")

    server = llama.install(tmp_path, run=fake_run)
    flat = [" ".join(c) for c in cmds]
    assert any(pins.LLAMA_CPP_COMMIT in c and "checkout" in c for c in flat)
    assert any("-DCMAKE_CUDA_ARCHITECTURES=80" in c for c in flat)
    assert str(server).startswith(str(tmp_path / "llama-bin")) and "sm80" in str(server)
    assert not any("/content" in c for c in flat)


@needs_upstream
def test_llama_install_refuses_guessing_arch_or_missing_nvcc(tmp_path, monkeypatch):
    import shutil

    from qmc import colab

    from qmc_runpod import llama
    monkeypatch.delenv("QMC_CUDA_ARCHS", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="nvcc"):
        llama.install(tmp_path, run=lambda c: None)
    monkeypatch.setattr(shutil, "which", lambda name: "/x/nvcc")
    monkeypatch.setattr(colab, "detect_cuda_arch", lambda: None)
    with pytest.raises(RuntimeError, match="compute capability"):
        llama.install(tmp_path, run=lambda c: None)


# ---------------------------------------------------------------- notebook root resolution
def _notebook_cell(index_contains: str) -> str:
    nb = json.loads((ROOT / "notebooks" / "Qwen-Q8-Chat-RunPod.ipynb").read_text())
    return next("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code" and index_contains in "".join(c["source"]))


@pytest.mark.parametrize("start", ["notebooks", "."])
def test_cell1_resolves_repo_root_from_notebooks_folder_or_root(start, tmp_path):
    cell = _notebook_cell("Cell 1")
    resolver = cell.split("# --- end root resolution ---")[0]
    code = resolver + "\nprint(REPO_DIR)\n"
    env = {k: v for k, v in __import__("os").environ.items() if k != "QMC_REPO_ROOT"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT / start, capture_output=True, text=True, env=env, check=True)
    assert Path(out.stdout.strip()) == ROOT
    # outside the repo it fails clearly instead of picking something else
    bad = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, env=env, check=False)
    # A test temp folder inside this checkout legitimately finds an ancestor repo.
    assert (bad.returncode != 0 and "repository root not found" in bad.stderr) or Path(bad.stdout.strip()) == ROOT
    # explicit override works from anywhere
    env["QMC_REPO_ROOT"] = str(ROOT)
    ok = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, env=env, check=True)
    assert Path(ok.stdout.strip()) == ROOT


def test_notebook_uses_repo_dir_not_cwd_after_resolution():
    nb = json.loads((ROOT / "notebooks" / "Qwen-Q8-Chat-RunPod.ipynb").read_text())
    later = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code" and "Cell 1" not in "".join(c["source"]))
    assert "os.getcwd" not in later and "Path.cwd" not in later and "%cd" not in later
    cell1_after = _notebook_cell("Cell 1").split("# --- end root resolution ---")[1]
    assert "getcwd" not in cell1_after and "REPO_DIR" in cell1_after


def test_first_check_is_one_capped_request_and_perf_criteria_default_to_skipped():
    first = _notebook_cell("Cell 4:")
    assert first.count("smoke.first_response") == 1 and "warm_runs" not in first
    assert "64 tokens" in first or "64" in first
    warm = _notebook_cell("Cell 4b")
    assert "evaluate_criteria" in warm and "OPTIONAL" in warm
    rep = report.checks_template()
    assert set(rep.values()) == {"skipped"} and "perf_criteria" in rep and "warm_runs" in rep


# ---------------------------------------------------------------- bootstrap script (public repo, pinned commit)
@pytest.mark.skipif(sys.platform == "win32", reason="Linux bash bootstrap requires POSIX paths")
def test_bootstrap_checks_out_exact_commit_and_rejects_bad_input(tmp_path):
    src = tmp_path / "src"
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    for i in range(2):
        (src / "f.txt").write_text(str(i))
        subprocess.run(["git", "-C", str(src), "add", "."], check=True)
        subprocess.run(["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", str(i)], check=True)
    shas = subprocess.run(["git", "-C", str(src), "rev-list", "HEAD"], capture_output=True, text=True, check=True).stdout.split()
    env = {**__import__("os").environ, "QMC_OPS_REPO_URL": str(src), "QMC_OPS_DIR": str(tmp_path / "ops")}
    script = str(ROOT / "scripts" / "pod-bootstrap.sh")
    assert subprocess.run(["bash", script, "main"], env=env, capture_output=True, check=False).returncode == 2  # branch names refused
    assert subprocess.run(["bash", script, shas[1]], env=env, capture_output=True, check=False).returncode == 0
    head = subprocess.run(["git", "-C", str(tmp_path / "ops"), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    assert head == shas[1]  # the older, pinned commit, not the branch tip
    (tmp_path / "ops" / "f.txt").write_text("dirty")
    assert subprocess.run(["bash", script, shas[1]], env=env, capture_output=True, check=False).returncode != 0  # dirty tree refused


# ---------------------------------------------------------------- provenance and report contents
def _git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "requirements-runpod.lock.txt").write_bytes(b"gradio==6.29.0\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], check=True)


def test_build_report_records_commit_lock_hash_image_cuda_build_and_all_checks(tmp_path):
    import hashlib

    from qmc_runpod import provenance, trial
    _git_repo(tmp_path)
    checks = report.checks_template()
    report.mark(checks, "upstream_pin", True)
    report.mark(checks, "app_launch", False)
    rep = trial.build_report(
        trial_id="t1", repo_root=tmp_path, lock_path=tmp_path / "requirements-runpod.lock.txt",
        model_records=[{"role": "chat", "file": "a.gguf", "revision": "r", "sha256_verified": True, "size_bytes": 1}],
        settings={"ctx": 8192, "thinking": False}, gpu={"gpu_name": "A100", "total_mib": 81920, "driver": "550"},
        build={"cuda_archs": "80", "runtime_tag": "py311-x86_64", "build_type": "Release", "build_seconds": 1500.0,
               "built_this_run": True},
        checks=checks, server_version="llama-server 1")
    assert report.validate(rep) == []
    assert rep["source"]["ops_commit"] == provenance.git_head(tmp_path) and rep["source"]["ops_tree_clean"] is True
    assert rep["source"]["lock_sha256"] == hashlib.sha256(b"gradio==6.29.0\n").hexdigest()
    assert rep["environment"]["image_tag"].startswith("runpod/pytorch:") and "image_digest" in rep["environment"]
    assert {"cuda_toolkit", "cuda_driver_api"} <= set(rep["environment"])
    assert rep["llama_cpp"]["cuda_archs"] == "80" and rep["llama_cpp"]["build_seconds"] == 1500.0
    assert rep["checks"]["upstream_pin"] == "pass" and rep["checks"]["app_launch"] == "fail" and rep["checks"]["asr"] == "skipped"
    (tmp_path / "dirty.txt").write_text("x")
    assert provenance.tree_clean(tmp_path) is False


def test_report_requires_every_check_and_only_pass_fail_skipped():
    rep = good_report()
    del rep["checks"]["asr"]
    assert any("asr" in e and "missing" in e for e in report.validate(rep))
    rep = good_report(); rep["checks"]["asr"] = "ok"
    assert report.validate(rep)
    rep = good_report(); rep["checks"]["made_up"] = "pass"
    assert any("not a known check" in e for e in report.validate(rep))
    rep = good_report(); del rep["checks"]
    assert report.validate(rep)


def test_pod_section_drops_env_ssh_and_everything_not_named():
    from qmc_runpod import trial
    summary = {"id": "abc123", "gpu_id": "NVIDIA A100-SXM4-80GB", "dataCenterId": "US-MD-1", "status": "EXITED",
               "env": {"JUPYTER_PASSWORD": "s3cr3t", "PUBLIC_KEY": "ssh-rsa AAA"}, "ssh": {"ip": "1.2.3.4"},
               "runtime": {"x": 1}, "name": NAME}
    sec = trial.pod_section(summary, price_per_hr=1.59)
    assert set(sec) <= {"id", "gpu_type", "data_center", "started_at", "final_status", "price_per_hr"}
    assert "s3cr3t" not in json.dumps(sec) and "ssh-rsa" not in json.dumps(sec)
    rep = good_report(); rep["pod"] = sec
    assert report.validate(rep) == []
    rep["pod"] = {**sec, "env": {"A": "b"}}
    assert report.validate(rep)


# ---------------------------------------------------------------- cold / warm separation and output cap
class RecordingChat:
    name = "chat"

    def __init__(self, deltas=5):
        self.params, self.loaded, self.deltas = [], False, deltas

    @property
    def is_loaded(self):
        return self.loaded

    def stream_chat(self, messages, params, cancel=None):
        from qmc.backends.base import ChatDelta
        self.params.append(params)
        for i in range(self.deltas):
            yield ChatDelta(content="x", finish_reason="length" if i == self.deltas - 1 else None)


class StubManager:
    def __init__(self, model):
        self.model = model

    def ensure(self, name):
        self.model.loaded = True

    def get(self, name):
        return self.model

    def is_loaded(self, name):
        return self.model.loaded


class StubApp:
    def __init__(self, model):
        self.manager = StubManager(model)


@needs_upstream
def test_first_response_is_one_request_with_explicit_output_cap_after_separate_load():
    from qmc_runpod import smoke
    model = RecordingChat()
    app = StubApp(model)
    with pytest.raises(RuntimeError, match="not loaded"):
        smoke.first_response(app)  # load time must never be counted as a request
    load = smoke.load_model(app)
    first = smoke.first_response(app)
    assert set(load) == {"load_s"} and len(model.params) == 1
    assert model.params[0].max_tokens == smoke.FIRST_MAX_TOKENS == 64 and first["max_tokens"] == 64
    assert first["phase"] == "first" and first["finish_reason"] == "length" and first["stream_deltas"] == 5


@needs_upstream
def test_warm_runs_use_256_cap_and_criteria_need_ten_clean_warm_runs():
    from qmc_runpod import smoke
    model = RecordingChat()
    app = StubApp(model)
    smoke.load_model(app)
    first = smoke.first_response(app)
    five = smoke.warm_runs(app, ["a"] * 5)
    assert all(p.max_tokens == 256 for p in model.params[1:])
    assert smoke.evaluate_criteria(five)["verdict"] == "not_evaluated"
    assert smoke.evaluate_criteria([first])["verdict"] == "not_evaluated"        # the cold request never counts
    assert smoke.evaluate_criteria([])["verdict"] == "not_evaluated"
    ten = smoke.warm_runs(app, ["a"] * 10)
    verdict = smoke.evaluate_criteria(ten)
    assert verdict["verdict"] in ("pass", "fail") and "not a guarantee" in verdict["note"]
    short_cap = smoke.warm_runs(app, ["a"] * 10, max_tokens=64)
    assert smoke.evaluate_criteria(short_cap)["verdict"] == "not_evaluated"      # wrong cap is not comparable
    slow = [{**r, "total_s": 99.0} for r in ten]
    assert smoke.evaluate_criteria(slow)["verdict"] == "fail"
    errs = [{**r, "error": True} for r in ten]
    assert smoke.evaluate_criteria(errs)["verdict"] == "not_evaluated"
