import ast
import json
import re
import socket
import subprocess
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
            "upstream": {"sha": pins.UPSTREAM_SHA, "notebook_blob": pins.UPSTREAM_NOTEBOOK_BLOB},
            "models": [{"role": "chat", "file": "a.gguf", "revision": "r", "sha256_verified": True, "size_bytes": 1}],
            "settings": {"ctx": 8192, "thinking": False, "share": False},
            "metrics": {"first_text_median_s": 1.2}, "checks": {"chat_smoke": "pass"}, "notes": ["ok"]}


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
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        assert "headers" not in kw, "client must not set auth headers"
        return self.script.pop(0)


def api_with(*responses):
    s = FakeSession(responses)
    return podapi.PodApi(session=s), s


def test_create_body_matches_plan():
    b = podapi.build_create_body(name="n", gpu_id="NVIDIA A100-SXM4-80GB", data_center_ids=["US-MD-1"])
    assert b["gpu"] == {"id": "NVIDIA A100-SXM4-80GB", "count": 1}
    assert b["disk"] == 20 and b["mounts"] == {"persistent": {"size": 80, "path": "/workspace"}}
    assert b["cloud"] == "SECURE" and b["startSsh"] is False and "env" not in b and "mounts" in b
    assert "network" not in b["mounts"] and b["ports"] == ["8888/http", "7860/http"]


def test_cost_estimate_matches_plan_numbers():
    assert podapi.estimate_cost(1.59, 2) == pytest.approx(3.2078, abs=1e-3)  # plan: ~$3.21
    assert podapi.estimate_cost(1.59, 2, volume_stopped_hours=10) > 3.2078


def test_create_is_guarded():
    body = podapi.build_create_body(name="n", gpu_id="g")
    api, s = api_with()
    with pytest.raises(podapi.PodApiError, match="not approved"):
        api.create_pod(body, approval="yes", price_per_hr=1.59, hours=2)
    with pytest.raises(podapi.PodApiError, match="exceeds budget"):
        api.create_pod(body, approval=podapi.APPROVAL, price_per_hr=1.59, hours=6.5, budget_usd=10)
    assert s.calls == []  # nothing was sent


def test_create_returns_only_allowlisted_fields():
    api, s = api_with(FakeResp(201, {"id": "abc123xyz", "status": "PROVISIONING", "env": {"JUPYTER_PASSWORD": "s3cr3t"},
                                      "ssh": {"x": 1}, "cost": 1.59, "name": "n"}))
    out = api.create_pod(podapi.build_create_body(name="n", gpu_id="g"), approval=podapi.APPROVAL, price_per_hr=1.59, hours=2)
    assert "env" not in out and "ssh" not in out and out["id"] == "abc123xyz"
    assert s.calls[0][0] == "POST" and s.calls[0][1].endswith("/v2/pods")


def test_stop_posts_action_then_reads_back_exited():
    api, s = api_with(FakeResp(200, {"status": "STARTING"}),
                      FakeResp(200, {"id": "abc123", "status": "RUNNING", "cost": 1.59}),
                      FakeResp(200, {"id": "abc123", "status": "EXITED", "cost": 0.0, "actions": ["start", "terminate"]}))
    out = api.stop_and_confirm("abc123", sleep=lambda _: None)
    assert out["status"] == "EXITED"
    assert s.calls[0][0] == "POST" and s.calls[0][1].endswith("/v2/pods/abc123/action") and s.calls[0][2]["json"] == {"action": "stop"}
    assert [c[0] for c in s.calls[1:]] == ["GET", "GET"]


def test_stop_failure_gives_console_instructions_and_never_terminates():
    api, s = api_with(FakeResp(409, {"error": "x"}))
    with pytest.raises(podapi.StopFailed) as ei:
        api.stop_and_confirm("abc123")
    msg = str(ei.value)
    assert "abc123" in msg and "Stop (not Terminate)" in msg
    assert not any("terminate" in str(c) for c in s.calls)
    clock = iter([0, 5, 400, 400, 400])
    api, s = api_with(FakeResp(200, {}), FakeResp(200, {"id": "abc123", "status": "RUNNING", "cost": 1.59}),
                      FakeResp(200, {"id": "abc123", "status": "RUNNING", "cost": 1.59}))
    with pytest.raises(podapi.StopFailed):
        api.stop_and_confirm("abc123", timeout_s=300, sleep=lambda _: None, clock=lambda: next(clock))


def test_pod_id_is_validated():
    api, _ = api_with()
    for bad in ("", "../x", "a b", "x" * 50):
        with pytest.raises(podapi.PodApiError):
            api.stop_pod(bad)


def test_no_terminate_or_delete_in_podapi():
    tree = ast.parse((PKG / "podapi.py").read_text())
    assert not [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and re.search("terminate|delete", n.name)]
    assert '"DELETE"' not in (PKG / "podapi.py").read_text() and '"terminate"' not in (PKG / "podapi.py").read_text().replace("no terminate", "")


def test_no_key_handling_in_code():
    for f in list(PKG.glob("*.py")) + [ROOT / "scripts" / "pod.py"]:
        code = "\n".join(l for l in f.read_text().splitlines() if not l.lstrip().startswith(("#", '"""')))
        assert "RUNPOD_API_KEY" not in code, f
        assert not re.search(r"headers\s*=|Bearer|os\.environ\[.*KEY", code) or f.name == "report.py", f


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
    assert "--require-hashes" in text


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
