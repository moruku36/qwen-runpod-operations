"""Explicit full one-user fake-only smoke; only localhost port0 is served."""
import asyncio
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests")]
from qmc_runpod.execution_adapters import SSHPeer
from qmc_runpod.ondemand import TerminationApproval
from qmc_runpod.production_gateway import ProductionGateway
from qmc_runpod.single_user_owui import build_remote_dispatcher,run_service
import test_c1_flow as core
import test_single_user_host as fixtures
from test_private_gateway import body

checks=[]; case=fixtures.SingleUserTests(); case.setUp()
try:
    with case.journal().open() as journal:
        host=case.host(journal); host.approve_session(core.grant())
        gateway=ProductionGateway(host,inference_key=fixtures.INFERENCE,control_key=fixtures.CONTROL)
        with run_service(host,gateway,port=0) as address:
            checks.append(fixtures.http(address,"/v1/models")[0]==200 and case.rest.calls==[])
            async def manual(sid,stream=False):
                dispatcher=build_remote_dispatcher(subject="fixture-user",port=address[1],fixture=True,enabled=True,
                    control_key=fixtures.CONTROL,inference_key=fixtures.INFERENCE,clock=case.clock.now,
                    response_factory=lambda kind,value:(kind,value))
                user=SimpleNamespace(id="fixture-user"); request=object(); context=dispatcher.begin(request,user)
                task=asyncio.create_task(dispatcher.final_chat(request,body(stream),user,context))
                if host.status()["phase"]!="ready":
                    end=time.monotonic()+2
                    while host.status()["phase"]!="bootstrapping":
                        assert time.monotonic()<end; await asyncio.sleep(.01)
                    host.register_peer(sid,SSHPeer("pod-"+sid,"8.8.8.8",12345,str(case.path/"known_hosts"),str(case.path/"key"),"SHA256:"+"a"*43))
                kind,value=await task
                return value if kind=="json" else await asyncio.to_thread(lambda:b"".join(value))
            result=asyncio.run(manual("s")); checks.append(result["choices"][0]["message"]["content"]=="ok")
            wire=asyncio.run(manual("s",True)); checks.append(wire.count(b"data: [DONE]")==1)
            end=time.monotonic()+2
            while host.release_count<2:
                assert time.monotonic()<end; time.sleep(.01)
            checks.append(host.release_count==2 and sum(method=="POST" for method,_,_ in case.rest.calls)==1)
            case.clock.set(host.status()["idle_deadline"]); end=time.monotonic()+2
            while host.status()["phase"]!="approval_pending":
                assert time.monotonic()<end; time.sleep(.01)
            checks.append(host.readback("s") and not any(method=="DELETE" for method,_,_ in case.rest.calls))
            row=host._row(); receipt=row["receipt"]
            report=json.loads((host.export_root/receipt["name"]).read_text())
            checks.append(len(report["inference_results"])==2 and all(r["outcome"]=="verified_written" for r in report["inference_results"]))
            approval=TerminationApproval("public-delete-s","terminate","s",row["pod_id"],receipt["sha256"],row["cleanup_deadline"])
            host.approve_cleanup(approval); checks.append(host.terminate("s")=="absent")
            checks.append(host.readback("s") and sum(method=="DELETE" for method,_,_ in case.rest.calls)==1)
            host.approve_session(core.grant("next")); result=asyncio.run(manual("next"))
            checks.append(result["model"]=="qwen-27b" and sum(method=="POST" for method,_,_ in case.rest.calls)==2)
            checks.append(all("synthetic-held-pod" not in path for method,path,sid in case.rest.calls))
        assert len(checks)==10 and all(checks)
        print(json.dumps({"simulated":True,"checks":checks,"real_provider_calls":0,"real_ssh_processes":0,
                          "real_credential_reads":0,"installed_owui_changes":0,"manual_sessions":2},indent=2))
finally: case.doCleanups()
