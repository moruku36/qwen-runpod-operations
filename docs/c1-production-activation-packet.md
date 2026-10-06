> Historical development contract/proposal. For accepted C1, disabled C2 v5
> and the unexecuted C3 boundary, see the
> [October 6 record](development-status-2026-10-06.en.md).

# Disabled execution code and the concrete C2/C3 boundary

This is an offline review draft, not full C1 acceptance or deployment approval.
All constructors deny execution by default. No real provider, SSH or installer
operation has been run. The 196-test repaired core and accepted26 are fixed inputs.

## Free code supplied

`execution_adapters.py`: typed exact effect gates; explicit injected bearer and
public literal REST IP with TLS verification/SNI for rest.runpod.io; no proxy,
redirect, retry or credential discovery; bounded responses/cancellation/normalized
errors. Exact scoped create/read/delete only. Raw provider env is stripped before
control commit. SSH argv uses strict host checking, explicit identity/known_hosts,
loopback tunnel, hidden owned subprocesses and bounded output/deadlines. Returned
cold scripts pin model/source/hash and never run at import or construction.

`production_boundary.py`: separate signed one-attempt journal, persisted unknown
intent before I/O, I/O outside control mutex, stop/close cancellation, exact original
operation reconciliation with immutable deadline. Default deny is restored after
restart. Service check/commit callbacks are trust roots; they cannot come from an
HTTP body, config boolean or checkpoint. No reviewed mock type gate is changed.

`provider_execution.py` connects request planning, durable boundary, injected REST
and stripped account/Pod/spec/price proof. Delete requires exact ownership,
allowlist, typed individual approval and external readback. The lifecycle host must
also atomically validate/consume approval and commit durable ownership/results.

`bootstrap_execution.py` connects the boundary to SSH scripts and strict returned
identity/model/source/binary/process proofs. Load checks authenticated model list
plus health and process arguments. It receives the original granted startup
deadline, distinct from the bounded READY inference deadline. Remote model key is
read in future remote Python memory, never interpolated into command arguments.
Tunnel establishment and live end-to-end proof remain gated C2/C3 operations.

`owui_installer.py` accepts supplied source bytes and an expected SHA. It verifies
the OWUI0.11.4 verified-user entry and final dispatch AST, separates the manual route
wrapper from internal app.state dispatch, and returns a disabled patch plus exact
original/rollback hashes. stage writes only a new explicitly selected review
directory. It cannot apply patches, install modules, change connections/auth/DB,
read installed settings, restart services or create Pods. Tested against the public
official release main.py SHA
7c824a89040aa7472d499584d19e56e60baa4890760414374e9e49ebf7dd0d3b.

`owui_installed_dispatcher.py` supplies the server callable projection: verified
user -> one-use identity context -> final text-only payload -> trusted queue ->
original-deadline READY wait -> private localhost forward. Client bookkeeping is
removed and never grants permission. Default port injection/subject allowlist is
empty. Close cancels active wait/forward and no background call can obtain an entry
context merely by reusing the route/request. Offline integration uses only the mock
core and ephemeral gateway; it is not evidence of deployed production binding.

## Remaining free-code and review blockers

Full C1 is still incomplete. Production lifecycle-host callbacks must be concretely
implemented/reviewed: atomically persist actual provider ownership and normalized
results; reconcile a late/unknown create using original-operation evidence; connect
actual bootstrap/load/tunnel readiness to the lifecycle; route inference through a
separate reviewed production ingress; preserve export/readback and consumed deletion
approval across crash. The repaired core remains mock-only and its exact HTTP gates
must not be bypassed by swapping live ports after construction. These host gaps are
free code work, not falsely classified as C2 configuration-only blockers.

Independent review of the core P1/P2 repair is pending. New adapter/boundary/installer
drafts also require independent review before integration or activation. Tests use
synthetic normalized responses and injected callbacks, not a real provider billing
or SSH/host-key/network proof. A REST failure yields unknown and never auto-retries.
Late results require explicit original-operation reconciliation; no name-only Pod
discovery or deletion is implemented. No automatic conditional deletion policy has
been authorized, so the design retains explicit per-session deletion approval.

## Concrete activation proposal to review later

The local disabled C2 v5 installation has been accepted and checked separately.
The current status and unexecuted C3 boundary are in
[the October 6 record](development-status-2026-10-06.en.md). OS and authenticated
application identities remain separate trust inputs; unrelated services are excluded.

Proposed separate service ports: gateway127.0.0.1:19180, owned SSH forward
127.0.0.1:19181 -> Pod127.0.0.1:8080; inference/control credentials distinct. These
ports are proposed only and have not been bound. Existing OWUI/Voice ports stay
outside the installation plan. No public inference endpoint is planned.

Pinned text profile: qwen-27b alias, context8192; llama commit
4da6337767f973e2b4d0797e5b323d77d8565e4a; model revision
ff733b88376282017b7f4675d6d96536c6ffa712; GGUF SHA
fb8413d0b5cec5ad7055e49630a986518ba90fdacc95a337aa535e8ff5bf0d16.
This is the historic fixed profile, not a Strata/Flash-Next substitution or proof
that today's cached diagnostic used these exact artifacts. Cold full build/download
planning is >=17.5min; the observed cached model load was347.956s, context8192,
one successful two-character answer, cleanup/result collection PASS. These timings
must remain distinct when selecting an absolute startup/work grant.

Operator decisions still needed, without sending secret values into chat:

- Named service identity and approved protected journal/head/signing-key storage;
  OS ownership/ACL/anti-rollback trust assumptions and fail-closed recovery policy.
- Exact authenticated app user allowlist and approved server verifier; read-only
  source SHA/backup/rollback target, manual/internal call-site review and later
  explicitly approved installation/service restart. Initial patch remains disabled.
- Exact provider account/image digest/A10080GB GPU identifier, volume/container
  sizes, vCPU/RAM, verified current GPU/storage rates and USD/runtime/UTC reserves.
  Current source uses public synthetic account/image/rates, not approved live inputs.
- Dedicated provider token facility and explicit pinned REST destination IP(s);
  dedicated SSH private key and independently verified host fingerprint/known_hosts
  enrollment for each newly created Pod. No TOFU or implicit existing-agent trust.
- Remote authenticated key-file provisioning, trusted model/binary/readiness proof,
  private tunnel ownership and bounded cancellation/cleanup of the owned processes.
- External export destination/readback SHA and exact individual deletion approval;
  separate authorization if a conditional idle-deletion policy is later wanted.
- Bounded C3 one-session paid proof with unique SID, all numeric caps and expiry,
  exact new-Pod ownership/terminate allowlist. Retained synthetic-held-pod is always
  excluded. Unknown create/delete must reconcile; never replay or infer absence.

No C2 setting action, credential read, Pod operation, AutoPay, fee, push, PR or merge
is authorized by this document. Model/runtime quota and CodexBar token remaining
values are unavailable; this draft does not claim Luna-medium selection or a quota.

Public primary source checks:
- https://docs.runpod.io/api-reference/pods/GET/pods/podId
- https://docs.runpod.io/api-reference/pods/POST/pods
- https://raw.githubusercontent.com/ggml-org/llama.cpp/4da6337767f973e2b4d0797e5b323d77d8565e4a/common/arg.cpp
- https://raw.githubusercontent.com/open-webui/open-webui/v0.11.4/backend/open_webui/main.py
