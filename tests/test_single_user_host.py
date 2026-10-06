"""One concrete production assembly exercised only with public fake ports."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import ast
import base64
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qmc_runpod.c1_authority import AuthorityJournal
from qmc_runpod.c1_ports import PortError
from qmc_runpod.execution_adapters import SSHPeer
from qmc_runpod.single_user_host import SingleUserHost
from qmc_runpod.production_gateway import ProductionGateway
from qmc_runpod.ondemand import TerminationApproval
from qmc_runpod.ondemand import CorruptCheckpoint
from qmc_runpod.upstream_sse import SSEDecoder
import test_c1_flow as core
from test_private_gateway import body
from test_ondemand_controller import FakeClock
import http.client as http_client

INFERENCE="single-user-public-inference-fixture"
CONTROL="single-user-public-control-fixture"


class FakeREST:
    def __init__(self): self.host=None; self.pods={}; self.calls=[]; self.unknown_create=False; self.unknown_delete=False
    def request(self,e,method,path,payload,cancel):
        assert not self.host._lock._is_owned()
        self.host.check_effect(e); self.calls.append((method,path,e.session_id))
        if method=="POST":
            spec=self.host.spec; pod="pod-"+e.session_id
            row={"id":pod,"consumerUserId":spec.account,"name":json.loads(payload)["name"],"image":spec.image,
                "interruptible":False,"gpu":{"count":1},"machine":{"gpuTypeId":spec.gpu_type},
                "volumeInGb":spec.volume_gb,"containerDiskInGb":spec.container_gb,"costPerHr":"1.80","env":{"secret":"excluded"}}
            self.pods[pod]=row
            if self.unknown_create: raise TimeoutError("untrusted provider text")
            return row
        if method=="DELETE":
            if self.unknown_delete: raise TimeoutError("unknown")
            self.pods.pop(e.pod_id,None); return True
        return self.pods.get(e.pod_id)


class FakeSSH:
    def __init__(self): self.host=None; self.calls=[]; self.tunnels=0; self.closed=0
    def run(self,e,peer,script,cancel):
        assert not self.host._lock._is_owned()
        self.host.check_effect(e); self.calls.append(e.action)
        pins=self.host.pins
        value={"session_id":e.session_id,"pod_id":e.pod_id,"model_sha256":pins.model_sha256,
               "llama_commit":pins.llama_commit,"binary_sha256":"c"*64}
        if e.action=="load": value.update(pid=123,authenticated=True,alias=pins.alias,context=pins.context)
        return json.dumps(value).encode()
    @contextmanager
    def tunnel(self,e,peer,cancel):
        assert not self.host._lock._is_owned()
        self.host.check_effect(e); self.tunnels+=1
        try: yield object()
        finally: self.closed+=1


class FakeUpstream:
    def __init__(self): self.calls=0; self.before_return=None
    def json(self,payload,rid,*,deadline,cancel,fence):
        fence(); self.calls+=1
        if self.before_return: self.before_return()
        fence(); return "ok"
    def stream(self,payload,rid,*,deadline,cancel,fence):
        self.calls+=1; decoder=SSEDecoder(rid)
        frames=[{"role":"assistant"},{"content":"ok"},{}]
        for index,delta in enumerate(frames):
            fence()
            chunk={"id":"public-provider","object":"chat.completion.chunk","model":"qwen-27b", "choices":[{"index":0,"delta":delta,"finish_reason":"stop" if index==2 else None}]}
            for event in decoder.feed(b"data: "+json.dumps(chunk).encode()+b"\n\n"): yield event
        for event in decoder.feed(b"data: [DONE]\n\n"): fence(); yield event
        decoder.finish()


def http(address,path,payload=None,*,key=INFERENCE,rid=None,token=None):
    connection=http_client.HTTPConnection(*address,timeout=2)
    headers={"Authorization":"Bearer "+key,"Connection":"close"}
    if rid: headers["X-Request-ID"]=rid
    if token: headers["X-Intent-Token"]=token
    if payload is not None: headers["Content-Type"]="application/json"
    try:
        connection.request("GET" if payload is None else "POST",path,body=None if payload is None else json.dumps(payload),headers=headers)
        response=connection.getresponse(); return response.status,response.read()
    finally: connection.close()


class SingleUserTests(unittest.TestCase):
    def setUp(self):
        self.case=core.C1FlowTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.path=self.case.path; self.clock=FakeClock(100); self.rest=FakeREST(); self.ssh=FakeSSH(); self.upstream=FakeUpstream()

    def journal(self): return AuthorityJournal(self.path/"host.sqlite",self.path/"head.json",key=core.KEY,domain="public-single-host-fixture")

    def host(self,journal):
        host=SingleUserHost(journal=journal,subject="fixture-user",enabled=True,fixture=True,rest=self.rest,ssh=self.ssh,upstream=self.upstream,
                            export_root=self.path/"export",clock=self.clock.now,idle_seconds=10)
        self.rest.host=self.ssh.host=host
        return host

    def start(self,host,payload=None,sid="s"):
        host.approve_session(core.grant(sid)); pending=host.enqueue("fixture-user",payload or body())
        self.assertEqual(host.step(),"created")
        host.register_peer(sid,SSHPeer("pod-"+sid,"8.8.8.8",12345,str(self.path/"known_hosts"),str(self.path/"key"),"SHA256:"+"a"*43))
        self.assertEqual(host.step(),"bootstrapped"); self.assertEqual(host.step(),"ready")
        return pending

    def cleanup(self,host,sid="s"):
        host.stop(sid); self.assertEqual(host.step(),"exporting"); self.assertEqual(host.export(sid),"approval_pending")
        row=host._row(sid)
        approval=TerminationApproval("approval-"+sid,"terminate",sid,row["pod_id"],row["receipt"]["sha256"],row["cleanup_deadline"])
        host.approve_cleanup(approval); return approval

    def test_default_host_gateway_import_construction_and_catalog_are_inert(self):
        with patch("subprocess.Popen",side_effect=AssertionError("process")):
            host=SingleUserHost(); gateway=ProductionGateway(host)
            self.assertEqual(host.status()["phase"],"absent")
            with self.assertRaises(PortError): host.approve_session(core.grant())
            with self.assertRaises(PortError):
                with gateway.serve(port=19180): self.fail("production bind")

    def test_json_sse_export_exact_delete_absence_and_new_session_recreate(self):
        with self.journal().open() as journal:
            host=self.host(journal); pending=self.start(host); gateway=ProductionGateway(host,inference_key=INFERENCE,control_key=CONTROL)
            with gateway.serve() as address:
                headers=host.ready_request(pending)
                self.assertEqual(http(address,"/v1/chat/completions",body(),rid=pending.request_id,token=headers["X-Intent-Token"])[0],200)
                streaming=host.enqueue("fixture-user",body(True)); headers=host.ready_request(streaming)
                status,wire=http(address,"/v1/chat/completions",body(True),rid=streaming.request_id,token=headers["X-Intent-Token"])
                self.assertEqual(status,200); self.assertEqual(wire.count(b"data: [DONE]"),1)
            self.assertEqual(host.release_count,2); self.cleanup(host)
            self.assertEqual(host.terminate("s"),"absent")
            self.assertTrue(host.readback("s")); self.assertEqual(host.status()["phase"],"absent")
            self.start(host,sid="next")
            self.assertEqual(sum(method=="POST" for method,_,_ in self.rest.calls),2); host.close()

    def test_catalog_status_missing_intent_and_wrong_user_never_allocate(self):
        with self.journal().open() as journal:
            host=self.host(journal); gateway=ProductionGateway(host,inference_key=INFERENCE,control_key=CONTROL)
            with gateway.serve() as address:
                for path in ("/v1/models","/status"): self.assertEqual(http(address,path)[0],200)
                self.assertEqual(http(address,"/v1/chat/completions",body(),rid="background")[0],403)
                self.assertEqual(http(address,"/_control/intents",{"payload":body()},key=CONTROL)[0],404)
            host.approve_session(core.grant())
            with self.assertRaises(PortError): host.enqueue("unverified",body())
            self.assertEqual(self.rest.calls,[]); host.close()

    def test_concurrent_worker_reservation_creates_exactly_once(self):
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); host.enqueue("fixture-user",body())
            with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(lambda _:host.step(),range(4)))
            self.assertEqual(sum(method=="POST" for method,_,_ in self.rest.calls),1)
            self.assertIn("created",results); host.close()

    def test_create_seal_crash_restores_ownership_for_cleanup_not_replay(self):
        class Crash(BaseException): pass
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); host.enqueue("fixture-user",body()); original=host._save
            def save():
                original()
                if host._row()["phase"]=="bootstrapping": raise Crash()
            with patch.object(host,"_save",save):
                with self.assertRaises(Crash): host.step()
            self.assertEqual(journal.latest()["sessions"]["s"]["owner"]["account"],host.spec.account)
        with self.journal().open() as journal:
            host=self.host(journal); self.assertEqual(host.status()["phase"],"draining")
            self.cleanup(host); self.assertEqual(host.terminate("s"),"absent")
            self.assertEqual(sum(method=="POST" for method,_,_ in self.rest.calls),1); host.close()

    def test_unknown_create_requires_original_operation_proof_and_never_second_post(self):
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); host.enqueue("fixture-user",body()); self.rest.unknown_create=True
            self.assertEqual(host.step(),"create_unresolved"); self.assertEqual(host.step(),"create_unresolved")
            with self.assertRaises(PortError): host.reconcile_create("s",pod_response=self.rest.pods["pod-s"],original_operation="b"*64)
            self.assertEqual(host.reconcile_create("s",pod_response=self.rest.pods["pod-s"],original_operation=host._row()["create_operation"]),"found")
            self.assertEqual(sum(method=="POST" for method,_,_ in self.rest.calls),1); host.close()

    def test_cold_ready_idle_is_atomic_and_original_work_expiry_unchanged(self):
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); host.enqueue("fixture-user",body()); host.step()
            host.register_peer("s",SSHPeer("pod-s","8.8.8.8",12345,str(self.path/"known_hosts"),str(self.path/"key"),"SHA256:"+"a"*43))
            host.step(); deadline=host.status()["deadline"]; self.clock.set(447.956); self.assertEqual(host.step(),"ready")
            saved=journal.latest()["sessions"]["s"]
            self.assertEqual(saved["deadline"],deadline); self.assertAlmostEqual(saved["idle_deadline"],457.956)
            self.clock.set(457.956); self.assertEqual(host.step(),"exporting"); host.close()

    def test_unknown_delete_restart_preserves_consumption_and_only_reads_absence(self):
        with self.journal().open() as journal:
            host=self.host(journal); self.start(host); approval=self.cleanup(host); self.rest.unknown_delete=True
            self.assertEqual(host.terminate("s"),"termination_unconfirmed"); host.close()
        with self.journal().open() as journal:
            host=self.host(journal)
            self.assertEqual(host.status()["phase"],"termination_unconfirmed")
            with self.assertRaises(PortError): host.terminate("s")
            self.rest.pods.pop("pod-s"); self.assertEqual(host.reconcile_termination("s"),"absent")
            self.assertEqual(sum(method=="DELETE" for method,_,_ in self.rest.calls),1)
            self.assertIn(approval.approval_id,journal.latest()["used_approvals"]); host.close()

    def test_export_crash_readback_and_exact_approval_survive_restart(self):
        with self.journal().open() as journal:
            host=self.host(journal); self.start(host); host.stop("s"); host.step()
            plan=host._row()["export_plan"]; host.export_root.mkdir()
            (host.export_root/plan["name"]).write_bytes(plan["bytes"].encode())
            host.close()
        with self.journal().open() as journal:
            host=self.host(journal); self.assertEqual(host.reconcile_export("s"),"approval_pending")
            self.assertTrue(host.readback("s")); host.close()

    def test_stop_none_wrong_session_and_inference_key_cannot_control_successor(self):
        with self.journal().open() as journal:
            host=self.host(journal); self.start(host); before=journal.latest()
            for sid in (None,"other",True):
                with self.assertRaises(PortError): host.stop(sid)
            self.assertEqual(journal.latest(),before)
            gateway=ProductionGateway(host,inference_key=INFERENCE,control_key=CONTROL)
            with gateway.serve() as address:
                self.assertEqual(http(address,"/_control/stop",{"session_id":"s"})[0],401)
                self.assertEqual(http(address,"/_control/stop",{"session_id":"other"},key=CONTROL)[0],409)
            host.close()

    def test_elapsed_price_and_absolute_deadline_reject_inference_without_step(self):
        with self.journal().open() as journal:
            host=self.host(journal); pending=self.start(host)
            self.clock.set(host.status()["deadline"])
            with self.assertRaises(PortError): host.ready_request(pending)
            self.assertEqual(self.upstream.calls,0); host.close()

    def test_readback_tamper_wrong_approval_retained_pod_never_delete(self):
        with self.journal().open() as journal:
            host=self.host(journal); self.start(host); approval=self.cleanup(host)
            path=host.export_root/host._row()["receipt"]["name"]; path.write_bytes(b"tampered")
            with self.assertRaises(PortError): host.terminate("s")
            self.assertFalse(any(method=="DELETE" for method,_,_ in self.rest.calls)); host.close()

    def test_close_completed_before_waiting_worker_never_creates(self):
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); host.enqueue("fixture-user",body())
            entered=threading.Event(); original=host._operation_scope
            def blocked(): entered.set(); return original()
            with ThreadPoolExecutor(max_workers=1) as pool:
                with host._lock,patch.object(host,"_operation_scope",blocked):
                    future=pool.submit(host.step); self.assertTrue(entered.wait(1)); host.close(); before=journal.latest()
                with self.assertRaisesRegex(PortError,"host_disabled"): future.result(1)
            self.assertEqual(journal.latest(),before); self.assertEqual(self.rest.calls,[])

    def test_stop_during_external_create_preserves_late_owned_pod_for_cleanup(self):
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); host.enqueue("fixture-user",body())
            entered=threading.Event(); release=threading.Event(); original=self.rest.request
            def blocked(*args):
                result=original(*args); entered.set(); self.assertTrue(release.wait(1)); return result
            with patch.object(self.rest,"request",blocked),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(host.step); self.assertTrue(entered.wait(1)); host.stop("s"); release.set()
                self.assertEqual(future.result(1),"created")
            self.assertEqual(host.status()["phase"],"draining"); self.assertEqual(host.status()["pod_id"],"pod-s")
            self.cleanup(host); self.assertEqual(host.terminate("s"),"absent"); host.close()

    def test_ready_seal_crash_restores_original_idle_and_drains_without_reconnect(self):
        class Crash(BaseException): pass
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); host.enqueue("fixture-user",body()); host.step()
            host.register_peer("s",SSHPeer("pod-s","8.8.8.8",12345,str(self.path/"known_hosts"),str(self.path/"key"),"SHA256:"+"a"*43)); host.step()
            self.clock.set(447.956); original=host._save
            def save():
                original()
                if host._row()["phase"]=="ready": raise Crash()
            with patch.object(host,"_save",save):
                with self.assertRaises(Crash): host.step()
            saved=journal.latest()["sessions"]["s"]; self.assertAlmostEqual(saved["idle_deadline"],457.956)
        with self.journal().open() as journal:
            host=self.host(journal); self.assertEqual(host.status()["phase"],"draining")
            self.assertEqual(host.status()["deadline"],saved["deadline"]); self.assertEqual(host.status()["idle_deadline"],saved["idle_deadline"])
            self.assertEqual(self.ssh.tunnels,1); host.close()

    def test_stream_stop_after_header_closes_without_false_done_and_releases_once(self):
        with self.journal().open() as journal:
            host=self.host(journal); pending=self.start(host,body(True)); headers=host.ready_request(pending)
            original=self.upstream.stream
            def stream(*args,**kwargs):
                events=original(*args,**kwargs)
                yield next(events); host.stop("s")
                yield from events
            gateway=ProductionGateway(host,inference_key=INFERENCE,control_key=CONTROL)
            with patch.object(self.upstream,"stream",stream),gateway.serve() as address:
                status,wire=http(address,"/v1/chat/completions",body(True),rid=pending.request_id,token=headers["X-Intent-Token"])
            self.assertEqual(status,200); self.assertNotIn(b"[DONE]",wire); self.assertEqual(host.release_count,1); host.close()

    def test_chat_bodies_are_not_in_journal_export_or_gateway_logs(self):
        sensitive="private-unpersisted-body-fixture-unique"
        payload=body(); payload["messages"][0]["content"]=sensitive
        with self.journal().open() as journal:
            host=self.host(journal); pending=self.start(host,payload); headers=host.ready_request(pending)
            gateway=ProductionGateway(host,inference_key=INFERENCE,control_key=CONTROL)
            with gateway.serve() as address:
                self.assertEqual(http(address,"/v1/chat/completions",payload,rid=pending.request_id,token=headers["X-Intent-Token"])[0],200)
            self.cleanup(host)
            self.assertNotIn(sensitive,json.dumps(journal.latest())); self.assertNotIn(sensitive,repr(gateway.logs()))
            self.assertNotIn(sensitive,(host.export_root/host._row()["receipt"]["name"]).read_text()); host.close()

    def test_expired_cleanup_makes_no_delete_and_never_replaces_owned_session(self):
        with self.journal().open() as journal:
            host=self.host(journal); self.start(host); self.cleanup(host)
            self.clock.set(host.status()["cleanup_deadline"])
            with self.assertRaises(PortError): host.terminate("s")
            self.assertEqual(host.step(),"cleanup_expired")
            with self.assertRaises(PortError): host.approve_session(core.grant("next",expiry=9000))
            self.assertFalse(any(method=="DELETE" for method,_,_ in self.rest.calls)); host.close()

    def test_rolled_back_signed_head_denies_new_inference_without_resetting_grant(self):
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); old=journal.head_path.read_bytes()
            pending=host.enqueue("fixture-user",body()); host.step()
            host.register_peer("s",SSHPeer("pod-s","8.8.8.8",12345,str(self.path/"known_hosts"),str(self.path/"key"),"SHA256:"+"a"*43)); host.step(); host.step()
            current=journal.head_path.read_bytes(); journal.head_path.write_bytes(old)
            try:
                with self.assertRaisesRegex(PortError,"trusted_head_changed"): host.ready_request(pending)
                self.assertEqual(self.upstream.calls,0)
            finally: journal.head_path.write_bytes(current)
            host.close()

    def test_new_session_requires_new_peer_enrollment_instead_of_reusing_old_pod(self):
        with self.journal().open() as journal:
            host=self.host(journal); self.start(host); self.cleanup(host); self.assertEqual(host.terminate("s"),"absent")
            host.approve_session(core.grant("next")); host.enqueue("fixture-user",body()); self.assertEqual(host.step(),"created")
            before=list(self.ssh.calls); self.assertEqual(host.step(),"peer_approval_required")
            self.assertEqual(self.ssh.calls,before); self.assertIsNone(host._peer); host.close()

    def test_injected_model_key_only_crosses_fake_ssh_stdin_and_ready_script_requires_auth(self):
        from qmc_runpod.bootstrap_execution import load_script
        from qmc_runpod.execution_adapters import Effect
        token="public-synthetic-model-key-unique"
        encoded=base64.b64encode(token.encode()).decode(); scripts=[]; original=self.ssh.run
        def capture(effect,peer,script,cancel):
            scripts.append(script); return original(effect,peer,script,cancel)
        with self.journal().open() as journal:
            host=self.host(journal); host._model_bearer=token
            with patch.object(self.ssh,"run",capture): self.start(host)
            self.assertIn(encoded.encode(),scripts[0]); self.assertNotIn(token.encode(),scripts[0])
            self.assertNotIn(encoded.encode(),scripts[1]); self.cleanup(host)
            persisted=json.dumps(journal.latest())+(host.export_root/host._row()["receipt"]["name"]).read_text()
            self.assertNotIn(token,persisted); self.assertNotIn(encoded,persisted); host.close()
        effect=Effect("s","a"*64,"load","pod-s",time.monotonic()+10)
        script=load_script(effect,"c"*64).decode()
        python=script.split("python3 - <<'QMC_READINESS'\n",1)[1].rsplit("QMC_READINESS",1)[0]
        ast.parse(python)
        self.assertIn("error.code in (401,403)",python)
        self.assertIn("authentication not required",python)
        with self.assertRaisesRegex(PortError,"invalid_injected_model_bearer"):
            SingleUserHost(model_bearer="invalid\nkey-value-unique")

    def _rest_checkpoint_repro(self,mode):
        from qmc_runpod.execution_adapters import RunPodREST,ExecutionGate
        from types import SimpleNamespace
        calls=[]; gates=[0]; seals=[0]
        with self.journal().open() as journal:
            host=self.host(journal)
            if mode in {"approval","readback","cleanup"}:
                self.start(host); self.cleanup(host)
                if mode!="cleanup":
                    row=host._row(); row["approval"]["expires_at"]=100.5; host._save()
                else:
                    self.rest.unknown_delete=True
                    self.assertEqual(host.terminate("s"),"termination_unconfirmed")
            else:
                host.approve_session(core.grant(work=1 if mode=="utc" else 2000,usd="0.101" if mode=="usd" else "10"))
                host.enqueue("fixture-user",body())
            before=journal.sequence; original_append=journal.append; original_gate=host.check_effect; original_readback=host.readback
            crossed={"utc":101.001,"usd":102,"approval":100.501,"readback":100.501,
                     "cleanup":host.status()["cleanup_deadline"]+.001}.get(mode)
            def advance():
                if mode=="cancel":
                    for cancel in host._active.values(): cancel.set()
                else: self.clock.set(crossed)
            def append(data):
                original_append(data)
                if gates[0]==3 and seals[0]==0:
                    seals[0]+=1
                    if mode!="readback": advance()
            def gate(effect): gates[0]+=1; return original_gate(effect)
            def readback(sid):
                result=original_readback(sid)
                if gates[0]==3 and mode=="readback": advance()
                return result
            case=self
            class Connection:
                sock=None
                def __init__(self,*args): pass
                def connect(self):
                    now={"utc":100.999,"usd":101.94,"approval":100.499,"readback":100.499,
                         "cleanup":host.status()["cleanup_deadline"]-.001}.get(mode,100)
                    case.clock.set(now); host._last_saved=0
                def request(self,*args,**kwargs): calls.append(args)
                def getresponse(self): return SimpleNamespace(status=204 if mode in {"approval","readback"} else 200)
                def close(self): pass
            host.rest=RunPodREST(gate=ExecutionGate(gate),address="8.8.8.8",bearer="synthetic-public-bearer-only")
            with patch.object(journal,"append",append),patch.object(host,"readback",readback),patch("qmc_runpod.execution_adapters._PinnedHTTPS",Connection):
                if mode=="cleanup": result=host.reconcile_termination("s")
                elif mode in {"approval","readback"}: result=host.terminate("s")
                else: result=host.step()
            self.assertEqual(calls,[]); self.assertEqual(gates[0],3); self.assertEqual(seals[0],1)
            self.assertGreater(journal.sequence,before)
            self.assertEqual(result,"termination_unconfirmed" if mode in {"approval","readback","cleanup"} else "create_unresolved")
            host.close()

    def test_rest_send_after_signed_heartbeat_rechecks_utc_deadline(self): self._rest_checkpoint_repro("utc")

    def test_rest_send_after_signed_heartbeat_rechecks_work_usd_cap(self): self._rest_checkpoint_repro("usd")

    def test_delete_send_after_signed_heartbeat_rechecks_individual_approval_expiry(self): self._rest_checkpoint_repro("approval")

    def test_delete_send_after_readback_rechecks_individual_approval_expiry(self): self._rest_checkpoint_repro("readback")

    def test_rest_send_after_signed_heartbeat_rechecks_active_cancellation(self): self._rest_checkpoint_repro("cancel")

    def test_read_send_after_signed_heartbeat_rechecks_cleanup_deadline(self): self._rest_checkpoint_repro("cleanup")

    def test_load_gate_after_protected_head_read_rechecks_idle(self):
        from qmc_runpod.execution_adapters import Effect
        with self.journal().open() as journal:
            host=self.host(journal); self.start(host); opened=[0]; original=host._open
            effect=Effect("s",host._operation("s","load"),"load","pod-s",time.monotonic()+100)
            def open_then_elapsed():
                original(); opened[0]+=1
                if opened[0]==2: self.clock.set(host.status()["idle_deadline"]+.001)
            with patch.object(host,"_open",open_then_elapsed):
                with self.assertRaisesRegex(PortError,"idle_expired"): host.check_effect(effect)
            self.assertEqual(opened[0],2); host.close()

    def test_chat_after_admission_seal_expiry_never_calls_upstream(self):
        with self.journal().open() as journal:
            host=self.host(journal); pending=self.start(host); headers=host.ready_request(pending); original=journal.append
            def append_then_elapsed(data):
                original(data)
                if data["sessions"]["s"]["active_chat"] is not None: self.clock.set(host.status()["deadline"]+.001)
            gateway=ProductionGateway(host,inference_key=INFERENCE,control_key=CONTROL)
            with patch.object(journal,"append",append_then_elapsed),gateway.serve() as address:
                status,_=http(address,"/v1/chat/completions",body(),rid=pending.request_id,token=headers["X-Intent-Token"])
            self.assertNotEqual(status,200); self.assertEqual(self.upstream.calls,0); self.assertEqual(host.release_count,1); host.close()

    def test_same_journal_second_host_is_refused_before_restore_or_duplicate_create(self):
        with self.journal().open() as journal:
            host=self.host(journal); other=FakeREST(); before=(journal.sequence,journal.latest())
            def duplicate():
                return SingleUserHost(journal=journal,subject="fixture-user",enabled=True,fixture=True,
                    rest=other,ssh=FakeSSH(),export_root=self.path/"export",clock=self.clock.now,idle_seconds=10)
            with self.assertRaisesRegex(PortError,"single_host_owner_refused"): duplicate()
            self.assertEqual((journal.sequence,journal.latest()),before)
            host.approve_session(core.grant()); host.enqueue("fixture-user",body()); self.assertEqual(host.step(),"created")
            saved=journal.latest(); self.assertEqual(saved["sessions"]["s"]["pod_id"],"pod-s")
            self.assertEqual(sum(m=="POST" for m,_,_ in self.rest.calls),1); self.assertEqual(other.calls,[])
            host.close(); saved=journal.latest()
            with self.assertRaisesRegex(PortError,"single_host_owner_refused"): duplicate()
            self.assertEqual(journal.latest(),saved)

    def test_concurrent_host_constructor_claim_is_atomic(self):
        with self.journal().open() as journal:
            barrier=threading.Barrier(4)
            def create(_):
                barrier.wait(2)
                try: return self.host(journal)
                except PortError as error: return str(error)
            with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(create,range(4)))
            winners=[result for result in results if type(result) is SingleUserHost]
            self.assertEqual(len(winners),1); self.assertEqual(results.count("single_host_owner_refused"),3)
            host=winners[0]; host.approve_session(core.grant()); host.enqueue("fixture-user",body()); host.step()
            self.assertEqual(sum(m=="POST" for m,_,_ in self.rest.calls),1); host.close()

    def test_host_snapshot_version_detects_another_journal_writer_and_refuses_overwrite(self):
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); host.enqueue("fixture-user",body())
            revised=journal.latest(); revised["other_writer_revision"]=1; journal.append(revised)
            for action in (host.step,host._save,host.close):
                with self.assertRaisesRegex(PortError,"host_snapshot_stale"): action()
                self.assertEqual(journal.latest(),revised)
            self.assertEqual(self.rest.calls,[]); self.assertTrue(host._closed)

    def test_reopened_journal_fences_old_host_effect_drop_and_close(self):
        journal=self.journal()
        with journal.open():
            old=self.host(journal); old.approve_session(core.grant()); pending=old.enqueue("fixture-user",body())
        with journal.open():
            new=self.host(journal); new.approve_session(core.grant("next")); new.enqueue("fixture-user",body()); new.step()
            saved=journal.latest()
            for action in (old.step,lambda:old.drop(pending),old.close):
                with self.assertRaisesRegex(PortError,"host_generation_stale"): action()
                self.assertEqual(journal.latest(),saved)
            self.assertEqual(sum(m=="POST" for m,_,_ in self.rest.calls),1)
            self.assertEqual(new.status()["pod_id"],"pod-next"); new.close()

    def test_old_chat_release_cannot_overwrite_reopened_journal_and_close_releases_only_old_tunnel(self):
        journal=self.journal(); opened=journal.open(); opened.__enter__()
        old=self.host(journal); pending=self.start(old); headers=old.ready_request(pending)
        ticket=old.admit(headers["X-Intent-Token"],body(),pending.request_id,time.monotonic()+10,threading.Event())
        owner=journal._owner
        with self.assertRaisesRegex(CorruptCheckpoint,"journal_in_use"): opened.__exit__(None,None,None)
        self.assertIs(journal._owner,owner); old.close()
        with self.assertRaisesRegex(CorruptCheckpoint,"journal_in_use"): journal.close()
        old.release(ticket); journal.close()
        with journal.open():
            new=self.host(journal); saved=journal.latest()
            self.assertEqual(new.status()["phase"],"draining")
            old.release(ticket)
            self.assertEqual(journal.latest(),saved)
            old.close()
            self.assertEqual(journal.latest(),saved); self.assertEqual(self.ssh.closed,1); new.close()

    def test_late_create_result_after_journal_reopen_cannot_commit_or_replay(self):
        journal=self.journal(); entered=threading.Event(); release=threading.Event(); original=self.rest.request
        def blocked(*args):
            result=original(*args); entered.set(); self.assertTrue(release.wait(2)); return result
        with ThreadPoolExecutor(max_workers=1) as pool,patch.object(self.rest,"request",blocked):
            with journal.open():
                old=self.host(journal); old.approve_session(core.grant()); old.enqueue("fixture-user",body())
                future=pool.submit(old.step); self.assertTrue(entered.wait(1))
                owner=journal._owner
                with self.assertRaisesRegex(CorruptCheckpoint,"journal_in_use"): journal.close()
                with self.assertRaises(CorruptCheckpoint):
                    with journal.open(): self.fail("replacement generation")
                self.assertIs(journal._owner,owner)
                # Control mutation remains responsive while network I/O waits;
                # only generation lifetime is pinned, no mutex crosses the wait.
                old.stop("s"); release.set(); self.assertEqual(future.result(1),"created")
                self.assertEqual(old.status()["phase"],"draining"); old.close()
            with journal.open():
                new=self.host(journal); saved=journal.latest()
                self.assertEqual(new.status()["pod_id"],"pod-s")
                with self.assertRaises(PortError): new.approve_session(core.grant("next"))
                self.assertEqual(sum(m=="POST" for m,_,_ in self.rest.calls),1)
                with self.assertRaisesRegex(PortError,"host_generation_stale"): old._save()
                self.assertEqual(journal.latest(),saved)
                new.close()

    def test_journal_close_reopen_during_final_send_head_wait_refuses_generation_switch(self):
        from qmc_runpod.execution_adapters import RunPodREST,ExecutionGate
        entered=threading.Event(); release=threading.Event(); gates=[0]; posts=[]; waited=[False]
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); host.enqueue("fixture-user",body())
            owner=journal._owner; original=journal._head; original_gate=host.check_effect; case=self
            def head():
                value=original()
                if gates[0]==3 and not waited[0]:
                    waited[0]=True; entered.set(); self.assertTrue(release.wait(2))
                return value
            def gate(effect): gates[0]+=1; return original_gate(effect)
            class Connection:
                sock=None
                def __init__(self,*args): pass
                def connect(self): pass
                def request(self,*args,**kwargs): posts.append((args[0],journal._owner is owner))
                def close(self): pass
            host.rest=RunPodREST(gate=ExecutionGate(gate),address="8.8.8.8",bearer="synthetic-public-bearer-only")
            with patch.object(journal,"_head",head),patch("qmc_runpod.execution_adapters._PinnedHTTPS",Connection),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(host.step); self.assertTrue(entered.wait(1))
                try:
                    with self.assertRaisesRegex(CorruptCheckpoint,"journal_in_use"): journal.close()
                    with self.assertRaises(CorruptCheckpoint):
                        with journal.open(): self.fail("new generation")
                    self.assertIs(journal._owner,owner)
                    host._active[host._operation("s","create")].set()
                finally: release.set()
                self.assertEqual(future.result(1),"create_unresolved")
            self.assertEqual(posts,[]); self.assertEqual(journal._uses,0); host.close()

    def test_journal_close_reopen_during_host_save_head_wait_never_saves_into_new_generation(self):
        entered=threading.Event(); release=threading.Event(); reads=[0]
        journal=self.journal()
        with journal.open():
            host=self.host(journal); owner=journal._owner; original=journal._head
            def head():
                value=original(); reads[0]+=1
                if reads[0]==2: entered.set(); self.assertTrue(release.wait(2))
                return value
            with patch.object(journal,"_head",head),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(host.approve_session,core.grant()); self.assertTrue(entered.wait(1))
                try:
                    with self.assertRaisesRegex(CorruptCheckpoint,"journal_in_use"): journal.close()
                    with self.assertRaises(CorruptCheckpoint):
                        with journal.open(): self.fail("replacement while save pending")
                    self.assertIs(journal._owner,owner)
                finally: release.set()
                future.result(1)
            self.assertEqual(journal.latest()["approved"],"s"); host.close()
        with journal.open():
            new=self.host(journal); saved=journal.latest()
            with self.assertRaisesRegex(PortError,"host_generation_stale"): host._save()
            self.assertEqual(journal.latest(),saved); new.close()

    def test_journal_append_refuses_reentrant_close_between_head_advance_and_commit(self):
        with self.journal().open() as journal:
            original=journal._advance_head; owner=journal._owner; refused=[]
            def advance(*args):
                original(*args)
                with self.assertRaisesRegex(CorruptCheckpoint,"journal_in_use"): journal.close()
                with self.assertRaises(CorruptCheckpoint):
                    with journal.open(): self.fail("reentrant generation replacement")
                refused.append(True); self.assertIs(journal._owner,owner)
            with patch.object(journal,"_advance_head",advance): journal.append({"public_fixture":1})
            self.assertEqual(journal.latest(),{"public_fixture":1}); self.assertEqual(refused,[True]); self.assertEqual(journal._uses,0)

    def test_journal_setup_close_reopen_uses_same_lifecycle_mutex(self):
        journal=self.journal(); opened=journal.open(); entered=threading.Event(); release=threading.Event(); original=journal._head
        def head():
            value=original(); entered.set(); self.assertTrue(release.wait(2)); return value
        with patch.object(journal,"_head",head),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(opened.__enter__); self.assertTrue(entered.wait(1))
            try:
                with self.assertRaisesRegex(CorruptCheckpoint,"journal_in_use"): journal.close()
                with self.assertRaises(CorruptCheckpoint):
                    with journal.open(): self.fail("second setup")
            finally: release.set()
            self.assertIs(future.result(1),journal)
        opened.__exit__(None,None,None); self.assertIsNone(journal._owner)

    def test_old_open_context_exit_cannot_close_new_generation(self):
        journal=self.journal(); old=journal.open(); old.__enter__(); old_owner=journal._owner
        journal.close(); new=journal.open(); new.__enter__(); new_owner=journal._owner
        self.assertIsNot(old_owner,new_owner)
        try:
            with self.assertRaisesRegex(CorruptCheckpoint,"journal_generation_stale"): old.__exit__(None,None,None)
            self.assertIs(journal._owner,new_owner); self.assertIsNotNone(journal._db)
            journal.append({"public_new_generation":1})
        finally: new.__exit__(None,None,None)

    def test_ready_tunnel_pins_generation_until_owned_child_closes(self):
        with self.journal().open() as journal:
            host=self.host(journal); self.start(host); owner=journal._owner
            self.assertEqual(journal._uses,1)
            with self.assertRaisesRegex(CorruptCheckpoint,"journal_in_use"): journal.close()
            self.assertIs(journal._owner,owner); host.close()
            self.assertEqual(journal._uses,0); self.assertEqual(self.ssh.closed,1)

    def test_bootstrap_wait_pins_generation_without_blocking_stop(self):
        entered=threading.Event(); release=threading.Event(); original=self.ssh.run
        def run(*args):
            data=original(*args); entered.set(); self.assertTrue(release.wait(2)); return data
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); host.enqueue("fixture-user",body()); host.step()
            host.register_peer("s",SSHPeer("pod-s","8.8.8.8",12345,str(self.path/"known_hosts"),str(self.path/"key"),"SHA256:"+"a"*43))
            with patch.object(self.ssh,"run",run),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(host.step); self.assertTrue(entered.wait(1))
                try:
                    with self.assertRaisesRegex(CorruptCheckpoint,"journal_in_use"): journal.close()
                    host.stop("s")
                finally: release.set()
                self.assertEqual(future.result(1),"startup_failed")
            self.assertEqual(journal._uses,0); host.close()

    def test_delete_and_absence_read_pin_one_generation_through_both_dispatches(self):
        refused=[]; original=self.rest.request
        with self.journal().open() as journal:
            host=self.host(journal); self.start(host); self.cleanup(host); owner=journal._owner
            def request(effect,*args):
                self.assertFalse(host._lock._is_owned()); self.assertFalse(journal._lock._is_owned())
                with self.assertRaisesRegex(CorruptCheckpoint,"journal_in_use"): journal.close()
                self.assertIs(journal._owner,owner); refused.append(effect.action); return original(effect,*args)
            with patch.object(self.rest,"request",request): self.assertEqual(host.terminate("s"),"absent")
            self.assertEqual(refused,["terminate","read"]); self.assertEqual(journal._uses,0); host.close()

    def test_stream_wait_pins_generation_until_cancelled_response_releases(self):
        entered=threading.Event(); release=threading.Event(); original=self.upstream.stream
        def stream(*args,**kwargs):
            entered.set(); self.assertTrue(release.wait(2)); yield from original(*args,**kwargs)
        with self.journal().open() as journal:
            host=self.host(journal); pending=self.start(host,body(True)); headers=host.ready_request(pending)
            gateway=ProductionGateway(host,inference_key=INFERENCE,control_key=CONTROL)
            with patch.object(self.upstream,"stream",stream),gateway.serve() as address,ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(http,address,"/v1/chat/completions",body(True),rid=pending.request_id,token=headers["X-Intent-Token"])
                self.assertTrue(entered.wait(1))
                try:
                    with self.assertRaisesRegex(CorruptCheckpoint,"journal_in_use"): journal.close()
                    host.stop("s")
                finally: release.set()
                status,wire=future.result(1)
            self.assertNotEqual(status,200); self.assertNotIn(b"[DONE]",wire)
            self.assertEqual(host.release_count,1); self.assertEqual(journal._uses,1)
            host.close(); self.assertEqual(journal._uses,0)

    def test_real_cpu_process_second_start_is_refused_while_journal_owner_is_open(self):
        import subprocess
        script="""import sys
sys.path.insert(0,sys.argv[1])
from qmc_runpod.c1_authority import AuthorityJournal
from qmc_runpod.ondemand import CorruptCheckpoint
try:
    with AuthorityJournal(sys.argv[2],sys.argv[3],key=bytes.fromhex(sys.argv[4]),domain='public-single-host-fixture').open():
        raise SystemExit('unexpected second process owner')
except CorruptCheckpoint as error:
    if str(error)!='single_owner_lock_refused': raise
    print('single_owner_lock_refused')
"""
        with self.journal().open() as journal:
            host=self.host(journal); host.approve_session(core.grant()); before=journal.latest()
            for closed in (False,True):
                if closed: host.close()
                result=subprocess.run([sys.executable,"-B","-c",script,str(Path(__file__).resolve().parents[1]),
                    str(journal.path),str(journal.head_path),core.KEY.hex()],capture_output=True,text=True,timeout=10,
                    check=True,shell=False,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                self.assertEqual(result.stdout.strip(),"single_owner_lock_refused")
                self.assertEqual(journal.latest(),before)
            self.assertEqual(self.rest.calls,[])


if __name__=="__main__": unittest.main()
