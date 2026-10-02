# RunPod implementation runbook

English | [日本語](runpod-runbook.ja.md)

Final status: [Trial report for 2026-10-02](trial-report-2026-10-02.en.md)

**Implementation now on `main`:** the implementation (scripts, notebook, dependency lock, tests) was merged into `main` after the trial; see the [README](../README.md) and the [implementation guide](implementation-guide.en.md). The commands below were executed from the [immutable implementation snapshot](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16), commit `69795d54490ffaeff62f18ca80f6181ea674bc16`; to reproduce the Pod run, check out that commit rather than the branch tip. Publishing these documents does not authorize a new paid run, restart, Terminate, or deletion. A fresh plan and authorization are required before another run.

**Final status on 2026-10-02:** The paid trial used two Pods. Outbound connectivity failed on `US-MD-1`; `selftest`, `net`, `venv`, and retrieval of a small artifact through logs succeeded on `EUR-IS-1`. CUDA compilation, downloading and hashing the complete model files, model loading, and inference were not performed. The final execution report recorded both Pods as `EXITED`. Compute and Container storage were confirmed as `Not running` in the console at each Pod's stop; the final shared screen around 16:05 JST shows only the second Pod. Each retained 80 GB Volume costs approximately $0.022/hour, approximately $1.07/day for both. Cleanup is pending and deletion is not authorized.

Status: in addition to the pinned implementation and CPU checks, `selftest`, `net`, and `venv` were validated on a real Pod on 2026-10-02. The following describes the implementation's procedure; it does not establish success for the unperformed CUDA compilation, model downloads, or inference. The reproducible implementation snapshot is `69795d54490ffaeff62f18ca80f6181ea674bc16`. The implementation is also on `main` after the post-trial merge; to reproduce the Pod run exactly, use the snapshot above.

## Implemented components

| Component | Location | Description |
| --- | --- | --- |
| Pins | [`qmc_runpod/pins.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/pins.py) | Upstream `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`, notebook blob, llama.cpp `4da6337767f973e2b4d0797e5b323d77d8565e4a`, and revisions/SHA256 values for both model files. Tests check agreement with [`baseline.json`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/baseline.json) |
| Paths | [`layout.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/layout.py) | Everything under `/workspace/qwen`; no `/content`, Drive, or Colab Secrets |
| Gradio 6 | [`chat_ui.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/chat_ui.py) | Upstream `ui_chat` raises TypeError for `gr.Chatbot(type="messages")`. Remove `type` only during construction, leaving upstream unchanged |
| Launch safeguards | [`launch.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/launch.py) | Abort if both authentication values are not present, port 7860 is occupied, sharing is enabled, an external chat API is selected, or llama-server binds anywhere other than 127.0.0.1. Also verify that unauthenticated access is rejected after launch |
| Download and build | [`models.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/models.py), [`llama.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/llama.py) | Download only the two pinned-revision files and verify SHA256. Build the pinned commit. Abort if nvcc or the architecture cannot be detected |
| Measurement | [`smoke.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/smoke.py) | Separate load, the first response (cold), and warm responses. Always specify an output-token cap |
| Reporting | [`report.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/report.py), [`trial.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/trial.py), [`provenance.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/provenance.py) | Allowlist-based reporting of the execution commit, dependency-lock hash, image/CUDA/build information, and pass/fail/skipped status for every check |
| Pod operations | [`podapi.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/podapi.py), [`scripts/pod.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod.py) | REST v2; does not handle keys directly. No terminate/delete functions |
| Dependencies | [`requirements-runpod.lock.txt`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/requirements-runpod.lock.txt) | 85 hash-pinned packages, including gradio 6.29.0 |

## Deploying code to a Pod from the public repository

1. A person opens Jupyter through **Connect** in the RunPod console. Do not put passwords or other credentials in chat
2. In Jupyter's terminal, clone and select the **immutable implementation SHA above**, not a branch name. **Execute one line at a time and inspect each result before proceeding**, without joining them with `&&`
   ```
   git clone https://github.com/moruku36/qwen-runpod-operations /workspace/qwen/ops
   ```
   ```
   cd /workspace/qwen/ops
   ```
   ```
   git checkout --detach 69795d54490ffaeff62f18ca80f6181ea674bc16
   ```
   ```
   git rev-parse HEAD
   ```
   ```
   git status --porcelain
   ```
   If `git rev-parse HEAD` exactly matches the requested SHA and `git status --porcelain` prints nothing, the working tree matches the commit. After cloning, the same verification is available through `bash scripts/pod-bootstrap.sh <SHA>`
