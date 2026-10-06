> Historical development contract/proposal. For accepted C1, disabled C2 v5
> and the unexecuted C3 boundary, see the
> [October 6 record](development-status-2026-10-06.en.md).

# On-demand controller Phase 0/1 contract

**Status:** accepted within the Phase 0/1 mock boundary after final independent review

Acceptance evidence: 37 CPU tests and three previous private reproductions
passed; the final independent review reported no blocking findings. Acceptance
applies only to simulated lifecycle behavior and the documented retained-memory
authority. It does not approve any live provider, real artifact/delete UI,
durable authority, deployment, credentials, billing or OWUI/tunnel setup.

**Runtime:** Python standard library, CPU only

**Live RunPod, gateway, tunnel and Open WebUI adapters:** not implemented

## Boundary

`qmc_runpod.ondemand` is isolated from the existing `podapi`, `retry`, shell,
and notebook execution paths. Import and default construction select only
in-memory mock ports. They do not read environment credentials, open sockets,
launch subprocesses, or change settings. There is no HTTP server or CLI in this
phase. `list_models()` reads a fixed local catalog; it never starts a session,
loads a model, sends inference, or extends a lease.

The intended later route remains:

```text
Open WebUI -> localhost gateway -> private tunnel -> authorized on-demand Pod
```

Only the controller and fake effects exist now. No connection from Open WebUI
is configured.

## Controller command contract

| Call | Effect in this prototype |
| --- | --- |
| `list_models()` | Returns a fixed catalog; no state changes |
| `status()` | Returns phase, simulated session/Pod ID and queue depth |
| `start(session_id, limits)` | Requires fresh typed work/cleanup scope, target `new-pod`, total USD cap, absolute expiry and separate explicit cleanup time/USD reserve; checks current time under the mutation lock |
| `run_one_step()` | Advances one mock lifecycle step and persists before each side effect |
| `queue_chat(request_id, prompt, session_id=...)` | Exact-session request; no chat before `ready` or before incremental cost/time reservation |
| `drop_chat(request_id, session_id=...)` | Drops only that queued request in the exact session |
| `abort(session_id=...)` | Drains the exact session; ambiguous create remains unresolved |
| `reconcile_start(session_id=...)` | Accepts exactly one complete unique match; unknown/incomplete/absent listings never authorize another create |
| `submit_termination_approval(approval)` | Requires an exact session, controller-owned Pod, verified export hash, action `terminate`, unused approval ID and unexpired approval |
| `confirm_termination(session_id=..., approval_id=...)` | Separate exact confirmation; consumes approval/operation before one mock attempt; absent only after a separate strict absence check |
| `reconcile_termination(session_id=...)` | Bounded read-only absence check after an unconfirmed outcome; never retries terminate |

`MockAuthority` is an explicitly injected trusted simulation ledger separate
from checkpoints. It retains the immutable grant, elapsed-time high-water,
fixed work/cleanup bounds, conservative cost reservations, consumed approvals
and expected checkpoint digest. Before each effect the controller compares the
checkpoint with this retained authority. Coherently extending checkpoint limits
or resetting usage cannot mint permission. The ledger itself is trusted input;
this is not protection against a caller who can alter the trusted object.

A fresh ledger accepts only a pristine empty checkpoint. Mock restoration of
active or historical state requires explicitly retaining and injecting the
original ledger and mock provider/artifact knowledge. Permission is never
reconstructed from JSON. Durable production authority and elapsed recovery are
not implemented. No credential, signature key or OS security setting is added.
The JSON store uses atomic replacement and a separate OS lock; failed intent
storage sends no effect and can require manual mock recovery.

## Lifecycle

```text
absent -> provisioning -> bootstrapping -> loading -> ready -> draining
       -> exporting -> termination_approval_pending -> terminating
       -> termination_unconfirmed -> absent (only after absence confirmation)
```

Create timeout or malformed response goes to `create_unresolved`. It is
reconciled by unique operation identity and is never blindly retried. Unverified
export goes to `failed`; missing, expired, reused or mismatched approval blocks
termination. Both create and terminate enter their unresolved state before the
effect. Terminate also durably consumes its approval and operation first. A
timeout or failed result checkpoint cannot resend the same attempt. Repeated
confirmation remains unconfirmed; only read-only reconciliation may close it.
No fresh-approval destructive retry workflow is implemented in Phase 1.

Termination re-observes monotonic time after receipt verification and its
checkpoint, and again immediately before dispatch after consumed-intent storage
and adapter lookup. Each admission checks the exact approval expiry, remaining
cleanup duration and reserved/total USD caps. No checkpoint separates the final
check from dispatch. If intent storage or adapter lookup exhausts permission,
no terminate is sent; the already consumed attempt remains unconfirmed and its
conservative cost reservation remains held.

Approval fields require non-empty string identities, action `terminate`, an
exact SHA256 receipt and finite positive expiry. Boolean identities/expiry fail
before acceptance. No active or queued request is valid during cleanup.
Callback-facing commands require session identity; closed IDs cannot be reused.

Time/expiry are re-read under the store mutation lock and compared with the
trusted ledger high-water; concurrent start cannot lower it. At start:

```text
work_deadline = min(start + max_runtime_seconds, expires_at - cleanup_runtime_seconds)
cleanup_deadline = min(start + max_runtime_seconds + cleanup_runtime_seconds, expires_at)
```

These bounds and idle duration never reset on restart. Only completed chat
updates idle time; models/status/queue do not. A late result is withheld.

`max_usd` is the total cap. Work can use at most `max_usd - cleanup_usd`;
cleanup can use at most `cleanup_usd`. Cleanup inputs are mandatory and must
cover export plus one individual deletion attempt. Toy maximum costs are chat
USD 0.01, export USD 0.0001 and terminate USD 0.0001; other effects cost zero.
Each known cost is reserved before dispatch and retained on uncertainty. These
are synthetic admission bounds, not real compute/storage/tax estimates.

Chat/export/terminate have one-second mock duration bounds. Export admission
also retains the following terminate time slot. Work exhaustion drains into
the separately reserved cleanup window. Expired cleanup stays unresolved or
enters `cleanup_expired`, with no automatic deletion. Waiting for human approval
can exhaust the window; recovery then needs a separately reviewed plan.
Arbitrary blocking ports and real deadline interruption are not implemented.

## Deliberately absent

- RunPod create/start/stop/terminate API calls and credential loading
- Bootstrap/download/model-load commands or private tunnel
- OpenAI-compatible HTTP handlers, local gateway process and OWUI registration
- Real pricing/usage reconciliation, durable external idempotency, or cloud
  state reconciliation guarantees
- A real verified/read-back artifact and deletion UI confirming the individual
  resource, retained data and consequences with a human
- Durable trusted authority/elapsed recovery and cross-process ledger sharing
- Reuse of the historical Pod, budget, run records, or trial results as new
  authorization

Every result in the prototype is simulated. Passing CPU tests is not authority
to enable a live provider, configure network/authentication, start a Pod, or
delete any resource.
