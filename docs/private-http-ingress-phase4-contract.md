> Historical development contract/proposal. For accepted C1, disabled C2 v5
> and the unexecuted C3 boundary, see the
> [October 6 record](development-status-2026-10-06.en.md).

# Phase4: ephemeral HTTP ingress and manual intent policy candidate

Scope: CPU mock authority and explicitly enabled ephemeral IPv4 loopback fixtures.
No deployment, real OWUI configuration, durable authority, RunPod API, SSH, GPU,
OpenAI service, credential discovery, billing, or destructive real operation.
The accepted Phase1–3b 21 files remain byte-identical. Independent review of this
new candidate is still required; passing tests do not establish real connectivity.

## W1 implemented surface

`PrivateGateway()` is inert and defaults to mock upstream. `serve()` permits only
literal `127.0.0.1` and port 0; it is an ephemeral test context, not a service CLI.
Public synthetic inference/control keys are distinct. Future secret storage and
deployment authentication are not supplied by these fixture constants.

| Route | Role | Effect |
| --- | --- | --- |
| GET /healthz | None | Liveness only |
| GET /v1/models | Inference | Fixed catalog, no mutation or allocation |
| GET /status | Inference | Trusted mock snapshot, no lease renewal |
| POST /_control/intents | Control | One attempt to mint a five-second READY intent |
| POST /v1/chat/completions | Inference + intent | Standard text JSON/SSE against bound READY mock session |
| POST /_control/stop | Control | Exact bound session cancellation and mock draining |

Control intent body is exactly `{"payload": <final standard chat body>}`. Both
intent mint and chat require a bounded `X-Request-ID`. Mint returns a random opaque
32-character `intent_token`; chat supplies it as `X-Intent-Token`. The token maps
to the accepted binding's one-use object and exact RID/session/Pod/payload digest.
An attempted chat consumes the HTTP token even if body mismatch/admission fails.
The binding checks deadline/permit after checkpoint work before budget reservation.
At most 64 unexpired intents are retained. The control key represents a trusted
server channel in this fixture: it does not prove an authenticated human gesture
or grant creation/deletion. An inference key alone cannot mint intents or stop.

No create/start/bootstrap/delete endpoint exists. Model listing, status, client
`metadata.task`, guessed user ID, ordinary chat, title generation, wrong scope,
and repeated token cannot implicitly provision or infer. Unsupported tools/media/
URLs/task fields are refused by the accepted text-only request builder.

The accepted parser bounds headers/body/framing and sixteen simultaneous clients.
The new decoder also rejects duplicate JSON keys and nested nonfinite overflow.
Requests use one connection, no pipelining. A per-request watcher signals EOF,
unsolicited bytes, shutdown, or processing deadline during an upstream wait.
Watchers are joined; upstream cancellation/deadline is enforced by the accepted
loopback adapter. Construction/import make no socket or environment reads.

JSON is normalized through the accepted binding, then checked against the original
session/Pod, finite clock, idle and processing deadline immediately before each
socket write. Any first accepted header byte commits status; a later failure closes
instead of appending a second HTTP response. No socket I/O occurs under controller
locks. A time/stop boundary may prevent completion after a partial response; the
gateway cannot retract bytes already delivered or prove remote cancellation.

SSE validates its first upstream frame before HTTP 200. Validated content is split
at Unicode character boundaries into bounded normalized frames (no provider IDs,
tools or metadata). The accepted per-write/overall guard and output/event limits
apply. The binding linearizes terminal success against stop under its controller
lock; the downstream writer still enforces cancellation/deadline/disconnect.
Verified upstream stop+[DONE] is one paired terminal. No EOF, malformed frame,
budget refusal, shutdown, cancel, or partial failure fabricates a success terminal.
After header commitment, only bounded safe error or close is possible. Admitted
tickets release once; failures and streaming never renew the idle deadline.

