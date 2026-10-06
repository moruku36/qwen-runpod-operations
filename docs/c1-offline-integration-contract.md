> Historical development contract/proposal. For accepted C1, disabled C2 v5
> and the unexecuted C3 boundary, see the
> [October 6 record](development-status-2026-10-06.en.md).

# C1 minimal offline integration candidate

The accepted26 implementation files (Phase1–4) are unchanged. These additions
connect the original minimum: authenticated manual selection of qwen-27b → approved
startup queue → one mock create/bootstrap/load → READY JSON/SSE → unused idle/
work-cost expiry → export/readback → individual exact deletion approval → fake
absence → new approved session recreates. Actual OWUI/RunPod/SSH/settings are not
accessed. No added multimodal/tool/title/embedding/background inference feature.

## Interfaces and operation

`OnDemandRuntime()` is inert: memory state, default-deny user verifier and empty
subject/grant allowlists; no sockets, disk, processes, environment or credentials.
`approve_session(UsageLimits)` is a trusted local control operation, not an HTTP
request/body assertion. The immutable session grant contains action scope, absolute
work/cleanup limits, USD and reserves. Profile Rate values are public synthetic
fixtures, not claimed current RunPod prices. PodSpec uses a synthetic image digest.
Production account/image/GPU/rate selection is C2 input, never inferred from history.

`OfflineOWUICallSites` projects the known OWUI0.11.4 manual routes and final form
to text-only chat fields. Bookkeeping metadata is stripped, never used as identity
or intent. A reviewed server call site must supply explicit ServerPurpose and a
verifier returning the actual authenticated user after get_verified_user. Reusing
the same request/route in title/tags/follow-up/tool/internal work cannot mint intent.
This does not install or patch the real backend; the verifier/call-site provenance
remains a deployment trust condition to independently review.

Manual context is consumed while still fresh before startup. Its immutable pending
payload lives only in memory, bound to RID/session/original work deadline. Queue is
limited to8, approved grant registry to16, and grant replacement is refused.
Concurrent manual requests serialize one controller startup. `status()` and model
listing do not advance the worker or renew anything. `tick()` is one explicit CPU
worker/watchdog step; no background thread or scheduled paid work starts by default.
The hosting loop must call it during startup, inference, unused idle and cleanup.
W9 fixtures drive this loop explicitly; persistent service scheduling is C2 work.

At first READY only, idle timing begins at load completion and is sealed once per
session. Work/cleanup UTC and USD limits retain their original start point. A long
cold load (347.956s fixture, or >=17.5min full build/download planning allowance)
does not expire merely because unused idle began before readiness. Restart, stream
content, catalog and status never refresh idle. READY permit is minted just before
dispatch, with the accepted five-second TTL; startup does not retain that short permit.

`ready_request()` yields normalized final body and one-use inference intent headers.
The caller sends them to the already-reviewed ephemeral gateway in fixtures; no
actual OWUI request is issued here. Normal JSON/SSE relay, parser/write/overall
limits and cancellation remain the accepted Phase4 behavior. Additional instance
composition checks elapsed price cost on every stream/JSON write; accepted source
is not altered and no provider class/type check is bypassed. Metered authority
checks time/rate/cost on reserve and dispatch fences as well as explicit watchdog
steps. Synthetic effect cost is conservatively added to elapsed GPU+storage rate,
overhead and cleanup reserve; this is a fixture accounting model, not an invoice.

Drop cancels the selected pending request; dropping the last pending startup request
with no dispatched chat closes an unattempted grant or drains an owned fake Pod.
It preserves a different dispatched chat. Explicit queue retry needs a new verified
manual context and retains the same grant/deadline. A dispatched/unknown inference
cannot be replayed by retry_queued. Unknown create uses the original operation and
explicit reconcile, never another POST. Stop is exact session scoped; None/wrong
session is not an active-session shortcut. Shutdown blocks further worker effects.

## Durable authority and recovery

`AuthorityJournal(path,head_path,key=...)` construction is inert. Explicit `open()`
creates only caller-selected local journal/head/owner files. The tests use temporary
workspace-owned public fixtures. No signing-key discovery/generation/persistence or
ACL change occurs. A nonblocking OS byte lock prevents two controllers owning the
same journal; SQLite append uses synchronous FULL, bounded records/chain/history
and MACs bound to the canonical journal path/domain. A separate signed head uses
compare-and-swap and atomic file replacement/fsync. It advances before SQL commit;
an interruption between the two refuses resume. No failed commit grants an effect.

