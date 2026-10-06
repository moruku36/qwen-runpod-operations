> Historical development contract/proposal. For accepted C1, disabled C2 v5
> and the unexecuted C3 boundary, see the
> [October 6 record](development-status-2026-10-06.en.md).

# Phase 2a synthetic JSON gateway

Status: local candidate awaiting independent review. This is not an OWUI
installation or a RunPod connection. Python standard library only.

`MockGateway()` is inert. Only an explicit context-managed `serve(host="127.0.0.1",
port=0)` listens. Nonloopback addresses and backend modes other than `mock` are
rejected. Only the existing in-memory controller and exact synthetic port classes
are accepted; no credential/environment lookup, real provider, subprocess,
remote URL resolution, tunnel, firewall change or persistent configuration exists.
Tests bind only ephemeral ports and close their listeners on exit.

The fixed, public synthetic authorization value is `Bearer
phase2a-public-synthetic-fixture`. It is a test fixture, not an API credential.
It is required on every route except GET `/healthz`.

| Method and route | Behavior |
| --- | --- |
| GET `/healthz` | Liveness only; no backend/readiness/authority evaluation |
| GET `/v1/models` | Fixed local catalog; no start, inference, lease or budget mutation |
| POST `/_mock/sessions` | Explicit synthetic start intent; 202 with session/request IDs; no inference payload retained |
| GET `/_mock/sessions/{id}` | Exact-session status only; no grant/idle mutation; unknown ID 404 |
| POST `/v1/chat/completions` | Existing READY session only; atomic controller admission; non-stream JSON completion |
| POST `/_mock/sessions/{id}/stop` | Exact-session mock abort/drain request; no export, confirmation or delete dispatched |

Start requires exactly `{"session_id":"s","approval_fixture":
"phase2a-synthetic-start-v1"}` and a valid `Idempotency-Key` header. The fixture
provides a synthetic total USD cap 0.1002, work duration 120 seconds and cleanup
reserve USD 0.0002/30 seconds. These are not actual prices or paid authority.
Same key/session returns the original 202 result without changing permission;
different session under that key is 409. Control competition is 409. The bounded
mock startup worker advances only create/bootstrap/load and stops on a changed
session or phase. Start never retains a chat while booting.

Chat requires `session_id`, `model: "qwen-27b"` and 1–16 text `messages` with
exactly `role` and `content`. Roles are system/user/assistant; content is a
nonempty string of at most 8192 characters. Optional `max_tokens` is an integer
1–256. Optional `stream` must be false; true is 400 `unsupported_stream`.
Unknown fields, tools, media parts, images, audio, URI/URL input and remote
providers/models are rejected. This is a narrow OpenAI-shaped JSON subset.

Request ID comes from a strict `X-Request-ID` or a generated UUID hex string.
Request/session/idempotency IDs must match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`.
Completed chat IDs cannot be reused. Busy admission is 429 `backend_busy`;
nonREADY/expired work is 503 `backend_not_ready`; stale session/control state is
409 `control_conflict`. These outcomes cannot trigger mock create. HTTP does not
perform a status-read followed by separate unchecked inference: READY, exact
session, work/idle boundary, budget reservation and inflight registration occur
under the controller store mutation lock. Execution rechecks ticket/session,
phase and permission; stop/expiry cannot revive an old session.

The bounded synthetic processing delay is cooperative test input, not a live
adapter timeout mechanism. Processing has a separate real monotonic deadline.
The deadline and cancellation event are mandatory execution inputs. The
controller checks them after acquiring its store lock, immediately before
dispatch after checkpoint/adapter work, and after transport return. Successful
release rechecks them under the lock before refreshing idle. Cancellation is
signalled without acquiring the release lock, so a contended worker cannot
dispatch after HTTP timeout/disconnect merely by winning that lock race.
Timeout returns 504 `processing_timeout`; early disconnect cancels the delay
before the fake effect. Cancellation/finalization release inflight exactly once;
uncertain/cancelled work retains its conservative cost reservation. Old tickets
cannot alter a replacement session. Only successful completed chat refreshes
idle. The instantaneous fake chat dispatch is serialized against controller
stop; arbitrary blocking/live adapter interruption is deliberately unsupported.
Admission at idle/work expiry clears active/queued work before saving DRAINING;
any outstanding cancelled ticket is then released once. Competing and original
expired chats both return 503 rather than saving an invalid active cleanup state.

HTTP handles one request per connection and always sends `Connection: close`.
POST requires one decimal, nonnegative `Content-Length`. Duplicate headers,
missing/negative/invalid length, Transfer-Encoding/chunked, folded/malformed
headers, truncation and invalid/duplicate-key/nonfinite JSON are rejected.
Request headers are limited to 16 KiB/64 fields, body to 64 KiB. Header and body
receive phases have independent absolute monotonic timeouts; response socket
I/O and mock processing have separate timeouts. At most 16 HTTP handlers are
admitted concurrently. Framing errors close the connection; pipelining and SSE
are not supported.

In-memory logs contain only ID, normalized route, status, duration and fixed
outcome. Unknown paths are logged as `unknown`. Bodies, authentication, outputs
and exception text are never logged. Public error messages are fixed codes.

Still absent: SSE, real model calls or RunPod, live credentials, durable authority
or gateway idempotency, real artifact verification/delete UI, actual export or
termination endpoint, unattended idle collector, OWUI settings/integration,
tunnel/firewall configuration, deployment and publication. No Phase 2a test
result authorizes those features.
