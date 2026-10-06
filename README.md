# Qwen RunPod operations

Updated: October 6, 2026. **C1 CPU implementation and disabled C2 v5 are
accepted; C3 live on-demand activation remains unexecuted.** The cached A100/27B
diagnostic completed one request at context8192 (load347.956s). See the
[current development record](docs/development-status-2026-10-06.en.md),
[日本語](docs/development-status-2026-10-06.ja.md), and
[C2 recovery constraints](scripts/c2/README.md). Publishing does not enable a
provider, create/restart/terminate a Pod, or authorize further spending.

The following October 2 account is a historical snapshot. Its retention, billing
and “untested” statements describe that date, not the current development status.

English | [日本語](README.ja.md)

Updated: October 2, 2026. **The first paid experiment reached connectivity, isolated Python setup, and small-artifact export. CUDA builds and Qwen GPU inference remain untested.** Both experiment Pods were stopped by approximately 16:05 JST, but two 80GB Pod Volumes remain. Their combined retention estimate is approximately $1.07/day. Cleanup and final Billing reconciliation are still pending. See the [full experiment report](docs/trial-report-2026-10-02.en.md).

## Purpose and current scope

This repository records an experiment to evaluate RunPod as an alternative to waiting for Colab compute-unit replenishment. Preserve the existing Gradio UI, llama.cpp, 27B Q8_K_L model, and official mmproj first. Text chat, image understanding, web search, CPU ASR, and history are planned evaluation targets; image generation/editing is outside scope. This experiment does not establish that RunPod is faster, cheaper, or fully migrated.

The initial plan selected one Secure Cloud On-Demand A100 80GB for an attended session of at most two hours, then proposed an A40 48GB comparison under the same conditions. A100 was chosen to reduce differences from the existing design, not because Q8 was proven to require 80GB. Serverless and Open WebUI integration remain separate future work.

Public rates checked on October 2 were $1.59/hour for A100 80GB and $0.49/hour for A40 48GB. Availability and the actual launch screen take precedence. Include startup, download, build, waiting, export, and shutdown verification in a session comparison; inference seconds alone are not the bill.

## What the first experiment established

- First Pod in US-MD-1: GitHub connectivity failed with `No route to host`; the root cause was not isolated
- Second Pod in EUR-IS-1: final `selftest → net → venv` chain passed, including hash-locked pip installation and isolated `pip check`
- A small external log/artifact round trip passed SHA256 verification; complete inference-result export remains untested
- A manual preparation attempt timed out after 240 seconds; a later pip installation succeeded in approximately 297 seconds
- CUDA build, full model download/hash verification, model load, inference, performance, images, search, and ASR remain untested
- Both Pods were stopped, not terminated. GPU-only time-based estimate: approximately $1.71, not a settled invoice. Two retained 80GB Volumes cost approximately $0.04444/hour combined
- The actual trial had an approved $10 total cap including storage, taxes, and fees. Exact final spending and remaining budget have not been reconciled in Billing. This does not authorize a subsequent trial

## Experiment lifecycle

**New plan → run within that plan's approval → export and read back reports, results, and reproduction metadata outside disposable resources → confirm exact deletion targets and consequences → remove approved experiment resources → start the next investigation with a new plan.**

The original default was an 80GB Pod Volume plus a 20GB Container Disk. Two A100 hours followed by immediate verified teardown were estimated at $3.21 before taxes. Seven-day Network Volume retention is not the default. History databases, model weights, caches, and build products are disposable unless an explicit preservation exception is agreed. Retain reports and non-secret reproduction information.

Actual cleanup has not reached that planned endpoint: the two stopped Volumes are retained. Stop does not end storage charges. Irreversible deletion requires verified export and action-time confirmation of the specific resources and data; the account, pre-existing/shared resources, and saved reports are excluded. Repeated fresh downloads and builds can cost more than short retention, so the next plan must compare those tradeoffs explicitly. No automatic hard budget cap is implemented.

## Pinned baseline