Review boundary fixes: exact stop validates bound/current session and dispatches
`controller.abort(session_id=<validated id>)` under binding→store locks. It never
calls the unscoped binding.abort. Token invalidation is scoped to that old session
outside those locks, preserving mint lock order and successor tokens. Content uses
a cumulative UTF-8 byte counter independent of frame/event/wire size; exceeding
the configured content cap emits no offending content or success terminal. The
single minimum processing/overall stream deadline is passed to binding/upstream
and the client watcher BEFORE admission/first read, so an upstream read wait cannot
outlive the short overall stream limit.

Logs retain only validated RID, normalized route, status, elapsed time, fixed outcome.
No body, prompt, response, URL query, role key, intent token, upstream exception,
authentication header or credentials are logged.

## W2 implemented policy core; deployment still pending

`OWUIIntentPolicy` pins Open WebUI 0.11.4. Its default verifier is absent and subject
allowlist empty. A trusted backend callback must supply the authenticated subject
after `get_verified_user`; body/header claims are not an authentication source.
The two known manual entry routes are `/api/chat/completions` and
`/api/v1/chat/completions`. Route alone is insufficient because background work
can reuse the original request. Reviewed SERVER call sites must explicitly pass
`ServerPurpose.MANUAL_CHAT`; title/tag/follow-up/embedding/tool/internal purposes
are denied at both begin and issue, even on the manual route.

Manual contexts are retained by object identity, bounded to 64, nonrenewable,
five-second, one attempted use. Issue re-verifies the same subject and rechecks
context expiry after verifier work before consuming it. Copied/foreign contexts
are refused. A context/request must never be published as a bearer authority.
The final transformed payload is bound only when READY. This short context does
not implement startup intent retention; W3 must retain the original approved manual
invocation with its own absolute deadline and create the short READY permit at
dispatch. Polling/retry cannot create a new manual grant.

The policy callback/call-site provenance is a trust assumption to review in the
actual backend integration. The fixture policy does not itself implement OWUI
authentication, install middleware, resolve app user ID, register a tool, or modify
memory/background routing. These remain explicit W2/C2 dependencies.

Primary source pins, inspected read-only:
- https://raw.githubusercontent.com/open-webui/open-webui/v0.11.4/backend/open_webui/main.py
- https://raw.githubusercontent.com/open-webui/open-webui/v0.11.4/backend/open_webui/routers/tasks.py
- https://raw.githubusercontent.com/open-webui/open-webui/v0.11.4/backend/open_webui/utils/chat.py

## Remaining completion sequence

C0 (accepted Phase3b) is complete. This candidate advances W1 and the W2 policy
core, not C1 as a whole. W0 still needs the non-secret authenticated app subject
and current connection flags; deployment configuration must preserve existing OWUI extensions
and unrelated local services. W2 actual manual/task call-site integration depends on those
facts and version pin. W3 connects original approved intent, bounded queue, single
create, cold/cached startup, READY dispatch, abort/drop/retry and status. Cold full
build/download >=17.5 minutes is separate from READY HTTP <=30 seconds; successful
cached diagnostics do not prove reprovision startup.

W4 must supply durable authority, protected trust root and crash/rollback recovery.
W5 supplies default-deny provider request/response/ownership/reconciliation ports.
W6 supplies pinned bootstrap/SSH fingerprint/tunnel/model/server readiness plans.
W7 supplies real time/rate/cost and cleanup reserve accounting; synthetic chat .01
USD is not the real RunPod rate. W8 supplies export/readback and exact-owned action
approval (automatic idle deletion needs a separately approved conditional policy).
W9 integrates these into one full fake flow with packaging, pins and independent
review. These code/plans can progress without using real credentials or provider
services. C1 requires all interfaces integrated and review without blocking defects.

C2 follows a concrete approved activation packet for OWUI connection/hook/subject,
inference/control auth, named service/state/secret/ACL, SSH endpoint identity,
private model server and exact usage/cleanup scope. W10/C3 then proves one bounded
approved real session including export, approved cleanup and actual billing.
C4 authorizes subsequent operation. No existing stopped Pod is part of this work.
