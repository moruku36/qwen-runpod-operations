#!/usr/bin/env python3
"""Entire control/export round trip with standard-library fakes. No network/GPU/model/UI.

Run only from a clean reviewed commit. Output is explicitly CPU simulation, never launch readiness.
python scripts/rehearse_retry.py --out cpu-evidence/rehearsal-FRESH
"""
import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from qmc_runpod import features, logchannel, report, retry, stages
from read_pod_logs import main as receive

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out",type=Path,required=True)
    a=ap.parse_args();out=a.out.resolve()
    if out.exists():
        raise ValueError("fresh rehearsal destination required")
    out.mkdir(parents=True)
    audio=out/"mock-audio.wav";audio.write_bytes(b"CPU placeholder; no speech, no ASR inference")
    transcript=out/"mock-transcript.txt";transcript.write_text("CPU placeholder",encoding="utf-8")
    run_id="cpu-rehearsal-"+dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    mirror=out/"mirror.log"
    p={"run_id":run_id,"commit":subprocess.check_output(["git","-C",str(REPO),"rev-parse","HEAD"],text=True).strip(),
       "root":str(out/"fake-pod"),"stages":list(retry.STAGES),"allocated_at":"2026-10-03T01:00:00+00:00",
       "test_deadline":"2026-10-03T02:40:00+00:00","export_deadline":"2026-10-03T02:50:00+00:00","stop_deadline":"2026-10-03T03:00:00+00:00",
       "approval_date":"2026-10-03","cap_usd":10,"quote_total_usd":4.49,"export_destination":str(out/"off-pod"),
       "lock_sha256":retry.digest(REPO/"requirements-runpod.lock.txt"),
       "feature_options":{"allow_public_search":True,"audio":str(audio),"audio_sha256":retry.digest(audio),
                          "transcript_file":str(transcript),"transcript_sha256":retry.digest(transcript),"licensed_audio":True}}
    # Dates, quote and licensed_audio=True are validation fakes here, not actual launch facts.
    # Packet is never persisted as a launch packet. The command callback cannot run real stages.
    def fake(cmd,**kwargs):
        name=cmd[2];r=stages.Runner(Path(p["root"]),mirror=mirror,run_id=run_id)
        if name=="selftest":stages.stage_selftest(r)
        elif name=="trial":
            rep={"trial_id":run_id,"execution_mode":"cpu_mock","checks":report.checks_template(),"features":features.template(),
                 "notes":["CPU control simulation; no real feature or application evaluation"]}
            path=report.write_report(rep,r.runs);bundle=path.with_suffix(".tar.gz")
            report.make_bundle([path],bundle);r.emit_artifact(bundle);r.set_status(name,"ok")
        else:r.set_status(name,"ok",mock=True)
        return 0
    clock=lambda:dt.datetime(2026,10,3,1,5,tzinfo=dt.timezone.utc)
    first=retry.execute(p,REPO,clock=clock,command=fake)
    if first!=2:raise AssertionError("expected external receipt gate")
    class LocalLog:
        last_log_read={"state":"simulation","events":0}
        def read_logs(self,*args,**kwargs):
            return [{"source":"container","line":line} for line in mirror.read_text(encoding="utf-8").splitlines()]
    probe=Path(p["root"])/"runs/selftest.txt"
    receiver=receive(["mock123","--run-id",run_id,"--expect-event","selftest:hello from the pod",
                      "--expect-file","selftest.txt="+retry.digest(probe),"--out",str(out/"off-pod")],api=LocalLog())
    if receiver:raise AssertionError("receiver failed")
    shutil.copyfile(out/"off-pod/receipt.json",Path(p["root"])/"selftest-receipt.json")
    resumed=retry.execute(p,REPO,clock=clock,command=fake)
    if resumed:raise AssertionError("mock resume failed")
    bundles=list((Path(p["root"])/"runs").glob("*.tar.gz"));bundle=bundles[0]
    receiver=receive(["mock123","--run-id",run_id,"--expect-event","trial:state=ok",
                      "--expect-file",bundle.name+"="+retry.digest(bundle),"--out",str(out/"off-pod")],api=LocalLog())
    exported=out/"off-pod"/bundle.name
    problems=report.verify_bundle(exported,expected_run_id=run_id)
    if receiver or problems:raise AssertionError("bundle retrieval failed")
    corrupted=out/"off-pod/corrupt.tar.gz";corrupted.write_bytes(b"deliberately corrupted")
    corrupt_problems=report.verify_bundle(corrupted)
    if not corrupt_problems:raise AssertionError("corruption undetected")
    evidence={"execution_mode":"cpu_mock","run_id":run_id,"commit":p["commit"],"lock_sha256":p["lock_sha256"],
              "selftest_gate_exit":first,"resume_exit":resumed,"receiver_exit":receiver,"bundle_problems":problems,
              "corrupt_bundle_detected":bool(corrupt_problems),"bundle_sha256":retry.digest(exported),
              "bootstrap_invocations":2,"real_gpu_or_features_executed":False,"go_for_paid_launch":False}
    (out/"rehearsal.json").write_text(json.dumps(evidence,indent=2),encoding="utf-8")
    print(json.dumps(evidence,indent=2));return 0

if __name__=="__main__":sys.exit(main())
