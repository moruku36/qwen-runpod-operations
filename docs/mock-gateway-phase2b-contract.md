> Historical development contract/proposal. For accepted C1, disabled C2 v5
> and the unexecuted C3 boundary, see the
> [October 6 record](development-status-2026-10-06.en.md).

# Phase 2b bounded synthetic SSE candidate

Status: local candidate for independent review. Phase 2a's exact accepted source,
hashes, patches and report are retained under `revision/accepted-phase2a/`.
Only `stream:true` on the existing authenticated chat route is added. Explicit
start, synthetic auth, nonstream JSON, strict input/framing, models/status,
controller grant and mock-only/loopback gates retain the Phase 2a contract.

The prior `unsupported_stream` response changes to bounded SSE. No automatic
start, live adapter, dependency, UI, config loader, credential or deployment is
added. The example `mock-only-disabled-live.example.json` is illustrative test
configuration; it does not apply any settings or start a process.

## Admission, headers and release

READY/session/permission/inflight admission occurs exactly once under the
controller lock. The instantaneous fake model uses the existing mandatory
deadline/cancel dispatch checks; for SSE its ticket release is deferred until
the output terminates. No token, queue or request is recreated on reconnect.

Before the first header byte, processing/stream deadline, cancellation, socket
disconnect, shutdown, exact ticket and current work/idle scope are checked.
No rejected pre-header request leaks a 200: it uses existing JSON/status codes.
HTTP status is committed at the first accepted header byte. Thereafter only a
fixed safe `event: error` or connection close is allowed, never another status
line. Error and incomplete streams have no fabricated stop/[DONE] sequence.

One handler writes each stream. Controller fences are short locked checks; no
controller lock is held across socket select/send, chunk delay or blocked
reader. Cancel is signalled before release-lock acquisition. Every path releases
the exact ticket once. Successful SSE also uses release without idle refresh:
chunks, heartbeat (absent), reconnect and terminal delivery never extend lease.

## Wire and finite budgets

UTF-8 SSE frames are `data: <JSON>\n\n`, followed on success by `data: [DONE]\n\n`.
JSON uses escaped newlines/quotes and unescaped valid UTF-8; text pieces split
on character boundaries. Any TCP byte fragmentation can be reassembled with an
incremental UTF-8 decoder. All completion chunks have the same strict request ID
and `qwen-27b` model. Order: assistant role, content pieces, one finish_reason
stop, one [DONE]. A client must require [DONE]; EOF alone is incomplete.

Default independent limits: content 8192 UTF-8 bytes, admitted SSE body 32768
bytes, 128 events (including error/finish/[DONE]), pending frame 1024 bytes,
8 characters per content piece, total stream time 0.5 seconds, each write wait
0.1 seconds. Hard constructor ceilings are 16384/65536 bytes, 1024 events,
2048-byte scratch/pending buffer and 64 characters per piece; time values are
finite and at most 10 seconds. Header size is fixed below 512 bytes. Overall
processing deadline still bounds execution and output. Only one bounded encoded
frame is pending; no producer queue, replay history or eager chunk list exists.
Limits are checked before admission of each output buffer. Exhausted budget may
require close when no room remains for a safe error event. All writes are
nonblocking with short select polls and repeated cancellation/deadline/scope
checks; a slow reader cannot suspend the watchdog or generate an unbounded queue.
The individual absolute write deadline is rechecked after the final controller
guard/checkpoint wait, immediately before send. Writable readiness is not retained
permission to send after that write window has elapsed. Before header commit this
returns JSON504; after commit it uses safe error/close without stop/[DONE].

Fixtures `utf8` and `long` are fixed public synthetic test text; they are local
constructor options, not HTTP inputs or model adapters. They exercise encoding
and budgets; default output comes from the existing fake transport.

## Terminal ordering and delivery

Terminal success is linearized against stop/expiry under the controller lock
immediately before the final finish/[DONE] buffer is sent. A control change that
wins first causes error/close with no success terminator. If success wins first,
a later stop/expiry does not rewrite that decision; it still does not refresh
idle or dispatch another inference. Error/disconnect likewise have one first
terminal decision. Finite write/processing/shutdown/cancel checks continue during
delivery. Failure after the success decision closes the incomplete delivery;
metrics mark `complete:false` and logs do not claim stream success. A terminal
decision is not proof that a remote reader received [DONE]. Partial TCP output
cannot be recalled; no production delivery/interrupt guarantee is inferred.

Metrics retain only ID, terminal decision/count, completion flag, admitted event/
byte counts, pending peak and header-commit flag. Existing request logs remain
ID/normalized route/status/duration/fixed outcome, without body/auth/output or
exception text. Context exit signals shutdown, shuts down accepted sockets,
joins handlers and bounded fake workers; no listener or orphan handler remains.

## CPU smoke and failure matrix

`python.exe -B scripts/mock_cpu_smoke.py` runs one isolated ephemeral-loopback
fixture, validates JSON/SSE, mock drain and safe error statuses, reports only
normalized results/logs/metrics, then shuts down. It cannot select a live backend.

| Failure or race | Expected result / evidence |
| --- | --- |
| No READY session, including title-like request | JSON503, zero create/chat |
| Model/status repeated | No grant/idle/allocation mutation |
| Auth/model/media/framing/ID error | Existing fixed JSON error; no dispatch |
| Header gate deadline/cancel/stop/expiry | JSON504/409/503, no 200 bytes |
| Dispatch store-lock contention then deadline/disconnect | Zero chat, release once |
| UTF-8/JSON/blankline TCP fragments | Exact text reconstruction, same ID/model |
| Failure after header | Existing HTTP200, safe error/close; no stop/[DONE] |
| Midstream disconnect, stale reconnect | Incomplete EOF, release once, ID409; no revival |
| Content/event/body/frame cap | Independent bounded rejection/close |
| Total deadline / nonwritable slow-reader fixture | Watchdog closes; no [DONE]; bounded buffer |
| Stop/expiry before or after success decision | One winner, no lease extension |
| Error/disconnect in either order | One winner, no false success |
| Shutdown | Cancel/close, release once, no listener/handler/worker orphan |

Still absent: real RunPod/OpenAI inference, actual cost/export/delete, durable
authority/inflight/idempotency, real permanent-deletion UI, unattended collector,
OWUI settings, tunnel/firewall/auth changes, credentials, public endpoint and
deployment. Wait for independent review; no live operation is authorized.
