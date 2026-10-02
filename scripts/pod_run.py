#!/usr/bin/env python3
"""Run setup/trial stages on the Pod without a notebook. Standard library only at start-up.

  python scripts/pod_run.py selftest           log-channel check (event + tiny artifact)
  python scripts/pod_run.py net                reach GitHub, PyPI and both pinned model hosts; fail = stop the Pod
  python scripts/pod_run.py source             upstream at the pinned commit
  python scripts/pod_run.py venv               isolated venv + hash-locked install + pip check (+ kernel)
  python scripts/pod_run.py build              llama-server at the pinned commit + SHA256-verified models (venv python)
  python scripts/pod_run.py trial [--warm] [--hold-min N]   launch, one capped request, report + bundle (venv python)
  python scripts/pod_run.py all                net -> source -> venv -> build, stopping at the first failure
  python scripts/pod_run.py status             progress, checks that are not "skipped", log tail
  python scripts/pod_run.py emit <file>        send a file to the log stream (base64 chunks + sha256)

Add --bg to run detached (the command returns at once; read progress with `status` or the Pod logs API).
There is no timeout on pip or the CUDA build; the owner decides when to stop.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from qmc_runpod import envsetup, layout, stages

for p in (layout.paths()["source"] / "qwen-multimodal-colab" / "src",):
    if p.is_dir():
        sys.path.insert(0, str(p))


def _self(argv):
    return [sys.executable, str(Path(__file__).resolve()), *argv]


def _reexec_in_venv(root):
    py = envsetup.venv_python(root)
    if not py.exists():
        sys.exit("the isolated venv does not exist yet: run `python scripts/pod_run.py venv` first")
    if not envsetup.running_in_venv(root):
        os.execv(str(py), [str(py), str(Path(__file__).resolve()), *sys.argv[1:]])


def cmd_status(r):
    st = r.status()
    for name in stages.STAGES:
        if name in st:
            d = st[name]
            extra = {k: v for k, v in d.items() if k not in ("started", "state")}
            print(f"{name:9s} {d.get('state'):8s} {json.dumps(extra, ensure_ascii=False)}")
    checks = {k: v for k, v in r.checks().items() if v != "skipped"}
    print("checks:", json.dumps(checks))
    logs = sorted(r.logs.glob("*.log"), key=lambda f: f.stat().st_mtime)
    if logs:
        print(f"-- tail of {logs[-1].name}")
        print("".join(logs[-1].read_text(errors="replace").splitlines(True)[-8:]), end="")


def cmd_all(r, args):
    for st in ("net", "source", "venv", "build"):
        rc = subprocess.run(_self([st, "--root", str(r.root)]), check=False).returncode
        if rc != 0:
            r.event("all", f"stopped at {st} (exit {rc})")
            return rc
    r.event("all", "net, source, venv and build finished")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=[*stages.STAGES, "all", "status", "emit"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--features", action="store_true")
    ap.add_argument("--allow-public-search", action="store_true")
    ap.add_argument("--audio", type=Path)
    ap.add_argument("--transcript-file", type=Path)
    ap.add_argument("--test-deadline", type=float)
    ap.add_argument("--bg", action="store_true")
    ap.add_argument("--warm", action="store_true")
    ap.add_argument("--hold-min", type=float, default=0)
    ap.add_argument("--mock", action="store_true", help="CPU rehearsal with the mock backend (no model, no GPU)")
    a = ap.parse_args(argv)
    r = stages.Runner(a.root, run_id=a.run_id)
    if a.bg:
        rest = [x for x in (argv if argv is not None else sys.argv[1:]) if x != "--bg"]
        logf = open(r.logs / f"{a.stage}.bg.log", "a")  # noqa: SIM115
        proc = subprocess.Popen(_self(rest), stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        r.event(a.stage, f"started in background pid={proc.pid}")
        print(f"started {a.stage} in background, pid {proc.pid}")
        return 0
    if a.stage == "status":
        cmd_status(r)
        return 0
    if a.stage == "emit":
        if not a.file:
            sys.exit("emit needs a file")
        r.emit_artifact(Path(a.file))
        return 0
    if a.stage == "all":
        return cmd_all(r, a)
    if a.stage in stages.VENV_STAGES and not a.mock:
        _reexec_in_venv(r.root)
    try:
        if a.stage == "selftest":
            stages.stage_selftest(r)
        elif a.stage == "net":
            return 0 if stages.stage_net(r) else 3
        elif a.stage == "source":
            stages.stage_source(r, ops_root=REPO)
        elif a.stage == "venv":
            stages.stage_venv(r, REPO / "requirements-runpod.lock.txt")
        elif a.stage == "build":
            stages.stage_build(r)
        elif a.stage == "trial":
            stages.stage_trial(r, mock=a.mock, warm=a.warm, hold_min=a.hold_min,
                               full_features=a.features, allow_search=a.allow_public_search, audio=a.audio,
                               transcript=a.transcript_file.read_text(encoding="utf-8").strip() if a.transcript_file else None,
                               test_deadline=a.test_deadline)
    except Exception as exc:  # noqa: BLE001
        r.set_status(a.stage, "fail", error=type(exc).__name__, message=str(exc)[:200])
        print(f"{a.stage} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    t0 = time.time()
    sys.exit(main())
