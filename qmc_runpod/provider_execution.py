"""One-attempt provider plans -> durable effect boundary -> explicit REST port.

Trusted lifecycle host supplies durable ownership/result commit and permission
checks. No default activation, no lifecycle/gateway type gate is changed.
"""
from dataclasses import dataclass
import time

from .c1_ports import PortError, ProviderPlans
from .execution_adapters import Effect, RunPodREST, validate_pod
from .production_boundary import EffectBoundary
from .ondemand import TerminationApproval


@dataclass(frozen=True)
class ReadbackProof:
    session_id: str
    pod_id: str
    sha256: str


class ProviderExecution:
    def __init__(self, *, plans=None, boundary=None, transport=None, verify_readback=None):
        self.plans = plans if plans is not None else ProviderPlans()
        self.boundary = boundary if boundary is not None else EffectBoundary()
        self.transport = transport if transport is not None else RunPodREST()
        self.verify_readback = verify_readback
        if type(self.plans) is not ProviderPlans or type(self.boundary) is not EffectBoundary:
            raise PortError("typed_provider_boundary_required")

    def create(self, session_id, operation_id, deadline):
        effect = Effect(session_id, operation_id, "create", None, deadline)
        def invoke(e, cancel, fence):
            request = self.plans.create(session_id, operation_id)
            fence()
            value = self.transport.request(e, request.method, request.path, request.body, cancel)
            proof = validate_pod(value, self.plans.spec, session_id)
            fence()
            # Only normalized proof reaches the trusted host's durable commit.
            # Raw response env/keys never enter the boundary journal or logs.
            return proof
        return self.boundary.execute(effect, invoke)

    def read_owned(self, session_id, pod_id, operation_id, deadline):
        self.plans.check_owner(session_id, pod_id)
        effect = Effect(session_id, operation_id, "read", pod_id, deadline)
        def invoke(e, cancel, fence):
            request = self.plans.read(session_id, pod_id, operation_id)
            fence(); value = self.transport.request(e, request.method, request.path, request.body, cancel)
            fence()
            return None if value is None else validate_pod(value, self.plans.spec, session_id, pod_id=pod_id)
        return self.boundary.execute(effect, invoke)

    def terminate(self, session_id, pod_id, operation_id, deadline, *, approval, readback, allowlist):
        self.plans.check_owner(session_id, pod_id)
        if (type(approval) is not TerminationApproval or type(readback) is not ReadbackProof
                or approval.action != "terminate" or approval.session_id != session_id or approval.pod_id != pod_id
                or readback.session_id != session_id or readback.pod_id != pod_id
                or approval.receipt_sha256 != readback.sha256 or time.time() >= approval.expires_at
                or allowlist != frozenset({pod_id}) or self.verify_readback is None
                or self.verify_readback(readback) is not True): raise PortError("exact_approval_and_readback_required")
        effect = Effect(session_id, operation_id, "terminate", pod_id, deadline)
        def invoke(e, cancel, fence):
            # The trusted check callback also validates approval consumption/
            # total cleanup reserve and UTC scope at connect/send/result gates.
            if time.time() >= approval.expires_at: raise PortError("cleanup_approval_expired")
            request = self.plans.terminate(session_id, pod_id, operation_id, allowlist)
            fence(); return self.transport.request(e, request.method, request.path, request.body, cancel)
        return self.boundary.execute(effect, invoke)