3. Open [`notebooks/Qwen-Q8-Chat-RunPod.ipynb`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/notebooks/Qwen-Q8-Chat-RunPod.ipynb). Cell 1 resolves the correct repository root whether opened from `notebooks/` or the repository root. It stops explicitly if it cannot find the root; `QMC_REPO_ROOT` can also specify it

## Pod procedure using an isolated Python environment

Do not install into system Python. `/usr/bin/python` includes OS packages, such as PyGObject requiring pycairo, that can cause `pip check` to fail for reasons unrelated to this project. Install dependencies into the independent venv at `/workspace/qwen/venv`. **Run one line at a time.**

```
python3 scripts/pod_run.py selftest
```
Check the log-based communication route described in [Pod connection options](pod-remote-ops.en.md).

```
python3 scripts/pod_run.py net
```
Check GitHub, PyPI, and the delivery endpoints for both pinned model files. **Stop immediately if this fails.**

```
python3 scripts/pod_run.py all --bg
```
Run `net` → `source` (fetch upstream at a pinned SHA) → `venv` (isolated venv, hash-pinned installation, `pip check` only inside the venv, and kernel registration) → `build` (CUDA build of llama-server, model downloads, and SHA256 verification). Stop at the first failure. The command returns immediately and the background procedure has no time limit.

```
python3 scripts/pod_run.py status
```
Show progress, checks, and the tail of the log.

```
python3 scripts/pod_run.py trial
```
Run only after `build` succeeds. Start the UI; login values are stored in `/workspace/qwen/secrets/gradio-login.txt` and must not be printed. Generate **one response capped at 64 tokens**, create a report and `.tar.gz`, and also send them through the log route. Optional `--warm` runs 10 warm responses; `--hold-min N` keeps the UI running for N minutes.

If using [`notebooks/Qwen-Q8-Chat-RunPod.ipynb`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/notebooks/Qwen-Q8-Chat-RunPod.ipynb), choose the **Python (qwen-venv)** kernel after `venv`. Cell 1 stops if another kernel is selected. Performance-criterion handling is unchanged: the thresholds are proposed criteria; `perf_criteria` remains `skipped` until 10 warm responses are available; the first response is excluded from the evaluation.

Pretrial CPU checks passed mocked `selftest`, `source`, `venv` with a real pip installation taking approximately 39 seconds, and `trial --mock`, covering UI, authentication, response, reporting, and log-based retrieval. **CUDA compilation, downloading and hashing complete model files, model loading, and inference on a Pod were not performed.** On the real Pod, hash-pinned installation in an isolated venv took approximately 297 seconds and `pip check` inside the venv passed. The earlier procedure with a 240-second limit timed out. A separate query confirmed the kernel's existence, but the implementation still reports success without checking the kernel-registration command's exit code. Its appearance in Jupyter's kernel list has not been checked.

## Creating a Pod Paid action requiring approval of a fresh plan

