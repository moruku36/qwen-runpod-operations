"""Public synthetic adapters, no external connection/process/credential read."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import base64
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qmc_runpod.c1_authority import AuthorityJournal
from qmc_runpod.c1_ports import PortError, PodSpec, ServerPins
from qmc_runpod.execution_adapters import (Effect, ExecutionGate, RunPodREST, SSHPeer, SSHExecution,
    bootstrap_script, server_argv, ssh_argv, validate_pod)
from qmc_runpod.execution_adapters import verify_known_host
from qmc_runpod.production_boundary import EffectBoundary
from qmc_runpod.owui_installer import build_plan
from qmc_runpod.provider_execution import ProviderExecution
from qmc_runpod.bootstrap_execution import BootstrapExecution, load_script

KEY = b"public-boundary-test-key-32-bytes-only"

def effect(action="create", pod=None, operation="a"*64):
    return Effect("s", operation, action, pod, time.monotonic()+2)

def main_source():
    return b'''@app.post('/api/chat/completions')
@app.post('/api/v1/chat/completions')
async def chat_completion(
    request: Request,
    form_data: dict,
    user=Depends(get_verified_user),
):
    async def process_chat(request, form_data, user):
        response = await chat_completion_handler(request, form_data, user)
        return response
    return await process_chat(request, form_data, user)
generate_chat_completions = chat_completion
generate_chat_completion = chat_completion
app.state.CHAT_COMPLETION_HANDLER = chat_completion
'''

class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def journal(self): return AuthorityJournal(self.path/"effects.sqlite", self.path/"head.json", key=KEY, domain="public-boundary-fixture")

    def test_default_ports_boundary_and_installer_are_inert(self):
        with patch.object(socket, "socket", side_effect=AssertionError("socket")), patch("subprocess.Popen", side_effect=AssertionError("process")):
            rest, ssh, boundary = RunPodREST(), SSHExecution(), EffectBoundary()
            self.assertEqual(boundary.status()["operations"], {})
            with self.assertRaisesRegex(PortError, "execution_disabled"): rest.request(effect(), "POST", "/pods", b"{}", threading.Event())
            with self.assertRaisesRegex(PortError, "execution_disabled"): ssh.run(effect("load", "pod"), None, b"", threading.Event())
            with self.assertRaisesRegex(PortError, "production_boundary_disabled"): boundary.execute(effect(), lambda *args: True)
            source = main_source(); plan = build_plan(source, version="0.11.4", expected_sha256=hashlib.sha256(source).hexdigest())
            self.assertEqual(plan.original, source)
            self.assertIn(b"DisabledDispatcher", plan.hook)

    def test_effect_identity_retained_pod_and_nonfinite_deadline_rejected(self):
        for pod, deadline in (("synthetic-held-pod", time.monotonic()+1), ("pod", float("nan")), ("pod", float("inf"))):
            with self.assertRaises(PortError): Effect("s", "a"*64, "read", pod, deadline)

    def test_gate_rechecks_deadline_after_slow_approval(self):
        e = Effect("s", "a"*64, "create", None, time.monotonic()+.01)
        def check(effect): time.sleep(.015); return True
        with self.assertRaises(PortError): ExecutionGate(check).check(e, threading.Event())

    def test_unapproved_mismatch_and_private_endpoint_never_connect(self):
        with patch.object(socket, "socket", side_effect=AssertionError("connect")):
            for checker, address, method in ((lambda e: False, "8.8.8.8", "POST"), (lambda e: True, "127.0.0.1", "POST"), (lambda e: True, "8.8.8.8", "DELETE")):
                rest = RunPodREST(gate=ExecutionGate(checker), address=address, bearer="synthetic-bearer-public-only")
                with self.assertRaises(PortError): rest.request(effect(), method, "/pods", b"{}", threading.Event())

    def test_rest_one_request_strips_exception_and_response_bound(self):
        class Response:
            status = 200
            def read(self, n): return b"x"*4096
        class Connection:
            sock = None
            calls = []
            def __init__(self, *args): pass
            def connect(self): pass
            def request(self, *args, **kwargs): self.calls.append(args[:2])
            def getresponse(self): return Response()
            def close(self): pass
        rest = RunPodREST(gate=ExecutionGate(lambda e: True), address="8.8.8.8", bearer="synthetic-bearer-public-only")
        with patch("qmc_runpod.execution_adapters._PinnedHTTPS", Connection):
            with self.assertRaisesRegex(PortError, "provider_outcome_unknown"): rest.request(effect(), "POST", "/pods", b"{}", threading.Event())
        self.assertEqual(Connection.calls, [("POST", "/v1/pods")])
        self.assertNotIn("synthetic", repr(rest))

    def test_pod_validation_requires_account_spec_price_and_exact_id(self):
        spec = PodSpec()
        row = {"id":"pod", "consumerUserId":spec.account, "name":"qmc-"+hashlib.sha256(b"s").hexdigest()[:24],
            "image":spec.image, "interruptible":False, "gpu":{"count":1}, "machine":{"gpuTypeId":spec.gpu_type},
            "volumeInGb":80, "containerDiskInGb":20, "networkVolume":None, "costPerHr":"1.80", "env":{"secret":"excluded"}}
        proof = validate_pod(row, spec, "s", pod_id="pod")
        self.assertEqual(set(proof), {"id", "account", "cost_per_hour"}); self.assertNotIn("secret", repr(proof))
        for key, value in (("consumerUserId","other"), ("costPerHr","Infinity"), ("costPerHr","1.81"), ("image","other"), ("id","synthetic-held-pod")):
            with self.subTest(key=key):
                with self.assertRaises(PortError): validate_pod(dict(row, **{key:value}), spec, "s", pod_id="pod")

    def test_pod_gpu_count_and_cost_bool_are_not_numeric_proof(self):
        spec=PodSpec()
        row={"id":"pod","consumerUserId":spec.account,"name":"qmc-"+hashlib.sha256(b"s").hexdigest()[:24],
            "image":spec.image,"interruptible":False,"gpu":{"count":1},"machine":{"gpuTypeId":spec.gpu_type},
            "volumeInGb":80,"containerDiskInGb":20,"costPerHr":"1.80"}
        for altered in (dict(row,gpu={"count":True}),dict(row,costPerHr=True),dict(row,costPerHr=1),dict(row,volumeInGb=True),dict(row,containerDiskInGb=True)):
            with self.assertRaisesRegex(PortError,"pod_proof_invalid"): validate_pod(altered,spec,"s")

    def test_bootstrap_and_ssh_are_fixed_private_and_injection_resistant(self):
        peer = SSHPeer("pod", "8.8.8.8", 12345, str(self.path/"known_hosts"), str(self.path/"key"), "SHA256:"+"a"*43)
        argv = ssh_argv(peer, forward=True)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("127.0.0.1:19181:127.0.0.1:8080", argv)
        self.assertNotIn("0.0.0.0", " ".join(argv))
        script = bootstrap_script("s", "pod")
        self.assertIn(ServerPins().model_sha256.encode(), script)
        self.assertIn(b"test -s /run/qmc/model-api-key", script)
        self.assertIn("--api-key-file", server_argv("s"))
        for sid in ("s;rm", "s\nnext", "../s"):
            with self.assertRaises(PortError): bootstrap_script(sid, "pod")
        self.assertFalse((self.path/"key").exists())

    def test_boundary_reserves_before_io_and_commits_after_io(self):
        with self.journal().open() as journal:
            committed=[]; boundary=EffectBoundary(journal=journal, check=lambda e: True, commit=lambda e,v: committed.append(v) or True)
            def invoke(e,cancel,fence):
                self.assertEqual(journal.latest()["operations"][e.operation_id]["outcome"], "unknown")
                self.assertFalse(boundary._lock._is_owned()); fence(); return "normalized-proof"
            self.assertEqual(boundary.execute(effect(), invoke).outcome, "committed")
            self.assertEqual(committed, ["normalized-proof"])
            with self.assertRaisesRegex(PortError, "effect_not_replayable"): boundary.execute(effect(), invoke)

    def test_close_during_io_cancels_and_never_commits_successor(self):
        with self.journal().open() as journal:
            committed=[]; boundary=EffectBoundary(journal=journal, check=lambda e: True, commit=lambda e,v: committed.append(v) or True)
            entered=threading.Event(); release=threading.Event()
            def invoke(e,cancel,fence): entered.set(); self.assertTrue(release.wait(1)); self.assertTrue(cancel.is_set()); return "late-pod-proof"
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(boundary.execute, effect(), invoke); self.assertTrue(entered.wait(1))
                boundary.close(); release.set(); self.assertEqual(future.result(1).outcome, "late_result")
            self.assertEqual(committed, [])

    def test_wrong_session_stop_does_not_cancel_and_exact_stop_does(self):
        with self.journal().open() as journal:
            boundary=EffectBoundary(journal=journal, check=lambda e: True, commit=lambda e,v: True)
            entered=threading.Event(); release=threading.Event()
            def invoke(e,cancel,fence):
                entered.set(); self.assertTrue(release.wait(1)); return not cancel.is_set()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(boundary.execute,effect(),invoke); self.assertTrue(entered.wait(1))
                boundary.stop("other"); self.assertFalse(next(iter(boundary._active.values())).is_set())
                boundary.stop("s"); release.set(); self.assertEqual(future.result(1).outcome, "late_result")

    def test_real_reservation_crash_restores_unknown_and_cannot_replay(self):
        class Crash(BaseException): pass
        with self.journal().open() as journal:
            boundary=EffectBoundary(journal=journal, check=lambda e: True, commit=lambda e,v: True)
            def invoke(*args): raise Crash()
            with self.assertRaises(Crash): boundary.execute(effect(),invoke)
        with self.journal().open() as journal:
            boundary=EffectBoundary(journal=journal, check=lambda e: True, commit=lambda e,v: True)
            self.assertEqual(boundary.status()["operations"]["a"*64]["outcome"], "unknown")
            with self.assertRaises(PortError): boundary.execute(effect(),lambda *args: self.fail("replay"))

    def test_default_restore_does_not_restore_activation(self):
        with self.journal().open() as journal:
            boundary=EffectBoundary(journal=journal, check=lambda e: True, commit=lambda e,v: True)
            boundary.execute(effect(),lambda *args: (_ for _ in ()).throw(RuntimeError("secret-body")))
        with self.journal().open() as journal:
            boundary=EffectBoundary(journal=journal)
            with self.assertRaises(PortError): boundary.execute(effect(operation="b"*64), lambda *args: self.fail("I/O"))
            self.assertNotIn("secret-body", repr(journal.latest()))

    def test_installer_cas_version_shape_and_disabled_backup_plan(self):
        source=main_source(); digest=hashlib.sha256(source).hexdigest()
        plan=build_plan(source,version="0.11.4",expected_sha256=digest)
        stage=plan.stage(self.path/"new-stage")
        self.assertEqual((stage/"original-main.py").read_bytes(), source)
        manifest=json.loads((stage/"manifest.json").read_text())
        self.assertFalse(manifest["enabled"]); self.assertFalse(manifest["installation_performed"])
        self.assertEqual(manifest["configuration_changes"], [])
        self.assertIn(b"app.state.CHAT_COMPLETION_HANDLER = qmc_original_chat_completion",plan.patched)
        self.assertIn(b"context=qmc_context",plan.patched)
        with self.assertRaises(PortError): plan.stage(stage)
        for version, expected in (("0.11.3",digest), ("0.11.4","0"*64)):
            with self.assertRaises(PortError): build_plan(source,version=version,expected_sha256=expected)
        altered=source.replace(b"get_verified_user", b"get_current_user")
        with self.assertRaises(PortError): build_plan(altered,version="0.11.4",expected_sha256=hashlib.sha256(altered).hexdigest())

    def test_provider_adapter_connects_plans_boundary_and_normalized_commit(self):
        from qmc_runpod.c1_ports import ProviderPlans
        plans=ProviderPlans(); committed=[]
        class Transport:
            calls=[]
            def request(self,e,method,path,body,cancel):
                self.calls.append((method,path))
                spec=plans.spec
                return {"id":"pod","consumerUserId":spec.account,"name":"qmc-"+hashlib.sha256(b"s").hexdigest()[:24],
                    "image":spec.image,"interruptible":False,"gpu":{"count":1},"machine":{"gpuTypeId":spec.gpu_type},
                    "volumeInGb":80,"containerDiskInGb":20,"costPerHr":"1.80","env":{"api_key":"never-retained"}}
        def commit(e,value):
            committed.append(value)
            plans.acknowledge(e.session_id,e.operation_id,{"id":value["id"]},account=value["account"])
            return True
        with self.journal().open() as journal:
            boundary=EffectBoundary(journal=journal,check=lambda e:True,commit=commit)
            adapter=ProviderExecution(plans=plans,boundary=boundary,transport=Transport())
            self.assertEqual(adapter.create("s","a"*64,time.monotonic()+2).outcome,"committed")
            self.assertEqual(Transport.calls,[("POST","/pods")]); plans.check_owner("s","pod")
            self.assertNotIn("api_key",repr(committed))
            with self.assertRaises(PortError): adapter.create("s","a"*64,time.monotonic()+2)
            with self.assertRaises(PortError): adapter.terminate("s","pod","b"*64,time.monotonic()+2,
                approval=None,readback=None,allowlist=frozenset({"pod"}))

    def test_bootstrap_adapter_validates_remote_pins_and_identity_before_commit(self):
        peer=SSHPeer("pod","8.8.8.8",12345,str(self.path/"known_hosts"),str(self.path/"key"),"SHA256:"+"a"*43)
        pins=ServerPins(); committed=[]
        row={"session_id":"s","pod_id":"pod","model_sha256":pins.model_sha256,"llama_commit":pins.llama_commit,"binary_sha256":"c"*64}
        class SSH:
            def run(self,e,peer,script,cancel): return json.dumps(row).encode()
        with self.journal().open() as journal:
            boundary=EffectBoundary(journal=journal,check=lambda e:True,commit=lambda e,v:committed.append(v) or True)
            adapter=BootstrapExecution(boundary=boundary,ssh=SSH())
            self.assertEqual(adapter.bootstrap(effect("bootstrap","pod"),peer).outcome,"committed")
            row["pod_id"]="other"
            self.assertEqual(adapter.bootstrap(effect("bootstrap","pod","b"*64),peer).outcome,"unknown")
            self.assertEqual(len(committed),1)
        script=load_script(effect("load","pod"),"c"*64)
        self.assertIn(b"/v1/models",script); self.assertIn(b"/health",script)
        self.assertNotIn(b"curl -H",script)

    def test_boundary_reconcile_cannot_extend_original_deadline(self):
        e=effect()
        with self.journal().open() as journal:
            boundary=EffectBoundary(journal=journal,check=lambda e:True,commit=lambda e,v:True)
            boundary.execute(e,lambda *args: (_ for _ in ()).throw(RuntimeError("unknown")))
            extended=Effect(e.session_id,e.operation_id,e.action,e.pod_id,e.deadline+100)
            with self.assertRaisesRegex(PortError,"original_effect_required"):
                boundary.reconcile(extended,lambda *args:self.fail("observe"),verified_commit=lambda *args:True)

    def test_ssh_gate_cancellation_and_retained_peer_precede_process(self):
        peer=SSHPeer("pod","8.8.8.8",12345,str(self.path/"known_hosts"),str(self.path/"key"),"SHA256:"+"a"*43)
        cancel=threading.Event(); cancel.set()
        with patch("subprocess.Popen",side_effect=AssertionError("process")):
            with self.assertRaises(PortError): SSHExecution(ExecutionGate(lambda e:True)).run(effect("load","pod"),peer,b"exit 0",cancel)
        with self.assertRaises(PortError): SSHPeer("synthetic-held-pod","8.8.8.8",12345,peer.known_hosts,peer.private_key,peer.fingerprint)

    def test_host_fingerprint_is_bound_to_exact_supplied_public_key(self):
        key=b"public synthetic host key bytes only"
        fingerprint="SHA256:"+base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip("=")
        peer=SSHPeer("pod","8.8.8.8",12345,str(self.path/"known_hosts"),str(self.path/"key"),fingerprint)
        data=b"[8.8.8.8]:12345 ssh-ed25519 "+base64.b64encode(key)+b"\n"
        verify_known_host(peer,data)
        for value in (data.replace(b"8.8.8.8",b"9.9.9.9"),data+b"other key value\n",data.replace(base64.b64encode(key),base64.b64encode(b"different synthetic public key"))):
            with self.assertRaisesRegex(PortError,"host_key_not_proven"): verify_known_host(peer,value)

    def test_ssh_post_lookup_cancel_or_expiry_prevents_run_and_tunnel_process(self):
        peer=SSHPeer("pod","8.8.8.8",12345,str(self.path/"known_hosts"),str(self.path/"key"),"SHA256:"+"a"*43)
        for mode in ("run","tunnel"):
            for failure in ("cancel","expiry"):
                with self.subTest(mode=mode,failure=failure):
                    cancel=threading.Event(); e=effect("load","pod")
                    ssh=SSHExecution(ExecutionGate(lambda e:True))
                    def lookup(*args,**kwargs):
                        if failure=="cancel": cancel.set()
                        else: object.__setattr__(e,"deadline",time.monotonic()-1)
                        return ("public-fake-ssh",)
                    with patch.object(ssh,"_argv",lookup),patch("subprocess.Popen") as popen:
                        with self.assertRaises(PortError):
                            if mode=="run": ssh.run(e,peer,b"public-script",cancel)
                            else:
                                with ssh.tunnel(e,peer,cancel): self.fail("tunnel")
                        popen.assert_not_called()

    def test_ssh_post_process_cancel_prevents_script_stdin_send(self):
        peer=SSHPeer("pod","8.8.8.8",12345,str(self.path/"known_hosts"),str(self.path/"key"),"SHA256:"+"a"*43)
        cancel=threading.Event(); ssh=SSHExecution(ExecutionGate(lambda e:True)); writes=[]
        class Input(io.BytesIO):
            def write(self,data): writes.append(data); return super().write(data)
        class Process:
            stdin=Input(); stdout=io.BytesIO(); returncode=0
            def poll(self): return 0
        def create(*args,**kwargs): cancel.set(); return Process()
        with patch.object(ssh,"_argv",return_value=("public-fake-ssh",)),patch("subprocess.Popen",create):
            with self.assertRaisesRegex(PortError,"bootstrap_outcome_unknown"): ssh.run(effect("load","pod"),peer,b"public-script",cancel)
        self.assertEqual(writes,[])


if __name__ == "__main__": unittest.main()
