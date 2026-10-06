"""CPU-only CLI import regressions: actual qmc imports in fresh interpreters."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("stage", ["build", "trial"])
@pytest.mark.parametrize("root_mode", ["absolute", "relative", "default"])
@pytest.mark.parametrize("default_exists", [False, True])
def test_stage_imports_qmc_from_runner_root(tmp_path, stage, root_mode, default_exists):
    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    default = tmp_path / "environment-root"
    chosen = default if root_mode == "default" else tmp_path / "explicit-root"
    if default_exists and chosen != default:
        wrong = default / "source/qwen-multimodal-colab/src/qmc"
        wrong.mkdir(parents=True)
        (wrong / "__init__.py").write_text("raise AssertionError('imported wrong workspace')\n")
    package = chosen / "source/qwen-multimodal-colab/src/qmc"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("ROOT_MARKER = 'selected-runner-root'\n")
    args = [stage]
    if root_mode != "default":
        args += ["--root", str(chosen if root_mode == "absolute" else Path("..") / chosen.name)]
    # Replace only costly stage bodies and venv re-execution. The script's real
    # argparse, Runner, path setup, dispatch and Python import machinery run.
    probe = '''
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("pod_run_probe", sys.argv[1])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
def stage(r, **kwargs):
    import qmc
    assert qmc.ROOT_MARKER == "selected-runner-root"
    expected = r.p["source"] / "qwen-multimodal-colab/src/qmc/__init__.py"
    assert Path(qmc.__file__).resolve() == expected.resolve()
    assert r.root.resolve() == Path(sys.argv[3]).resolve()
    print("QMC_IMPORT_OK", qmc.__file__)
cli.stages.stage_build = stage
cli.stages.stage_trial = stage
cli._reexec_in_venv = lambda root: None
raise SystemExit(cli.main(json.loads(sys.argv[2])))
'''
    env = {k: v for k, v in os.environ.items() if not k.startswith(("QMC_", "PYTHON"))}
    env.update(QMC_WORKSPACE=str(default), QMC_LOG_MIRROR="off")
    result = subprocess.run([sys.executable, "-I", "-c", probe,
                             str(REPO / "scripts/pod_run.py"), json.dumps(args), str(chosen)],
                            cwd=cwd, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "QMC_IMPORT_OK" in result.stdout


def test_selftest_without_upstream_still_works(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("QMC_", "PYTHON"))}
    env.update(QMC_WORKSPACE=str(tmp_path / "unused"), QMC_LOG_MIRROR="off")
    root = tmp_path / "fresh"
    result = subprocess.run([sys.executable, "-I", str(REPO / "scripts/pod_run.py"),
                             "selftest", "--root", str(root)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((root / "runs/status.json").read_text())["selftest"]["state"] == "ok"


def test_real_build_reaches_upstream_pin_check_before_gpu_work(tmp_path):
    """Exercise the real build -> llama.install -> qmc.colab import boundary."""
    root = tmp_path / "chosen"
    package = root / "source/qwen-multimodal-colab/src/qmc"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    # A deliberately wrong pin stops immediately after the actual import, before
    # CUDA discovery, subprocesses, downloads or any paid operation can occur.
    (package / "colab.py").write_text("LLAMA_CPP_COMMIT = 'cpu-import-probe'\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("QMC_", "PYTHON"))}
    env.update(QMC_WORKSPACE=str(tmp_path / "different-default"), QMC_LOG_MIRROR="off")
    result = subprocess.run([sys.executable, "-I", str(REPO / "scripts/pod_run.py"),
                             "build", "--mock", "--root", str(root)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert "upstream llama.cpp pin changed" in result.stderr
    status = json.loads((root / "runs/status.json").read_text())["build"]
    assert status["state"] == "fail"
    assert status["error"] == "RuntimeError"
    assert not (root / "llama.cpp").exists()
