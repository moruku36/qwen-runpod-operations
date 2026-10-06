"""Concrete OWUI verified-entry -> single host -> private localhost client wiring.

All injection is operator/service-side. There is no environment or secret lookup,
installer apply, automatic thread, remote request or setting mutation on import.
"""
import asyncio
import hashlib
import http.client
import json
import math
import re
import socket
import threading
import time
from contextlib import contextmanager

from .c1_authority import encode
from .c1_ports import PortError, PodSpec, Rate
from .execution_adapters import ExecutionGate, RunPodREST, SSHExecution
from .owui_installed_dispatcher import InstalledDispatcher
from .private_chat import TunnelEndpoint
from .production_gateway import ProductionGateway
from .single_user_host import SingleUserHost
from .upstream_sse import LoopbackUpstream, SSEDecoder, StreamEnd, _Response, _strict_json


class IngressClient:
    def __init__(self,host,*,port=19180,inference_key=None,enabled=False,fixture=False,response_factory=None):
        if (host is not None and type(host) is not SingleUserHost) or type(port) is not int or not 1<=port<=65535 or port in {3001,8080,11434}: raise PortError("private_ingress_endpoint_required")
        if port!=19180 and not fixture: raise PortError("fixed_ingress_port_required")
        self.host=host; self.port=port; self._key=inference_key; self.enabled=enabled is True
        self.factory=response_factory or self._response

    @staticmethod
    def _response(kind,value):
        # Loaded only by an explicitly enabled installed OWUI request. Its
        # existing Starlette dependency supplies native JSON/stream responses.
        from starlette.responses import JSONResponse, StreamingResponse
        return JSONResponse(value) if kind=="json" else StreamingResponse(value,media_type="text/event-stream")

    def _fence(self,pending,cancel,deadline):
        if not self.enabled or self._key is None or cancel.is_set() or time.monotonic()>=deadline: raise PortError("ingress_disabled_or_expired")
        if self.host is None: return
        with self.host._lock:
            self.host._open(); row=self.host._row(pending.session_id); now=self.host._work(row); self.host._owned(row)
            if row["phase"]!="ready" or now>=row["idle_deadline"] or pending.request_id not in row["dispatched_ids"]: raise PortError("ingress_scope_expired")

    def forward(self,pending,payload,headers,cancel):
        deadline=min(pending.deadline,time.monotonic()+10)
        self._fence(pending,cancel,deadline)
        if (hashlib.sha256(encode(payload)).hexdigest()!=pending.payload_sha256
                or headers.get("X-Request-ID")!=pending.request_id or not re.fullmatch(r"[A-Za-z0-9_-]{32}",headers.get("X-Intent-Token",""))): raise PortError("bound_ready_forward_required")
        if type(self._key) is not str or not re.fullmatch(r"[A-Za-z0-9._~-]{16,256}",self._key): raise PortError("injected_inference_key_required")
        connection=http.client.HTTPConnection("127.0.0.1",self.port,timeout=max(.001,deadline-time.monotonic()))
        connection.response_class=_Response; done=threading.Event(); watcher=None; response=None
        def close():
            done.set()
            if response is not None: response.close()
            connection.close()
            if watcher is not None: watcher.join(1)
        try:
            self._fence(pending,cancel,deadline); connection.connect(); sock=connection.sock
            def watch():
                while not done.wait(.01):
                    if cancel.is_set() or time.monotonic()>=deadline:
                        try: sock.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                        return
            watcher=threading.Thread(target=watch,daemon=True); watcher.start()
            self._fence(pending,cancel,deadline)
            connection.request("POST","/v1/chat/completions",body=encode(payload),headers={**headers,
                "Authorization":"Bearer "+self._key,"Content-Type":"application/json","Connection":"close"})
            response=connection.getresponse(); self._fence(pending,cancel,deadline)
            kind="text/event-stream" if payload.get("stream",False) else "application/json"
            if response.status!=200 or response.getheader("Content-Type","").split(";",1)[0].strip()!=kind: raise PortError("private_ingress_refused")
            lengths=response.headers.get_all("Content-Length",[]); transfers=response.headers.get_all("Transfer-Encoding",[])
            if (len(lengths)>1 or len(transfers)>1 or (lengths and transfers) or (transfers and transfers!=["chunked"])
                    or (lengths and (not lengths[0].isdigit() or not 0<int(lengths[0])<=65536))): raise PortError("private_ingress_framing")
            def read():
                self._fence(pending,cancel,deadline)
                if response.isclosed():
                    if response.length not in (None,0): raise PortError("private_ingress_truncated")
                    return b""
                sock.settimeout(max(.001,deadline-time.monotonic())); chunk=response.read1(4096)
                self._fence(pending,cancel,deadline)
                if not chunk and response.length not in (None,0): raise PortError("private_ingress_truncated")
                return chunk
            if kind=="application/json":
                data=bytearray()
                while True:
                    chunk=read()
                    if not chunk: break
                    data.extend(chunk)
                    if len(data)>65536: raise PortError("private_ingress_body_bound")
                value=_strict_json(bytes(data).decode())
                if value.get("id")!=pending.request_id or value.get("model")!="qwen-27b": raise PortError("private_ingress_binding")
                result=self.factory("json",value); close(); return result
            def wire():
                decoder=SSEDecoder(pending.request_id)
                try:
                    while not decoder.done:
                        chunk=read()
                        if not chunk: break
                        for event in decoder.feed(chunk):
                            self._fence(pending,cancel,deadline); yield event.wire if type(event) is StreamEnd else event
                    decoder.finish()
                finally: close()
            return self.factory("sse",wire())
        except BaseException:
            cancel.set(); close(); raise


