"""Installed callable projection wired to explicit offline lifecycle/HTTP fixtures."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qmc_runpod.c1_ports import PortError
from qmc_runpod.owui_installed_dispatcher import EntryContext, InstalledDispatcher, PendingCall
from qmc_runpod.owui_intent_policy import ServerPurpose
from qmc_runpod.upstream_sse import prepare
import test_c1_flow as core
from test_private_gateway import body, request


class InstalledDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.case=core.C1FlowTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.runtime=self.case.runtime(); self.runtime.approve_session(core.grant())
        self.req=object(); self.dropped=[]; self.forwarded=[]

    def dispatcher(self, wait=None):
        runtime=self.runtime
        def enqueue(subject, clean):
            context=runtime.policy.begin(self.req, server_route="/api/chat/completions", server_purpose=ServerPurpose.MANUAL_CHAT)
            runtime.accept(context,clean,"r",session_id="s")
            remaining=runtime.controller.store.snapshot()["session"]["deadline"]-runtime.controller.clock.now()
            digest=hashlib.sha256(json.dumps(clean,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
            return PendingCall("r","s",time.monotonic()+remaining,digest)
        async def ready(pending,cancel):
            if cancel.is_set(): raise PortError("cancelled")
            self.case.startup(runtime)
            return runtime.ready_request(pending.request_id)[1]
        async def forward(pending,payload,headers,cancel):
            if cancel.is_set(): raise PortError("cancelled")
            self.forwarded.append(pending.request_id)
            with runtime.gateway.serve() as address:
                return request(address,"POST","/v1/chat/completions",payload,rid=pending.request_id,token=headers["X-Intent-Token"])
        def drop(pending): self.dropped.append(pending.request_id); runtime.drop(pending.request_id)
        return InstalledDispatcher(subjects={"fixture-user"},verify=lambda req,user:"fixture-user" if req is self.req and user=="verified-user" else None,
            enqueue=enqueue,wait_ready=wait or ready,forward=forward,drop=drop)

    def test_final_manual_pipeline_queues_and_forwards_through_private_http(self):
        dispatcher=self.dispatcher(); context=dispatcher.begin(self.req,"verified-user")
        payload=dict(body(),metadata={"manual":False,"user_id":"forged"},chat_id="bookkeeping")
        result=asyncio.run(dispatcher.final_chat(self.req,payload,"verified-user",context))
        self.assertEqual(result[0],200); self.assertEqual(self.forwarded,["r"])
        self.assertEqual(sum(a=="create" for a,_ in self.runtime.controller.provider.calls),1)
        self.assertEqual(self.runtime.controller.http_release_count,1)

    def test_background_route_reuse_without_registered_context_never_creates(self):
        dispatcher=self.dispatcher()
        with self.assertRaises(PortError): asyncio.run(dispatcher.final_chat(self.req,body(),"verified-user"))
        self.assertEqual(self.runtime.controller.provider.calls,[])

    def test_forged_context_and_wrong_user_never_enqueue(self):
        dispatcher=self.dispatcher(); context=dispatcher.begin(self.req,"verified-user")
        fake=EntryContext(context.sequence,context.subject,context.deadline,context.request)
        for value,user in ((fake,"verified-user"),(context,"wrong-user")):
            with self.assertRaises(PortError): asyncio.run(dispatcher.final_chat(self.req,body(),user,value))
        self.assertEqual(self.runtime.status()["queued"],0)

    def test_tools_or_other_model_cannot_start(self):
        for payload in (dict(body(),tools=[{"name":"exec"}]),dict(body(),model="different")):
            dispatcher=self.dispatcher(); context=dispatcher.begin(self.req,"verified-user")
            with self.assertRaises(Exception): asyncio.run(dispatcher.final_chat(self.req,payload,"verified-user",context))
        self.assertEqual(self.runtime.controller.provider.calls,[])

    def test_close_during_ready_wait_cancels_without_create_or_forward(self):
        async def scenario():
            entered=asyncio.Event(); release=asyncio.Event()
            async def wait(pending,cancel):
                entered.set(); await release.wait()
                if cancel.is_set(): raise PortError("cancelled")
                self.fail("startup after close")
            dispatcher=self.dispatcher(wait); context=dispatcher.begin(self.req,"verified-user")
            task=asyncio.create_task(dispatcher.final_chat(self.req,body(),"verified-user",context))
            await entered.wait(); dispatcher.close(); release.set()
            with self.assertRaises(PortError): await task
        asyncio.run(scenario())
        self.assertEqual(self.forwarded,[]); self.assertEqual(self.dropped,["r"])
        self.assertEqual(self.runtime.controller.provider.calls,[])

    def test_default_constructor_is_disabled(self):
        dispatcher=InstalledDispatcher()
        with self.assertRaisesRegex(PortError,"installed_integration_disabled"): dispatcher.begin(self.req,"verified-user")
        self.assertEqual(self.runtime.controller.provider.calls,[])


if __name__ == "__main__": unittest.main()
