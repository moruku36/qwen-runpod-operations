> Historical development contract/proposal. For accepted C1, disabled C2 v5
> and the unexecuted C3 boundary, see the
> [October 6 record](development-status-2026-10-06.en.md).

# OWUI request binding and bounded upstream SSE (Phase 3b review candidate)

New files extend the accepted Phase 3a seam without changing its four files or
the accepted Phase 1/2a/2b eleven files. No production setting is applied. The
new isolated local branch is `work/owui-binding-upstream-sse`; Git trust
and ownership exceptions are not changed. These files/patch are separate review
artifacts, not committed deployment or PR output.

## Standard request, trusted session, explicit intent

`OWUIBinding` consumes the same minimal text-only OpenAI-compatible body as the
private model server: model/messages/stream/max_tokens. OWUI does not need to
supply session_id. A trusted local control call binds the exact already-READY
controller session and Pod; it never creates, starts, loads or terminates a Pod.
Controller, authority and lifecycle ports are restricted to the same exact CPU
mock classes. Default upstream is a no-network public fixture. Explicit
`LoopbackUpstream` is tested only against ephemeral 127.0.0.1 HTTP fixtures here.

Each inference requires an internal one-use `InteractivePermit`: object identity,
request ID, bound session/Pod, normalized payload digest, five-second TTL and
64-pending-intent bound. It is issued only by a trusted control/user-gesture hook;
there is no HTTP endpoint that issues it, and no body/header can assert it. Client
session_id, metadata/task claims and interactive flags are rejected. Models and
status are local nonrenewing reads. Background/title-like calls without a permit
perform zero upstream inference and zero allocation; prompt guessing is not used.
Actual OWUI authenticated gesture middleware is still to be wired and verified.

TTL is checked at permit consumption, not for the entire admitted inference.
Consumption occurs after `_begin`'s checkpoint, immediately before reservation.
Processing deadline/cancel and permit identity/TTL are rechecked after checkpoint
and admission lookups. Expiry before reservation means zero reservation/admission/
upstream call. After valid consumption, session/work/idle/processing/cancel fences
continue to apply; dispatch does not renew TTL. A later cancellation does not
refund an earlier valid conservative synthetic reservation.

Admission occurs under the existing controller lock after a fresh processing
deadline/cancel check, including store-lock wait. Existing trusted session scope,
READY/idle/work budget, one-active-request and replay fences apply. The upstream
body contains no control/session/permit fields. Its provider ID/metadata is never
trusted as the gateway request identity. Both JSON and SSE return the admitted
request ID. Provider/lifecycle methods are never called by this binding layer.
The controller's reservation is synthetic cost evidence, not a live USD meter.
The accepted controller API has no processing/permit hook inside admission.
This binding therefore mirrors its locked mock admission with the added
post-checkpoint/pre-reservation gate, without modifying accepted controller code.

Every stream, error, early drop, explicit close and normal completion releases
the exact ticket once. All releases use successful:false, so no JSON/chunk/comment/
reconnect/terminal extends idle or the absolute work deadline. Stop/close signals
cancel before controller locking; paused generator/response/watchdog resources
close synchronously. A running generator closes via its owner context and the
socket watchdog; consumers must use the supplied context manager. No unbounded
background consumer or replay buffer is created.

## Upstream transport and SSE profile

`upstream_sse.py` implements inactive-by-default loopback HTTP JSON/SSE. Structured
`TunnelEndpoint` excludes DNS/public hosts/aliases and reserved local service port.
No proxy, redirect, retry, alternative provider or OpenAI cloud service exists.
Optional auth is caller-injected in memory; tests are auth-free. No credential
file/environment or logger is consulted. Errors expose fixed codes only.

HTTP status must be 200 and content type match the requested mode. Duplicate
Content-Length/Transfer-Encoding, mixed framing, unsupported encodings and lengths
over 64 KiB are refused. SSE supports length-delimited, close-delimited and normal
HTTP chunked bodies. Header reads cap at 16 KiB/68 lines (including status/blank/
interim-response lines); chunk/trailer metadata cap at 32 KiB/4096 lines. A
<=30-second mandatory absolute deadline and caller cancel bracket connect, final
dispatch, headers, each bounded read, parsing and relay. The transport checks its
own cancel/deadline before and after the external fence for every buffered event
and final JSON return. A caller's no-op fence cannot waive those boundaries.
A socket shutdown watchdog
also covers header/body drip and slow/paused consumers; it never takes controller
locks. Cancellation during connect is checked after connect and bounded by its
remaining timeout. Logical dispatch is fenced immediately before request I/O;
remote server cancellation/delivery cannot be proved atomically from a local
socket check, and no production such guarantee is claimed.

SSE parser limits: read chunk 4096 bytes, cumulative input 65536 bytes, one frame
8192 bytes, 256 data events, cumulative UTF-8 content 8192 bytes, cumulative
normalized output 65536 bytes and encoded frame 8192 bytes. TCP/HTTP chunk and
UTF-8 character boundaries are independent; LF/CRLF and multiline data work.
Comments consume byte/frame budgets and cannot renew a lease. Arbitrary event/id/
retry fields, executable events, malformed UTF-8, duplicate JSON keys, NaN/
Infinity and overflow such as nested 1e400 are rejected, including unknown
metadata before it is discarded. The finite-float parser is new Phase 3b code;
accepted Phase 3a is unchanged (its unknown overflow metadata was discarded).

Provider chunks require a stable bounded ID, exact qwen-27b model and chunk object,
one choice/index0, initial assistant role, text-only deltas and finish_reason stop.
Tools, reasoning/media deltas and usage-only chunks are unsupported in this
strict initial profile. Verify the actual pinned llama-server profile before
enabling this adapter; a newer server's generic documentation is not that proof.
Finish is held until [DONE]; EOF/truncation/errors produce no success terminal.
After DONE, extra data in the same consumed buffer is refused and the upstream
connection closes; it does not wait indefinitely to validate future remote bytes.

Final success is fenced/claimed under the controller lock before finish+[DONE]
are exposed. Stop/expiry/cancel that wins first withholds the terminal. A stop
after the success decision does not rewrite it. Partial content can already have
been consumed when a later error occurs; a production HTTP ingress must preserve
pre-header error status and post-header safe error/close semantics and require
DONE at the client. This library yields normalized bytes and raises fixed errors;
it does not start a production HTTP server or claim delivery to OWUI.

## Verification and complete remaining path

New `tests/test_owui_upstream.py` covers a bound OWUI-shaped request through
actual ephemeral upstream HTTP JSON/SSE, chunk/UTF-8 fragmentation, authority/
intent/replay/budget/expiry/busy fences, stop vs terminal order, cancel/close,
header/body/framing bounds, finite JSON and exact release. All 32 new and 93
existing tests passed (125 total). `scripts/owui_binding_smoke.py` completes six CPU-only binding
checks with no socket at all; existing loopback CPU smoke remains separate.

The implementation pieces now include mock lifecycle, gateway JSON/SSE, inactive
private HTTP, bound standard requests and upstream JSON/SSE. Remaining free
integration is the production HTTP ingress + trusted OWUI user/task hook, durable
authority/store and real provider/SSH-port contracts behind default-deny grants.
Do not weaken accepted MockGateway's exact mock port gate to inject a live
adapter. Concrete OWUI runtime/version/user/host-network metadata is needed to
finish the correct hook and non-secret deployment plan. Then review that plan,
configure approved credentials/access, run one explicitly scoped real session,
verify private readiness and end-to-end chat/cancel/idle/export/cleanup. The
approval/access packet is `private-live-access-approval.md`; no part is applied.
