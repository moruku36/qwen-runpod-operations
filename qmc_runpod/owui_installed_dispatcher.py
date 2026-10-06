"""OWUI0.11.4 server-only call site contract; inert/default denied.

Installer wrapper supplies verified user and an object-identity manual context.
No client metadata supplies a session grant, operation, intent or background flag.
Enqueue/READY/forward callbacks are explicitly injected service code, not config.
"""
import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import math
import threading
import time

from .c1_ports import PortError, identity
from .upstream_sse import prepare


@dataclass(frozen=True)
class EntryContext:
    sequence: int
    subject: str
    deadline: float
    request: object = field(repr=False)


@dataclass(frozen=True)
class PendingCall:
    request_id: str
    session_id: str
    deadline: float
    payload_sha256: str


class InstalledDispatcher:
    def __init__(self, *, subjects=frozenset(), verify=None, enqueue=None, wait_ready=None, forward=None, drop=None):
        self._subjects, self._verify = frozenset(subjects), verify
        self._enqueue, self._wait_ready, self._forward, self._drop = enqueue, wait_ready, forward, drop
        self._lock = threading.RLock(); self._contexts = {}; self._active = {}; self._sequence = 0; self._closed = False

    def _subject(self, request, user):
        if self._closed or self._verify is None or any(callback is None for callback in (self._enqueue, self._wait_ready, self._forward, self._drop)):
            raise PortError("installed_integration_disabled")
        subject = self._verify(request, user)
        if type(subject) is not str or subject not in self._subjects: raise PortError("verified_app_subject_required")
        return subject

    def begin(self, request, user):
        with self._lock:
            subject = self._subject(request, user)
            now = time.monotonic()
            self._contexts = {k:v for k,v in self._contexts.items() if v.deadline > now}
            if len(self._contexts) >= 64: raise PortError("manual_context_limit")
            self._sequence += 1
            context = EntryContext(self._sequence, subject, now+5, request)
            self._contexts[context.sequence] = context
            return context

    async def final_chat(self, request, form_data, user, context=None):
        with self._lock:
            subject = self._subject(request, user)
            if (type(context) is not EntryContext or self._contexts.get(context.sequence) is not context
                    or context.request is not request or context.subject != subject or time.monotonic() >= context.deadline):
                raise PortError("server_manual_context_required")
            # Consume before any queue/provider effect. Verifier time counts.
            self._contexts.pop(context.sequence)
        if type(form_data) is not dict: raise PortError("final_chat_object_required")
        # The final pipeline may add bookkeeping; it never becomes authority.
        bookkeeping = {"metadata", "chat_id", "id", "parent_id"}
        payload = {key:value for key,value in form_data.items() if key not in bookkeeping}
        # RID/SID come from service control, not client metadata or model catalog.
        clean = prepare(payload, "installed-projection")
        frozen = json.dumps(clean, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        digest = hashlib.sha256(frozen).hexdigest()
        cancel = threading.Event()
        with self._lock:
            self._subject(request, user)
            # Enqueue is a bounded pure control operation; provider I/O belongs
            # to the host's separately fenced worker, outside this mutex.
            pending = self._enqueue(subject, clean)
            if (type(pending) is not PendingCall or pending.payload_sha256 != digest
                    or type(pending.deadline) not in (int, float) or not math.isfinite(pending.deadline)):
                raise PortError("trusted_queue_receipt_required")
            identity(pending.request_id); identity(pending.session_id)
            if pending.request_id in self._active: raise PortError("request_replay")
            self._active[pending.request_id] = cancel
        try:
            # Startup allowance is the approved original work deadline. The
            # short inference intent is minted only after wait_ready succeeds.
            remaining = pending.deadline-time.monotonic()
            if remaining <= 0: raise PortError("original_startup_deadline_expired")
            ready = await asyncio.wait_for(self._wait_ready(pending, cancel), timeout=remaining)
            with self._lock:
                self._subject(request, user)
                if cancel.is_set() or time.monotonic() >= pending.deadline: raise PortError("original_startup_deadline_expired")
            # Service forward checks exact pending/body binding and one-use
            # inference headers at the private localhost gateway. No payload
            # supplied by wait_ready can replace the original final form.
            result = await self._forward(pending, json.loads(frozen), ready, cancel)
            with self._lock:
                self._subject(request, user)
                if cancel.is_set() or time.monotonic() >= pending.deadline: raise PortError("original_startup_deadline_expired")
            return result
        except BaseException:
            cancel.set(); self._drop(pending)
            raise
        finally:
            with self._lock: self._active.pop(pending.request_id, None)

    def close(self):
        with self._lock:
            self._closed=True; self._contexts.clear()
            for cancel in self._active.values(): cancel.set()