1. Run `python scripts/pod.py plan --launch-at HH:MM` to inspect the proposed creation request and estimate. This is read-only and produces a unique name in the form `qwen-trial-date-time-4digits`
2. After authorization, use the name from `plan` in `python scripts/pod.py create --approve LAUNCH-PAID-POD --name <name-from-plan> --launch-at HH:MM`
3. If the create API returns a **timeout, connection error, or 5xx**, a billable Pod may already have been created. The tool reports that the creation result is unknown and exits; **it does not retry automatically**. Check:
   - `python scripts/pod.py find <Pod-name>`, a read-only exact-name lookup
   - The same name in the [RunPod console](https://www.runpod.io/console/pods)
   - If it exists, stop it using the procedure below. Do not retry `create` until its absence has been confirmed
4. The implementation treats 4xx responses as definite failures in which a Pod was not created

## Stopping The original target was 2026-10-02 16:45 JST

`python scripts/pod.py stop <pod_id>`

- Send `{"action":"stop"}` to `POST /v2/pods/{id}/action`, then read back `GET /v2/pods/{id}`
- **The pinned implementation's success condition** is `status == EXITED` **and** a present, numeric `cost` of zero. Missing, null, string, and boolean values are not treated as zero
- **Known limitation:** during the 2026-10-02 trial, API `cost` stayed at 1.59 after `EXITED`. The console showed Compute and Container storage as `Not running`. This number alone does not show that the GPU remained running; the implementation's zero-cost check failed to confirm the stop. Read back operating state and retained-storage cost separately, and also consult the console. The cause is unresolved, and this documentation publication does not change the code
- Retry verification after communication errors until the deadline, five minutes by default. If confirmation is unavailable by then, report failure and display the **Pod ID and console Stop instructions**, using Stop rather than Terminate. If the Pod is `locked`, unlock it through the console
- An 80 GB Pod Volume continues to incur retention charges after Stop. Terminate is not implemented here and requires separate confirmation because it is irreversible

## Artifact retrieval The original start target was 2026-10-02 16:30 JST

1. Cell 5 creates `report-*.json` and a `.tar.gz` with a MANIFEST under `/workspace/qwen/runs`
2. Download them through Jupyter and store them outside the Pod
3. Read the saved `.tar.gz` back with `python scripts/verify_bundle.py <file>`, checking hashes, file list, allowlist, and the presence of every check. Proceed to stopping after verification passes
4. `report_exported` is always `skipped` inside the Pod because it must be determined by reading the artifact back outside the Pod

### Report contents

Execution commit and clean-tree status; dependency-lock SHA256; image tag, with the digest marked `unavailable_in_pod` because it cannot be obtained inside the Pod; nvcc and driver CUDA information; llama.cpp commit, architecture, runtime, build duration, and whether it was built in this run; GPU; model revisions and verification results; configuration; separate load/cold/warm measurements; and every check's pass/fail/skipped status. Do not save the Pod's entire `env`, SSH information, runtime object, API response, or configuration. Extract only explicitly named fields.

## Estimate produced by plan

Start with `GPU hourly rate × (time from launch to target stop + 10 minutes of stop-completion margin)`. Add running Container/Volume storage, **retention of the stopped Pod Volume, 24 hours by default**, and **a contingency for taxes and fees, 20% by default, which is an assumption rather than a published charge**.

**This is an estimate, not an automatic stop or a guaranteed billing cap.** Human confirmation of the stop and reconciliation against actual Billing charges are required. The v2 API does not expose balance or auto-recharge settings, so those require a private console check. `create` refuses to submit if its estimate exceeds the configured limit, $10 by default, but that is not an account-level spending cap.

## Required permissions Unchanged by this documentation publication

APIs used: `GET /v2/catalog/gpus`, `GET /v2/catalog/datacenters`, `POST /v2/pods`, `GET /v2/pods` for `find`, `GET /v2/pods/{id}`, and `POST /v2/pods/{id}/action` for stop only. Optional: `GET /v2/billing/pods`. DELETE/terminate, secrets, ssh-keys, templates, network volumes, serverless, and clusters are not required. The OpenAPI specification does not name per-endpoint permission scopes; consult the console. Before the trial, create/stop write permissions could not be established without executing those operations; they succeeded during the trial. Do not assume the same permissions apply in a future environment or under different authentication.

## Remaining issues

1. A person connected to Jupyter during the trial. Confirm authentication and connection details for each new Pod; never place passwords in chat or Git
2. The pinned model delivery endpoints were reachable from `EUR-IS-1`, but full model downloads and SHA256 verification were not performed. Reachability does not fully validate a revision or model contents
3. llama.cpp CUDA compilation, model loading, GPU memory use, 32k context, images, search, and ASR remain untested. Stop readback exposed the API `cost` discrepancy described above
4. Pod Volumes are tied to their host, and the API specification states that a host failure can lose the data. A failure before retrieval can therefore lose the results
5. Capacity changes; a creation plan does not reserve inventory

## What the next fresh plan must include

- Retention, retrieval, and deletion decisions for the two remaining 80 GB Volumes. Do not Terminate or delete them without authorization
- Current inventory, GPU price, running storage charges, stopped-storage charges, and a total spending limit. Neither the historical $1.59/hour price nor the previous $10 limit is new authorization
- Time for approximately five minutes of dependency installation, the untested CUDA build and full model downloads, artifact retrieval, and the stop deadline
- Staged execution: `selftest` → `net` → authorized later steps. Stop if connectivity fails
- How API state, console state, and billing will be checked in light of the pinned implementation's stop-verification limitation. Obtain new authorization for another run or restart

## Immutable implementation links

These files are on `main` after the implementation merge. To reproduce the trial exactly, still check out the pinned commit above instead of the branch tip before using the commands.

- [`scripts/pod.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod.py)
- [`scripts/pod_run.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod_run.py)
- [`scripts/pod-bootstrap.sh`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod-bootstrap.sh)
- [`scripts/verify_bundle.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/verify_bundle.py)
- [`notebooks/Qwen-Q8-Chat-RunPod.ipynb`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/notebooks/Qwen-Q8-Chat-RunPod.ipynb)
