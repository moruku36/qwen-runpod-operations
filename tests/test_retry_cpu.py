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
import contextlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from qmc_runpod import control, features, logchannel, report, retry, smoke, stages, envsetup, podapi
import read_pod_logs


@contextlib.contextmanager
def trial_fakes(r, *, generate=None, feature_run=None, load=None):
    """Exercise production trial/report flow without Gradio, GPU, model, provider or credential use."""
    from qmc_runpod import launch, llama, provenance, handoff
    records=[{"role":role,"file":spec["file"],"revision":spec["revision"],"sha256_verified":False,"size_bytes":0}
             for role,spec in stages.pins.MODELS.items()]
    r._write(r.runs/"build.json",{"server":"cpu-fake","model_paths":["cpu-fake.gguf",None],"records":records,
                                 "build":{"cuda_archs":"none","runtime_tag":"cpu_fake","build_type":"none","build_seconds":0,"built_this_run":False}})
    app=SimpleNamespace(cfg=SimpleNamespace(server_port=17861))
    sample={"first_delta_s":.1,"total_s":.2,"stream_deltas":2,"finish_reason":"stop","max_tokens":64,"error":False,"phase":"first"}
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules,{"gradio":SimpleNamespace(__version__="cpu_fake")}))
        for obj,name,value in ((launch,"launch",lambda **kw:app),(launch,"check_auth_enforced",lambda *a:True),
                               (launch,"stop_app",lambda app:None),(llama,"server_version",lambda p:"cpu_fake"),
                               (handoff,"read_build",lambda root:json.loads((root/"runs/build.json").read_text())),
                               (smoke,"load_model",load or (lambda app:{"load_s":.1})),
                               (smoke,"first_response",lambda app:sample),(smoke,"gpu_memory_mib",lambda:{}),
                               (provenance,"nvcc_release",lambda:"not_run"),(provenance,"driver_cuda_version",lambda:"not_run")):
            stack.enter_context(patch.object(obj,name,value))
        if generate:stack.enter_context(patch.object(smoke,"generate",generate))
        if feature_run:stack.enter_context(patch.object(features,"run",feature_run))
        yield app


