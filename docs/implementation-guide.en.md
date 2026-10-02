# Implementation guide: code used on October 2, 2026

English | [日本語](implementation-guide.ja.md)

This guide lists the code that was written and used on October 2, 2026, what each part does, what was actually run where, and what remains unverified. It does not authorize any paid run, restart, access change, or deletion. Read it with the [trial report](trial-report-2026-10-02.en.md), which has the results and costs.

## Where the code came from (commit map)

| Commit | Content |
| --- | --- |
| `052cff2` | Cloud-environment preparation (`scripts/setup-cloud-env.sh`): runpodctl and CPU checks |
| `c9c5763` | First launcher: pins, paths, Gradio 6 adapter, launch safeguards, model/llama.cpp helpers, report, REST v2 Pod tooling, hash-locked dependencies, CPU tests |
| `213ad9e` | Hardening: stop confirmation, create-timeout handling, estimate, report contents, repository-root resolution, cold/warm separation, pinned-commit bootstrap |
| `30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4` | Isolated venv, staged runner, log channel, network gate. **The code executed on the Pod in the final run** |
| `69795d54490ffaeff62f18ca80f6181ea674bc16` | Trial-log update only; code identical to `30d6c5d…` |

The implementation branch was merged into `main` after the trial. History is preserved (no squash or rebase), so the commits above and the permalinks in the other documents stay valid. To reproduce the Pod run exactly, check out `30d6c5d…`, not the branch tip.

## Repository layout

```
qmc_runpod/            launcher package (wraps the upstream `qmc` package; upstream is not modified)
scripts/               command-line tools (Pod runner, Pod API tool, log reader, bundle checker, bootstrap)
notebooks/             Qwen-Q8-Chat-RunPod.ipynb (thin, secret-free)
tests/                 CPU tests (75 passing at 30d6c5d…)
requirements-runpod.in / .lock.txt   inputs and the hash-locked set (CPython 3.11, 85+ packages)
baseline.json          pins recorded before the trial (planning snapshot)
```

## Package `qmc_runpod/`

| Module | Role |
| --- | --- |
| `pins.py` | Upstream commit and notebook blob, llama.cpp commit, both model files (repo, file, revision, SHA256), ports. A test compares them with `baseline.json` |
| `layout.py` | Pod paths under `/workspace/qwen` (overridable with `QMC_WORKSPACE`), non-secret environment. No `/content`, no Drive |
| `chat_ui.py` | Gradio 6 adapter: upstream `ui_chat` passes `type="messages"` to `gr.Chatbot`, which Gradio 6.29.0 rejects (TypeError). The argument is dropped while the upstream UI is built |
| `launch.py` | Fail-closed start: both login values required, no auto-moved port, no `share`, `llama-server` on `127.0.0.1`, no external chat API. Checks afterwards that an unauthenticated request is refused |
| `models.py` | Downloads exactly two files at pinned revisions and verifies size and SHA256 before use |
| `llama.py` | Builds `llama-server` at the pinned commit under the workspace; refuses to guess the GPU architecture |
| `smoke.py` | Chat-only measurements that keep load, the first (cold) request and warm runs apart, with an explicit output cap (`max_tokens`). Criteria are evaluated only from 10 clean warm runs |
| `report.py`, `trial.py`, `provenance.py` | Allowlist-only report (unknown keys and secret-like strings are rejected), commit/lock-hash/image/CUDA/build facts, all checks as pass/fail/skipped, bundle with manifest and read-back verification |
| `podapi.py` | REST v2 client without any credential handling; creation guard and estimate; unique searchable Pod names; "create result unknown" handling; stop with deadline-based read-back. **No terminate/delete function exists** |
| `envsetup.py` | Isolated venv helpers (no system site-packages, hash-locked install, `pip check` inside the venv, kernel registration) |
| `stages.py` | Staged, resumable runner: `selftest`, `net`, `source`, `venv`, `build`, `trial`; status file; logs; mirror to the Pod's stdout |
| `logchannel.py` | Event lines and SHA256-checked artifact chunks that survive a log stream |

## Scripts

| Script | Use |
| --- | --- |
| `scripts/pod_run.py` | Runs stages on the Pod (`selftest`, `net`, `source`, `venv`, `build`, `trial`, `all`, `status`, `emit`); `--bg` runs detached; no timeout on pip or the CUDA build |
| `scripts/pod.py` | `plan` (read-only), `create` (paid; needs the approval token and the planned name), `find`, `status`, `stop` |
| `scripts/read_pod_logs.py` | Reads a Pod's log stream through the read-only API, shows only status events, rebuilds and verifies artifacts |
| `scripts/verify_bundle.py` | Read-back check of a report bundle stored outside the Pod |
| `scripts/pod-bootstrap.sh` | Clones this repository at one full commit SHA and checks that the tree is clean |
| `scripts/e2e_mock_ui.py` | Manual CPU check: real chat-only UI with the mock backend, driven in Chromium |
| `scripts/setup-cloud-env.sh`, `scripts/make-pod-bundle.sh` | Cloud-environment preparation; tarball of the Pod files |

## Tests

`tests/` holds CPU-only tests (pytest). They need the pinned upstream checkout (set `QMC_UPSTREAM_DIR`; tests that need it are skipped otherwise). They cover the pins, the absence of Colab/Drive paths, launch safeguards, the Gradio 6 adapter and the mock chat-only UI behind a login, model verification, the allowlist report, Pod API behavior with fake HTTP (estimate, unknown create results, stop confirmation, no terminate), notebook root resolution, cold/warm separation, the isolated venv commands, the network gate, and the log channel. A CPU rehearsal (`pod_run.py trial --mock`) produces a bundle that is read back through the log channel.

## What was actually run, and where

| Item | Where | Result |
| --- | --- | --- |
| CPU tests, mock UI in Chromium, isolated venv with real pip (about 39 s), `trial --mock` | Development environment | Passed |
| `selftest`, `net`, `venv` through the log channel | Real Pod, `EUR-IS-1`, commit `30d6c5d…` | Passed (event lines, SHA256-matching artifact, all hosts reachable, hash-locked install in about 297 s, `pip check` inside the venv, kernel registration confirmed separately) |
| Create, stop and restart through REST v2 | Real account | Create and stop worked; the API `cost` stayed 1.59 after stop (see below) |

**Not run on a Pod:** `source`, `build` (CUDA compilation and model download), `trial`, the notebook, model load, inference, images, search, ASR, performance measurement.

## Known issues and open items

1. The API `cost` of a stopped Pod stayed at its running rate while the console showed Not running. The stop check (`EXITED` and a numeric 0) therefore cannot succeed on this account; use the console as the second source until the criterion is revisited.
2. The `venv` stage reports `kernel=qwen-venv` regardless of the registration command's exit code; the kernel was confirmed by a separate check.
3. Log lines from long installs are throttled, so progress can look stalled.
4. `start` is not a function of the Pod tool; the restart on October 2 used a one-off API call. Terminate is deliberately absent.
5. The `ensurepip` fallback (host pip with `--python`) and `chain`-style sequencing are not implemented as stages; the final run used a short shell block that ran `selftest`, `net` and `venv` in order.
6. The CUDA build, the model download, inference and the usability criteria remain to be validated in a new, separately approved plan.

## Public repository cautions

This repository is public. Do not commit secrets, authentication URLs, account or payment information, raw conversations, private identifiers, model weights, or notebook outputs. Reports must pass the allowlist (`qmc_runpod/report.py`).
