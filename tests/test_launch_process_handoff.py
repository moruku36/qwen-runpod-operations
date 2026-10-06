"""CPU process-boundary contract against the real pinned upstream backend.

The only application replacement is Gradio UI. The executable and tiny model
files are explicit CPU fixtures, not model/GPU acceptance evidence. Build and
trial run in independent interpreters with no shared Python environment state.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("QMC_UPSTREAM_DIR", "/home/user/moruku36/qwen-multimodal-colab"))
requires_upstream = pytest.mark.skipif(
    not (UPSTREAM / "src/qmc").is_dir(), reason="real pinned upstream checkout required"
)

SERVER = r'''#!PYTHON
import json, os, pathlib, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
if '--version' in sys.argv:
    print('CPU fixture executable, no GPU');sys.exit(0)
args=sys.argv[1:]
def arg(name):return args[args.index(name)+1]
safe=list(args)
if '--api-key' in safe:safe[safe.index('--api-key')+1]='<redacted>'
pathlib.Path(os.environ['CPU_MARKER']).write_text(json.dumps({
    'server':sys.argv[0], 'argv':safe, 'LD_LIBRARY_PATH':os.environ.get('LD_LIBRARY_PATH'),
    'PATH':os.environ.get('PATH'), 'VIRTUAL_ENV':os.environ.get('VIRTUAL_ENV')}))
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(200 if self.path=='/health' else 404);self.end_headers();self.wfile.write(b'{}')
    def do_POST(self):
        if self.headers.get('Authorization')!='Bearer '+arg('--api-key'):
            self.send_response(401);self.end_headers();return
        self.rfile.read(int(self.headers.get('Content-Length',0)))
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"CPU fixture"},"finish_reason":null}]}\n\n')
        self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
HTTPServer((arg('--host'),int(arg('--port'))),Handler).serve_forever()
'''

NVIDIA = '''#!/bin/sh
case "$*" in
 *--query-gpu=name,memory.used,memory.total,driver_version*) echo 'NVIDIA A100-SXM4-80GB, 1, 81920, CPU-fixture';;
 *--query-gpu=name,memory.total*) echo 'NVIDIA A100-SXM4-80GB, 81920';;
 *--query-gpu=memory.used,memory.total*) echo '1, 81920';;
 *--query-gpu=compute_cap*) echo '8.0';;
 *) echo 'CPU fixture CUDA Version: 12.4';;
esac
'''


def _executable(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(0o755)


def _worker(phase: str, workspace: Path, upstream: Path, mode: str) -> None:
    import importlib.util
    from types import SimpleNamespace
    from unittest.mock import patch

    sys.path[:0] = [str(ROOT), str(upstream / "src")]
    from qmc_runpod import envsetup, handoff, layout, llama, models, pins, stages, toolchain
    from qmc import colab

    runner = stages.Runner(workspace, mirror=False, run_id="cpu-process-" + mode)
    if phase == "build":
        server = llama.bin_dir("80", workspace) / "llama-server"
        _executable(server, SERVER.replace("#!PYTHON", "#!" + sys.executable))
        specs = {}
        for role, spec in pins.MODELS.items():
            model = workspace / "hf-cache" / spec["file"]
            model.parent.mkdir(parents=True, exist_ok=True)
            model.write_bytes(("synthetic CPU fixture " + role).encode())
            specs[role] = {**spec, "size_bytes": model.stat().st_size,
                           "sha256": hashlib.sha256(model.read_bytes()).hexdigest()}
        original_which = llama.shutil.which
        # Real install's cached-binary branch sets the process-local variable.
        # These mocks only replace the costly build prerequisites and downloads.
        with patch.object(toolchain, "assert_ready", lambda _: envsetup.activation_env(workspace)), \
             patch.object(colab, "detect_cuda_arch", lambda: "80"), \
             patch.object(llama.shutil, "which", lambda name: "/cpu/unused-nvcc" if name == "nvcc" else original_which(name)):
            stages.stage_build(runner, fetch=lambda: models.fetch_pinned(
                workspace / "hf-cache", specs=specs,
                download=lambda repo, filename, revision, cache: workspace / "hf-cache" / filename))
        print(json.dumps({"build_child_bin_env": os.environ["QMC_LLAMA_BIN_DIR"],
                          "handoff": handoff.read_build(workspace)}))
        return

    # Neither the compiler/build child nor an activated shell supplies this.
    inherited_bin = os.environ.get("QMC_LLAMA_BIN_DIR")
    os.environ.update(envsetup.activation_env(workspace))
    layout.apply_env(workspace)
    from qmc import config, gpu_manager
    from qmc.backends.llama_server import find_llama_server
    from qmc_runpod import launch

    before = config.load_config()
    fallback_before = find_llama_server(before.llama_bin_dir)
    sys.modules["gradio"] = SimpleNamespace(__version__="cpu-fixture")
    from qmc_runpod import chat_ui
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    created = []
    class Demo:
        def __init__(self, app):
            self.app = app
            self.http = None
        def queue(self, **kwargs):
            return self
        def launch(self, **kwargs):
            # Only Gradio UI is replaced. Authentication check makes an actual
            # local HTTP request and backend auth stays entirely real.
            class UIHandler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_GET(self):
                    self.send_response(401)
                    self.end_headers()
            self.http = HTTPServer(("127.0.0.1", kwargs["server_port"]), UIHandler)
            self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
            self.thread.start()
            return self
        def close(self):
            if self.http:
                self.http.shutdown()
                self.http.server_close()
                self.thread.join(timeout=3)
    def ui(app):
        created.append(app)
        return Demo(app)

    with patch.object(chat_ui, "build_ui", ui):
        bundle = stages.stage_trial(runner, evaluation_profile="chat_only")
    app = created[0]
    backend = app.manager.get("chat")
    report = json.loads(next(runner.runs.glob("report-*.json")).read_text())
    print(json.dumps({
        "inherited_bin": inherited_bin,
        "pre_handoff_resolver": str(fallback_before) if fallback_before else None,
        "cfg_bin": str(app.cfg.llama_bin_dir),
        "resolved_server": str(find_llama_server(app.cfg.llama_bin_dir)),
        "model_paths": [str(p) for p in backend._paths],
        "data_dir": str(app.cfg.data_dir), "db": str(app.cfg.local_db_path),
        "hf_cache": str(app.cfg.hf_cache_dir),
        "ctx": backend.ctx_size, "fit_mib": backend.fit_target,
        "gpu_name": app.gpu.name, "profile": app.profile.key,
        "torch_present": importlib.util.find_spec("torch") is not None,
        "host": app.cfg.chat.host, "port": app.cfg.chat.port, "ui_port": app.cfg.server_port,
        "auth_present": bool(app.cfg.auth_user and app.cfg.auth_password),
        "share": app.cfg.share, "chat_only": app.cfg.chat_only,
        "backend_loaded_after_cleanup": backend.is_loaded,
        "report_checks": report["checks"], "report_settings": report["settings"],
        "bundle_exists": bundle.is_file(),
        "stub_launch": json.loads(Path(os.environ["CPU_MARKER"]).read_text()),
        "pkill_called": Path(os.environ["CPU_PKILL_MARKER"]).exists(),
    }))


def _run_worker(phase: str, workspace: Path, mode: str, env: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--worker", phase,
         str(workspace), str(UPSTREAM), mode],
        env=env, capture_output=True, text=True, timeout=35,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    return json.loads(proc.stdout.splitlines()[-1])


@requires_upstream
@pytest.mark.parametrize("mode", ["clean_env", "stale_env", "stale_path", "stale_profile"])
def test_separate_build_and_trial_use_exact_artifacts_and_real_backend(tmp_path, mode):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bindir = tmp_path / "fixture-bin"
    _executable(bindir / "nvidia-smi", NVIDIA)
    _executable(bindir / "pkill", '#!/bin/sh\necho called > "$CPU_PKILL_MARKER"\nexit 1\n')
    stale = tmp_path / "stale" / "llama-server"
    _executable(stale, '#!/bin/sh\necho WRONG_EXECUTABLE >&2\nexit 91\n')
    env = {
        "PATH": os.pathsep.join([str(bindir), str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
        "HOME": str(tmp_path / "home"), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
        "LD_LIBRARY_PATH": "/cpu-fixture/existing-runtime-libs",
        "CPU_MARKER": str(tmp_path / "server-launch.json"),
        "CPU_PKILL_MARKER": str(tmp_path / "pkill-called"),
    }
    built = _run_worker("build", workspace, mode, env)
    if mode == "stale_env":
        env["QMC_LLAMA_BIN_DIR"] = str(stale.parent)
    elif mode == "stale_path":
        env["PATH"] = str(stale.parent) + os.pathsep + env["PATH"]
    elif mode == "stale_profile":
        env["QMC_GPU_PROFILE"] = "cpu"
    trial = _run_worker("trial", workspace, mode, env)
    handoff = built["handoff"]
    server = Path(handoff["server"])
    assert trial["inherited_bin"] == (str(stale.parent) if mode == "stale_env" else None)
    assert trial["pre_handoff_resolver"] == (None if mode in {"clean_env", "stale_profile"} else str(stale))
    assert trial["cfg_bin"] == str(server.parent)
    assert trial["resolved_server"] == str(server)
    assert trial["stub_launch"]["server"] == str(server)
    assert trial["model_paths"] == handoff["model_paths"]
    command = trial["stub_launch"]["argv"]
    for flag, value in (("-m", handoff["model_paths"][0]), ("--mmproj", handoff["model_paths"][1]),
                        ("-c", "8192"), ("--host", "127.0.0.1"), ("--port", "8012"), ("--fit-target", "2048")):
        assert command[command.index(flag) + 1] == value
    assert trial["host"] == "127.0.0.1" and trial["port"] == 8012 and trial["ui_port"] == 7860
    assert trial["data_dir"] == str(workspace / "data")
    assert trial["db"] == str(workspace / "db/history.db")
    assert trial["hf_cache"] == str(workspace / "hf-cache")
    assert trial["ctx"] == trial["report_settings"]["ctx"] == 8192
    assert trial["profile"] == "a100_80" and trial["fit_mib"] == 2048
    assert trial["auth_present"] and trial["chat_only"] and not trial["share"]
    assert trial["stub_launch"]["LD_LIBRARY_PATH"] == str(server.parent) + ":/cpu-fixture/existing-runtime-libs"
    assert trial["stub_launch"]["PATH"].split(os.pathsep)[0] == str(workspace / "venv/bin")
    assert trial["stub_launch"]["VIRTUAL_ENV"] == str(workspace / "venv")
    assert not trial["pkill_called"] and not trial["backend_loaded_after_cleanup"]
    assert trial["report_checks"]["first_response"] == "pass" and trial["bundle_exists"]


@requires_upstream
def test_no_torch_gpu_detection_still_uses_nvidia_smi(tmp_path):
    bindir = tmp_path / "bin"
    _executable(bindir / "nvidia-smi", NVIDIA)
    code = textwrap.dedent('''
        import importlib.abc, json, sys
        class NoTorch(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'torch' or fullname.startswith('torch.'):
                    raise ModuleNotFoundError('torch deliberately absent in isolated venv fixture')
        sys.meta_path.insert(0, NoTorch())
        from qmc.gpu_manager import detect_gpu, select_profile
        info = detect_gpu()
        profile = select_profile(info)
        print(json.dumps({'name':info.name,'gib':info.total_gib,'profile':profile.key,'fit':profile.chat_fit_target_mib}))
    ''')
    env = {"PATH": str(bindir) + ":/usr/bin:/bin", "PYTHONPATH": str(UPSTREAM / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True, timeout=10)
    row = json.loads(proc.stdout)
    assert row == {"name": "NVIDIA A100-SXM4-80GB", "gib": 80.0, "profile": "a100_80", "fit": 2048}


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--worker":
    _worker(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5])
