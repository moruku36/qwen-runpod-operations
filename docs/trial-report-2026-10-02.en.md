# First RunPod experiment report for October 2 2026

English | [日本語](trial-report-2026-10-02.ja.md) | [README](../README.md)

Repository: `moruku36/qwen-runpod-operations`  
Reporting window: October 2, 2026, 14:28 to approximately 16:05 JST (UTC+9)  
Conclusion: **Pod startup and shutdown, connectivity checks, an isolated Python environment, and retrieval of logs and a small artifact were demonstrated. Qwen GPU inference was not reached.**

## 1 What was completed

The final limited run completed `selftest → net → venv`. It reached GitHub, PyPI, and the pinned model delivery endpoints. Hash-locked dependency installation and `pip check` passed in an environment isolated from the operating system's Python packages. Reading logs from outside the Pod and retrieving a test file with a matching SHA256 were also confirmed. [Trial log](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/trial-log-2026-10-02.ja.md)

GPU compute was stopped at approximately 16:05. Both Pods were retained to preserve intermediate work. **Two 80GB Volumes remain, costing approximately $0.0444/hour or $1.07/day combined.** The original plan's step of deleting experiment resources after verified export is incomplete.

| Check | Result |
| --- | --- |
| A100 SXM 80GB startup and shutdown | Performed; console displays were compared with API reports |
| Connectivity | The first Pod failed to connect to GitHub; the second reached the required delivery endpoints |
| Python dependencies | Isolated venv, hash-locked installation, and `pip check` inside the venv passed |
| Jupyter kernel | A separate inspection found `qwen-venv` registered; the UI kernel list and selection were not checked |
| External log and artifact retrieval | Status lines and a small `selftest.txt` were retrieved with matching SHA256 |
| CUDA build and model execution | Not performed |
| Performance and feature evaluation | Not performed; speed, VRAM, 32k context, image understanding, web search, and ASR cannot be evaluated |

### Evidence boundaries

This report reconciles the operator's terminal output and console screenshots, execution reports and the GitHub trial log, and read-only inspection of pinned commits. Pod operations and the reported 75 tests were not rerun while writing this report. Final shutdown is an addition based on conversation reports and screen verification; it is not in the referenced trial log's 16:03 snapshot. Times without second-level evidence are approximate.

## 2 Experiment timeline

Each Pod was created in Secure Cloud with one A100 SXM 80GB, a GPU rate of $1.59/hour, a 20GB Container Disk, and an 80GB Pod Volume. The third run resumed the second Pod; no third Pod was created.

| Time JST | Event and result |
| --- | --- |
| 14:28:02 | First Pod created in US-MD-1 |
| By approximately 14:35 | Both git and curl failed to reach GitHub; IPv4 also returned `No route to host`. A HEAD request to the Hugging Face homepage returned 200 |
| 14:35:38–40 | Stop request returned HTTP 200. The execution report identified a system shutdown line at 14:35:40. The operator's console also showed Compute and Container storage as `Not running` |
| 14:50:42 | Second Pod created in EUR-IS-1 |
| By approximately 15:19 | GitHub, PyPI, and model endpoint checks succeeded. The operator verified a clean checkout of `213ad9ed…`. Following Notebook Cell 1 dependency installation, `pip check` failed because of an OS-side dependency. Manual venv preparation also timed out after 240 seconds |
| 15:19:48 | Second Pod stopped; HTTP 200 and a read-back state of `EXITED`. The operator confirmed shutdown in the console |
| 15:37:04–05 | Second Pod resumed; Start returned HTTP 200 and the execution report confirmed `RUNNING` |
| 15:57:02 | A limited run began at pinned commit `30d6c5d8…` from one block the operator pasted into Jupyter. The run ID is withheld in this public edition |
| 15:57:02 | `hello from the pod` and `selftest.txt` arrived through the logs API; the execution report confirmed a matching file SHA256 |
| 15:57:06 | `net` passed: three GitHub repositories, PyPI, pythonhosted, and the pinned GGUF/mmproj delivery endpoints were checked |
| 15:57:06–16:02:28 | Venv creation took approximately 24 seconds; hash-locked pip installation approximately 297 seconds. `pip check` passed inside the venv |
| 16:02:33–35 | After kernel registration, a separate check found `qwen-venv` present. The limited chain completed |
| Approximately 16:05 | The final execution report recorded Stop HTTP 200 and both Pods `EXITED`. The final shared screenshot showed the second Pod's Compute and Container storage as `Not running`, with its 80GB Volume retained |