class CPU(unittest.TestCase):
    def clock(self):
        """Explicit fixture instant; production deadlines continue to use the real clock."""
        return dt.datetime(2026,10,3,1,5,tzinfo=dt.timezone.utc)
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
                "test_deadline": "2026-10-03T02:20:00+00:00", "export_deadline": "2026-10-03T02:25:00+00:00", "stop_deadline": "2026-10-03T03:00:00+00:00",
                "approval_date": "2026-10-03", "resource_action": "resume", "pod_id": "CPUFAKE123456",
                "pod_status": "RUNNING", "start_time_source": "resume_response.startedAt",
                "cap_usd": 10, "quote_total_usd": 4.49, "account_balance_usd": 10.528, "storage_reserve_usd": .528,
                "max_runtime_minutes": 120, "stop_buffer_minutes": 10,
                "absolute_stop_deadline": "2026-10-03T03:00:00+00:00",
                "stop_request_deadline": "2026-10-03T02:50:00+00:00",
                "stop_operator_confirmed": True, "console_stop_verified": True, "stop_operator": "CPU fixture operator",
                "no_autopay_confirmed": True, "balance_observed_at": "2026-10-03T00:55:00+00:00",
                "cost_record": "synthetic CPU-only fixture",
                "cost_estimates": {"gpu_rate_usd_per_hour": 1.59, "running_storage_usd_per_hour": .022,
                                   "session_quote_usd": 4, "additional_storage_24h_usd": 0,
                                   "delayed_billing_margin_usd": .49},
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
    def test_explicit_absolute_stop_bound_is_respected(self):
        p=self.packet();p["absolute_stop_deadline"]="2026-10-03T02:40:00+00:00"
        p["test_deadline"]="2026-10-03T02:00:00+00:00";p["export_deadline"]="2026-10-03T02:10:00+00:00"
        p["stop_request_deadline"]="2026-10-03T02:30:00+00:00";p["stop_deadline"]="2026-10-03T02:40:00+00:00"
        self.validate(p)
    def test_wrong_day(self):
        p=self.packet();p["allocated_at"]="2026-10-02T01:00:00+00:00"
        with self.assertRaises(ValueError):self.validate(p)
    def test_over_cap(self):
        p=self.packet();p["quote_total_usd"]=11
        with self.assertRaises(ValueError):self.validate(p)
    def test_unconfirmed_stop_or_autopay_blocks(self):
        for key in ("stop_operator_confirmed", "console_stop_verified", "no_autopay_confirmed"):
            p=self.packet();p[key]=False
            with self.subTest(key=key),self.assertRaises(ValueError):self.validate(p)
    def test_storage_reserve_is_subtracted_before_spend_cap(self):
        p=self.packet();p["cap_usd"]=10.01
        with self.assertRaises(ValueError):self.validate(p)
    def test_missing_runtime_or_balance_evidence_blocks(self):
        for key in ("max_runtime_minutes", "balance_observed_at", "cost_record"):
            p=self.packet();p.pop(key)
            with self.subTest(key=key),self.assertRaises(ValueError):self.validate(p)
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
            self.assertEqual(retry.execute(p,ROOT,clock=self.clock,command=fail),9)
        self.assertEqual(calls,["selftest"])
    def test_failed_or_cancelled_trial_cannot_reemit_same_run(self):
        for terminal in ("failed","cancelled","running"):
            p=self.packet();p["root"]=str(self.p/terminal);root=Path(p["root"]);root.mkdir()
            state={"run_id":p["run_id"],"packet_sha256":hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest(),
                   "completed":list(retry.STAGES[:-1]),"current_stage":"trial","state":terminal}
            (root/"retry-checkpoint.json").write_text(json.dumps(state))
            calls=[]
            with patch.object(retry,"validate_packet"):
                with self.assertRaisesRegex(ValueError,"terminal"):
                    retry.execute(p,ROOT,clock=self.clock,command=lambda *a,**kw:calls.append(a))
            self.assertEqual(calls,[])
    def test_command_exception_marks_terminal_checkpoint(self):
        p=self.packet()
        def fail(*a,**kw):raise RuntimeError("CPU test cleanup failure")
        with patch.object(retry,"validate_packet"):
            with self.assertRaises(RuntimeError):retry.execute(p,ROOT,clock=self.clock,command=fail)
            state=json.loads((Path(p["root"])/"retry-checkpoint.json").read_text())
            self.assertEqual(state["state"],"failed")
            with self.assertRaisesRegex(ValueError,"terminal"):retry.execute(p,ROOT,clock=self.clock,command=fail)
    def test_trial_has_separate_export_reserve(self):
        p=self.packet();p["export_deadline"]=p["test_deadline"]
        with self.assertRaises(ValueError):self.validate(p)
    def test_test_and_export_deadlines_respect_approved_milestones(self):
        for key,value in (("test_deadline","2026-10-03T02:41:00+00:00"),("export_deadline","2026-10-03T02:51:00+00:00")):
            p=self.packet();p[key]=value
            with self.assertRaises(ValueError):self.validate(p)
    def test_outer_trial_uses_export_deadline(self):
        p=self.packet();root=Path(p["root"]);root.mkdir()
        state={"run_id":p["run_id"],"packet_sha256":hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest(),
               "completed":list(retry.STAGES[:-1]),"state":"running"}
        (root/"retry-checkpoint.json").write_text(json.dumps(state));seen=[]
        def command(cmd,**kw):seen.append(kw["seconds"]);return self.fake_stage(cmd,**kw)
        clock=lambda:dt.datetime(2026,10,3,1,5,tzinfo=dt.timezone.utc)
        with patch.object(retry,"validate_packet"):
            self.assertEqual(retry.execute(p,ROOT,clock=clock,command=command),0)
        # Export is 02:25 UTC and this test starts at 01:05 UTC: 80 minutes.
        # The outer command gets the report reserve beyond the 02:20 work limit.
        self.assertEqual(seen,[4800])
        self.assertGreater(seen[0],(retry.instant(p["test_deadline"])-clock()).total_seconds())
    def test_expired_execution_never_calls_stage(self):
        p=self.packet();calls=[]
        expired=lambda:retry.instant(p["test_deadline"])
        with patch.object(retry,"validate_packet"):
            self.assertEqual(retry.execute(p,ROOT,clock=expired,command=lambda *a,**kw:calls.append(a)),124)
        self.assertEqual(calls,[])
        self.assertEqual(json.loads((Path(p["root"])/"retry-checkpoint.json").read_text())["state"],"cancelled")
    def test_stale_root_rejected(self):
        p=self.packet();Path(p["root"]).mkdir();(Path(p["root"])/"old").touch()
        with patch.object(retry,"validate_packet"):
            with self.assertRaises(ValueError):retry.execute(p,ROOT,clock=self.clock)
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
    def test_malformed_ui_receipt_is_fail_not_exception(self):
        path=self.p/"ui.json"
        for text in ("{broken","[]","null","42"):
            path.write_text(text)
            self.assertEqual(features.ui_receipt(path,"cpu-run-123")["status"],"fail")
    def read_bundle_report(self,bundle):
        with tarfile.open(bundle) as tar:
            return json.load(tar.extractfile(next(m.name for m in tar.getmembers() if m.name.startswith("report-"))))
    def test_ui_snapshot_precedes_feature_reload(self):
        r=stages.Runner(self.p/"ws",mirror=None,run_id="cpu-run-123")
        receipt={"run_id":"cpu-run-123","observer":"owner","input_to_display":True,"language":"ja","ctx":8192}
        path=r.root/"ui-check.json";path.write_text(json.dumps(receipt))
        def reload(*a,**kw):
            receipt["ctx"]=32768;path.write_text(json.dumps(receipt));return features.template()
        with trial_fakes(r,feature_run=reload):bundle=stages.stage_trial(r,full_features=True)
        rep=self.read_bundle_report(bundle)
        self.assertEqual(rep["features"]["ui_chat"]["status"],"pass")
        self.assertEqual(json.loads(path.read_text())["ctx"],32768)
    def test_malformed_ui_still_exports_diagnostic_bundle(self):
        r=stages.Runner(self.p/"ws",mirror=self.p/"mirror",run_id="cpu-run-123")
        (r.root/"ui-check.json").write_text("[]")
        with trial_fakes(r):bundle=stages.stage_trial(r)
        self.assertEqual(self.read_bundle_report(bundle)["checks"]["ui_chat"],"fail")
        self.assertEqual(report.verify_bundle(bundle),[])
        self.assertTrue(logchannel.decode_artifacts((self.p/"mirror").read_text().splitlines(),run_id="cpu-run-123")[bundle.name]["ok"])
    def test_warm_cancellation_preserves_partial_samples(self):
        r=stages.Runner(self.p/"ws",mirror=None,run_id="cpu-run-123")
        def generate(*a,**kw):
            (r.root/"CANCEL").touch()
            return {"first_delta_s":.1,"total_s":.2,"stream_deltas":2,"finish_reason":"stop","max_tokens":256,"error":False}
        with trial_fakes(r,generate=generate):
            with self.assertRaises(stages.StageError):stages.stage_trial(r,mock=True,warm=True)
        bundle=next(r.runs.glob("*.tar.gz"));rep=self.read_bundle_report(bundle)
        self.assertEqual(len(rep["metrics"]["warm_samples"]),1)
        self.assertEqual(rep["checks"]["perf_criteria"],"skipped")
        self.assertEqual(r.status()["trial"]["state"],"fail")
        saved_sha=retry.digest(bundle)
        with trial_fakes(r):
            with self.assertRaisesRegex(stages.StageError,"already attempted"):
                stages.stage_trial(r,mock=True)
        self.assertEqual(retry.digest(bundle),saved_sha)
    def test_interrupted_features_preserve_completed_row_and_ui(self):
        r=stages.Runner(self.p/"ws",mirror=None,run_id="cpu-run-123")
        (r.root/"ui-check.json").write_text(json.dumps({"run_id":"cpu-run-123","observer":"owner","input_to_display":True,"language":"ja","ctx":8192}))
        def interrupt(*a,**kw):
            kw["on_result"]("image_understanding",features.result(False,{"attempted":True},"CPU test failure"))
            raise control.WorkInterrupted()
        with trial_fakes(r,feature_run=interrupt):
            with self.assertRaises(stages.StageError):stages.stage_trial(r,full_features=True)
        rep=self.read_bundle_report(next(r.runs.glob("*.tar.gz")))
        self.assertEqual(rep["checks"]["ui_chat"],"pass")
        self.assertEqual(rep["checks"]["image_understanding"],"fail")
        self.assertEqual(rep["checks"]["ctx_32k"],"skipped")
    @unittest.skipUnless(os.name=="posix","Linux venv symlink identity")
    def test_real_venv_kernel_rejects_base_python_symlink_target(self):
        root=self.p/"kernel";subprocess.run(envsetup.create_cmd(root,without_pip=True),check=True)
        code=(f"import sys;from pathlib import Path;sys.path.insert(0,{str(ROOT)!r});"
              "from qmc_runpod.envsetup import kernel_identity_ok;"
              f"root=Path({str(root)!r});assert kernel_identity_ok(sys.executable,root);"
              "assert not kernel_identity_ok(str(Path(sys.executable).resolve()),root)")
        self.assertEqual(subprocess.run([str(envsetup.venv_python(root)),"-c",code]).returncode,0)
    @unittest.skipUnless(sys.platform.startswith("linux"),"owned Linux process-group stragglers")
    def test_exited_leader_does_not_leave_term_ignoring_child(self):
        pidfile=self.p/"child.pid"
        child="import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
        code=(f"import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c',{child!r}]);"
              f"Path({str(pidfile)!r}).write_text(str(p.pid));time.sleep(.2)")
        self.assertEqual(retry.bounded_command([sys.executable,"-c",code],seconds=2,cancel_file=self.p/"CANCEL",output=self.p/"out",grace_seconds=.1),0)
        child_pid=int(pidfile.read_text());end=time.monotonic()+2
        while time.monotonic()<end:
            stat=Path(f"/proc/{child_pid}/stat")
            if not stat.exists() or stat.read_text().rsplit(")",1)[1].split()[0]=="Z":break
            time.sleep(.05)
        else:self.fail("owned straggler still live")
    @unittest.skipUnless(os.name=="posix","POSIX signal disappearance race")
    def test_group_disappearance_race_is_harmless(self):
        proc=SimpleNamespace(pid=123456,_qmc_owned_session=True,wait=lambda **kw:None)
        with patch.object(retry,"_live_group",return_value=True),patch.object(os,"killpg",side_effect=ProcessLookupError):
            retry.terminate_tree(proc,grace_seconds=0)
    @unittest.skipUnless(os.name=="posix","Linux main-thread work deadline")
    def test_inner_deadline_exports_while_outer_process_still_alive(self):
        root=self.p/"deadline"
        code=(f"import sys,time;from pathlib import Path;sys.path.insert(0,{str(ROOT/'tests')!r});"
              "from test_retry_cpu import trial_fakes;from qmc_runpod import stages;"
              f"r=stages.Runner(Path({str(root)!r}),mirror=None,run_id='cpu-run-123');\n"
              "with trial_fakes(r,load=lambda app:time.sleep(20)):\n"
              " try:stages.stage_trial(r,mock=True,test_deadline=time.time()+.15)\n"
              " except stages.StageError:pass\n")
        started=time.monotonic()
        self.assertEqual(retry.bounded_command([sys.executable,"-c",code],seconds=3,cancel_file=self.p/"CANCEL",output=self.p/"out"),0)
        self.assertLess(time.monotonic()-started,3)
        bundle=next((root/"runs").glob("*.tar.gz"));self.assertEqual(report.verify_bundle(bundle),[])
        self.assertEqual(self.read_bundle_report(bundle)["checks"]["first_response"],"skipped")
    def test_deadline_skips_real_features(self):
        rows=features.run(None,self.p,deadline=0)
        self.assertEqual({r["status"] for r in rows.values()},{"skipped"})
        self.assertIn("licensed",rows["asr"]["reason"])
    def test_work_base_exceptions_cleanup_and_export(self):
        from qmc_runpod import launch
        for exc in (KeyboardInterrupt, SystemExit):
            with self.subTest(exc=exc.__name__):
                r=stages.Runner(self.p/exc.__name__,mirror=None,run_id="cpu-run-123")
                def interrupted(app):raise exc()
                with trial_fakes(r,load=interrupted),patch.object(launch,"stop_app") as stop:
                    with self.assertRaises(stages.StageError):stages.stage_trial(r,mock=True)
                    stop.assert_called_once()
                self.assertEqual(report.verify_bundle(next(r.runs.glob("*.tar.gz"))),[])
    def test_export_base_exceptions_cleanup(self):
        from qmc_runpod import launch
        for exc in (KeyboardInterrupt,SystemExit):
            with self.subTest(exc=exc.__name__):
                r=stages.Runner(self.p/exc.__name__,mirror=None,run_id="cpu-run-123")
                with trial_fakes(r),patch.object(launch,"stop_app") as stop,patch.object(r,"emit_artifact",side_effect=exc):
                    with self.assertRaises(exc):stages.stage_trial(r,mock=True)
                    stop.assert_called_once()
    def test_early_interruption_preserves_new_ui_receipt(self):
        r=stages.Runner(self.p/"ui",mirror=None,run_id="cpu-run-123")
        def interrupted(app):
            (r.root/"ui-check.json").write_text(json.dumps({"run_id":r.run_id,"observer":"owner","input_to_display":True,"language":"ja","ctx":8192}))
            raise control.WorkInterrupted()
        with trial_fakes(r,load=interrupted):
            with self.assertRaises(stages.StageError):stages.stage_trial(r)
        rep=self.read_bundle_report(next(r.runs.glob("*.tar.gz")))
        self.assertEqual(rep["checks"]["ui_chat"],"pass")
        self.assertIn("not completed",rep["features"]["ctx_32k"]["reason"])
    def test_unavailable_build_metadata_is_not_mock_success(self):
        r=stages.Runner(self.p/"build",mirror=None,run_id="cpu-run-123")
        with trial_fakes(r):
            (r.runs/"build.json").unlink()
            with self.assertRaises(stages.StageError):stages.stage_trial(r)
        rep=self.read_bundle_report(next(r.runs.glob("*.tar.gz")))
        self.assertEqual(rep["llama_cpp"]["runtime_tag"],"unavailable")
        self.assertNotIn("build_seconds",rep["llama_cpp"])
        self.assertEqual(rep["environment"]["cuda_toolkit"],"not_measured")
    def test_cli_refusal_preserves_prior_status(self):
        import pod_run
        r=stages.Runner(self.p/"prior",mirror=None,run_id="cpu-run-123")
        for state in ("ok","fail"):
            r.set_status("trial",state,bundle="existing.tar.gz")
            original=r.status_path.read_bytes()
            self.assertEqual(pod_run.main(["trial","--mock","--root",str(r.root),"--run-id",r.run_id]),1)
            self.assertEqual(r.status_path.read_bytes(),original)
        self.assertTrue((r.logs/"trial-refusal.log").exists())
    @unittest.skipUnless(os.name=="posix","Linux export-phase cancellation")
    def test_cancel_during_export_is_deferred_and_cleanup_runs(self):
        import signal
        from qmc_runpod import launch
        r=stages.Runner(self.p/"export",mirror=None,run_id="cpu-run-123")
        emit=r.emit_artifact
        def cancelled(path):
            (r.root/"CANCEL").touch()
            os.kill(os.getpid(),signal.SIGTERM)
            os.kill(os.getpid(),signal.SIGINT)
            emit(path)
        with trial_fakes(r),patch.object(launch,"stop_app") as stop,patch.object(r,"emit_artifact",side_effect=cancelled):
            bundle=stages.stage_trial(r,mock=True,export_deadline=time.time()+2)
            stop.assert_called_once()
        self.assertEqual(report.verify_bundle(bundle),[])
    @unittest.skipUnless(os.name=="posix","Linux hard finalization deadline")
    def test_finalization_timeout_still_attempts_cleanup(self):
        from qmc_runpod import launch
        r=stages.Runner(self.p/"export-bound",mirror=None,run_id="cpu-run-123")
        with trial_fakes(r),patch.object(launch,"stop_app") as stop,patch.object(r,"emit_artifact",side_effect=lambda p:time.sleep(10)):
            started=time.monotonic()
            with self.assertRaises(control.WorkInterrupted):stages.stage_trial(r,mock=True,export_deadline=time.time()+.15)
            stop.assert_called_once()
            self.assertLess(time.monotonic()-started,1)
    @unittest.skipUnless(sys.platform.startswith("linux"),"Linux controller outer bound")
    def test_outer_deadline_kills_term_ignoring_owned_process(self):
        started=time.monotonic()
        rc=retry.bounded_command([sys.executable,"-c","import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)"],seconds=.2,cancel_file=self.p/"CANCEL",output=self.p/"bound",grace_seconds=30)
        self.assertEqual(rc,124)
        self.assertLess(time.monotonic()-started,2)
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
        from qmc_runpod import toolchain
        r=stages.Runner(self.p,mirror=None)
        cfg=envsetup.venv_dir(self.p);cfg.mkdir();(cfg/"pyvenv.cfg").write_text("include-system-site-packages = false")
        def run(stage,cmd,**kw):return 5 if "ipykernel" in cmd else 0
        r.run=run
        with patch.object(toolchain,"assert_ready",return_value={}), patch.object(subprocess,"run",return_value=SimpleNamespace(stdout="3.11",returncode=0)):
            with self.assertRaisesRegex(stages.StageError,"kernel registration"):
                stages.stage_venv(r,Path("lock"))
        self.assertEqual(r.checks()["kernel_registered"],"fail")

if __name__=="__main__":unittest.main(verbosity=2)
