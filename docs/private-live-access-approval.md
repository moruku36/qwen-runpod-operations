> Historical development contract/proposal. For accepted C1, disabled C2 v5
> and the unexecuted C3 boundary, see the
> [October 6 record](development-status-2026-10-06.en.md).

# Concrete access/approval packet for the real OWUI -> RunPod connection

Prepared proposal only. All live access and mutation remain disabled. Existing
stopped Pod `synthetic-held-pod`, existing OWUI extensions, unrelated local services and measurement
jobs remain untouched. No real key, password, config, tunnel or API was read or
used. Do not approve an unspecified "enable everything" operation.

## One reviewed connection plan

Proposed route: authenticated OWUI backend -> gateway on
`http://127.0.0.1:19180/v1` -> SSH local forwarding at
`http://127.0.0.1:19181/v1` -> exact owned Pod's `127.0.0.1:8080/v1` llama-server.
These are candidate ports, not verified free ports or currently applied values.
OpenAI-compatible is the protocol; no OpenAI-service key, endpoint, fallback,
quota or billing permission is needed. Keep OWUI login/authentication enabled.

The actual OWUI backend's OS/container/WSL namespace, version, service identity,
current connection record and exposed authenticated user/task routes are unknown
in this selected checkout. First obtain those *non-secret* facts and port occupancy
read-only, without changing unrelated local processes. If the backend is
in another namespace, its localhost is not this Windows host. Prepare a specific
private reachability rule for that case; do not bind 0.0.0.0 or create a public
RunPod proxy as an automatic workaround. Before approval, name the final service,
exact connection record, local ports, Pod/session and rollback backup.

| Target | Access/configuration to approve | Narrow scope and verification |
| --- | --- | --- |
| Existing OWUI provider connection | Admin edit of one reviewed connection record: base URL to private gateway, fixed model qwen-27b, one gateway auth reference. Backup exact previous values first; approve any required service restart separately. | Retain current users/chats/auth. No extra cloud provider/fallback. Confirm models/status do not allocate or renew a lease. Do not change existing memory or Voice integration. |
| OWUI trusted user/task hook | Reviewed backend code/plugin access to authenticated user identity, explicit manual send event and server-generated request ID; internal IPC/API to mint one-use permits for that exact session + payload. | Title/tag/follow-up/other background dispatch cannot mint permits. Client-provided metadata/headers are not proof of a manual send. Select the exact authorized user and session owner before deployment. |
| Local gateway identity/auth | Run gateway under a named existing or separately approved service identity. Inject a gateway-only bearer via an approved secret facility; authorize the OWUI backend to present it to gateway and only the trusted control hook to issue permits. | No real key appears in repo/docs/logs/URL or in a broadly exposed config. Backend inference key alone is not authority to provision/delete. Grant is session/action/time/USD scoped; local credential storage method and any persistent rights must be reviewed. |
| Private SSH tunnel | Windows gateway/tunnel process uses a dedicated approved SSH key to one exact owned Pod endpoint/user. Verify host fingerprint before connecting; approve exact key provisioning/known_hosts persistence and session-limited forwarding. | Bind only local 127.0.0.1:19181 -> that Pod's 127.0.0.1:8080. No public HTTP expose, wildcard bind, reverse forwarding or reuse of unverified pre-existing access. Actual RunPod SSH mode must support the approved forwarding mode; public docs do not establish that for this Pod. |
| Pod model server | Approved bootstrap/launch on the exact session Pod: model/server pins, alias qwen-27b, Pod-loopback port8080, approved optional model-server bearer if required. Gateway may call only readiness and admitted chat through the verified tunnel. | Readiness must prove the expected loaded model/endpoint, not catalog presence. No general remote command execution from arbitrary chat input. No settings applied during this free phase. |
| RunPod management controller | A separately approved account credential injected only into the controller's default-deny provider port. Controller may read/reconcile and create/start only resources in the new session grant; exact ownership IDs are checked locally. | Confirm the provider's available key scopes rather than assuming per-Pod key isolation exists. No account-wide list result or historical budget is ownership/authorization. Existing stopped Pod is excluded unless a future instruction names it explicitly. No AutoPay/payment change. |
| Session billing/runtime limits | Exact GPU/image/datacenter/resource target, current accepted rate, max total USD including storage/fees, work/idle/absolute UTC expiry, and export/cleanup reserve. Approve first legitimate manual-chat create/start plus bounded queue/loading behavior. | Prior cached A100/27B load347.956s and cold build/download >17.5min are different cost scenarios. Values must be filled before real activation, not inferred from mock .01 costs or old trial caps. No retry renews a deadline. |
| Export and destructive cleanup | Approved external artifact destination and readback access, then separate exact resource/action approval for Terminate/delete after verified export. | Pod ID + immutable session ownership + export SHA must match the allowlist. No account/shared/pre-existing resources. Current implementation sends no destructive real request. |
| Durable state/restart | Approved local durable state location and trusted authority recovery method, with access restricted to the named controller identity. | Restart cannot recreate grants/budget or accept a copied JSON checkpoint as authority. Authentication/ACL changes are not implied by a writable workspace. |

## Completion order

1. Collect the non-secret runtime/namespace/route facts and finalize the exact plan.
2. Finish/review the HTTP ingress + authenticated manual-task hook and durable
   authority/provider/tunnel contracts using mocks. Preserve default-deny startup.
3. Obtain approval for the specific settings, secret injection/ACL/key installation,
   private tunnel and scoped GPU session as one concrete bounded activation plan.
   Keep Terminate/delete as their own verified-export/action approval.
4. Apply only that approved plan; verify catalog/background denial before any
   legitimate inference, exact model readiness, JSON/SSE/cancel, unchanged deadlines,
   idle transition and export/readback. Record actual billing and cleanup outcome.
5. Review operational proof before ongoing on-demand use or automatic cleanup.

This packet is not a permission request to the user yet: unknown runtime/identity/
target/budget fields must be resolved by the parent into a concrete reviewable plan.
Local implementation and synthetic tests do not configure any of the access above.
