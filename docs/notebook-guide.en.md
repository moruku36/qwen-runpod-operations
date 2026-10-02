# RunPod notebook guide

English | [日本語](notebook-guide.ja.md) | [README](../README.md)

This guide provides the human-facing instructions and cell descriptions for [Qwen-Q8-Chat-RunPod.ipynb at commit 69795d54490ffaeff62f18ca80f6181ea674bc16](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/notebooks/Qwen-Q8-Chat-RunPod.ipynb). The notebook was merged into `main` unchanged after the trial; this guide does not copy or alter its code cells. Code comments and identifiers in that immutable notebook remain authoritative.

**Status on October 2, 2026:** only the final limited `selftest → net → venv` chain was completed. The notebook's CUDA/model/inference stages were not completed on a real GPU. Both Pods are stopped with retained storage; another execution requires a fresh approved plan. See the [experiment report](trial-report-2026-10-02.en.md).

## Original opening instructions and their scope

The implementation notebook is titled “Qwen Q8 Chat on RunPod (chat-only).” Its opening instructions call for environment preparation with `python scripts/pod_run.py all` using an isolated venv, followed by selecting **Python (qwen-venv)** as the kernel. The notebook itself does not install into system Python.

**Important:** `all` is a broad execution path that includes work beyond the successfully tested limited stages. It was not run in the final trial and is not authorized by this guide. Use the [runbook](runpod-runbook.en.md) and the next approved plan to select individual stages. Commands require a checkout that contains the implementation (`main` after the merge, or the pinned commit for an exact reproduction).

Open the notebook through the Pod's Jupyter, reached from RunPod console → Connect. It can start from the repository root or the `notebooks/` directory. Follow cells 1 through 5 in order, with optional Cell 4b only when authorized and appropriate, then use Cell 6 for app cleanup. No credentials are stored in the notebook; the Gradio login values are entered at runtime through hidden inputs.

- Cell 4 sends one short request with an explicit 64-token output cap. It is a smoke check, not a performance measurement
- Optional Cell 4b measures warm runs separately after Cell 4 passes and the operator elects to run it within the approved scope. Without warm evidence, performance checks stay `skipped`
- Stopping the app does not stop the Pod. Stop externally through `python scripts/pod.py stop <pod_id>` (REST v2) or the console, under the applicable authorization

## Cell 1 Repository root and isolated kernel

The environment, hash-locked installation, and `pip check` are prepared by `python scripts/pod_run.py venv`, not by installing into the Pod's system Python. Mixing OS packages such as PyGObject/pycairo into this check caused the earlier failure.

The root finder works from the repository root, `notebooks/`, or an explicitly set `QMC_REPO_ROOT`. It locates `qmc_runpod/pins.py` and `requirements-runpod.lock.txt`. The notebook refuses to continue unless running inside a venv; select **Python (qwen-venv)** with Kernel → Change kernel, or prepare the kernel first if absent.

All report checks begin as `skipped`. The cell records the operations commit and clean-tree status, creates the source directory, fetches the pinned upstream commit, and checks both upstream HEAD and Notebook blob. A mismatch aborts. It runs `pip check` inside the venv and records lock SHA256, Python version, and environment path. A printed clean status or local CPU test must not be treated as GPU validation.

## Cell 2 Environment, CUDA build, and pinned models

The cell sets non-secret environment values with context 8192 and web search off. Context 32768 is a separate later run. It installs llama-server at the pinned commit, records build time, CUDA architecture, runtime tag, Release build type, and whether the binary was built in this run. It downloads the specified model files at fixed revisions and records SHA256 verification.

These steps can consume significant GPU allocation time and network/disk capacity. They were not executed in the final limited run. Endpoint HEAD responses do not establish that this cell downloaded or verified the model bytes.

## Cell 3 Authenticated chat-only UI

Both `QMC_AUTH_USER` and `QMC_AUTH_PASSWORD` are required, entered with hidden input. Launch refuses to start without both. The cell starts the chat-only application and checks rejection of unauthenticated access. The printed proxy URL is an access route, not authentication by itself. Preserve `SHARE=False` and the loopback-only model API; do not put login values or live authentication URLs in reports or Git.

## Cell 4 One cold smoke request

The cell times model load separately, then sends one short request capped at 64 tokens and inspects GPU memory. It marks the first response as passing only when there is no error and at least one stream delta. This cold request does not establish warm performance.

## Optional Cell 4b Warm measurements

This cell requires `first_response == pass` and uses ten fixed Japanese prompts, each capped at 256 output tokens. It records warm-run summaries. Performance criteria are `not_evaluated` unless ten clean warm runs are available; skipped or failed tests must not be presented as a pass.

The test prompts remain byte-identical in the executable notebook because changing them changes the comparison. Their meanings are provided here for English readers:

1. Explain Japan's four seasons in 50 Japanese characters
2. What is the sum of 1 through 10?
3. How can a list be reversed in Python?
4. How high is Mount Fuji?
5. Give a short greeting
6. What is the difference between Git commit and push?
7. Explain the basic preparation of miso soup in three lines
8. State the difference between TCP and UDP briefly
9. Describe today's mood briefly
10. What is a prime number?

## Cell 5 Allowlisted report and export bundle

The report includes separate model-load/cold-response metrics, output cap, stream deltas, finish reason, optional first-delta time, and VRAM when available. Warm summaries are added only if they exist. Reproduction data includes operations commit, dependency lock, model records, GPU/build metadata, llama-server and Gradio versions, and allowlisted settings: context, thinking off, search off, CPU small ASR, TTS off, share off, and chat-only mode.

A JSON report and `.tar.gz` bundle are written beneath the runs directory. Download the bundle through Jupyter, then confirm `report_exported` from outside the Pod by reading files back and verifying the manifest/hashes. A locally printed path is not verified external preservation. A small selftest export does not establish success for this actual inference bundle.

## Cell 6 Stop the application

`launch.stop_app(APP)` stops the application and model process only. It neither stops nor deletes the Pod. Use an external RunPod stop route and inspect actual state, system shutdown evidence, and console Compute/Container status. During the real trial the API `cost` remained 1.59 despite `EXITED` and stopped console displays; `cost == 0` alone is not a reliable confirmation gate. Reconcile charges separately in Billing.

Stop retains the Pod Volume and its storage fees. Termination is separate and irreversible for Container/Pod Volume data; it needs verified external export and target-specific action-time approval. This guide grants no startup, broad `all` execution, restart, new access, or deletion permission.
