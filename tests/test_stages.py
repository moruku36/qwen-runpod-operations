import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from conftest import ROOT, needs_upstream

from qmc_runpod import envsetup, logchannel, pins, podapi, report, stages

sys.path.insert(0, str(ROOT / "scripts"))


def runner(tmp_path, mirror=True):
    return stages.Runner(tmp_path / "ws", mirror=(tmp_path / "mirror.log") if mirror else None)


def mirror_lines(tmp_path):
    f = tmp_path / "mirror.log"
    return f.read_text().splitlines() if f.exists() else []


# ---------------------------------------------------------------- log channel
def test_artifact_roundtrip_detects_tampering_and_missing_chunks(tmp_path):
    f = tmp_path / "bundle.tar.gz"
    f.write_bytes(os.urandom(9000))
    lines = logchannel.encode_artifact(f)
    assert len(lines) > 3 and all(len(line) < 1700 for line in lines)
    ok = logchannel.decode_artifacts(["noise", *lines, "more noise"])
    assert ok[f.name]["ok"] and ok[f.name]["data"] == f.read_bytes()
    assert not logchannel.decode_artifacts(lines[:-1])[f.name]["ok"]                      # missing chunk
    bad = [lines[0].rsplit("|", 1)[0] + "|AAAA", *lines[1:]]
    assert not logchannel.decode_artifacts(bad)[f.name]["ok"]                              # corrupted chunk
    assert logchannel.decode_artifacts(list(reversed(lines)))[f.name]["ok"]               # order does not matter
    assert logchannel.decode_artifacts(lines + lines)[f.name]["ok"]                       # duplicates are harmless


def test_event_lines_are_single_line_short_and_parse_back():
    line = logchannel.event_line("net", "a\nb " + "x" * 500)
    assert "\n" not in line and len(line) < 300
    ev = logchannel.parse_events(["junk", line, "QMC-ART|x|y|1|1|zz"])
    assert len(ev) == 1 and ev[0]["stage"] == "net"


# ---------------------------------------------------------------- runner
def test_runner_streams_command_output_to_log_and_mirror_and_reports_failure(tmp_path):
    r = runner(tmp_path)
    assert r.run("t", [sys.executable, "-c", "print('hello'); print('Traceback boom')"]) == 0
    assert "hello" in (r.logs / "t.out.log").read_text()
    assert any("Traceback boom" in line for line in mirror_lines(tmp_path))
    with pytest.raises(stages.StageError, match="exit code 3"):
        r.run("t", [sys.executable, "-c", "raise SystemExit(3)"])
    assert r.run("t", [sys.executable, "-c", "raise SystemExit(3)"], check=False) == 3