def build_dispatcher(host,client):
    if type(host) is not SingleUserHost or type(client) is not IngressClient: raise PortError("concrete_host_client_required")
    def verify(request,user):
        # Caller is the installer wrapper after get_verified_user; body/header
        # user IDs are never read here. Internal dispatcher lacks entry context.
        return getattr(user,"id",None) if host.enabled and not host._closed else None
    async def wait_ready(pending,cancel):
        while True:
            if cancel.is_set() or time.monotonic()>=pending.deadline: raise PortError("startup_cancelled_or_expired")
            with host._lock:
                host._open(); row=host._row(pending.session_id)
                phase=row["phase"]
            if phase=="ready": return host.ready_request(pending)
            if phase=="create_unresolved":
                with host._lock: creating=host._row()["create_operation"] in host._active
                if creating:
                    await asyncio.sleep(.01); continue
            if phase not in {"provisioning","bootstrapping","loading"}: raise PortError("startup_requires_explicit_reconciliation")
            await asyncio.to_thread(host.step)
            await asyncio.sleep(.01)
    async def forward(pending,payload,headers,cancel):
        return await asyncio.to_thread(client.forward,pending,payload,headers,cancel)
    return InstalledDispatcher(subjects={host.subject} if host.subject else frozenset(),verify=verify,
        enqueue=host.enqueue,wait_ready=wait_ready,forward=forward,drop=host.drop)


def build_remote_dispatcher(*,subject=None,port=19180,control_key=None,inference_key=None,enabled=False,
                            fixture=False,response_factory=None,clock=None):
    """Actual two-process OWUI backend path; provider keys stay in the controller.

    Only this verified server callable owns control credentials. User JSON/header
    purpose/subject claims never populate its subject. Default calls are disabled.
    """
    utc=clock or time.time
    client=IngressClient(None,port=port,inference_key=inference_key,enabled=enabled,fixture=fixture,response_factory=response_factory)
    def control(path,body):
        if not enabled or type(control_key) is not str or not re.fullmatch(r"[A-Za-z0-9._~-]{16,256}",control_key): raise PortError("remote_control_disabled")
        connection=http.client.HTTPConnection("127.0.0.1",port,timeout=2); connection.response_class=_Response
        done=threading.Event(); watchdog=None; response=None; end=time.monotonic()+2
        try:
            connection.connect(); sock=connection.sock
            def watch():
                while not done.wait(.01):
                    if time.monotonic()>=end:
                        try: sock.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                        return
            watchdog=threading.Thread(target=watch,daemon=True); watchdog.start()
            if time.monotonic()>=end: raise PortError("remote_control_expired")
            connection.request("POST",path,body=encode(body),headers={"Authorization":"Bearer "+control_key,"Content-Type":"application/json","Connection":"close"})
            response=connection.getresponse()
            if response.status!=200: raise PortError("remote_control_refused")
            data=response.read(4097)
            if len(data)>4096 or time.monotonic()>=end: raise PortError("remote_control_response_bound")
            return _strict_json(data.decode())
        except Exception: raise PortError("remote_control_refused") from None
        finally:
            done.set()
            if response is not None: response.close()
            connection.close()
            if watchdog is not None: watchdog.join(1)
    def verify(request,user): return getattr(user,"id",None) if enabled else None
    def enqueue(verified_subject,clean):
        result=control("/_control/manual",{"subject":verified_subject,"payload":clean})
        from .owui_installed_dispatcher import PendingCall
        deadline=result["work_expires_at"]
        if type(deadline) not in (int,float) or not math.isfinite(deadline): raise PortError("remote_deadline_invalid")
        return PendingCall(result["request_id"],result["session_id"],time.monotonic()+max(0,deadline-utc()),result["payload_sha256"])
    async def wait_ready(pending,cancel):
        while not cancel.is_set() and time.monotonic()<pending.deadline:
            value=await asyncio.to_thread(control,"/_control/request",{"request_id":pending.request_id})
            if value["session_id"]!=pending.session_id or value["payload_sha256"]!=pending.payload_sha256: raise PortError("remote_request_binding")
            if value.get("requires_reconciliation"): raise PortError("remote_startup_requires_reconciliation")
            if value["phase"]=="ready":
                ready=await asyncio.to_thread(control,"/_control/ready",{"request_id":pending.request_id})
                return ready["headers"]
            if value["phase"] not in {"provisioning","create_unresolved","bootstrapping","loading"}: raise PortError("remote_startup_unavailable")
            await asyncio.sleep(.05)
        raise PortError("startup_cancelled_or_expired")
    async def forward(pending,payload,headers,cancel): return await asyncio.to_thread(client.forward,pending,payload,headers,cancel)
    def drop(pending):
        try: control("/_control/drop",{"request_id":pending.request_id})
        except Exception: pass
    return InstalledDispatcher(subjects={subject} if subject else frozenset(),verify=verify,enqueue=enqueue,wait_ready=wait_ready,forward=forward,drop=drop)


