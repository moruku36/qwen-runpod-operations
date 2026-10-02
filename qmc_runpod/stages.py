"""Non-interactive, resumable setup/trial stages for the Pod.

Each stage logs to files under ``<root>/runs/logs`` and mirrors short event lines to the Pod's stdout
(PID 1) so they can be read remotely with the read-only Pod logs API (see ``logchannel``). Progress is
kept in ``<root>/runs/status.json``. Importing this module needs only the standard library; stages that
need third-party packages run under the isolated venv (``scripts/pod_run.py`` re-executes itself there).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import envsetup, layout, logchannel, pins

STAGES = ("selftest", "net", "source", "venv", "build", "trial")
VENV_STAGES = {"build", "trial"}
MIRROR_PATH = Path("/proc/1/fd/1")

def _hf_url(role: str) -> str:
    m = pins.MODELS[role]
    return f"https://huggingface.co/{m['repo']}/resolve/{m['revision']}/{m['file']}"


GIT_TARGETS = [
    ("ops_repo", "https://github.com/moruku36/qwen-runpod-operations"),
    ("upstream_repo", pins.UPSTREAM_REPO),
    ("llama_cpp_repo", "https://github.com/ggml-org/llama.cpp"),
]
HTTP_TARGETS = [  # (name, url, must_be_200)
    ("github", "https://github.com", False),
    ("pypi", "https://pypi.org/simple/pip/", True),
    ("pythonhosted", "https://files.pythonhosted.org/", False),  # the root answers 404; reaching it is the test
    ("hf_mmproj", _hf_url("mmproj"), True),
    ("hf_chat_gguf", _hf_url("chat"), True),
]


class StageError(RuntimeError):
    pass


class TrialRefused(StageError):
    """A refusal is separate from the immutable status of the prior attempt."""


class Runner:
    def __init__(self, root: Path | None = None, mirror: Path | None | bool = True, run_id: str | None = None):
        self.p = layout.paths(root)
        self.root = self.p["root"]
        self.runs = self.p["runs"]
        self.logs = self.runs / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.status_path = self.runs / "status.json"
        self.checks_path = self.runs / "checks.json"
        if mirror is True:  # QMC_LOG_MIRROR: "off" or a file path (for rehearsals); default: the Pod's PID 1 stdout
            override = os.environ.get("QMC_LOG_MIRROR")
            mirror = None if override == "off" else (Path(override) if override else MIRROR_PATH)
        self.mirror = mirror or None
        self._last_mirror = 0.0
        self.run_id = run_id

    # ---- logging
    def _mirror(self, line: str) -> None:
        if not self.mirror:
            return
        try:
            with open(self.mirror, "a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def event(self, stage: str, message: str) -> None:
        line = logchannel.event_line(stage, message, run_id=self.run_id)
        with open(self.logs / f"{stage}.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
        self._mirror(line)

    def emit_artifact(self, path: Path, name: str | None = None) -> None:
        for line in logchannel.encode_artifact(path, name, run_id=self.run_id):
            self._mirror(line)
        self.event("emit", f"artifact {name or Path(path).name} sent to log stream")

    # ---- status / checks
    def _read(self, path: Path) -> dict:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, path: Path, data: dict) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        tmp.replace(path)

    def set_status(self, stage: str, state: str, **detail) -> None:
        st = self._read(self.status_path)
        cur = st.get(stage, {})
        now = time.time()
        if state == "running":
            cur = {"started": now}
        cur.update({"state": state, **detail})
        if state in ("ok", "fail") and "started" in cur:
            cur["seconds"] = round(now - cur["started"], 1)
        st[stage] = cur
        self._write(self.status_path, st)
        self.event(stage, f"state={state} " + " ".join(f"{k}={v}" for k, v in detail.items()))

    def status(self) -> dict:
        return self._read(self.status_path)

    def checks(self) -> dict:
        from . import report

        saved = self._read(self.checks_path)
        base = report.checks_template()
        base.update({k: v for k, v in saved.items() if k in base})
        return base

    def mark(self, name: str, ok: bool | None) -> None:
        from . import report

        checks = self.checks()
        report.mark(checks, name, ok)
        self._write(self.checks_path, checks)

    # ---- commands
    def run(self, stage: str, cmd: list[str], *, timeout: float | None = None, env: dict | None = None,
            cwd: Path | None = None, check: bool = True) -> int:
        """Run ``cmd``, stream its output into the stage log, mirror a throttled subset. No default timeout."""
        self.event(stage, "$ " + " ".join(str(c) for c in cmd)[:200])
        proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                bufsize=1, env={**os.environ, **(env or {})}, cwd=str(cwd) if cwd else None)
        timer = None
        if timeout:
            timer = threading.Timer(timeout, proc.kill)
            timer.start()
        try:
            with open(self.logs / f"{stage}.out.log", "a", encoding="utf-8", errors="replace") as out:
                for line in proc.stdout:
                    out.write(line)
                    out.flush()
                    now = time.time()
                    important = any(k in line for k in ("rror", "Traceback", "fatal", "FAILED"))
                    if important or now - self._last_mirror >= 5:
                        self._last_mirror = now
                        self._mirror(logchannel.event_line(stage, line.strip() or "-"))
            rc = proc.wait()
        finally:
            if timer:
                timer.cancel()
        if rc != 0 and check:
            raise StageError(f"{stage}: command failed with exit code {rc}: {' '.join(str(c) for c in cmd)[:120]}")
        return rc


# ------------------------------------------------------------------ stages
def stage_selftest(r: Runner) -> None:
    """Prove the log channel end to end: an event line and a tiny artifact."""
    r.set_status("selftest", "running")
    probe = r.runs / "selftest.txt"
    probe.write_bytes(b"qmc log channel selftest\n")
    r.event("selftest", "hello from the pod")
    r.emit_artifact(probe, "selftest.txt")
    r.set_status("selftest", "ok")


def probe_http(url: str, must_be_200: bool, *, opener=None, timeout: float = 20) -> dict:
    opener = opener or urllib.request.urlopen
    t0 = time.monotonic()
    try:
        resp = opener(urllib.request.Request(url, method="HEAD"), timeout=timeout)
        code = getattr(resp, "status", None) or resp.getcode()
        ok = code == 200 if must_be_200 else code < 500
        return {"ok": ok, "detail": f"HTTP {code}", "seconds": round(time.monotonic() - t0, 1)}
    except urllib.error.HTTPError as exc:  # the server answered: the network path works
        ok = exc.code < 500 and not must_be_200
        return {"ok": ok, "detail": f"HTTP {exc.code}", "seconds": round(time.monotonic() - t0, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": type(exc).__name__ + ": " + str(exc)[:80], "seconds": round(time.monotonic() - t0, 1)}


def stage_net(r: Runner, *, opener=None, git_targets=None, http_targets=None) -> bool:
    """Reach every host the setup needs. A single failure means: stop the Pod and report."""
    r.set_status("net", "running")
    results = {}
    for name, url in (git_targets or GIT_TARGETS):
        rc = r.run("net", ["git", "ls-remote", url, "HEAD"], timeout=30, check=False,
                   env={"GIT_TERMINAL_PROMPT": "0"})
        results[name] = {"ok": rc == 0, "detail": f"git exit {rc}"}
    for name, url, must in (http_targets or HTTP_TARGETS):
        results[name] = probe_http(url, must, opener=opener)
    for name, res in results.items():
        r.event("net", f"{name}: {'OK' if res['ok'] else 'FAIL'} {res['detail']}")
    ok = all(v["ok"] for v in results.values())
    (r.runs / "net.json").write_text(json.dumps(results, indent=1))
    r.set_status("net", "ok" if ok else "fail", failed=",".join(k for k, v in results.items() if not v["ok"]) or "none",
                 advice="" if ok else "STOP the Pod and report; do not continue setup")
    return ok


def stage_source(r: Runner, *, repo_url: str = pins.UPSTREAM_REPO, sha: str = pins.UPSTREAM_SHA,
                 blob: str | None = pins.UPSTREAM_NOTEBOOK_BLOB, ops_root: Path | None = None) -> None:
    from . import provenance

    r.set_status("source", "running")
    src = r.p["source"] / "qwen-multimodal-colab"
    if not (src / ".git").exists():
        r.run("source", ["git", "clone", "--filter=blob:none", repo_url, src])
    r.run("source", ["git", "-C", src, "fetch", "origin", sha])
    r.run("source", ["git", "-C", src, "checkout", "-q", "--detach", sha])
    head = provenance.git_head(src)
    got_blob = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD:Qwen-Q8-Chat-Colab.ipynb"],
                              capture_output=True, text=True, check=False).stdout.strip()
    ok = head == sha and (blob is None or got_blob == blob)
    r.mark("upstream_pin", ok)
    if ops_root is not None:
        r.mark("ops_commit_clean", provenance.tree_clean(ops_root))
    if not ok:
        r.set_status("source", "fail", head=head[:12], blob=got_blob[:12])
        raise StageError("upstream pin mismatch")
    r.set_status("source", "ok", head=head[:12])


def _venv_has_pip(r: Runner) -> bool:
    return subprocess.run([str(envsetup.venv_python(r.root)), "-m", "pip", "--version"], capture_output=True,
                          check=False).returncode == 0


def stage_venv(r: Runner, lock: Path, *, python: str | None = None, kernel_prefix: Path | None = None,
               register_kernel: bool = True) -> None:
    """Create the isolated venv, install the hash-locked set, pip check inside it, register the kernel."""
    r.set_status("venv", "running", lock=lock.name)
    ver = subprocess.run([python or sys.executable, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                         capture_output=True, text=True, check=False).stdout.strip()
    if ver != envsetup.LOCK_PYTHON:  # the lock is hashed for CPython 3.11 wheels only
        r.set_status("venv", "fail", reason=f"python {ver or 'unknown'}; the lock needs {envsetup.LOCK_PYTHON}")
        raise StageError(f"python {ver or 'unknown'} cannot use the lock (needs {envsetup.LOCK_PYTHON})")
    env = dict(envsetup.PIP_ENV)
    host_pip = False
    if not envsetup.venv_python(r.root).exists():
        if r.run("venv", envsetup.create_cmd(r.root, python), check=False) != 0:
            # typically "ensurepip is not available": retry without pip and drive the venv with the host's pip
            shutil.rmtree(envsetup.venv_dir(r.root), ignore_errors=True)
            r.event("venv", "venv with pip failed; retrying --without-pip and using the host pip with --python")
            r.run("venv", envsetup.create_cmd(r.root, python, without_pip=True))
            host_pip = True
    elif not _venv_has_pip(r):
        host_pip = True
    isolated, why = envsetup.is_isolated(r.root)
    if not isolated:
        r.set_status("venv", "fail", reason=f"not isolated: {why}")
        raise StageError(f"venv is not isolated: {why}")
    r.run("venv", envsetup.install_cmd(lock, r.root, host_pip=host_pip, python=python), env=env)  # no timeout
    ok = r.run("venv", envsetup.check_cmd(r.root, host_pip=host_pip, python=python), env=env, check=False) == 0
    r.mark("lock_installed", ok)
    if not ok:
        r.set_status("venv", "fail", reason="pip check failed inside the venv")
        raise StageError("pip check failed inside the isolated venv")
    if register_kernel:
        rc = r.run("venv", envsetup.kernel_cmd(r.root, kernel_prefix), env=env, check=False)
        r.mark("kernel_registered", False)
        if rc != 0:
            raise StageError(f"kernel registration failed with exit code {rc}")
        r.run("venv", envsetup.kernel_verify_cmd(r.root, kernel_prefix), env=env)
        r.mark("kernel_registered", True)
    r.set_status("venv", "ok", kernel=envsetup.KERNEL_NAME if register_kernel else "not_registered")


def stage_build(r: Runner, *, run_llama=None, fetch=None) -> None:
    """Under the venv python: llama-server at the pinned commit, models at pinned revisions (SHA256-verified)."""
    from . import llama, models

    r.set_status("build", "running")
    layout.apply_env(r.root)
    existing = list(r.p["llama_bin"].glob("*/llama-server"))
    t0 = time.monotonic()
    server = (run_llama or (lambda: llama.install(r.root, run=lambda cmd: r.run("build", cmd))))()
    build = {"cuda_archs": server.parent.name.split("-sm")[-1].split("-py")[0], "runtime_tag": llama.runtime_tag(),
             "build_type": "Release", "build_seconds": round(time.monotonic() - t0, 1), "built_this_run": not existing}
    r.mark("llama_built", server.exists())
    r.event("build", "llama-server ready; fetching pinned models")
    t0 = time.monotonic()
    paths, records = (fetch or (lambda: models.fetch_pinned(r.p["hf_cache"])))()
    build["download_seconds"] = round(time.monotonic() - t0, 1)
    verified = len(records) == 2 and {x["role"] for x in records} == {"chat", "mmproj"} and all(x["sha256_verified"] for x in records)
    r.mark("models_verified", verified)
    if not server.exists() or not verified:
        raise StageError("CUDA server or complete model verification missing")
    (r.runs / "build.json").write_text(json.dumps({
        "server": str(server), "build": build, "records": records, "model_paths": [str(p) if p else None for p in paths]}))
    r.set_status("build", "ok", build_seconds=build["build_seconds"])


def write_login(root: Path, user: str, password: str) -> Path:
    """Login for the Gradio UI, readable only by this user. The path is shown, never the contents."""
    d = layout.paths(root)["root"] / "secrets"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    f = d / "gradio-login.txt"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"user: {user}\npassword: {password}\n")
    return f


def stage_trial(r: Runner, *, mock: bool = False, warm: bool = False, hold_min: float = 0,
                host: str | None = None, port: int | None = None, trial_id: str | None = None,
                full_features: bool = False, allow_search: bool = False, audio: Path | None = None,
                transcript: str | None = None, test_deadline: float | None = None,
                export_deadline: float | None = None) -> Path:
    """Launch the chat-only app, one capped smoke request, optional warm runs, then report + bundle."""
    import datetime

    if r.run_id and "trial" in r.status():
        r.event("trial-refusal", "trial already attempted; prior status preserved; use a fresh run ID and root")
        raise TrialRefused("trial already attempted in this run; use a fresh run ID and root")

    import gradio

    from . import launch, report, smoke, trial
    from . import control, features

    r.set_status("trial", "running", mock=mock)
    env = layout.apply_env(r.root, ctx=8192, web_search="off")
    checks = r.checks()
    model_paths, records = None, [{"role": "chat", "file": pins.MODELS["chat"]["file"],
                                   "revision": pins.MODELS["chat"]["revision"], "sha256_verified": False, "size_bytes": 0},
                                  {"role": "mmproj", "file": pins.MODELS["mmproj"]["file"],
                                   "revision": pins.MODELS["mmproj"]["revision"], "sha256_verified": False, "size_bytes": 0}]
    build = ({"cuda_archs": "none", "runtime_tag": "mock", "build_type": "none", "build_seconds": 0.0, "built_this_run": False}
             if mock else {"cuda_archs": "unavailable", "runtime_tag": "unavailable", "build_type": "unavailable"})
    server_version = "mock" if mock else "unavailable"
    app, failure = None, None
    metrics, gpu, feature_results = {}, {}, features.template()
    ui_snapshot_frozen = False
    if not mock and r.run_id:
        feature_results["ui_chat"] = features.ui_receipt(r.root / "ui-check.json", r.run_id)
    notes = ["report_exported is confirmed outside the Pod after read-back", "perf criteria need 10 warm runs"]
    if mock:
        notes.append("CPU rehearsal with the mock backend")
    def sample_saved(rows):
        metrics["warm_samples"] = list(rows)
        r._write(r.runs / "warm-samples.json", {"samples": rows})
    def work():
        control.check_work(test_deadline, r.root / "CANCEL")
    with control.finalization_guard(export_deadline) as arm_finalization:
        try:
            try:
                with control.work_guard(test_deadline, r.root / "CANCEL"):
                    if not mock:
                        info = json.loads((r.runs / "build.json").read_text())
                        model_paths = tuple(Path(p) if p else None for p in info["model_paths"])
                        records, build = info["records"], info["build"]
                        from . import llama
                        server_version = llama.server_version(Path(info["server"]))
                    user, pw = "qwen-" + secrets.token_hex(3), secrets.token_urlsafe(18)
                    login = write_login(r.root, user, pw)
                    os.environ["QMC_AUTH_USER"], os.environ["QMC_AUTH_PASSWORD"] = user, pw
                    app = launch.launch(mock=mock, host=host, port=port, model_paths=model_paths)
                    report.mark(checks, "app_launch", True)
                    report.mark(checks, "auth_enforced", launch.check_auth_enforced(app.cfg.server_port, host or "127.0.0.1"))
                    r.event("trial", f"UI up on port {app.cfg.server_port}; login file: {login}")
                    load = smoke.load_model(app)
                    work()
                    first = smoke.first_response(app)
                    gpu = smoke.gpu_memory_mib()
                    report.mark(checks, "first_response", (not first["error"]) and first["stream_deltas"] > 0)
                    metrics = {"load_s": load["load_s"], "first_max_tokens": first["max_tokens"], "first_total_s": first["total_s"],
                               "first_stream_deltas": first["stream_deltas"], "first_finish_reason": first["finish_reason"]}
                    if first["first_delta_s"] is not None:
                        metrics["first_first_delta_s"] = first["first_delta_s"]
                    if gpu.get("used_mib") is not None:
                        metrics["vram_used_mib"] = gpu["used_mib"]
                    if warm and checks["first_response"] == "pass":
                        prompts = ["日本の四季を50字で説明して。", "1から10までの和は？", "Pythonでリストを逆順にする方法は？",
                                   "富士山の高さは？", "挨拶を一言。", "Gitのcommitとpushの違いは？",
                                   "味噌汁の基本の作り方を3行で。", "TCPとUDPの違いを一言で。", "今日の気分を一言で。", "素数とは？"]
                        runs = smoke.warm_runs(app, prompts, on_sample=sample_saved, before_sample=work)
                        summary = smoke.summarize(runs)
                        crit = smoke.evaluate_criteria(runs)
                        report.mark(checks, "warm_runs", summary["ok_runs"] == len(prompts))
                        report.mark(checks, "perf_criteria", None if crit["verdict"] == "not_evaluated" else crit["verdict"] == "pass")
                        metrics.update({"warm_" + k: v for k, v in summary.items()})
                        metrics["warm_samples"] = runs
                    # UI observation belongs to the 8k baseline, before the explicit 32k reload.
                    deadline = time.monotonic() + hold_min * 60
                    while time.monotonic() < deadline and not (r.root / "ui-check.json").exists():
                        if (r.root / "CANCEL").exists() or (test_deadline is not None and time.time() >= test_deadline):
                            break
                        r.event("trial", f"8k UI observation window; {int(deadline - time.monotonic())}s left")
                        time.sleep(min(5, max(.1, deadline - time.monotonic())))
                    # Freeze the 8k observation before feature adapters may reload at 32k or rewrite files.
                    ui_snapshot = features.ui_receipt(r.root / "ui-check.json", r.run_id) if not mock and r.run_id else None
                    ui_snapshot_frozen = True
                    feature_results = features.template()
                    if ui_snapshot is not None:
                        feature_results["ui_chat"] = ui_snapshot
                    if full_features and not mock and checks["first_response"] == "pass" and checks["auth_enforced"] == "pass":
                        feature_results = features.run(app, r.root / "fixtures", allow_search=allow_search,
                                                       audio=audio, transcript=transcript, deadline=test_deadline,
                                                       cancel=r.root / "CANCEL",
                                                       on_result=lambda name,row: feature_results.update({name: row}))
                    elif mock:
                        for row in feature_results.values():
                            row["reason"] = "CPU mock is not real feature evidence"
                    if ui_snapshot is not None:
                        feature_results["ui_chat"] = ui_snapshot
            except control.WorkInterrupted:
                failure = "work_interrupted"
                if not ui_snapshot_frozen and not mock and r.run_id:
                    feature_results["ui_chat"] = features.ui_receipt(r.root / "ui-check.json", r.run_id)
                notes.append("work interrupted; diagnostic export within separate outer deadline")
                for row in feature_results.values():
                    if row["status"] == "skipped":
                        row["reason"] = "work deadline or cancellation; not completed"
            except (Exception, KeyboardInterrupt, SystemExit) as exc:
                failure = type(exc).__name__
                if not ui_snapshot_frozen and not mock and r.run_id:
                    feature_results["ui_chat"] = features.ui_receipt(r.root / "ui-check.json", r.run_id)
                notes.append("runtime failed: " + failure)
                for row in feature_results.values():
                    if row["status"] == "skipped":
                        row["reason"] = "work failed or interrupted; not completed"
            # Finalize even on work cancellation/error.
            arm_finalization()
            if not mock and build.get("runtime_tag") == "unavailable":
                notes.append("build metadata unavailable; build timings not measured")
            for name, row in feature_results.items():
                checks[name] = row["status"]
            rep = trial.build_report(
                trial_id=trial_id or r.run_id or "t" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M"), repo_root=Path(__file__).resolve().parents[1],
                lock_path=Path(__file__).resolve().parents[1] / "requirements-runpod.lock.txt", model_records=records, gpu=gpu,
                build=build, checks=checks, metrics=metrics, server_version=server_version, gradio_version=gradio.__version__,
                settings={"ctx": int(env["QMC_CHAT_CTX"]), "thinking": False, "web_search": "off", "asr_device": "cpu",
                          "asr_model": "small", "tts": False, "share": False, "chat_only": True},
                notes=notes, capture_runtime=failure is None)
            rep["execution_mode"] = "cpu_mock" if mock else "real_gpu"
            rep["features"] = feature_results
            path = report.write_report(rep, r.runs)
            bundle = r.runs / (path.stem + ".tar.gz")
            report.make_bundle([path], bundle)
            r._write(r.checks_path, checks)
            r.emit_artifact(bundle)
            failed = failure is not None or any(checks[name] == "fail" for name in ("auth_enforced", "first_response", "warm_runs"))
            r.set_status("trial", "fail" if failed else "ok", bundle=bundle.name,
                         feature_coverage="partial" if any(row["status"] == "skipped" for row in feature_results.values()) else "attempted")
            if failed:
                raise StageError("trial interrupted or acceptance failed; diagnostic bundle was emitted")
            return bundle
        finally:
            if app is not None:
                launch.stop_app(app)