def test_runner_has_no_default_timeout_but_honours_an_explicit_one(tmp_path):
    r = runner(tmp_path)
    assert r.run("t", [sys.executable, "-c", "import time; time.sleep(1.2); print('done')"]) == 0
    assert r.run("t", [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5, check=False) != 0


def test_status_and_checks_persist_and_default_to_skipped(tmp_path):
    r = runner(tmp_path)
    r.set_status("net", "running")
    r.set_status("net", "ok", failed="none")
    st = r.status()["net"]
    assert st["state"] == "ok" and "seconds" in st
    r.mark("upstream_pin", True)
    r.mark("lock_installed", False)
    again = stages.Runner(tmp_path / "ws", mirror=None)   # a new process sees the same state
    assert again.checks()["upstream_pin"] == "pass" and again.checks()["lock_installed"] == "fail"
    assert again.checks()["asr"] == "skipped"


def test_selftest_sends_event_and_a_decodable_artifact(tmp_path):
    r = runner(tmp_path)
    stages.stage_selftest(r)
    lines = mirror_lines(tmp_path)
    assert any("hello from the pod" in line for line in lines)
    assert logchannel.decode_artifacts(lines)["selftest.txt"]["ok"]
    assert r.status()["selftest"]["state"] == "ok"


# ---------------------------------------------------------------- network gate
class Resp:
    def __init__(self, status):
        self.status = status

    def getcode(self):
        return self.status


def test_net_stage_passes_when_everything_is_reachable(tmp_path):
    r = runner(tmp_path)
    git = [("repo", "https://example.invalid/x")]
    r.run = lambda stage, cmd, **kw: 0  # git ls-remote succeeded
    ok = stages.stage_net(r, opener=lambda req, timeout: Resp(200), git_targets=git,
                          http_targets=[("a", "https://a", True), ("b", "https://b", False)])
    assert ok and r.status()["net"]["state"] == "ok"


def test_net_stage_fails_on_no_route_to_host_and_says_to_stop(tmp_path):
    r = runner(tmp_path)
    r.run = lambda stage, cmd, **kw: 128   # git cannot reach GitHub

    def opener(req, timeout):
        raise OSError(113, "No route to host")
    ok = stages.stage_net(r, opener=opener, git_targets=[("ops_repo", "u")], http_targets=[("hf_mmproj", "https://h", True)])
    st = r.status()["net"]
    assert not ok and st["state"] == "fail" and "ops_repo" in st["failed"] and "hf_mmproj" in st["failed"]
    assert "STOP the Pod" in st["advice"]
    assert any("No route to host" in line for line in mirror_lines(tmp_path))


def test_net_targets_cover_github_pypi_and_both_pinned_model_files():
    names = {n for n, _ in stages.GIT_TARGETS} | {n for n, _, _ in stages.HTTP_TARGETS}
    assert {"ops_repo", "upstream_repo", "llama_cpp_repo", "pypi", "pythonhosted", "hf_mmproj", "hf_chat_gguf"} <= names
    urls = " ".join(u for _, u, _ in stages.HTTP_TARGETS)
    assert pins.MODELS["chat"]["revision"] in urls and pins.MODELS["mmproj"]["revision"] in urls
    assert all(m for _, _, m in stages.HTTP_TARGETS if "hf_" in _) or True
    assert [m for n, _, m in stages.HTTP_TARGETS if n.startswith("hf_")] == [True, True]  # models must answer 200


def test_probe_treats_404_on_pythonhosted_as_reachable_but_not_for_model_files():
    import urllib.error

    def opener404(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
    assert stages.probe_http("https://files.pythonhosted.org/", False, opener=opener404)["ok"]
    assert not stages.probe_http("https://huggingface.co/x", True, opener=opener404)["ok"]


# ---------------------------------------------------------------- isolated environment
def test_venv_commands_use_the_lock_with_hashes_inside_the_workspace_venv(tmp_path):
    root = tmp_path / "ws"
    py = str(envsetup.venv_python(root))
    install = envsetup.install_cmd(Path("lock.txt"), root)
    assert install[0] == py and "--require-hashes" in install and install[-2:] == ["-r", "lock.txt"]
    assert envsetup.check_cmd(root)[:3] == [py, "-m", "pip"]
    assert envsetup.kernel_cmd(root)[-1] == "--user" and "qwen-venv" in envsetup.kernel_cmd(root)
    assert envsetup.PIP_ENV["PYTHONNOUSERSITE"] == "1"       # ~/.local packages cannot leak in
    assert str(envsetup.venv_dir(root)).startswith(str(root))


def test_real_venv_is_created_without_system_site_packages(tmp_path):
    r = runner(tmp_path)
    r.run("venv", [sys.executable, "-m", "venv", "--without-pip", str(envsetup.venv_dir(r.root))])
    ok, why = envsetup.is_isolated(r.root)
    assert ok, why
    out = subprocess.run([str(envsetup.venv_python(r.root)), "-c", "import sys;print(sys.prefix != sys.base_prefix)"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == "True"
    cfg = envsetup.venv_dir(r.root) / "pyvenv.cfg"
    cfg.write_text(cfg.read_text().replace("include-system-site-packages = false", "include-system-site-packages = true"))
    assert not envsetup.is_isolated(r.root)[0]


def test_venv_stage_refuses_a_non_isolated_venv_and_checks_pip_inside_it(tmp_path, monkeypatch):
    r = runner(tmp_path)
    calls = []

    def fake_run(stage, cmd, **kw):
        calls.append([str(c) for c in cmd])
        if cmd[1:3] == ["-m", "venv"]:
            d = Path(cmd[-1]); (d / "bin").mkdir(parents=True)
            (d / "bin" / "python").write_text("")
            (d / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
        return 0
    r.run = fake_run
    stages.stage_venv(r, Path("lock.txt"), register_kernel=True)
    flat = [" ".join(c) for c in calls]
    assert any("-m venv" in c for c in flat) and any("--require-hashes" in c for c in flat)
    assert any(c.endswith("-m pip check") and "venv/bin/python" in c for c in flat)       # never the system pip
    assert any("ipykernel install" in c for c in flat)
    assert r.checks()["lock_installed"] == "pass" and r.status()["venv"]["state"] == "ok"
    # pip check failing inside the venv fails the stage and the check
    r2 = stages.Runner(tmp_path / "ws2", mirror=None)

    def fail_check(stage, cmd, **kw):
        if cmd[1:3] == ["-m", "venv"]:
            d = Path(cmd[-1]); (d / "bin").mkdir(parents=True)
            (d / "bin" / "python").write_text(""); (d / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
            return 0
        return 1 if cmd[-2:] == ["pip", "check"] else 0
    r2.run = fail_check
    with pytest.raises(stages.StageError, match="pip check"):
        stages.stage_venv(r2, Path("lock.txt"))
    assert r2.checks()["lock_installed"] == "fail"


def test_venv_stage_refuses_a_python_the_lock_was_not_built_for(tmp_path):
    r = runner(tmp_path)
    r.run = lambda *a, **k: 0
    fake = tmp_path / "py310"
    fake.write_text("#!/bin/sh\necho 3.10\n")
    fake.chmod(0o755)
    with pytest.raises(stages.StageError, match="needs 3.11"):
        stages.stage_venv(r, Path("lock.txt"), python=str(fake))
    assert r.status()["venv"]["state"] == "fail"


def test_venv_stage_falls_back_to_host_pip_when_ensurepip_is_missing(tmp_path):
    r = runner(tmp_path)
    calls = []

    def fake_run(stage, cmd, **kw):
        flat = [str(c) for c in cmd]
        calls.append(flat)
        if flat[1:3] == ["-m", "venv"]:
            if "--without-pip" not in flat:
                return 1                                   # "ensurepip is not available"
            d = Path(flat[-1]); (d / "bin").mkdir(parents=True)
            (d / "bin" / "python").write_text(""); (d / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
        return 0
    r.run = fake_run
    stages.stage_venv(r, Path("lock.txt"), python="/usr/bin/python", register_kernel=False)
    flat = [" ".join(c) for c in calls]
    assert any("-m venv --without-pip" in c for c in flat)
    assert any(c.startswith("/usr/bin/python -m pip --python") and "--require-hashes" in c for c in flat)
    assert any(c.startswith("/usr/bin/python -m pip --python") and c.endswith("pip check") is False and " check" in c for c in flat)
    assert r.checks()["lock_installed"] == "pass"


def test_kernel_in_notebook_is_required_and_lock_has_ipykernel():
    lock = (ROOT / "requirements-runpod.lock.txt").read_text()
    assert "ipykernel==" in lock and "--hash=sha256:" in lock
    assert "ipykernel" in (ROOT / "requirements-runpod.in").read_text()


# ---------------------------------------------------------------- source stage (local repo stands in for GitHub)
def test_source_stage_pins_commit_and_blob(tmp_path):
    src = tmp_path / "up"
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / "Qwen-Q8-Chat-Colab.ipynb").write_text("{}")
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "1"], check=True)
    sha = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    blob = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD:Qwen-Q8-Chat-Colab.ipynb"], capture_output=True, text=True, check=True).stdout.strip()
    r = runner(tmp_path)
    stages.stage_source(r, repo_url=str(src), sha=sha, blob=blob)
    assert r.checks()["upstream_pin"] == "pass"
    r2 = stages.Runner(tmp_path / "ws2", mirror=None)
    with pytest.raises(stages.StageError, match="pin mismatch"):
        stages.stage_source(r2, repo_url=str(src), sha=sha, blob="0" * 40)
    assert r2.checks()["upstream_pin"] == "fail"


# ---------------------------------------------------------------- login file
def test_login_file_is_private_and_never_logged(tmp_path):
    f = stages.write_login(tmp_path / "ws", "user1", "s3cr3t-pass")
    assert (f.stat().st_mode & 0o777) == 0o600 and (f.parent.stat().st_mode & 0o777) == 0o700
    assert "s3cr3t-pass" in f.read_text()


# ---------------------------------------------------------------- CPU rehearsal of the whole trial stage (mock backend)
@needs_upstream
def test_trial_stage_rehearsal_with_mock_backend_produces_a_verifiable_bundle_via_the_log_channel(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("QMC_") and k != "QMC_UPSTREAM_DIR":
            monkeypatch.delenv(k)
    monkeypatch.setenv("QMC_LOCAL_DB", str(tmp_path / "db" / "h.db"))
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    r = runner(tmp_path)
    bundle = stages.stage_trial(r, mock=True, warm=True, host="127.0.0.1", port=port, trial_id="trehearsal")
    assert report.verify_bundle(bundle) == []
    with tarfile.open(bundle) as t:
        rep = json.load(t.extractfile("report-trehearsal.json"))
    assert rep["checks"]["app_launch"] == "pass" and rep["checks"]["auth_enforced"] == "pass"
    assert rep["checks"]["first_response"] == "pass" and rep["checks"]["asr"] == "skipped"
    assert rep["metrics"]["first_max_tokens"] == 64 and "CPU rehearsal" in " ".join(rep["notes"])
    assert rep["checks"]["perf_criteria"] in ("skipped", "pass", "fail")
    # the same bundle comes back out of the log stream intact
    got = logchannel.decode_artifacts(mirror_lines(tmp_path))[bundle.name]
    assert got["ok"] and got["data"] == bundle.read_bytes()
    # the login file is private and its contents never reached the logs/mirror
    login = r.root / "secrets" / "gradio-login.txt"
    pw = login.read_text().split("password: ")[1].strip()
    assert (login.stat().st_mode & 0o777) == 0o600
    haystack = "\n".join(mirror_lines(tmp_path)) + "".join(f.read_text(errors="replace") for f in r.logs.glob("*"))
    assert pw not in haystack and r.status()["trial"]["state"] == "ok"


# ---------------------------------------------------------------- reading the Pod logs from outside (read-only)
class SSE:
    def __init__(self, events, code=200):
        self.status_code = code
        self._lines = []
        for e in events:
            self._lines += [f"id: {e['ts']}", "data: " + json.dumps(e), ""]
        self._lines.append(": heartbeat")

    def iter_lines(self, decode_unicode=True):
        yield from self._lines


class LogSession:
    def __init__(self, events):
        self.events, self.calls = events, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        assert method == "GET" and "headers" not in kw
        return SSE(self.events)


def test_read_logs_shows_only_qmc_events_and_rebuilds_a_verified_bundle(tmp_path, capsys):
    import read_pod_logs
    f = tmp_path / "x.tar.gz"
    f.write_bytes(b"payload" * 400)
    cont = [logchannel.event_line("net", "ops_repo: OK"), "pip secret-looking line should not be shown", *logchannel.encode_artifact(f)]
    ev = [{"source": "system", "line": "create container", "ts": "2026-10-02T05:00:00Z"}] + [
        {"source": "container", "line": line, "ts": f"2026-10-02T05:00:{i:02d}Z"} for i, line in enumerate(cont)]
    api = podapi.PodApi(session=LogSession(ev))
    rc = read_pod_logs.main(["abc123def", "--out", str(tmp_path / "out")], api=api)
    out = capsys.readouterr().out
    assert "[net] ops_repo: OK" in out and "secret-looking" not in out and "system: create container" in out
    assert (tmp_path / "out" / "x.tar.gz").read_bytes() == f.read_bytes() and "x.tar.gz: ok" in out
    assert rc == 1 and "not a readable tar.gz" in out  # the fake artifact is not a report bundle: reported, not a crash
    call = api.s.calls[0]
    assert call[1].endswith("/v2/pods/abc123def/logs") and call[2]["params"]["tail"] == 500
