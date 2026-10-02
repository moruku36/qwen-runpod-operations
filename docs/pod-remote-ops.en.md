# Connection options for Claude to work with a Pod

English | [日本語](pod-remote-ops.ja.md)

Final status: [Trial report for 2026-10-02](trial-report-2026-10-02.en.md)

**Documentation-only publication:** `main` contains documentation, without executable scripts or the notebook. Implementation descriptions and commands below require a separate checkout of the [immutable implementation snapshot](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16), commit `69795d54490ffaeff62f18ca80f6181ea674bc16`. Publishing these documents does not authorize a new paid run, restart, Terminate, or deletion. A fresh plan and authorization are required before another run.

**Final status on 2026-10-02:** The paid trial used two Pods. Outbound connectivity failed on `US-MD-1`; `selftest`, `net`, `venv`, and retrieval of a small artifact through logs succeeded on `EUR-IS-1`. CUDA compilation, downloading and hashing the complete model files, model loading, and inference were not performed. The final execution report recorded both Pods as `EXITED`. Compute and Container storage were confirmed as `Not running` in the console at each Pod's stop; the final shared screen around 16:05 JST shows only the second Pod. Each retained 80 GB Volume costs approximately $0.022/hour, approximately $1.07/day for both. Cleanup is pending and deletion is not authorized.

Purpose: reduce the need for a person to relay commands and logs, and let Claude receive Pod progress, diagnostic results, and artifacts. **This documentation publication did not change authentication, permissions, or network settings.** Option A was validated during the authorized trial on 2026-10-02. Options B and C have not been configured; do not use new permissions or connection methods without the required authorization.

## Environment and findings during the trial

- Claude's available Pod interface was REST v2 at `api.runpod.io`. The environment supplied authentication without exposing the key to Claude. Pod creation, retrieval, stop, listing, and **log reading** succeeded with the trial's authentication
- Claude's environment used an outbound HTTPS proxy. Hosts outside the allowlist, such as `docs.runpod.io` and `huggingface.co`, were rejected. Direct TCP connections to a Pod, including SSH, could not be assumed to work
- `GET /v2/pods/{id}/logs` is a read-only SSE API with `source=container|system`. System creation and stop events remained available after a Pod stopped. Ordinary container-command output was absent in the first trial, but the later Option A test received lines written to PID 1's standard output

## Option A Recommended Log-based communication without new permissions

The Pod execution script, [`scripts/pod_run.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod_run.py), writes short status lines in the form `QMC|time|stage|content` and small artifacts as base64 chunks with SHA256 hashes to the Pod's standard output through PID 1. Claude receives these using **only REST v2 log reading**, with `scripts/read_pod_logs.py <pod_id>`.

- Supported: observing progress, failures, and connectivity-check results; retrieving reports as allowlisted `.tar.gz` bundles. Received artifacts are checked against SHA256 and an allowlist
- Not supported: Claude executing arbitrary commands or running arbitrary diagnostics. A person starts the process by pasting a line such as `python3 scripts/pod_run.py all --bg` into the Jupyter terminal
- Required permissions: the existing trial key's Pod-read permissions were sufficient. **No additional permissions or network settings were required for this route**
- **Validated on a real Pod on 2026-10-02 in EUR-IS-1:** `python3 scripts/pod_run.py selftest` sent `hello from the pod` and `selftest.txt` through PID 1's standard output to the logs API, and the reconstructed file's SHA256 matched. Progress from `net` and `venv` was also received. A production inference report has not been retrieved. In a future authorized run, repeat this self-test immediately after startup. If it is not visible, investigate; using Option B requires its separate authorization
- Development tests had already checked log encoding, reconstruction, tamper detection, and retrieval of a `trial --mock` artifact through this route

## Option B Execution inside the Pod through the Jupyter API and RunPod HTTPS proxy

Claude would use Jupyter REST/WebSocket APIs for terminals and kernels at `https://<pod_id>-8888.proxy.runpod.net`, allowing direct command execution, diagnosis, and file retrieval.

The user must decide the following; these settings were not changed:

1. **Network permission:** add `*.proxy.runpod.net`, or just the target Pod's hostname, to the Claude environment's Network access configuration. It was believed to be disallowed, but that was not verified
2. **Provide Jupyter authentication to the Claude environment:** each Pod receives an automatically generated password visible in the console. Use an environment secret rather than chat, updating it whenever a new Pod is created. A fixed value would require creating a RunPod account secret and referring to it in the creation request's `env`. That requires **Secrets write permission**; the current tool does not send `env`, so code changes and authorization would also be required
3. **WebSocket support:** passage of WebSocket traffic through the environment's proxy has not been verified

Risk: Jupyter shell access grants full authority inside the Pod. The official runpodctl README describes a Pod-scoped RunPod key available inside a Pod for `runpodctl`; do not assume its contents are safe to expose. Avoid commands that print `env` or similar credential-bearing output.

## Option C SSH Not recommended

This would require registering an SSH public key on the account, write permission for `/v2/account/ssh-keys`, `startSsh: true`, and an exposed `22/tcp` port with a public IP. Raw TCP from Claude's environment was expected to be unavailable because of the HTTPS-proxy restriction, so this option was excluded.

## Authentication and permission comparison No changes made

| Item | Option A | Option B | Option C |
| --- | --- | --- | --- |
| RunPod key permissions | Existing trial scope: Pod read/write and log read | Additional Secrets write permission if using a fixed password | SSH-key write permission |
| Claude environment network permission | No addition; `api.runpod.io` was allowed | `*.proxy.runpod.net` required | Raw TCP required; impractical in this environment |
| Environment secrets | None additional | Jupyter password | Private key |
| Human action | Paste one line in Jupyter | Configure connection information | Several steps |
| Status | `selftest`, logs, and small-artifact retrieval validated on a real Pod | Unverified and not configured | Not recommended and not configured |

## Recommended procedure for the next authorized trial

1. Decide how to handle retained Volumes, then define the new trial's costs, stop target, and artifact destination. Resuming an existing stopped Pod also requires a fresh plan and the required authorization
2. Repeat Option A's `selftest`, aiming to check the communication route in the first minute. If it passes, run `net`. The later `all` → `trial` sequence includes unverified CUDA compilation, full model downloads, and inference; proceed only after that scope and its costs have been authorized. Claude receives progress and artifacts through `read_pod_logs.py`
3. Only if Option A fails or arbitrary-command diagnostics are necessary, assess Option B's network, authentication, and WebSocket requirements and obtain the approvals required for settings changes and credential transfer

Option A is a one-way log and artifact-retrieval route, not a connection for remotely executing arbitrary commands. Log throttling may omit intermediate progress lines during a long installation.

## Immutable implementation links

These files are not present on documentation-only `main`. Check out the pinned commit above before using the commands.

- [`scripts/pod_run.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod_run.py)
- [`scripts/read_pod_logs.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/read_pod_logs.py)
