> Historical development contract/proposal. For accepted C1, disabled C2 v5
> and the unexecuted C3 boundary, see the
> [October 6 record](development-status-2026-10-06.en.md).

# Phase 3a private connection seam (review candidate)

The accepted Phase 1/2a/2b source stays byte-identical. New `private_chat.py`
implements a standalone inactive-by-default HTTP client seam, not a live gateway
or RunPod control plane. No paid operation, existing Pod, existing OWUI service,
unrelated local services, credential store, config or firewall is touched. No package is
installed. New local tests use only ephemeral `127.0.0.1` fixture listeners.

Intended routing is OWUI backend -> localhost gateway -> private tunnel ->
Pod-hosted llama-server `/v1/chat/completions`. "OpenAI-compatible" specifies the
wire protocol. OpenAI's cloud API is not part of this route. Official references:
[OWUI provider connections](https://docs.openwebui.com/getting-started/quick-start/)
and [RunPod SSH access](https://docs.runpod.io/pods/configuration/use-ssh).
These references do not verify a real deployment, a specific SSH-forwarding mode
or current Pod access. No SSH command is run or emitted as a ready-to-run command.

## Implemented and exercised

Construction/import and pure request building do no I/O. An explicit
`allow_loopback_io=True` permits a bounded local fixture exchange; it is not a
paid-use approval. Endpoints use structured port and exactly `127.0.0.1`, never
URL input or DNS aliases. Port 3001 is excluded. No proxy discovery, redirect,
retry, cloud fallback, model discovery, bootstrap or lifecycle action exists.
Optional bearer is supplied in memory by the caller; tests use only the existing
public synthetic fixture. No credential is loaded or logged; request bodies are
excluded from repr. Exceptions expose fixed codes and never upstream text.

The request contract is text-only qwen-27b, 1-16 system/user/assistant messages,
8192 characters/message, 64 KiB encoded body, integer max_tokens 1-256, and
stream:false. URLs, multimodal inputs, tools and extra request fields are refused.
Responses require status 200, JSON content type, exactly one bounded positive
Content-Length, no Transfer-Encoding, strict UTF-8/nonduplicate finite JSON, one
assistant choice and finish_reason:stop. Unknown provider metadata is discarded.
The real server's model alias must be verified as qwen-27b before wiring this
contract; no live alias or llama-server launch setting has been inspected/applied.
Provider-native completion IDs/usage are intentionally not forwarded yet; the
gateway must eventually normalize these to its own admitted ticket. This reader
does not accept upstream SSE/chunked bodies. Accepted Phase 2b mock SSE remains
independent and unchanged.

Every call requires an absolute monotonic deadline <=30 seconds ahead and a
cancel event. Checks bracket connect, dispatch, headers, each body read and
completion parsing. A short polling watchdog shuts down the *same* connected
socket on cancel/deadline, including slow header drip. Reader, response, socket
and watchdog are cleaned up on every path. A cancellation during socket connect
is checked after connect and remains bounded by the socket's absolute remaining
timeout; this standalone seam does not promise atomic dispatch against controller
stop/expiry. Controller-locked admission and exact release must be integrated and
reviewed before any live use.

## Settings proposal and approval boundaries

`private-connection-disabled.example.json` is descriptive, with no loader. The
candidate ports are unallocated suggestions, not occupied or configured ports.
Loopback URLs work only if the OWUI *backend* and gateway share Windows host
networking. Container/WSL localhost reachability has not been verified; any host
bridge/listen expansion needs a separate concrete access plan, not a wildcard
listen or automatic use of host.docker.internal.

Before live connection: independently review this candidate; identify the actual
OWUI runtime/version and back up the exact connection fields; approve the exact
settings and injected gateway/model-server auth method; verify occupied ports;
approve and verify exact Pod ownership plus private-tunnel key/host fingerprint,
forwarding mode and endpoint identity. Do not infer identity from loopback alone.
Auth-free HTTP is a test fixture, not a recommendation to remove live OWUI auth.
Keep model-list/title/background actions allocation-free. Their actual OWUI
classification/middleware and production nonrenewing status path are pending.

Before RunPod provisioning or GPU use: obtain a new explicit session grant with
exact scope, price/cold-start scenario, work/idle/absolute deadlines, USD cap and
cleanup reserve. Cached load success (347.956 seconds in prior evidence) does not
establish fresh reprovision: cold build/download exceeded 17.5 minutes. The
existing stopped Pod is outside all fixture scope. No live create/start/stop/
terminate/delete adapter has been added. Destructive cleanup still requires exact
ownership allowlist, export/readback verification and specific action approval.

Remaining implementation: durable authority across controller restart, real
RunPod control port with default-deny usage grants and reconcile semantics,
verified tunnel identity, authoritative readiness (not catalog presence), upstream
bounded SSE with cancellation/error terminal semantics, admitted-ticket adapter
wiring, cost accounting and cleanup UI/export. Mock fixtures do not establish
those production guarantees.

## Local verification

Run only the CPU unittest files and accepted smoke. Never run the repository's
historical paid/provisioning scripts. New tests prove inert/disabled defaults,
cloud/alias/Voice-port denial, pure UTF-8 serialization, strict request/response
contracts, auth-free fixture HTTP, accepted mock gateway rejection without Pod
mutation, no redirects/retries, deadline/header drip, cancellation and no orphan
watchdog. The accepted mock gateway deliberately requires a `session_id` beyond
provider-native wire format; the new test proves direct provider-shaped input is
rejected without admission or allocation. This is a concrete integration blocker:
OWUI-facing code must bind a previously authorized exact session internally and
strip its private control fields before model-server dispatch. Do not remove the
accepted gateway's session check just to make plain OWUI requests succeed.
Existing 74 tests and accepted 11-file SHA verification remain required.
