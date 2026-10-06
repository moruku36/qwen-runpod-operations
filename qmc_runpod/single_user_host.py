"""Concrete single-user, individually approved on-demand host; default disabled.

One journal holds grants, effect intents, real ownership, readiness and deletion
consumption atomically. The old mock controller/gateway are not used for real I/O.
Explicit step() is the sole worker entry; importing/constructing starts nothing.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
import uuid

from .bootstrap_execution import _proof, load_script
from .c1_authority import AuthorityJournal, encode
from .c1_ports import PortError, PodSpec, ProviderPlans, Rate, ServerPins, identity, sha
from .execution_adapters import Effect, ExecutionGate, RunPodREST, SSHExecution, SSHPeer, bootstrap_script, validate_pod
from .ondemand import UsageLimits, UsageScope, TerminationApproval, _limits_dict, REQUIRED_ACTIONS
from .owui_installed_dispatcher import PendingCall
from .private_chat import TunnelEndpoint
from .upstream_sse import LoopbackUpstream, prepare


@dataclass
class ChatTicket:
    session_id: str
    pod_id: str
    request_id: str
    deadline: float
    cancel: threading.Event = field(repr=False)
    terminal: bool = False
    released: bool = False
    generation_lease: object = field(default=None,repr=False)


class SingleUserHost:
    def __init__(self, *, journal=None, subject=None, enabled=False, fixture=False,
                 rest=None, ssh=None, upstream=None, export_root=None, spec=None, rate=None,
                 pins=None, clock=None, idle_seconds=300,model_bearer=None):
        self._lock=threading.RLock(); self._worker_lock=threading.Lock(); self._closed=False; self._last_saved=0.0
        self._journal_owner=self._journal_claim=self._journal_version=None
        self.enabled=enabled is True; self.fixture=fixture is True
        self.journal=journal; self.subject=subject; self.clock=clock or time.time
        if model_bearer is not None and (type(model_bearer) is not str or not 16<=len(model_bearer)<=256 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-" for c in model_bearer)):
            raise PortError("invalid_injected_model_bearer")
        self._model_bearer=model_bearer
        self.spec=spec or PodSpec(); self.rate=rate or Rate(); self.pins=pins or ServerPins()
        if any(type(v) is not t for v,t in ((self.spec,PodSpec),(self.rate,Rate),(self.pins,ServerPins))): raise PortError("typed_profile_required")
        if type(idle_seconds) not in (int,float) or not math.isfinite(idle_seconds) or not 1<=idle_seconds<=3600: raise PortError("invalid_idle")
        self.idle_seconds=float(idle_seconds)
        self.export_root=Path(export_root).resolve() if export_root is not None else None
        self.plans=ProviderPlans(self.spec); self._queued={}; self._tokens={}; self._active={}; self._chat=None
        self._peer=None; self._tunnel=None; self.release_count=0
        self._data={"schema":"single-user-host-v1","configuration":self._configuration(),"current":None,
                    "approved":None,"sessions":{},"used_approvals":[],"high_water":0.0}
        self.rest=rest if rest is not None else RunPodREST(gate=ExecutionGate(self.check_effect))
        self.ssh=ssh if ssh is not None else SSHExecution(ExecutionGate(self.check_effect))
        self.upstream=upstream if upstream is not None else LoopbackUpstream(TunnelEndpoint(port=19181))
        if journal is not None:
            if type(journal) is not AuthorityJournal: raise PortError("journal_required")
            saved=self._claim_journal()
            if saved is not None:
                if saved.get("schema")!=self._data["schema"] or saved.get("configuration")!=self._configuration(): raise PortError("host_configuration_changed")
                self._data=json.loads(encode(saved))
                now=self._now_raw()
                if now<self._data["high_water"]: raise PortError("clock_recovery_required")
                for sid,row in self._data["sessions"].items():
                    identity(sid); self._limits(row)
                    if row["pod_id"] is not None:
                        self.plans.attempted.add(row["create_operation"])
                        self.plans.acknowledge(sid,row["create_operation"],{"id":row["pod_id"]},account=row["owner"]["account"])
                row=self._row(required=False)
                if row:
                    row["pending_ids"]=[]
                    if row["active_chat"] is not None:
                        row["interrupted"].append(row["active_chat"]); row["active_chat"]=None
                        row["phase"]="draining"
                    elif row["phase"]=="provisioning": self._close_unused(row)
                    elif row["phase"] in {"bootstrapping","loading"}: row["phase"]="draining"
                    elif row["phase"]=="ready":
                        # A previous process/tunnel cannot be treated as an owned
                        # new connection after restart. Preserve all deadlines;
                        # drain rather than automatically reconnect/infer.
                        row["phase"]="draining"
                self._save()

    def _claim_journal(self):
        # The OS owner lock excludes other journal instances/processes. Claim
        # this already-open object too, before reading any private host snapshot.
        # A claim lasts for the entire open generation, even after host.close;
        # transfer requires journal close/reopen so late old work is fenced.
        with self.journal._lock:
            if self.journal._db is None or self.journal._owner is None:
                raise PortError("opened_journal_owner_required")
            prior=getattr(self.journal,"_single_user_host_claim",None)
            if prior is not None and prior[0] is self.journal._owner:
                raise PortError("single_host_owner_refused")
            self._journal_owner=self.journal._owner
            self._journal_claim=(self._journal_owner,object())
            self.journal._single_user_host_claim=self._journal_claim
            self._journal_version=(self.journal.sequence,self.journal.digest)
            return self.journal.latest()

    def _journal_fence_locked(self):
        if (self.journal._db is None or self.journal._owner is not self._journal_owner
                or getattr(self.journal,"_single_user_host_claim",None) is not self._journal_claim):
            raise PortError("host_generation_stale")
        if (self.journal.sequence,self.journal.digest)!=self._journal_version:
            raise PortError("host_snapshot_stale")
        if self.journal._head()!={"sequence":self._journal_version[0],"digest":self._journal_version[1]}:
            raise PortError("trusted_head_changed")

    @contextmanager
    def _generation_scope(self):
        with self.journal._lock:
            if (self.journal._db is None or self.journal._owner is not self._journal_owner
                    or getattr(self.journal,"_single_user_host_claim",None) is not self._journal_claim):
                raise PortError("host_generation_stale")
            lease=self.journal.use(self._journal_owner); lease.__enter__()
        try: yield
        finally: lease.__exit__(None,None,None)

    def _configuration(self):
        return {"spec":vars(self.spec),"rate":[str(self.rate.gpu_usd_per_hour),str(self.rate.storage_usd_per_hour),str(self.rate.overhead_usd)],
                "pins":vars(self.pins),"idle_seconds":self.idle_seconds,
                "subject_sha256":hashlib.sha256(self.subject.encode()).hexdigest() if type(self.subject) is str else None,
                "fixture":self.fixture,
                "export_root":str(self.export_root) if self.export_root else None}

    def _now_raw(self):
        value=self.clock()
        if type(value) not in (int,float) or not math.isfinite(value): raise PortError("invalid_clock")
        return float(value)

    def _now(self):
        now=self._now_raw()
        if now<self._data["high_water"]: raise PortError("clock_recovery_required")
        self._data["high_water"]=now
        return now

    def _save(self):
        if self.journal is None: raise PortError("durable_host_required")
        # Compare the HOST's last committed version, not the mutable shared
        # journal version. Owner + version check + append are one locked claim.
        with self._generation_scope(),self.journal._lock:
            self._journal_fence_locked()
            self.journal.append(self._data)
            self._journal_version=(self.journal.sequence,self.journal.digest)
        self._last_saved=time.monotonic()

    def _heartbeat(self):
        # Gate polling remains immediate; durable clock checkpoints are bounded
        # to once per second so a >=17.5min cold build cannot exhaust the ledger
        # at the SSH watchdog's 10ms cadence. Every effect/transition seals now.
        if time.monotonic()-self._last_saved>=1: self._save()

    def _open(self):
        if self._closed or not self.enabled or self.journal is None or self.export_root is None or type(self.subject) is not str:
            raise PortError("host_disabled")
        # Validate the protected monotonic head at each effect/write gate without
        # appending a 10ms clock record. A copied/rolled-back signed head cannot
        # expand authority or continue I/O in this already-running process.
        with self._generation_scope(),self.journal._lock:
            self._journal_fence_locked()

    @contextmanager
    def _operation_scope(self):
        if self.journal is None: raise PortError("host_disabled")
        # Pin the generation across the entire effect, not just its final gate.
        # No host/journal mutex crosses network/process/backpressure waits.
        with self._generation_scope():
            with self._lock: self._open()
            yield

    @contextmanager
    def _owned_tunnel_scope(self,effect,peer,cancel):
        # READY keeps an owned SSH child alive, so its generation lease lasts
        # until that exact child/context has closed, not just until load returns.
        with self._generation_scope(),self.ssh.tunnel(effect,peer,cancel) as child:
            yield child

    def _row(self, sid=None, *, required=True):
        current=self._data["current"]
        if sid is not None and (type(sid) is not str or sid!=current): raise PortError("stale_session")
        row=self._data["sessions"].get(current)
        if row is None and required: raise PortError("no_session")
        return row

    @staticmethod
    def _limits(row):
        values=row["limits"]
        return UsageLimits.create(max_runtime_seconds=values["max_runtime_seconds"],max_usd=values["max_usd"],expires_at=values["expires_at"],
            scope=UsageScope(row["id"],values["target"],frozenset(values["actions"])),
            cleanup_runtime_seconds=values["cleanup_runtime_seconds"],cleanup_usd=values["cleanup_usd"])

    @staticmethod
    def _operation(sid,action): return hashlib.sha256((sid+"\0"+action).encode()).hexdigest()

    def approve_session(self, limits):
        with self._lock:
            self._open()
            if type(limits) is not UsageLimits or limits.scope.target!="new-pod" or limits.scope.actions!=REQUIRED_ACTIONS: raise PortError("exact_new_pod_grant_required")
            sid=identity(limits.scope.session_id)
            if self._data["approved"] is not None:
                prior=self._data["sessions"][self._data["approved"]]
                if prior["phase"]=="approved" and self._now()>=self._limits(prior).expires_at:
                    prior["phase"]="closed"; self._data["approved"]=None
            if self._data["current"] is not None or self._data["approved"] is not None or sid in self._data["sessions"]: raise PortError("session_grant_conflict")
            if len(self._data["sessions"])>=32: raise PortError("session_history_bound")
            now=self._now()
            if now>=limits.expires_at or not self.rate.work_allowed(limits,now,now): raise PortError("grant_expired_or_reserve_insufficient")
            self._data["sessions"][sid]={"id":sid,"phase":"approved","limits":_limits_dict(limits),"started_at":None,
                "deadline":None,"cleanup_deadline":None,"idle_deadline":None,"pod_id":None,"owner":None,
                "create_operation":self._operation(sid,"create"),"effects":{},"readiness":None,"peer_fingerprint":None,
                "pending_ids":[],"dispatched_ids":[],"active_chat":None,"completed":[],"inference_results":[],"interrupted":[],"abort_requested":False,
                "receipt":None,"export_plan":None,"approval":None,"events":[],"read_count":0}
            self._data["approved"]=sid; self._save()

    def enqueue(self, subject, payload):
        clean=prepare(payload,"projection"); frozen=encode(clean); digest=hashlib.sha256(frozen).hexdigest()
        with self._lock:
            self._open()
            if subject!=self.subject: raise PortError("verified_app_subject_required")
            if len(self._queued)>=8: raise PortError("queue_limit")
            if self._data["current"] is None:
                sid=self._data["approved"]
                if sid is None: raise PortError("approved_session_required")
                row=self._data["sessions"][sid]; now=self._now(); limits=self._limits(row)
                end=min(now+limits.max_runtime_seconds,limits.expires_at-limits.cleanup_runtime_seconds)
                if now>=end: raise PortError("grant_expired")
                row.update(phase="provisioning",started_at=now,
                    deadline=end,
                    cleanup_deadline=min(now+limits.max_runtime_seconds+limits.cleanup_runtime_seconds,limits.expires_at))
                self._peer=None
                self._data["current"]=sid; self._data["approved"]=None
            row=self._row(); self._work(row)
            if len(row["dispatched_ids"])>=128: raise PortError("request_history_bound")
            if row["phase"] not in {"provisioning","create_unresolved","bootstrapping","loading","ready"}: raise PortError("session_unavailable")
            rid=uuid.uuid4().hex
            self._queued[rid]=(row["id"],frozen,digest); row["pending_ids"].append(rid); self._save()
            return PendingCall(rid,row["id"],time.monotonic()+max(0,row["deadline"]-self._now()),digest)

    def _work(self,row):
        now=self._now(); limits=self._limits(row)
        if now>=row["deadline"] or self.rate.cost(row["started_at"],now)>=limits.max_usd-limits.cleanup_usd:
            raise PortError("work_budget_expired")
        return now

    def _cleanup(self,row):
        now=self._now()
        if now>=row["cleanup_deadline"] or self.rate.cost(row["started_at"],now)>self._limits(row).max_usd: raise PortError("cleanup_budget_expired")
        return now

    def _owned(self,row):
        self.plans.check_owner(row["id"],row["pod_id"])
        if row["owner"]!={"account":self.spec.account,"operation":row["create_operation"],"session_id":row["id"]}: raise PortError("ownership_not_proven")

    def check_effect(self,effect):
        with self._lock:
            self._open(); row=self._effect_authority_locked(effect)
            if effect.action=="terminate" and not self.readback(row["id"]):
                raise PortError("cleanup_approval_invalid")
            self._heartbeat()
            # Readback, journal append/fsync and protected-head reads can wait.
            # Decide again after all of them, with no persistence/file I/O after
            # this final UTC/USD/approval/cancellation check before dispatch.
            self._open(); self._effect_authority_locked(effect)
            return True

    def _effect_authority_locked(self,effect):
        """Fresh authority decision only; caller holds lock; no disk/network I/O."""
        row=self._row(effect.session_id)
        if self._active.get(effect.operation_id) is None or self._active[effect.operation_id].is_set(): raise PortError("effect_cancelled")
        record=row["effects"].get(effect.operation_id)
        if not record or record["action"]!=effect.action or record["pod_id"]!=effect.pod_id: raise PortError("effect_scope_mismatch")
        if time.monotonic()>=effect.deadline: raise PortError("effect_deadline")
        if effect.action in {"read","terminate"}: self._cleanup(row); self._owned(row)
        else:
            now=self._work(row)
            if row["abort_requested"] or row["phase"] not in {"create_unresolved","bootstrapping","loading","ready"}: raise PortError("effect_cancelled")
            if effect.pod_id is not None: self._owned(row)
            if effect.action=="load" and row["phase"]=="ready" and now>=row["idle_deadline"]: raise PortError("idle_expired")
        if effect.action=="terminate":
            approval=row["approval"]
            if (row["phase"]!="termination_unconfirmed" or approval is None or self._now()>=approval["expires_at"]
                    or approval["id"] not in self._data["used_approvals"]):
                raise PortError("cleanup_approval_invalid")
        return row

    def _reserve(self,row,action,*,operation=None,cleanup=False):
        now=self._cleanup(row) if cleanup else self._work(row)
        op=operation or self._operation(row["id"],action)
        if op in row["effects"]: raise PortError("effect_not_replayable")
        if len(row["effects"])>=128: raise PortError("effect_history_bound")
        row["effects"][op]={"action":action,"pod_id":None if action=="create" else row["pod_id"],"outcome":"unknown"}
        end=row["cleanup_deadline"] if cleanup else row["deadline"]
        e=Effect(row["id"],op,action,row["effects"][op]["pod_id"],time.monotonic()+max(0,end-now))
        cancel=threading.Event(); self._save(); self._active[op]=cancel
        return e,cancel

    def register_peer(self,sid,peer):
        with self._lock:
            self._open(); row=self._row(sid); self._owned(row)
            if type(peer) is not SSHPeer or peer.pod_id!=row["pod_id"]: raise PortError("exact_owned_peer_required")
            if row["peer_fingerprint"] not in (None,peer.fingerprint): raise PortError("peer_pin_replacement_refused")
            row["peer_fingerprint"]=peer.fingerprint; self._peer=peer; self._save()

    def step(self):
        if not self._worker_lock.acquire(False): return "worker_busy"
        try:
            with self._operation_scope(): return self._step()
        finally: self._worker_lock.release()

    def _step(self):
        with self._lock:
            self._open(); row=self._row(required=False)
            if not row: return "idle"
            self._heartbeat()
            try: self._cleanup(row)
            except PortError:
                row["phase"]="cleanup_expired"; self._cancel(row); self._save(); return "cleanup_expired"
            if row["phase"] in {"provisioning","bootstrapping","loading","ready"}:
                try:
                    now=self._work(row)
                    if row["phase"]=="ready" and now>=row["idle_deadline"]: raise PortError("idle_expired")
                except PortError: self._stop_locked(row)
            phase=row["phase"]
            if phase=="provisioning":
                row["phase"]="create_unresolved"; request=self.plans.create(row["id"],row["create_operation"])
                effect,cancel=self._reserve(row,"create",operation=row["create_operation"])
            elif phase in {"bootstrapping","loading"}:
                if self._peer is None or self._peer.pod_id!=row["pod_id"]: return "peer_approval_required"
                self._owned(row); action="bootstrap" if phase=="bootstrapping" else "load"
                effect,cancel=self._reserve(row,action)
                peer=self._peer; binary=row["readiness"]["binary_sha256"] if action=="load" else None
            elif phase=="draining":
                if row["active_chat"] is not None or self._chat is not None: return "inference_draining"
                self._owned(row); self._detach_tunnel_locked()
                return self._prepare_export_locked(row)
            elif phase=="exporting": return "export_reconciliation_required"
            elif phase=="ready": return "ready"
            else: return phase
        # No controller/host lock is held over network, SSH, tunnel or build I/O.
        tunnel=None; ready_committed=False
        try:
            if effect.action=="create":
                value=self.rest.request(effect,request.method,request.path,request.body,cancel)
                proof=validate_pod(value,self.spec,effect.session_id,max_gpu_usd_per_hour=self.rate.gpu_usd_per_hour)
            else:
                script=bootstrap_script(effect.session_id,effect.pod_id,self.pins) if effect.action=="bootstrap" else load_script(effect,binary,self.pins)
                if effect.action=="bootstrap" and self._model_bearer is not None:
                    # In-memory approved secret crosses only encrypted SSH stdin.
                    # It is absent from argv, journals, plans, reports and logs.
                    encoded=base64.b64encode(self._model_bearer.encode()).decode()
                    prefix=("set -eu\numask 077\ntest \"${RUNPOD_POD_ID:-}\" = '"+effect.pod_id+"'\n"
                        "mkdir -p /run/qmc\nchmod 700 /run/qmc\n"
                        "test ! -e /run/qmc/model-api-key\n"
                        "printf '%s' '"+encoded+"' | base64 --decode > /run/qmc/model-api-key\nchmod 600 /run/qmc/model-api-key\n")
                    script=prefix.encode()+script
                value=self.ssh.run(effect,peer,script,cancel)
                proof=_proof(value,effect,self.pins,binary,ready=effect.action=="load")
            with self._lock:
                row=self._row(effect.session_id)
                if effect.action=="create":
                    pod=self.plans.acknowledge(row["id"],effect.operation_id,{"id":proof["id"]},account=proof["account"])
                    row["pod_id"]=pod; row["owner"]={"account":proof["account"],"session_id":row["id"],"operation":effect.operation_id}
                    row["effects"][effect.operation_id]["outcome"]="committed"
                    try: self._work(row); work_valid=True
                    except PortError: work_valid=False
                    row["phase"]="draining" if row["abort_requested"] or cancel.is_set() or self._closed or not work_valid else "bootstrapping"
                    self._save(); return "created"
                self.check_effect(effect)
                row["readiness"]=proof; row["effects"][effect.operation_id]["outcome"]="committed"
                if effect.action=="bootstrap": row["phase"]="loading"; self._save(); return "bootstrapped"
            # Retain an owned tunnel for READY. Its load grant remains original;
            # close/stop and idle/work deadlines cancel the same effect gate.
            tunnel=self._owned_tunnel_scope(effect,peer,cancel); tunnel.__enter__()
            with self._lock:
                self.check_effect(effect); row=self._row(effect.session_id)
                self._tunnel=tunnel; row["phase"]="ready"; row["idle_deadline"]=self._now()+self.idle_seconds
                self._save(); ready_committed=True; return "ready"
        except Exception:
            with self._lock:
                row=self._row(effect.session_id)
                if effect.action!="create": self._stop_locked(row)
                self._save()
            return "create_unresolved" if effect.action=="create" else "startup_failed"
        finally:
            if tunnel is not None and not ready_committed:
                with self._lock:
                    cancel.set()
                    if self._tunnel is tunnel: self._tunnel=None
                tunnel.__exit__(None,None,None)
            with self._lock:
                if not (effect.action=="load" and self._tunnel is not None): self._active.pop(effect.operation_id,None)

    def _cancel(self,row):
        for op,cancel in self._active.items():
            if op in row["effects"]: cancel.set()
        if self._chat is not None and self._chat.session_id==row["id"] and not self._chat.terminal: self._chat.cancel.set()
        self._tokens.clear()

    def _close_unused(self,row):
        row["phase"]="closed"; row["pending_ids"]=[]; self._data["current"]=None

    def _stop_locked(self,row):
        row["abort_requested"]=True; self._cancel(row)
        self._queued={rid:item for rid,item in self._queued.items() if item[0]!=row["id"]}; row["pending_ids"]=[]
        if row["pod_id"] is not None:
            if row["phase"] not in {"draining","exporting","approval_pending","termination_unconfirmed","cleanup_expired"}: row["phase"]="draining"
        elif row["create_operation"] not in row["effects"]: self._close_unused(row)
        else: row["phase"]="create_unresolved"
        self._save()

    def stop(self,sid):
        identity(sid)
        with self._lock: self._open(); self._stop_locked(self._row(sid))

    def drop(self,pending):
        with self._lock:
            self._open()
            item=self._queued.pop(pending.request_id,None)
            if item is None: return False
            row=self._row(pending.session_id); row["pending_ids"].remove(pending.request_id)
            if not row["pending_ids"] and not row["dispatched_ids"] and row["active_chat"] is None: self._stop_locked(row)
            else: self._save()
            return True

    def _detach_tunnel_locked(self):
        # Signal under lock. Process/context close is done explicitly outside it.
        if self._tunnel is not None:
            op=self._operation(self._row()["id"],"load")
            if op in self._active: self._active[op].set()

    def close_tunnel(self):
        with self._lock: tunnel,self._tunnel=self._tunnel,None
        if tunnel is not None: tunnel.__exit__(None,None,None)

    def _prepare_export_locked(self,row):
        report={"session_id":row["id"],"pod_id":row["pod_id"],"owner":row["owner"],"pins":vars(self.pins),
            "limits":row["limits"],"started_at":row["started_at"],"deadline":row["deadline"],"cleanup_deadline":row["cleanup_deadline"],
            "readiness":row["readiness"],"effects":row["effects"],"inference_results":row["inference_results"],
            "interrupted_requests":row["interrupted"],"fixture":self.fixture}
        data=encode(report); digest=hashlib.sha256(data).hexdigest()
        row["export_plan"]={"sha256":digest,"name":row["id"]+"-"+digest+".json","bytes":data.decode()}
        row["phase"]="exporting"; self._save()
        return "exporting"

    def export(self,sid):
        with self._operation_scope(): return self._export(sid)

    def _export(self,sid):
        with self._lock:
            self._open(); row=self._row(sid); self._cleanup(row); self._owned(row)
            if row["phase"]!="exporting": raise PortError("export_not_pending")
            plan=dict(row["export_plan"])
        self.close_tunnel()
        self.export_root.mkdir(parents=True,exist_ok=True)
        path=self.export_root/plan["name"]
        try:
            with path.open("xb") as stream: stream.write(plan["bytes"].encode()); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError: pass
        return self.reconcile_export(sid)

    def reconcile_export(self,sid):
        with self._lock:
            self._open(); row=self._row(sid); self._cleanup(row); plan=row["export_plan"]
            if row["phase"] not in {"exporting","approval_pending"} or plan is None: raise PortError("export_not_pending")
            path=self.export_root/plan["name"]
            with path.open("rb") as stream: data=stream.read(32769)
            if len(data)>32768 or hashlib.sha256(data).hexdigest()!=plan["sha256"]: raise PortError("export_readback_invalid")
            row["receipt"]={"sha256":plan["sha256"],"name":plan["name"]}; row["phase"]="approval_pending"; self._save()
            return "approval_pending"

    def readback(self,sid):
        row=self._data["sessions"].get(sid)
        if not row or row["receipt"] is None: return False
        try:
            with (self.export_root/row["receipt"]["name"]).open("rb") as stream: data=stream.read(32769)
            return len(data)<=32768 and hashlib.sha256(data).hexdigest()==row["receipt"]["sha256"]
        except OSError: return False

    def approve_cleanup(self,approval):
        with self._lock:
            self._open()
            if type(approval) is not TerminationApproval: raise PortError("typed_termination_approval_required")
            identity(approval.approval_id)
            row=self._row(approval.session_id); now=self._cleanup(row); self._owned(row)
            if (row["phase"]!="approval_pending" or approval.action!="terminate" or approval.pod_id!=row["pod_id"]
                    or approval.receipt_sha256!=row["receipt"]["sha256"] or now>=approval.expires_at
                    or approval.expires_at>row["cleanup_deadline"] or approval.approval_id in self._data["used_approvals"]
                    or not self.readback(row["id"])): raise PortError("exact_termination_approval_required")
            row["approval"]={"id":approval.approval_id,"session_id":approval.session_id,"pod_id":approval.pod_id,
                             "sha256":approval.receipt_sha256,"expires_at":approval.expires_at}
            self._save()

    def terminate(self,sid):
        with self._operation_scope(): return self._terminate(sid)

    def _terminate(self,sid):
        with self._lock:
            self._open(); row=self._row(sid); now=self._cleanup(row); self._owned(row)
            approval=row["approval"]
            if (row["phase"]!="approval_pending" or not approval or now>=approval["expires_at"]
                    or approval["id"] in self._data["used_approvals"] or not self.readback(sid)): raise PortError("exact_termination_approval_required")
            row["phase"]="termination_unconfirmed"; self._data["used_approvals"].append(approval["id"])
            request=self.plans.terminate(sid,row["pod_id"],self._operation(sid,"terminate"),frozenset({row["pod_id"]}))
            effect,cancel=self._reserve(row,"terminate",cleanup=True)
        self.close_tunnel()
        try: self.rest.request(effect,request.method,request.path,request.body,cancel)
        except Exception: return "termination_unconfirmed"
        finally:
            with self._lock: self._active.pop(effect.operation_id,None)
        return self.reconcile_termination(sid)

    def reconcile_termination(self,sid):
        with self._operation_scope(): return self._reconcile_termination(sid)

    def _reconcile_termination(self,sid):
        with self._lock:
            self._open(); row=self._row(sid); self._cleanup(row); self._owned(row)
            if row["phase"]!="termination_unconfirmed": raise PortError("termination_not_unconfirmed")
            row["read_count"]+=1
            effect,cancel=self._reserve(row,"read",operation=self._operation(sid,"absence-"+str(row["read_count"])),cleanup=True)
            request=self.plans.read(sid,row["pod_id"],effect.operation_id)
        try:
            value=self.rest.request(effect,request.method,request.path,request.body,cancel)
            with self._lock:
                self.check_effect(effect); row=self._row(sid)
                if value is not None:
                    validate_pod(value,self.spec,sid,pod_id=row["pod_id"],max_gpu_usd_per_hour=self.rate.gpu_usd_per_hour)
                    return "termination_unconfirmed"
                row["effects"][effect.operation_id]["outcome"]="committed"
                row["phase"]="closed"; self._data["current"]=None; self._save(); return "absent"
        except Exception: return "termination_unconfirmed"
        finally:
            with self._lock: self._active.pop(effect.operation_id,None)

    def reconcile_create(self,sid,*,pod_response,original_operation):
        # Trusted local operator supplies original create-response/operation proof.
        # No name-only scan/list-all or blind second POST is provided.
        with self._lock:
            self._open(); row=self._row(sid); self._cleanup(row)
            if row["phase"]!="create_unresolved" or original_operation!=row["create_operation"]: raise PortError("original_create_proof_required")
            proof=validate_pod(pod_response,self.spec,sid,max_gpu_usd_per_hour=self.rate.gpu_usd_per_hour)
            self.plans.attempted.add(original_operation)
            pod=self.plans.acknowledge(sid,original_operation,{"id":proof["id"]},account=proof["account"])
            row["pod_id"]=pod; row["owner"]={"account":proof["account"],"session_id":sid,"operation":original_operation}
            row["effects"][original_operation]["outcome"]="committed"
            row["phase"]="bootstrapping" if self._queued and not row["abort_requested"] else "draining"
            self._save(); return "found"

    def ready_request(self,pending):
        with self._lock:
            self._open(); row=self._row(pending.session_id); now=self._work(row); self._owned(row)
            item=self._queued.get(pending.request_id)
            if (row["phase"]!="ready" or now>=row["idle_deadline"] or item is None or item[2]!=pending.payload_sha256
                    or row["readiness"] is None or self._tunnel is None): raise PortError("request_not_ready")
            token=secrets.token_urlsafe(24)
            mono=time.monotonic(); self._tokens={key:value for key,value in self._tokens.items() if value[4]>mono}
            if len(self._tokens)>=64: raise PortError("intent_limit")
            self._tokens[token]=(pending.session_id,row["pod_id"],pending.request_id,item[2],time.monotonic()+5)
            self._queued.pop(pending.request_id); row["pending_ids"].remove(pending.request_id)
            row["dispatched_ids"].append(pending.request_id); self._save()
            return {"X-Request-ID":pending.request_id,"X-Intent-Token":token}

    def admit(self,token,payload,rid,deadline,cancel):
        digest=hashlib.sha256(encode(prepare(payload,rid))).hexdigest()
        with self._lock:
            self._open(); permit=self._tokens.pop(token,None)
            if permit is None or permit[2]!=rid or permit[3]!=digest or time.monotonic()>=permit[4]: raise PortError("interactive_intent_required")
            row=self._row(permit[0]); self._owned(row)
            ticket=ChatTicket(permit[0],permit[1],rid,deadline,cancel)
            if self._chat is not None or row["active_chat"] is not None or rid in row["completed"]: raise PortError("backend_busy_or_replay")
            ticket.generation_lease=self._generation_scope()
            ticket.generation_lease.__enter__()
            self._chat=ticket
            try:
                self.chat_fence(ticket); row["active_chat"]=rid; self._save()
            except BaseException:
                self._chat=None; ticket.generation_lease.__exit__(None,None,None); ticket.generation_lease=None
                raise
            return ticket

    def chat_fence(self,ticket,*,claim_terminal=False):
        with self._lock:
            self._open(); row=self._row(ticket.session_id); now=self._work(row); self._owned(row)
            if (self._chat is not ticket or ticket.released or row["pod_id"]!=ticket.pod_id
                    or (row["phase"]!="ready" and not ticket.terminal) or now>=row["idle_deadline"]
                    or ticket.cancel.is_set() or time.monotonic()>=ticket.deadline): raise PortError("chat_scope_expired")
            if claim_terminal: ticket.terminal=True
            return True

    def release(self,ticket,*,successful=False,content_bytes=0,stream=False):
        with self._lock:
            if ticket.released: return
            ticket.released=True; self.release_count+=1
            row=self._data["sessions"][ticket.session_id]
            if row["active_chat"]==ticket.request_id: row["active_chat"]=None
            if ticket.request_id not in row["completed"]: row["completed"].append(ticket.request_id)
            row["inference_results"].append({"request_id":ticket.request_id,"outcome":"verified_written" if successful is True else "interrupted_or_unknown",
                                           "content_bytes":content_bytes if type(content_bytes) is int and 0<=content_bytes<=8192 else 0,
                                           "stream":stream is True})
            if self._chat is ticket: self._chat=None
            try: self._save()
            finally:
                if ticket.generation_lease is not None:
                    ticket.generation_lease.__exit__(None,None,None); ticket.generation_lease=None

    def status(self):
        with self._lock:
            row=self._row(required=False)
            return {"phase":row["phase"] if row else "absent","session_id":row["id"] if row else None,
                "pod_id":row["pod_id"] if row else None,"queued":len(self._queued),
                "deadline":row["deadline"] if row else None,"cleanup_deadline":row["cleanup_deadline"] if row else None,
                "idle_deadline":row["idle_deadline"] if row else None,"fixture":self.fixture,"enabled":self.enabled and not self._closed}

    def close(self):
        try:
            with self._lock:
                if self._closed: return
                try:
                    row=self._row(required=False)
                    if row: self._stop_locked(row)
                finally: self._closed=True
        finally: self.close_tunnel()
