> Historical development contract/proposal. For accepted C1, disabled C2 v5
> and the unexecuted C3 boundary, see the
> [October 6 record](development-status-2026-10-06.en.md).

# Minimal single-user completion candidate

This document fixes the remaining scope after the parent's independent review.
It supersedes older handoff descriptions of missing assembly, without changing
those historical artifacts. Code is a C1 review candidate; independent acceptance,
C2 actual installation/settings and C3 paid proof remain separate gates.

## One supported path

Verified manual Open WebUI 0.11.4 send for one configured user -> backend
`build_remote_dispatcher` -> authenticated localhost gateway -> already approved
new-session grant -> create once -> explicit new-Pod SSH fingerprint enrollment ->
pinned bootstrap/load -> authenticated READY -> bound JSON/SSE -> idle/absolute
budget drain -> local export and SHA readback -> individual exact-Pod deletion
approval -> DELETE once -> GET absence -> next separately approved session.

The final assembly is `single_user_host.py`, `production_gateway.py` and
`single_user_owui.py`, using AuthorityJournal and the guarded REST/SSH drivers.
The earlier EffectBoundary/ProviderExecution/BootstrapExecution callback drafts
remain review history and regression fixtures; they are not additional runtime
controllers. The real assembly does not substitute real ports into MockProvider.
Imports, default constructors and scripts do not launch this service.

## Fixed work mapping

| Work | Minimum implemented connection | CPU evidence | Remaining gate |
| --- | --- | --- | --- |
| W0 | Fixed qwen-27b text profile, context8192; explicit user, pins, rate, protected journal/export and loopback ports | Configuration mismatch/disabled construction tests; public 0.11.4 installer source proof | C2 verify actual user, source SHA, current flags, account/image/rate and named service identity |
| W1 | Separate inference/control auth, bounded private HTTP JSON/SSE, status/catalog without effects | Private ingress regressions plus single-user HTTP tests | C2 approved 127.0.0.1:19180 listen |
| W2 | Verified manual route context; native background/direct requests denied; backend-only remote dispatcher | Installed route tests, public installer proof and separate-process-shaped HTTP fixture | C2 stage/apply against exact installed source and connect dispatcher; approved restart |
| W3 | One worker/start lock, bounded cold queue, original request deadline, drop/abort, no blind create retry | Concurrent create, unknown outcomes, stop/close races and recreation smoke | C1 independent review; C3 real cold proof |
| W4 | One journal for grant, intent, owner, READY/idle, export and consumed deletion approval; signed protected head and owner lock | Crash-at-create/READY seals, rollback, restart and approval-consumption tests | C2 protected trust root/key/head/owner; same-owner/key-holder compromise is outside guarantee |
| W5 | Default-denied REST, exact account/session/op/spec owner proof, retained Pod exclusion, DELETE once and read-only reconciliation | REST framing/deadline/strict type tests, unknown-create/delete regressions | C2 approved credential facility/account attestation and actual schema/rate; C3 effects |
| W6 | Literal private tunnel, strict fingerprint, fixed source/model/binary hashes, API key through SSH stdin; authenticated positive and unauthenticated negative READY checks | SSH post-lookup/post-spawn cancellation tests; generated readiness Python AST; fake bootstrap/load proof | C2 exact SSH executable/key/public known_hosts/fingerprint and remote auth setup; C3 build/load |
| W7 | Elapsed GPU/storage/overhead budget from provisioning, immutable work/cleanup expiry, initial READY idle and owned watchdog | Fake clock/cost/idle/absolute expiry; no status/chat/stream lease extension | C2 numerical USD/time/rate grant; C3 observed billing and cleanup |
| W8 | Outside-Pod immutable report, readback SHA, exact SID/Pod/action/expiry approval consumed before DELETE, GET absence | Export tamper, expired cap, unknown delete/restart, full recreation smoke | C2 protected export path and operator approval entry; C3 real absence/billing proof |
| W9 | Complete installed-shaped manual -> control HTTP -> queue -> READY -> inference HTTP JSON/SSE -> export -> approved delete -> new session | 250 allowlisted CPU tests and smoke11/6/8/10; source/patch hashes | Whole candidate independent review before calling C1 accepted |
| W10 | No live operation performed | Evidence explicitly distinguishes synthetic from real | C2 approved settings then C3 separately approved paid one-session trial |

Minimum scope includes individual approval after export. Automatic conditional
deletion policy, multiuser, multimodal/mmproj, tools/embedding, title/tag/background
generation, alternative models, cloud exports, warm reconnect and invoice
guarantees are deferred. These are not hidden prerequisites for this minimum.
Cold full build/download exceeding17.5min and cached load347.956s are separate
facts. Five-second dispatch permits are minted only after READY; they do not bound
cold startup. Every wait retains the original approved budget/deadline.

## Reviewable C2 proposal; nothing applied

Use the existing native Windows OWUI backend namespace. Proposal: a separate
named controller process, gateway127.0.0.1:19180 and SSH forward127.0.0.1:19181
to the new owned Pod's loopback inference endpoint. Existing unrelated services
must remain outside this installation scope.

The protected state/authority journal, trust head and exports directory need an
explicitly reviewed local root and named service identity. The application user
identity is separate from the OS account. C2 must verify the installed source,
connection flags and hooks before a hash-bound backup/patch and approved restart.
No private DB, environment or startup-script read is authorized by this document.

Actual provider account, immutable image, GPU identifier, CPU/RAM, current GPU
and storage rates, total USD cap, work/idle/UTC expiry and cleanup reserve must be
approved numerical inputs. Public fixture identities/prices are not production
values. The approved credential facility must attest the account before any POST;
a post-create consumerUserId check alone cannot prevent charging a wrong account.
Inject separate provider, inference, control and model bearer values in memory;
never put actual secrets in chat, repository, URLs, logs or persisted report.

Approve the absolute SSH executable, private key reference and exact public
known_hosts line/fingerprint obtained out of band for each newly owned Pod.
No trust-on-first-use or old-Pod peer reuse. Under an approved bootstrap effect,
the injected model key is written through encrypted SSH stdin with mode600 under
mode700 /run/qmc. Readiness requires pinned process identity, authenticated model
and health responses and unauthenticated model-list refusal.

Operator grant, peer enrollment, export/readback, cleanup approval and terminate
remain local trusted host calls. No HTTP endpoint accepts a grant, peer or delete
approval. After export, an operator approves exact SID/Pod/report SHA/action/expiry.
The worker exports automatically but never deletes automatically. Approval wait,
unknown provider result or expired cleanup cap is visible and prevents successor
allocation; these states do not prove that provider billing stopped. Recovery
retains original ownership and consumed intent; it does not replay POST/DELETE.

## Today and next gate

Today: local implementation and public synthetic fixtures only. No real provider
API/SSH/model download/credentials/settings/ACL/service restart occurred. Stopped
Pod synthetic-held-pod remains outside every effect allowlist. No push/PR/merge.
The fixed core is parent-accepted. Adapter review fixes and this concrete assembly
need whole independent review. C2 factual inputs and access/settings approval,
then a bounded C3 paid trial, remain the only activation blockers. Model selector
and token quota are unconfirmed because no configured read-only meter is exposed.