Approximately 20 minutes between the 15:37 restart and the 15:57 work start also count as GPU allocation time. The first Pod is absent from the final screenshot, but shutdown had been verified earlier and the final API report still recorded `EXITED`. [Primary timing record](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/trial-log-2026-10-02.ja.md)

## 3 Problems and findings

### GitHub connectivity failure

On the first Pod, ordinary and IPv4 connections both returned `No route to host` within milliseconds. The Hugging Face homepage was reachable. The same GitHub failure did not recur on the second Pod.

**The root cause is unresolved.** Host-specific routing and transient failures were not isolated. The evidence does not establish a US-MD-1-wide outage, the `globalNetwork` setting, or the presence or absence of a compliance label as the cause. The first Pod's network command output was not retained by the logs API at that time; the operator's terminal output is the evidence. The next run should begin with connectivity checks and saved output.

### Mixing system Python and application dependencies

The original Cell 1 used `/usr/bin/python`. PyGObject 3.42.1 in `/usr/lib/python3/dist-packages` required pycairo, causing `pip check` to fail. The project lock contains neither PyGObject nor pycairo. The observed problem was using an environment containing OS-side packages for the application's dependency check.

The revision installs the hash-locked CPython 3.11 dependencies into an isolated venv without system site-packages. In the final run, ensurepip was available and `pip check` passed inside the venv. Separating the environment being validated worked better than adding missing OS-side packages ad hoc. [Environment isolation implementation](https://github.com/moruku36/qwen-runpod-operations/blob/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4/qmc_runpod/envsetup.py)

### The 240-second timeout

Manual isolated-environment preparation exited with code 124 after 240 seconds. No OPEN or KERNEL completion markers appeared. This output alone does not prove dependency-resolution failure or a broken pip installation.

A later run succeeded after approximately 297 seconds for pip installation alone. **The 240-second limit was shorter than the duration actually observed in that successful run.** Because these were separate runs, this does not prove the earlier attempt would have succeeded if allowed to continue. Future stage timeouts, overall spending/time limits, and progress checks should be handled separately. This run's approximately five-minute duration is not a completion-time guarantee for the next one.

### Log transport and success criteria

Short status lines and hashed data written to PID 1's stdout reached the real Pod logs API. The expected SHA256 of the retrieved `selftest.txt` was:

`467e5a2b0ffc26ed3a065a9b8dbfbff233d6c978814172af05e215a2df910786`

This demonstrates log reception and reconstruction of a small test file. It does not demonstrate arbitrary external command execution or complete retrieval of actual inference results or a large bundle. The operator entered the initial command, and execution decisions still required human mediation.

After shutdown, a CLI submitted a read-only report-check request to an existing execution session and queue acceptance was confirmed. Automatic receipt of the reply was not verified. This does not demonstrate direct Pod control or automatic repair.

Read-only inspection also identified these improvements:

- `read_logs` can suppress non-HTTP exceptions and return empty or partial logs
- The receiver can exit with code 0 even if it receives nothing. A fresh run ID, expected events, expected files, and hash agreement need independent checks
- Kernel registration uses `check=False`, and its exit code is not reflected in the success state. This run relied on a separate KernelSpec inspection; a `state=ok` line alone is insufficient evidence
- Do not decide failure from stderr output or interrupted logs alone. Combine exit codes, exceptions, artifacts, and stage status

[Log API code](https://github.com/moruku36/qwen-runpod-operations/blob/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4/qmc_runpod/podapi.py) / [Receiver](https://github.com/moruku36/qwen-runpod-operations/blob/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4/scripts/read_pod_logs.py) / [Stage implementation](https://github.com/moruku36/qwen-runpod-operations/blob/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4/qmc_runpod/stages.py)

### Shutdown display and API discrepancy

After Stop, the API status became `EXITED` and the console showed Compute and Container storage stopped, but the API `cost` remained 1.59. The field's meaning, update timing, and discrepancy with the documented expectation remain unresolved. It is evidence neither of continuing GPU charges nor of a zero bill.

The current code's condition of `EXITED` and `cost == 0` alone could not confirm this shutdown. Before the next run, review shutdown verification to distinguish state, system shutdown logs, and console displays; reconcile actual charges in Billing. [Current implementation runbook](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/runpod-runbook.ja.md)

## 4 Costs

The agreed experiment cap was **$10 total, including storage, taxes, and fees**. This authorization covered that day's limited trial; it does not automatically authorize another trial.

| Allocation interval | Approximate duration | Estimated GPU cost |
| --- | --- | ---: |
| First Pod, 14:28:02–14:35:40 | 7 minutes 38 seconds | $0.20 |
| Second Pod, 14:50:42–15:19:48 | 29 minutes 6 seconds | $0.77 |
| Second Pod resumed, 15:37:05–approximately 16:05 | 28 minutes | $0.74 |
| Total | 65 minutes | **Approximately $1.71** |

These are **GPU-only estimates** obtained by multiplying allocation time by $1.59/hour, not settled charges. They exclude disks during runtime, storage after shutdown, taxes, and fees. An intermediate account screen cannot establish the final total including later runs. The approximately $1.20 cumulative estimate reported mid-run is not used as the final cumulative figure. **Settled spending and the exact remainder of the $10 budget are unknown because Billing has not been reconciled.**

The published stopped-Volume rate is $0.20/GB/month. Using a 720-hour comparison month, 80GB × 2 costs $0.04444/hour, or approximately $1.0667 per 24 hours. This is consistent with each console showing approximately $0.022/hour. Costs continue while the Volumes are retained; keeping them until tomorrow is not free. [RunPod pricing](https://docs.runpod.io/pods/pricing)

## 5 Retained resources and preservation limits

| Item | Final treatment |
| --- | --- |
| First Pod (US-MD-1) | Stopped, 80GB Volume retained, not terminated |
| Second Pod (EUR-IS-1) | Stopped, 80GB Volume retained, not terminated |
| Second Pod `/workspace/qwen` | Stopped with the intent of retaining the earlier checkout and `isolated-*` intermediate work |
| Second Pod `/workspace/qwen-r3` | Stopped with the intent of retaining final code and the venv, logs, and state beneath `ws` |
| Outside the Pods | GitHub code and trial records, the small selftest artifact reported as retrieved and verified, and this report |

No post-shutdown file inventory was read back, so completeness and reusability of retained data are unverified. It is too early to claim completed model downloads or preservation of an inference-result bundle.

Under the official storage model, an ordinary Stop clears the Container Disk while preserving `/workspace` on the Volume Disk. Kernel registration with `--user` is in the user's home directory and may not survive a restart. The venv's referenced Python and compatibility with the image must also be rechecked. A host-bound Volume is not a substitute for an external backup. [Storage types](https://docs.runpod.io/pods/storage/types) / [Stop and Terminate](https://docs.runpod.io/pods/manage-pods)

The first Pod's system log included the words “network volume.” Based on the creation configuration and console rate, this report treats its storage as a Pod Volume; that wording is not evidence that an independent Network Volume was created.

The latest decision was to retain intermediate work. This report authorizes no new resources, restart, or Terminate action. Before deletion, read back required results outside the Pod and obtain action-time approval identifying the Pod ID, data that will disappear, and verified destination.

## 6 Next-run procedure

**Write a new experiment plan and complete preparation in a free or existing CPU environment before resuming GPU compute.**

1. **Settle costs and preservation first.** Check actual charges and remaining budget in Billing. Decide which Volumes to retain, for how long, what to export, and what to delete. Obtain separate, target-specific deletion approval to avoid indefinite unnecessary storage
2. **Prepare reproducibility without GPU allocation.** Fix and test kernel-registration exit handling, empty/partial log reception, and shutdown verification. Pin the dependency lock, Python, and image; consider reusable environments or prebuilt images. Distinguish current README/runbook status from the historical baseline snapshot. This documentation update makes that distinction without changing baseline.json
3. **Prepare execution and export paths before startup.** Record the execution commit, stages, run ID, start mechanism, export destination, maximum cost, and shutdown conditions. Direct Jupyter automation and WebSocket connections remain unverified; authentication, permissions, and networking changes require separate consideration. SSH was not configured in this trial
4. **Proceed stage by stage after approval.** Start with connectivity and log selftests; stop on failure. Verify the isolated venv and kernel before the pinned upstream `source` stage, CUDA `build`, and complete pinned-model download with SHA256 verification. The final limited run did not execute `source`, `build`, `trial`, or `all`
5. **Keep the first inference small.** Separate model load from the cold request and begin with one request capped at 64 tokens. Ten warm requests capped at 256 tokens, 32k context, images, search, and ASR may run only if included in the next approved scope. CPU mock success must not be relabeled real GPU success
6. **Include export and shutdown in the experiment.** Export allowlisted reports and bundles, then read back the complete file list, hashes, and checks outside the Pod. Verify the actual Stop state and reconcile Billing. Delete only approved targets after export is verified

The useful result is a concrete list of preparation steps to complete before spending GPU time and the evidence needed to establish success. RunPod suitability for Qwen, and cost/speed comparisons with Colab, remain undecided until a CUDA build and real inference are completed.

## 7 Reproduction metadata and references

- Operations repository: [moruku36/qwen-runpod-operations](https://github.com/moruku36/qwen-runpod-operations)
- Investigated branch: `claude/modest-knuth-8z1698`. This publication changes documentation only; it does not merge that implementation into main
- Trial-log snapshot: `69795d54490ffaeff62f18ca80f6181ea674bc16`. Its latest change is documentation-only and is distinct from the code executed on the Pod
- Final Pod code: [`30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4`](https://github.com/moruku36/qwen-runpod-operations/commit/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4)
- Code whose clean checkout the operator verified in the second run: `213ad9ede6fb44becd5e642813093b4102f10edc`
- Final code dependency-lock SHA256: `d9fb03b78fed58478ebe7e67023d93d331e5b9b8ffcba613f267d4599efdc8d0`. Calculated from the file at the pinned GitHub commit when preparing this report, not by retrieving the Pod's copy again
- Pinned upstream Notebook commit: `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`; Notebook blob: `5e40a89f0253356c9c8d72698a93508f4d30d8aa`
- Pinned llama.cpp commit: `4da6337767f973e2b4d0797e5b323d77d8565e4a`
- Planned models: Huihui Qwen3.8 27B Q8_K_L and the official Qwen3.8 27B mmproj. Pinned revisions and expected SHA256 values are in [`pins.py`](https://github.com/moruku36/qwen-runpod-operations/blob/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4/qmc_runpod/pins.py). HEAD 200 proves endpoint reachability, not full download or hash verification
- pythonhosted HEAD 404 confirms an HTTP response from the requested root URL; it does not demonstrate package download or existence of a particular file
- Image recorded in the first Pod's system log: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`; digest: `sha256:61a4aafb0094cd773f11eefa378929d5a687bd775febeb78eac62fc824141fb5`. This is not an independent measurement of the second Pod's or resumed session's actual digest
- [Trial log](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/trial-log-2026-10-02.ja.md), [Runbook](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/runpod-runbook.ja.md), [Remote-operation design](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/pod-remote-ops.ja.md)

This public edition omits Pod identifiers, account balances, payment settings, secrets, authentication values, conversation history, and model weights. It records the first experiment and provides no approval for additional spending, connection-permission changes, restart, or deletion.