At each original controller seal, full trusted authority (limits/usage/effect intent/
ownership approvals/closed sessions/time high-water) is journaled before effects.
Memory checkpoint is reconstructed from that ledger, not accepted as a grant.
Ledger-only rollback, modified rows, missing/tampered head, incomplete commit and
unknown checkpoint are denied. A changed Rate/Pins/PodSpec or backwards recovery
clock requires recovery; it cannot reset limits. Real wall/monotonic clock recovery
must be chosen in C2, with fail-closed behavior when elapsed time is not trustworthy.

The injected key, ledger and independent head are service-owned trust roots; C2
must protect them with the named service identity and approved storage/ACL/secret
facility. A process/owner able to rewrite both trust roots or use the signing key
can forge history; this code does not claim protection against that owner. It is
not a remote monotonic witness or account-level spending cap. Power-loss/corruption
recovery never deletes/resets the ledger automatically.

Journal auxiliary snapshots are immutable and require no runtime lock during a
controller seal. They retain only fake provider world, readiness proofs, report
bytes/pins and pending IDs. Chat bodies/responses, authentication values and keys
are absent. Pending original bodies and gesture objects never restore. Restart
before unattempted create closes unused authority, mid-bootstrap drains instead of
loading without a request, active inference drains without replay, and unknown
create/delete requires reconciliation. READY retains the first-idle baseline.

## Provider/bootstrap/export seams

`ProviderPlans` records REST-v1 request DTOs and exact trusted create acknowledgement
ownership. Create/terminate operations are not replayable; account/session/Pod/
allowlist checks exclude retained synthetic-held-pod. Matching a resource name alone
is not ownership; reconciliation requires original operation-index knowledge.
`dispatch()` ALWAYS denies live provider execution. DTOs are paired with the original
MockProvider in the harness, not connected to a real network driver. Official shape
references (public docs only, no API calls):
- https://docs.runpod.io/api-reference/pods/POST/pods
- https://docs.runpod.io/api-reference/pods/DELETE/pods/podId

`BootstrapPlans` pins model/source hashes from the immutable historical baseline,
alias qwen-27b, context8192 and private pod127.0.0.1:8080/forward127.0.0.1:19181.
No real command or SSH is executed. Fake readiness requires matching Pod/session,
SHA/commit/profile, authenticated endpoint and owned process proof; an HTTP health
reply alone does not establish those facts. These attestations are explicitly mock,
not evidence of a downloaded model or today's cached run. Real image/server/model
verification, SSH host fingerprint/key/secret-file/bootstrap execution remain C2/C3
connection inputs and gated adapters, not callable operations in this candidate.

Export writes bounded synthetic reproduction pins and event names outside the fake
Pod world, readback verifies SHA, and the resulting digest replaces the mock export
receipt. Report bytes persist in the service journal across fake Pod deletion.
Terminate requires that readback AND the original typed exact session/Pod/export/
action/expiry approval; consumed unknown delete cannot replay after restart.
Without approval, worker stays approval_pending until cleanup authority expires;
it never infers a policy for automatic deletion. No claim that storage billing stops
at an approval wait or provider outage. A conditional idle cleanup policy is a later
separate authorization, beyond this minimum individual-approval flow.

## Verification and gates

Run only the explicit CPU fixture command `python -B scripts/c1_cpu_smoke.py` or the
allowlisted review runner. No persistent service/production CLI is included. Tests
tripwire socket/DNS/bind to literal127.0.0.1; mock smoke serves only port0. No existing
OWUI/Ollama/Voice endpoint is contacted. Default import/construction stays offline.

C1 is an OFFLINE INTEGRATION CANDIDATE until independent review accepts its durable state,
server call-site trust assumptions, meter/queue and full fake flow. It is not a
completed live provider driver, installed OWUI hook, SSH bootstrap, protected OS
service or real-price billing proof. These absences are explicit connection seams,
not silent completed work. Remaining FREE CODE before full C1 is the concrete
provider/SSH execution adapter and reviewed production-boundary wiring, plus the
version-specific installer/projection into actual backend callable interfaces.
They must be implemented/tested offline behind default deny. The accepted gateway's
exact mock-port gate cannot simply be configured to accept live ports. These code
gaps are not mislabeled as user settings or a C2-only approval blocker.
C2 then needs the concrete deployment/adapter activation
packet with actual user, image/rate/account, protected trust roots, authentication,
tunnel and export settings; W10/C3 needs an approved bounded paid end-to-end run.
No app user ID, private memory DB, real key or access setting was inspected/changed.
