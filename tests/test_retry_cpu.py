"""Standard-library-only CPython 3.11 suite. No external services or model bytes."""
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from qmc_runpod import features, logchannel, report, retry, smoke, stages, envsetup, podapi
import read_pod_logs


class CPU(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def rep(self):
        return {"trial_id": "cpu-run-123", "execution_mode": "cpu_mock", "checks": report.checks_template(),
                "features": features.template()}
    def packet(self):
        audio=self.p/"mock-audio.wav";audio.write_bytes(b"CPU test placeholder, not speech")
        transcript=self.p/"mock-transcript.txt";transcript.write_text("Synthetic test transcript")
        return {"run_id": "cpu-run-123", "commit": "a"*40, "root": str(self.p / "ws"),
                "stages": list(retry.STAGES), "allocated_at": "2026-10-03T01:00:00+00:00",
                "test_deadline": "2026-10-03T02:40:00+00:00", "stop_deadline": "2026-10-03T03:00:00+00:00",
                "approval_date": "2026-10-03", "cap_usd": 10, "quote_total_usd": 4.49,
                "export_destination": "owner-private-destination", "lock_sha256": retry.digest(ROOT/"requirements-runpod.lock.txt"),
                "feature_options": {"allow_public_search":True,"audio":str(audio),"audio_sha256":retry.digest(audio),
                                    "transcript_file":str(transcript),"transcript_sha256":retry.digest(transcript),"licensed_audio":True}}
    def test_missing_speech_fixture_blocks_packet(self):
        p=self.packet();p["feature_options"]["audio"] = str(self.p/"missing.wav")
        with self.assertRaises(ValueError):self.validate(p)
    def validate(self,p):
        with patch.object(subprocess,"check_output",side_effect=["a"*40," "]):
            retry.validate_packet(p,ROOT,now=dt.datetime(2026,10,3,1,5,tzinfo=dt.timezone.utc))
    def test_packet_valid(self):
        self.validate(self.packet())
    def test_wrong_day(self):
        p=self.packet();p["allocated_at"]="2026-10-02T01:00:00+00:00"
        with self.assertRaises(ValueError):self.validate(p)
    def test_over_cap(self):
        p=self.packet();p["quote_total_usd"]=11
        with self.assertRaises(ValueError):self.validate(p)
    def test_nan_cap(self):
        p=self.packet();p["cap_usd"]=float("nan")
        with self.assertRaises(ValueError):self.validate(p)
    def test_over_two_hours(self):
        p=self.packet();p["stop_deadline"]="2026-10-03T03:01:00+00:00"
        with self.assertRaises(ValueError):self.validate(p)
    def test_no_naive_deadline(self):
        p=self.packet();p["test_deadline"]="2026-10-03T02:00:00"
        with self.assertRaises(ValueError):self.validate(p)
    def test_lock_tamper(self):
        p=self.packet();p["lock_sha256"]="0"*64
        with self.assertRaises(ValueError):self.validate(p)
    def test_dirty_execution_commit(self):
        with patch.object(subprocess,"check_output",side_effect=["a"*40," M file"]):
            with self.assertRaises(ValueError):retry.validate_packet(self.packet(),ROOT)
    def test_stage_omission(self):
        p=self.packet();p["stages"]=["trial"]
        with self.assertRaises(ValueError):self.validate(p)
    def test_local_cancellation_before_start(self):
        cancel=self.p/"CANCEL";cancel.touch()
        self.assertEqual(retry.bounded_command(["nonexistent"],seconds=1,cancel_file=cancel,output=self.p/"out"),124)
    def test_silent_process_deadline(self):
        started=time.monotonic()
        rc=retry.bounded_command([sys.executable,"-c","import time;time.sleep(20)"],seconds=.2,cancel_file=self.p/"CANCEL",output=self.p/"out")
        self.assertEqual(rc,124);self.assertLess(time.monotonic()-started,5)
    def test_local_command_failure(self):
        self.assertEqual(retry.bounded_command([sys.executable,"-c","raise SystemExit(7)"],seconds=2,cancel_file=self.p/"CANCEL",output=self.p/"out"),7)
    def fake_stage(self,cmd,**kwargs):
        stage=cmd[2];root=Path(cmd[cmd.index("--root")+1]);r=stages.Runner(root,mirror=None,run_id="cpu-run-123")
        if stage=="selftest":stages.stage_selftest(r)
        else:r.set_status(stage,"ok")
        return 0
    def test_checkpoint_waits_for_external_receipt_then_resumes(self):
        p=self.packet();clock=lambda:dt.datetime(2026,10,3,1,5,tzinfo=dt.timezone.utc)
        with patch.object(retry,"validate_packet"):
            self.assertEqual(retry.execute(p,ROOT,clock=clock,command=self.fake_stage),2)
            root=Path(p["root"])
            receipt={"run_id":p["run_id"],"verified":True,"expected_events":["selftest:hello from the pod"],
                     "expected_files":["selftest.txt="+retry.digest(root/"runs/selftest.txt")]}
            (root/"selftest-receipt.json").write_text(json.dumps(receipt))
            self.assertEqual(retry.execute(p,ROOT,clock=clock,command=self.fake_stage),0)
            self.assertEqual(json.loads((root/"retry-checkpoint.json").read_text())["completed"],list(retry.STAGES))
    def test_failed_stage_blocks_following_stages(self):
        p=self.packet();calls=[]
        def fail(cmd,**kw):calls.append(cmd[2]);return 9
        with patch.object(retry,"validate_packet"):
            self.assertEqual(retry.execute(p,ROOT,command=fail),9)
        self.assertEqual(calls,["selftest"])
    def test_stale_root_rejected(self):
        p=self.packet();Path(p["root"]).mkdir();(Path(p["root"])/"old").touch()
        with patch.object(retry,"validate_packet"):
            with self.assertRaises(ValueError):retry.execute(p,ROOT)
    def test_fresh_run_filters_old_events_and_artifacts(self):
        f=self.p/"selftest.txt";f.write_bytes(b"proof")
        lines=logchannel.encode_artifact(f,run_id="old-run-123")+logchannel.encode_artifact(f,run_id="new-run-123")
        lines += [logchannel.event_line("selftest","hello",run_id="new-run-123")]
        self.assertTrue(logchannel.decode_artifacts(lines,run_id="new-run-123")[f.name]["ok"])
        self.assertEqual(logchannel.parse_events(lines,run_id="absent-run"),[])
    def test_conflicting_chunks_fail(self):
        f=self.p/"selftest.txt";f.write_bytes(b"proof")
        lines=logchannel.encode_artifact(f)
        self.assertFalse(logchannel.decode_artifacts(lines+[lines[0].rsplit("|",1)[0]+"|AAAA"])[f.name]["ok"])
    def test_path_escape_artifact_refused(self):
        f=self.p/"selftest.txt";f.write_bytes(b"proof")
        self.assertEqual(logchannel.decode_artifacts(logchannel.encode_artifact(f,"../escape")),{})
    def test_empty_logs_not_success(self):
        api=SimpleNamespace(read_logs=lambda *a,**kw:[])
        with patch("sys.stdout",new=io.StringIO()):
            self.assertEqual(read_pod_logs.main(["abc123","--out",str(self.p)],api=api),1)
    def test_strict_receiver_receipt(self):
        f=self.p/"selftest.txt";f.write_bytes(b"proof")
        lines=[logchannel.event_line("selftest","hello",run_id="cpu-run-123"),*logchannel.encode_artifact(f,run_id="cpu-run-123")]
        api=SimpleNamespace(read_logs=lambda *a,**kw:[{"source":"container","line":x} for x in lines])
        with patch("sys.stdout",new=io.StringIO()):
            rc=read_pod_logs.main(["abc123","--run-id","cpu-run-123","--expect-event","selftest:hello","--expect-file",f.name+"="+retry.digest(f),"--out",str(self.p/"out")],api=api)
        self.assertEqual(rc,0);self.assertTrue(json.loads((self.p/"out/receipt.json").read_text())["verified"])
    def test_partial_logs_failure_observed(self):
        api=podapi.PodApi(session=SimpleNamespace(request=lambda *a,**kw: (_ for _ in ()).throw(ConnectionError())))
        self.assertEqual(api.read_logs("abc123"),[])
        self.assertEqual(api.last_log_read["state"],"empty");self.assertEqual(api.last_log_read["error"],"ConnectionError")
    def test_bundle_roundtrip_and_required_checks(self):
        f=report.write_report(self.rep(),self.p);bundle=self.p/"bundle.tar.gz";report.make_bundle([f],bundle)
        self.assertEqual(report.verify_bundle(bundle,expected_run_id="cpu-run-123"),[])
        self.assertTrue(report.verify_bundle(bundle,expected_run_id="stale-run"))
        self.assertTrue(report.verify_bundle(bundle,required_checks=("asr",)))
    def test_corrupt_bundle_rejected(self):
        b=self.p/"bundle.tar.gz";b.write_bytes(b"corrupt");self.assertTrue(report.verify_bundle(b))
    def test_bundle_byte_tamper(self):
        f=report.write_report(self.rep(),self.p);b=self.p/"bundle.tar.gz";manifest=report.make_bundle([f],b)
        with tarfile.open(b,"w:gz") as tar:
            for name,data in ((f.name,b"{}"),("MANIFEST.json",json.dumps(manifest).encode())):
                info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
        self.assertTrue(report.verify_bundle(b))
    def test_duplicate_tar_member_refused(self):
        b=self.p/"bundle.tar.gz"
        with tarfile.open(b,"w:gz") as tar:
            for _ in range(2):
                info=tarfile.TarInfo("MANIFEST.json");info.size=2;tar.addfile(info,io.BytesIO(b"{}"))
        self.assertTrue(report.verify_bundle(b))
    def test_warm_samples_keep_error_and_finish_reason(self):
        r=self.rep();r["metrics"]={"warm_samples":[{"phase":"warm","first_delta_s":None,"total_s":1,"stream_deltas":0,"finish_reason":"unknown","max_tokens":256,"error":True}]}
        self.assertEqual(report.validate(r),[])
        r["metrics"]["warm_samples"][0]["answer"]="private data"
        self.assertTrue(report.validate(r))
    def test_unknown_finish_reason_cannot_pass_performance(self):
        runs=[{"phase":"warm","first_delta_s":.1,"total_s":1,"stream_deltas":5,"finish_reason":"unknown","max_tokens":256,"error":False} for _ in range(10)]
        self.assertEqual(smoke.evaluate_criteria(runs)["verdict"],"not_evaluated")
        for r in runs:r["finish_reason"]="stop"
        self.assertEqual(smoke.evaluate_criteria(runs)["verdict"],"pass")
    def test_vision_fixture_deterministic(self):
        sha=features.make_vision_fixture(self.p/"vision.png")
        self.assertEqual(sha,features.make_vision_fixture(self.p/"vision2.png"))
        self.assertTrue((self.p/"vision.png").read_bytes().startswith(b"\x89PNG"))
    def test_ui_receipt_stale_fails_missing_skips(self):
        path=self.p/"ui.json"
        self.assertEqual(features.ui_receipt(path,"new-run-123")["status"],"skipped")
        row={"run_id":"old-run-123","observer":"owner","input_to_display":True,"language":"ja","ctx":8192}
        path.write_text(json.dumps(row))
        self.assertEqual(features.ui_receipt(path,"new-run-123")["status"],"fail")
        row["run_id"]="new-run-123";path.write_text(json.dumps(row))
        self.assertEqual(features.ui_receipt(path,"new-run-123")["status"],"pass")
    def test_deadline_skips_real_features(self):
        rows=features.run(None,self.p,deadline=0)
        self.assertEqual({r["status"] for r in rows.values()},{"skipped"})
        self.assertIn("licensed",rows["asr"]["reason"])
    def test_context_short_prompt_does_not_pass(self):
        model=SimpleNamespace(ctx_size=8192)
        app=SimpleNamespace(manager=SimpleNamespace(get=lambda n:model,unload=lambda n:None,ensure=lambda n:None))
        row=features.context_check(app,lambda p,d:{"tokens":list(range(100))},lambda p:[{"n_ctx":32768}],lambda:{})
        self.assertEqual(row["status"],"fail")
    def test_context_oom_degradation_rejected(self):
        model=SimpleNamespace(ctx_size=8192)
        app=SimpleNamespace(manager=SimpleNamespace(get=lambda n:model,unload=lambda n:None,ensure=lambda n:setattr(model,"ctx_size",16384)))
        row=features.context_check(app,lambda p,d:{"tokens":list(range(30000))},lambda p:[{"n_ctx":16384}],lambda:{})
        self.assertEqual(row["status"],"fail")
    def test_context_real_usage_required(self):
        model=SimpleNamespace(ctx_size=8192)
        app=SimpleNamespace(manager=SimpleNamespace(get=lambda n:model,unload=lambda n:None,ensure=lambda n:None))
        def post(path,data):
            return {"tokens":list(range(30000))} if path=="/tokenize" else {"content":"violet-731 amber-842","tokens_evaluated":30000,"truncated":False}
        row=features.context_check(app,post,lambda p:[{"n_ctx":32768}],lambda:{"used_mib":50000})
        self.assertEqual(row["status"],"pass")
    def test_kernel_failure_propagates(self):
        r=stages.Runner(self.p,mirror=None)
        cfg=envsetup.venv_dir(self.p);cfg.mkdir();(cfg/"pyvenv.cfg").write_text("include-system-site-packages = false")
        def run(stage,cmd,**kw):return 5 if "ipykernel" in cmd else 0
        r.run=run
        with patch.object(subprocess,"run",return_value=SimpleNamespace(stdout="3.11",returncode=0)):
            with self.assertRaisesRegex(stages.StageError,"kernel registration"):
                stages.stage_venv(r,Path("lock"))
        self.assertEqual(r.checks()["kernel_registered"],"fail")

if __name__=="__main__":unittest.main(verbosity=2)
