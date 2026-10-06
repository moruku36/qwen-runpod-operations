"""Concrete private ingress for SingleUserHost; no mock controller/type-gate swap.

Only neutral reviewed HTTP framing/serialization helpers are reused. No inherited
constructor, mock provider, mock state, public lifecycle or HTTP grant route exists.
"""
from contextlib import contextmanager
import hmac
import json
import math
import re
import select
import socket
import threading
import time
import uuid

from .c1_ports import PortError
from .mock_gateway import MockGateway, GatewayError, StreamLimits, _Stream, _identity
from .private_gateway import PrivateGateway
from .private_chat import PrivateChatError
from .single_user_host import SingleUserHost
from .upstream_sse import _strict_json, prepare, StreamEnd


class ProductionGateway:
    # Transport helpers have no provider/controller dependencies or type gates.
    _receive=staticmethod(MockGateway._receive)
    _read=MockGateway._read
    _json_write=PrivateGateway._json_write
    _watch_client=PrivateGateway._watch_client
    _frames=PrivateGateway._frames
    _stream_frame=staticmethod(MockGateway._stream_frame)
    _stream_budget=staticmethod(MockGateway._stream_budget)

    def __init__(self,host=None,*,inference_key=None,control_key=None,listen_approved=False,
                 processing_seconds=10,stream_limits=None):
        if host is not None and type(host) is not SingleUserHost: raise PortError("single_user_host_required")
        for key in (inference_key,control_key):
            if key is not None and (type(key) is not str or not re.fullmatch(r"[A-Za-z0-9._~-]{16,256}",key)): raise PortError("invalid_injected_key")
        if inference_key is not None and control_key is not None and hmac.compare_digest(inference_key,control_key): raise PortError("distinct_role_keys_required")
        if type(processing_seconds) not in (int,float) or not math.isfinite(processing_seconds) or not 0<processing_seconds<=30: raise PortError("invalid_processing_deadline")
        self.host=host; self._inference_key=inference_key; self._control_key=control_key; self.listen_approved=listen_approved is True
        self.header_timeout=self.body_timeout=.5; self.socket_timeout=.5; self.processing_timeout=float(processing_seconds)
        self.stream_limits=stream_limits or StreamLimits(content_bytes=8192,output_bytes=65536,events=1024,frame_bytes=2048,piece_chars=64,total_seconds=10,write_seconds=.5)
        if type(self.stream_limits) is not StreamLimits: raise PortError("bounded_stream_limits_required")
        self._closing=threading.Event(); self._serving=False; self._workers=[]
        self._socket_lock=threading.Lock(); self._active_sockets=set(); self._connections=threading.BoundedSemaphore(16)
        self._log_lock=threading.Lock(); self._logs=[]

    @contextmanager
    def serve(self,*,host="127.0.0.1",port=0):
        if host!="127.0.0.1" or type(port) is not int or port not in {0,19180}: raise PortError("private_fixed_or_fixture_port_required")
        if port!=0 and not self.listen_approved: raise PortError("production_listen_disabled")
        with MockGateway.serve(self,host=host,port=port) as address: yield address

    def _handle(self,sock):
        if not self._connections.acquire(False): return
        try: self._handle_request(sock)
        finally: self._connections.release()

    def _authorize(self,path,headers):
        if path=="/healthz": return
        key=self._control_key if path.startswith("/_control/") else self._inference_key
        if key is None or not hmac.compare_digest(headers.get("authorization",""),"Bearer "+key): raise GatewayError(401,"unauthorized")

    def _guard(self,sock,ticket):
        if self._closing.is_set(): ticket.cancel.set()
        readable,_,_=select.select([sock],[],[],0)
        if readable: ticket.cancel.set()
        self.host.chat_fence(ticket)

    def _write(self,sock,ticket,wire,committed):
        until=min(ticket.deadline,time.monotonic()+self.stream_limits.write_seconds)
        sock.setblocking(False); pending=memoryview(wire)
        while pending:
            self._guard(sock,ticket)
            left=until-time.monotonic()
            if left<=0: raise GatewayError(504,"response_write_timeout")
            _,ready,_=select.select([],[sock],[],min(.01,left))
            if not ready: continue
            self._guard(sock,ticket)
            if time.monotonic()>=until: raise GatewayError(504,"response_write_timeout")
            try: sent=sock.send(pending)
            except BlockingIOError: continue
            if sent<=0: raise GatewayError(499,"client_disconnected")
            committed[0]=True; pending=pending[sent:]

    def _chat(self,sock,payload,rid,token,committed):
        clean=prepare(payload,rid); deadline=time.monotonic()+self.processing_timeout
        if clean["stream"]: deadline=min(deadline,time.monotonic()+self.stream_limits.total_seconds)
        cancel=threading.Event(); ticket=self.host.admit(token,clean,rid,deadline,cancel)
        complete=False; content_bytes=0
        try:
            with self._watch_client(sock,cancel,deadline):
                fence=lambda:self.host.chat_fence(ticket)
                if not clean["stream"]:
                    content=self.host.upstream.json(clean,rid,deadline=deadline,cancel=cancel,fence=fence)
                    value={"id":rid,"object":"chat.completion","model":"qwen-27b",
                        "choices":[{"index":0,"message":{"role":"assistant","content":content},"finish_reason":"stop"}]}
                    self._json_write(sock,200,value,deadline=deadline,committed=committed,fence=lambda:self._guard(sock,ticket))
                    content_bytes=len(content.encode("utf-8")); complete=True
                    return
                events=self.host.upstream.stream(clean,rid,deadline=deadline,cancel=cancel,fence=fence)
                stream=_Stream(ticket,cancel,"",deadline,self.stream_limits)
                try:
                    first=next(events) # Validate upstream before committing 200.
                    self._write(sock,ticket,b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",committed)
                    event=first
                    while True:
                        if type(event) is StreamEnd: event=event.wire
                        for wire,terminal in self._frames(stream,event):
                            self._stream_budget(stream,wire,events=2 if terminal else 1)
                            if terminal: self.host.chat_fence(ticket,claim_terminal=True)
                            self._write(sock,ticket,wire,committed)
                            if terminal:
                                content_bytes=getattr(stream,"content_bytes",0); complete=True; return
                        event=next(events)
                finally:
                    events.close()
        finally:
            self.host.release(ticket,successful=complete,content_bytes=content_bytes,stream=clean["stream"])
            cancel.set()

    def _handle_request(self,sock):
        rid=uuid.uuid4().hex; path="unknown"; status=500; outcome="internal_error"; committed=[False]
        try:
            method,path,headers,data=self._read(sock)
            routes={"/healthz","/v1/models","/status","/v1/chat/completions","/_control/stop",
                    "/_control/manual","/_control/request","/_control/ready","/_control/drop"}
            if path not in routes: raise GatewayError(404,"not_found")
            self._authorize(path,headers)
            if method!=("POST" if path=="/v1/chat/completions" or path.startswith("/_control/") else "GET"): raise GatewayError(405,"method_not_allowed")
            if "x-request-id" in headers: rid=_identity(headers["x-request-id"])
            if path=="/healthz": value={"alive":True}
            elif path=="/v1/models": value={"object":"list","data":[{"id":"qwen-27b","object":"model","owned_by":"private"}]}
            elif path=="/status": value=self.host.status() if self.host is not None else {"phase":"absent","enabled":False}
            else:
                if headers.get("content-type","").lower()!="application/json": raise GatewayError(415,"unsupported_media_type")
                value=_strict_json(data.decode("utf-8"))
                if type(value) is not dict: raise GatewayError(400,"invalid_json")
                if self.host is None: raise GatewayError(503,"backend_not_ready")
                if path=="/_control/stop":
                    if set(value)!={"session_id"}: raise GatewayError(409,"control_conflict")
                    self.host.stop(value["session_id"]); value=self.host.status()
                elif path=="/_control/manual":
                    if set(value)!={"subject","payload"}: raise GatewayError(400,"unsupported_input")
                    pending=self.host.enqueue(value["subject"],value["payload"])
                    value={"request_id":pending.request_id,"session_id":pending.session_id,
                           "payload_sha256":pending.payload_sha256,"work_expires_at":self.host.status()["deadline"]}
                elif path.startswith("/_control/"):
                    if set(value)!={"request_id"}: raise GatewayError(400,"unsupported_input")
                    rid=_identity(value["request_id"])
                    with self.host._lock:
                        item=self.host._queued.get(rid)
                        if item is None: raise GatewayError(404,"request_unavailable")
                        row=self.host._row(item[0])
                        from .owui_installed_dispatcher import PendingCall
                        pending=PendingCall(rid,item[0],time.monotonic()+max(0,row["deadline"]-self.host._now_raw()),item[2])
                        if path=="/_control/request":
                            value={"request_id":rid,"session_id":item[0],"phase":row["phase"],
                                   "work_expires_at":row["deadline"],"payload_sha256":item[2],
                                   "requires_reconciliation":row["phase"]=="create_unresolved" and row["create_operation"] not in self.host._active}
                        elif path=="/_control/ready": value={"headers":self.host.ready_request(pending)}
                        else: value={"dropped":self.host.drop(pending)}
                else:
                    if "x-request-id" not in headers: raise GatewayError(400,"request_id_required")
                    self._chat(sock,value,rid,headers.get("x-intent-token",""),committed)
                    status=200; outcome="success"; return
            self._json_write(sock,200,value,committed=committed); status=200; outcome="success"
        except GatewayError as error: status,outcome=error.status,error.code
        except PortError as error:
            code=error.args[0] if error.args else ""
            status,outcome={"interactive_intent_required":(403,"interactive_intent_required"),"stale_session":(409,"control_conflict"),
                "backend_busy_or_replay":(429,"backend_busy"),"host_disabled":(503,"backend_not_ready")}.get(code,(503,"backend_not_ready"))
        except PrivateChatError: status,outcome=502,"upstream_refused"
        except (UnicodeError,ValueError): status,outcome=400,"invalid_json"
        except OSError: status,outcome=499,"client_disconnected"
        except Exception: status,outcome=500,"internal_error"
        finally:
            if status>=400 and status!=499 and not committed[0]:
                try: self._json_write(sock,status,{"error":{"code":outcome,"message":outcome},"request_id":rid})
                except Exception: pass
            with self._log_lock:
                self._logs.append({"id":rid,"route":path if path in {"/healthz","/v1/models","/status","/v1/chat/completions","/_control/stop","/_control/manual","/_control/request","/_control/ready","/_control/drop"} else "unknown","status":status})
                del self._logs[:-256]

    def logs(self):
        with self._log_lock: return [dict(row) for row in self._logs]