def build_service(*,journal=None,export_root=None,subject=None,spec=None,rate=None,enabled=False,
                  rest_address=None,provider_bearer=None,ssh_executable=None,model_bearer=None,
                  inference_key=None,control_key=None,idle_seconds=300):
    """Explicit approved C2 injection. Disabled is the default; no files are read.

    journal must already be opened under its named service owner. Actual credentials
    are injected by an approved operator secret facility, never found by this code.
    New Pod peer enrollment is still explicit host.register_peer, not TOFU.
    """
    if enabled:
        if (type(spec) is not PodSpec or type(rate) is not Rate or spec.account.startswith("public-fixture")
                or spec.image.startswith("public-fixture") or any(v is None for v in
                (journal,export_root,subject,rest_address,provider_bearer,ssh_executable,model_bearer,inference_key,control_key))):
            raise PortError("complete_approved_service_injection_required")
    host=SingleUserHost(journal=journal,subject=subject,enabled=enabled,export_root=export_root,spec=spec,rate=rate,idle_seconds=idle_seconds,model_bearer=model_bearer)
    host.rest=RunPodREST(gate=ExecutionGate(host.check_effect),address=rest_address,bearer=provider_bearer)
    host.ssh=SSHExecution(ExecutionGate(host.check_effect),executable=ssh_executable)
    host.upstream=LoopbackUpstream(TunnelEndpoint(port=19181),allow_loopback_io=enabled,bearer=model_bearer)
    gateway=ProductionGateway(host,inference_key=inference_key,control_key=control_key,listen_approved=enabled)
    client=IngressClient(host,inference_key=inference_key,enabled=enabled)
    return host,gateway,build_dispatcher(host,client)


@contextmanager
def run_service(host,gateway,*,port=19180):
    """Explicit C2 service entry (or explicit port0 fixture), never auto-launched.

    The worker drains idle/absolute expiry and exports local readback; it does not
    delete without the separate trusted individual approve_cleanup/terminate calls.
    """
    with host._lock: host._open()
    stopped=threading.Event()
    def poll():
        while not stopped.is_set():
            try:
                result=host.step()
                if result=="exporting": host.export(host.status()["session_id"])
            except Exception:
                # Do not log raw provider/process/credential exceptions. State
                # retains unknown intent or cleanup-expired status for recovery.
                pass
            stopped.wait(.05)
    worker=threading.Thread(target=poll,name="single-user-approved-worker",daemon=False)
    with gateway.serve(port=port) as address:
        worker.start()
        try: yield address
        finally:
            stopped.set(); host.close(); worker.join(2)
            if worker.is_alive(): raise PortError("owned_worker_shutdown_incomplete")
