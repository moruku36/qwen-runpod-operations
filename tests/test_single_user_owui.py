"""Full manual OWUI callable -> concrete host -> real local ingress fixture."""
import asyncio
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qmc_runpod.c1_ports import PortError
from qmc_runpod.execution_adapters import SSHPeer
from qmc_runpod.production_gateway import ProductionGateway
from qmc_runpod.single_user_host import SingleUserHost
from qmc_runpod.single_user_owui import IngressClient,build_dispatcher,build_remote_dispatcher,build_service,run_service
import test_c1_flow as core
import test_single_user_host as fixtures
from test_private_gateway import body


class SingleUserOWUITests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.SingleUserTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)

    def test_cold_manual_json_then_sse_through_concrete_host_and_private_ingress(self):
        async def scenario(host,gateway,address):
            client=IngressClient(host,port=address[1],fixture=True,enabled=True,inference_key=fixtures.INFERENCE,response_factory=lambda kind,value:(kind,value))
            dispatcher=build_dispatcher(host,client); user=SimpleNamespace(id="fixture-user"); request=object()
            context=dispatcher.begin(request,user)
            task=asyncio.create_task(dispatcher.final_chat(request,dict(body(),metadata={"user_id":"forged"}),user,context))
            end=time.monotonic()+2
            while host.status()["phase"]!="bootstrapping":
                if time.monotonic()>=end: self.fail("create wait")
                await asyncio.sleep(.01)
            # Explicit trusted peer enrollment; never TOFU from provider/body.
            host.register_peer("s",SSHPeer("pod-s","8.8.8.8",12345,str(self.case.path/"known_hosts"),str(self.case.path/"key"),"SHA256:"+"a"*43))
            kind,value=await task
            self.assertEqual(kind,"json"); self.assertEqual(value["choices"][0]["message"]["content"],"ok")
            context=dispatcher.begin(request,user)
            kind,events=await dispatcher.final_chat(request,body(True),user,context)
            self.assertEqual(kind,"sse")
            wire=await asyncio.to_thread(lambda:b"".join(events))
            self.assertEqual(wire.count(b"data: [DONE]"),1)
            self.assertEqual(host.release_count,2)
            self.assertEqual(sum(method=="POST" for method,_,_ in self.case.rest.calls),1)
        with self.case.journal().open() as journal:
            host=self.case.host(journal); host.approve_session(core.grant())
            gateway=ProductionGateway(host,inference_key=fixtures.INFERENCE,control_key=fixtures.CONTROL)
            with gateway.serve() as address: asyncio.run(scenario(host,gateway,address))
            self.case.cleanup(host); self.assertEqual(host.terminate("s"),"absent"); host.close()

    def test_default_service_factory_and_background_entry_are_disabled(self):
        with patch("subprocess.Popen",side_effect=AssertionError("process")):
            host,gateway,dispatcher=build_service()
            self.assertFalse(host.enabled)
            with self.assertRaises(PortError): dispatcher.begin(object(),SimpleNamespace(id="fixture-user"))
        with self.case.journal().open() as journal:
            host=self.case.host(journal); client=IngressClient(host,enabled=True,inference_key=fixtures.INFERENCE)
            dispatcher=build_dispatcher(host,client)
            with self.assertRaises(PortError): asyncio.run(dispatcher.final_chat(object(),body(),SimpleNamespace(id="fixture-user")))
            self.assertEqual(self.case.rest.calls,[]); host.close()

    def test_explicit_service_watchdog_catalog_does_not_create_and_idle_exports(self):
        with self.case.journal().open() as journal:
            host=self.case.host(journal); host.approve_session(core.grant())
            gateway=ProductionGateway(host,inference_key=fixtures.INFERENCE,control_key=fixtures.CONTROL)
            with run_service(host,gateway,port=0) as address:
                self.assertEqual(fixtures.http(address,"/v1/models")[0],200)
                self.assertEqual(self.case.rest.calls,[])
                pending=host.enqueue("fixture-user",body())
                end=time.monotonic()+2
                while host.status()["phase"]!="bootstrapping":
                    if time.monotonic()>=end: self.fail("startup")
                    time.sleep(.01)
                host.register_peer("s",SSHPeer("pod-s","8.8.8.8",12345,str(self.case.path/"known_hosts"),str(self.case.path/"key"),"SHA256:"+"a"*43))
                while host.status()["phase"]!="ready":
                    if time.monotonic()>=end: self.fail("readiness")
                    time.sleep(.01)
                self.case.clock.set(host.status()["idle_deadline"])
                while host.status()["phase"]!="approval_pending":
                    if time.monotonic()>=end: self.fail("idle export")
                    time.sleep(.01)
                self.assertTrue(host.readback("s"))
                self.assertFalse(any(method=="DELETE" for method,_,_ in self.case.rest.calls))

    def test_changed_verified_subject_cannot_restore_an_existing_grant(self):
        with self.case.journal().open() as journal:
            host=self.case.host(journal); host.approve_session(core.grant()); host.close()
        with self.case.journal().open() as journal:
            with self.assertRaisesRegex(PortError,"host_configuration_changed"):
                SingleUserHost(journal=journal,subject="different-user",enabled=True,fixture=True,export_root=self.case.path/"export",clock=self.case.clock.now,idle_seconds=10)

    def test_remote_backend_control_queue_ready_and_private_forward_full_path(self):
        async def scenario(host,address):
            dispatcher=build_remote_dispatcher(subject="fixture-user",port=address[1],fixture=True,enabled=True,
                control_key=fixtures.CONTROL,inference_key=fixtures.INFERENCE,clock=self.case.clock.now,
                response_factory=lambda kind,value:(kind,value))
            user=SimpleNamespace(id="fixture-user"); request=object(); context=dispatcher.begin(request,user)
            task=asyncio.create_task(dispatcher.final_chat(request,dict(body(),metadata={"manual":True,"subject":"wrong"}),user,context))
            end=time.monotonic()+2
            while host.status()["phase"]!="bootstrapping":
                if time.monotonic()>=end: self.fail("remote create wait")
                await asyncio.sleep(.01)
            host.register_peer("s",SSHPeer("pod-s","8.8.8.8",12345,str(self.case.path/"known_hosts"),str(self.case.path/"key"),"SHA256:"+"a"*43))
            kind,value=await task; self.assertEqual(kind,"json"); self.assertEqual(value["model"],"qwen-27b")
            context=dispatcher.begin(request,user)
            kind,events=await dispatcher.final_chat(request,body(True),user,context)
            self.assertEqual(kind,"sse")
            wire=await asyncio.to_thread(lambda:b"".join(events)); self.assertEqual(wire.count(b"data: [DONE]"),1)
            self.assertEqual(host.release_count,2)
        with self.case.journal().open() as journal:
            host=self.case.host(journal); host.approve_session(core.grant())
            gateway=ProductionGateway(host,inference_key=fixtures.INFERENCE,control_key=fixtures.CONTROL)
            with run_service(host,gateway,port=0) as address: asyncio.run(scenario(host,address))
            # Shutdown drains without auto-delete. Exact local operator approval
            # remains separate from every backend HTTP credential.
            self.assertFalse(any(method=="DELETE" for method,_,_ in self.case.rest.calls))

    def test_inference_credentials_cannot_enqueue_or_approve_delete_over_http(self):
        with self.case.journal().open() as journal:
            host=self.case.host(journal); host.approve_session(core.grant())
            gateway=ProductionGateway(host,inference_key=fixtures.INFERENCE,control_key=fixtures.CONTROL)
            with gateway.serve() as address:
                self.assertEqual(fixtures.http(address,"/_control/manual",{"subject":"fixture-user","payload":body()})[0],401)
                for path in ("/_control/approve","/_control/terminate","/_control/grant","/_control/peer"):
                    self.assertEqual(fixtures.http(address,path,{},key=fixtures.CONTROL)[0],404)
                self.assertEqual(fixtures.http(address,"/_control/manual",{"subject":"wrong-user","payload":body()},key=fixtures.CONTROL)[0],503)
            self.assertEqual(self.case.rest.calls,[]); host.close()


if __name__=="__main__": unittest.main()
