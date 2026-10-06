"""Version-pinned manual intent policy; no installed OWUI hook or authentication.

The verifier is a trusted backend integration boundary, NOT a body/header user
claim. Purpose is explicitly supplied by reviewed SERVER call sites. Reusing an
HTTP request object during title/tool/background work does not confer intent.
The default verifier is absent and the default subject allowlist is empty.
"""
from dataclasses import dataclass, field
from enum import Enum
import re
import threading
import time

from .private_gateway import PrivateGateway

OWUI_VERSION = "0.11.4"
MANUAL_ROUTES = frozenset({"/api/chat/completions", "/api/v1/chat/completions"})


class IntentPolicyError(RuntimeError):
    pass


class ServerPurpose(Enum):
    MANUAL_CHAT = "manual_chat"
    TITLE = "title"
    TAGS = "tags"
    FOLLOW_UP = "follow_up"
    EMBEDDING = "embedding"
    TOOL = "tool"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ManualContext:
    sequence: int
    request: object = field(repr=False, compare=False)
    subject: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True)
class ForwardIntent:
    request_id: str
    intent_token: str = field(repr=False)

    def headers(self):
        return {"X-Request-ID": self.request_id, "X-Intent-Token": self.intent_token}


class OWUIIntentPolicy:
    """Pure policy core around the mock gateway; never reads credentials or DB.

    ``verify_subject(request)`` must return the authenticated subject from trusted
    OWUI backend state after get_verified_user, never request JSON metadata. This
    class cannot prove a caller has installed that verifier/call-site distinction.
    That deployment must be independently reviewed before any real connection.
    """
    def __init__(self, gateway, *, version=OWUI_VERSION, verify_subject=None, allowed_subjects=frozenset()):
        if type(gateway) is not PrivateGateway or version != OWUI_VERSION:
            raise IntentPolicyError("unsupported_runtime")
        if verify_subject is not None and not callable(verify_subject):
            raise IntentPolicyError("invalid_verifier")
        if (type(allowed_subjects) is not frozenset or len(allowed_subjects) > 16
                or any(type(subject) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", subject)
                       for subject in allowed_subjects)):
            raise IntentPolicyError("invalid_subject_allowlist")
        self.gateway, self._verify, self._allowed = gateway, verify_subject, allowed_subjects
        self._lock = threading.Lock()
        self._contexts = {}
        self._sequence = 0

    def _subject(self, request):
        if self._verify is None:
            raise IntentPolicyError("verified_user_required")
        try:
            subject = self._verify(request)
        except Exception:
            raise IntentPolicyError("verified_user_required") from None
        if type(subject) is not str or subject not in self._allowed:
            raise IntentPolicyError("subject_not_allowed")
        return subject

    def begin(self, request, *, server_route, server_purpose):
        """Called only at trusted manual entry; background dispatch MUST specify its purpose.

        No purpose default, no client metadata inspection, no HTTP endpoint.
        Context represents one manual invocation, not a renewable lease/grant.
        """
        if type(server_route) is not str or server_route not in MANUAL_ROUTES or server_purpose is not ServerPurpose.MANUAL_CHAT:
            raise IntentPolicyError("manual_invocation_required")
        subject = self._subject(request)
        with self._lock:
            now = time.monotonic()
            self._contexts = {k: v for k, v in self._contexts.items() if v.expires_at > now}
            if len(self._contexts) >= 64:
                raise IntentPolicyError("context_limit")
            self._sequence += 1
            context = ManualContext(self._sequence, request, subject, now + 5)
            self._contexts[context.sequence] = context
            return context

    def issue_ready(self, context, payload, request_id, *, server_purpose):
        """Mint only at READY with the FINAL text payload sent to the gateway.

        This short context does not cover cold startup; W3 must retain approved
        manual intent independently and create a new READY context using that
        original verified invocation. Status/retry cannot fabricate it.
        """
        if server_purpose is not ServerPurpose.MANUAL_CHAT:
            raise IntentPolicyError("manual_invocation_required")
        with self._lock:
            if (type(context) is not ManualContext or self._contexts.get(context.sequence) is not context
                    or context.expires_at <= time.monotonic()):
                raise IntentPolicyError("manual_context_required")
            if self._subject(context.request) != context.subject:
                raise IntentPolicyError("changed_verified_subject")
            if context.expires_at <= time.monotonic():
                raise IntentPolicyError("manual_context_required")
            self._contexts.pop(context.sequence)  # One attempt, including refusal.
        result = self.gateway._mint({"payload": payload}, request_id)
        return ForwardIntent(result["request_id"], result["intent_token"])

    def close(self):
        with self._lock:
            self._contexts.clear()
            self._verify = None