- Upstream: [moruku36/qwen-multimodal-colab](https://github.com/moruku36/qwen-multimodal-colab)
- Verified upstream commit: `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`
- Target: [Qwen-Q8-Chat-Colab.ipynb](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/Qwen-Q8-Chat-Colab.ipynb)
- Notebook blob: `5e40a89f0253356c9c8d72698a93508f4d30d8aa`
- [Upstream PR 35](https://github.com/moruku36/qwen-multimodal-colab/pull/35) merged October 1, 2026
- The old `Qwen-Multimodal-Colab.ipynb` is unchanged and is not this experiment's entry point

[baseline.json](baseline.json) is the **original planning snapshot**, not current operational status. Its `planning_only_no_gpu_run`, `resources_created: false`, and unapproved-spending fields describe the pretrial phase. Its null image/lock fields likewise do not replace the later report. The file remains unchanged; the [bilingual baseline guide](docs/baseline-guide.en.md) explains its fields and their historical scope.

## Documentation

Each document has matching English and Japanese versions. Commands, paths, identifiers, and pins are kept unchanged across translations.

| Topic | English | 日本語 |
| --- | --- | --- |
| Final first-experiment report | [Report](docs/trial-report-2026-10-02.en.md) | [実験レポート](docs/trial-report-2026-10-02.ja.md) |
| Original operations plan and costs | [Plan](docs/operations-plan.en.md) | [運用計画](docs/operations-plan.ja.md) |
| Notebook portability review | [Review](docs/portability-review.en.md) | [移植差分](docs/portability-review.ja.md) |
| Session checklist | [Checklist](docs/session-checklist.en.md) | [チェックリスト](docs/session-checklist.ja.md) |
| Blank experiment record | [Template](docs/experiment-template.en.md) | [記録テンプレート](docs/experiment-template.ja.md) |
| Primary sources | [Sources](docs/sources.en.md) | [一次資料](docs/sources.ja.md) |
| Historical CPU/cloud preparation | [Preparation](docs/cloud-env-prep.en.md) | [クラウド準備](docs/cloud-env-prep.ja.md) |
| Implementation guide (code used on Oct 2) | [Guide](docs/implementation-guide.en.md) | [実装ガイド](docs/implementation-guide.ja.md) |
| Implementation runbook | [Runbook](docs/runpod-runbook.en.md) | [実行手順](docs/runpod-runbook.ja.md) |
| Remote-operation design | [Remote operations](docs/pod-remote-ops.en.md) | [遠隔操作](docs/pod-remote-ops.ja.md) |
| Detailed October 2 trial log | [Trial log](docs/trial-log-2026-10-02.en.md) | [試行ログ](docs/trial-log-2026-10-02.ja.md) |
| Notebook instructions and cells | [Notebook guide](docs/notebook-guide.en.md) | [Notebookガイド](docs/notebook-guide.ja.md) |
| Baseline snapshot field guide | [Baseline guide](docs/baseline-guide.en.md) | [baseline解説](docs/baseline-guide.ja.md) |

## Implementation

The implementation (the `qmc_runpod/` package, `scripts/`, the RunPod notebook, the hash-locked dependencies, and CPU tests) was merged into `main` after the October 2 trial. History is preserved, so the permalinks in these documents remain valid. See the [implementation guide](docs/implementation-guide.en.md) for what each part does, what ran where, and what is unverified.

The code executed on the Pod in the final run is commit [30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4](https://github.com/moruku36/qwen-runpod-operations/commit/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4); the later change [69795d54490ffaeff62f18ca80f6181ea674bc16](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16) updated only the trial log. To reproduce that run exactly, check out the pinned commit rather than the branch tip. The [notebook guide](docs/notebook-guide.en.md) translates the notebook's human-facing instructions without changing its cells.

Next work requires a new plan with explicit scope, time/spending limits, export destinations, and authorization. Review log-receipt failure handling, kernel-registration checks, and the API `cost` shutdown discrepancy before another paid run. No resource creation, restart, new access, or deletion is authorized by reading or publishing these documents.

This repository is public. Do not commit secrets, authentication URLs, account/payment information, raw conversations, private identifiers, model weights, or Notebook outputs. Use redacted reports and allowlisted reproduction data.
