# October 6 development status: accepted C1 / disabled C2 v5 / pending C3

[日本語](development-status-2026-10-06.ja.md)

This record supersedes the October 2 README status and the earlier local proposal
documents for current development progress. Historical trial reports remain dated
evidence; their resource retention, balance and prices are not current authorization.
Publishing this code does not enable a provider or authorize a paid session.

| Stage | Recorded outcome | Limit |
| --- | --- | --- |
| C1 CPU controller / bridge | Independently accepted source; fake-only lifecycle, durable authority, queue/status, gateway and OWUI dispatch contracts | Synthetic provider/SSH/results are not operational or billing proof |
| C2 v5 | Independently accepted disabled local installation and race repairs; corrected OWUI planner, backend import/startup and disabled gateway verified; v5 reapplication completed | No private input handoff, credential facility, provider, journal or tunnel enabled |
| C3 | Not executed | Live reprovision/readiness/inference/idle termination and operational budgets still need a separate reviewed activation plan |

The intended route is authenticated Open WebUI → local gateway → private tunnel →
fresh, exactly owned RunPod allocation when an admitted request needs it →
OpenAI-compatible chat after verified readiness → export/readback and separately
authorized termination when idle. A later request recreates the resource.
The controller uses a startup lock, a bounded queue, immutable absolute deadlines
and budget limits. Restart, abort, disconnect and retry cannot renew a grant.
Catalog reads and title/tag/follow-up generation cannot provision or renew a lease.
Deletion requires exact immutable ownership, the resource allowlist, verified export
and action-scoped authority. Imports and default construction deny live effects.
No credential discovery or secret logging is part of the new controller.

The OWUI 0.11.4 planner preserves the original public route signature and verified
user dependency. It defines the manual wrapper before following module uses;
legacy aliases and `app.state` use the original internal pipeline, without a
manual-send context. Background dispatch cannot mint an inference/provision permit.
The disabled installed hook rejects on-demand chat until a separate secure binding.
Public health was checked successfully, while unauthenticated model/status routes
returned 401. An empty login form was checked without reading account/session data.

The accepted private source snapshot had 50 source files plus 27 supporting
evidence files. Its source-manifest SHA256 was
`4f9a58c0862e668f53d61bb320a96b04469e8fc6d34d0ce3e5e3691f9d044ed2`.
Local acceptance recorded 274 CPU unit tests, 11 planner regression checks and
43 v5 native/record checks passing. The repaired backend dependency import,
174 OWUI module imports and startup/shutdown also passed. An expanded nonmigration
module sweep had 190 passing imports and 15 missing optional SDK imports, matching
the baseline; it is not evidence that every optional integration was installed.
These are historical acceptance results, separate from this publication's CI.
Sanitized public paths/fixtures, portable test inputs and the hook-retention wording
mean the published tree is not byte-identical to the private reviewed packet.

Public publication includes the CPU source and tests, the build-to-trial artifact
handoff fixes, pinned native-tool preparation, and C2 reusable recovery primitives
with inert operator templates. The templates omit local paths/owner identities and
must not be treated as an installable accepted bundle. See the
[C2 recovery constraints](../scripts/c2/README.md). Private bundle history, generated
deployment manifests, ACL snapshots, inputs, raw process/runtime logs, browser
profiles, user identities, keys and model/cache artifacts are excluded.
CI uses standard CPU runners, synthetic fixtures and no provider credentials.
Tests requiring a separate upstream checkout explicitly skip when it is absent;
CI does not claim those skipped backend integration cases passed.

Publication validation on Windows passed the dependency-free 278-test contract
suite and seven additional synthetic planner regressions. Native PowerShell 5.1
fixtures passed 15 main-CAS and 17 hook/exclusive-create cases. The first temporary
main fixture exposed inherited-ACL differences; the harness now supplies the
protected-file ACL precondition required by C2 and changes only synthetic files.
The staged public blobs were checked for excluded private artifacts, machine
paths/identities, literal user UUIDs and secret/token patterns, with no matches.
Local pytest installation encountered a PyPI distribution-host TLS handshake
failure; the broader Linux pytest suite is validated by GitHub CPU CI instead.
Publication CI also exposed a Windows socket timer race: a socket using only the
remaining absolute request budget could expire just before the monotonic deadline
and be classified as a generic 502. The public transport now reports that wait as
request deadline (504), with explicit cancellation taking precedence and no retry
or budget extension. A deterministic fake-connection regression covers both cases.
The legacy CLI static credential guard is retained alongside a package-wide AST
check for ambient credential lookups; injected bridge authentication is explicit.

The already observed A100 80GB / 27B GGUF diagnostic loaded in **347.956 seconds**,
used context **8192**, completed one request with a two-character answer, and passed
cleanup/result collection. This was a cached diagnostic. A cold full build/download
was **at least 17.5 minutes**; fresh reprovision/startup latency remains unmeasured.
Neither the cached load nor CPU CI proves unattended C3 end-to-end behavior.
No new GPU or RunPod lifecycle action was performed during publication.

Before C3, remaining work includes the explicitly supplied authenticated application
subject, distinct inference/control credentials through the reviewed local facility,
verified SSH identity and per-resource host key, immutable provider/resource ownership
and action grants, numerical work/idle/absolute budgets and export/cleanup reserve,
and an attended real reprovision/readiness/chat/cancel/idle/termination proof.
The existing stopped resource is outside this publication's execution scope.
Unrelated OWUI extensions and other local applications remain outside the change.

The Colab source remains pinned at
[`9e5ff82cf604dd3b0377de81b89e1f4aaa422947`](https://github.com/moruku36/qwen-multimodal-colab/commit/9e5ff82cf604dd3b0377de81b89e1f4aaa422947),
already merged in [PR #35](https://github.com/moruku36/qwen-multimodal-colab/pull/35).
This RunPod work changes no Colab source; no duplicate Colab PR is needed.
