# Baseline planning snapshot guide

English | [日本語](baseline-guide.ja.md) | [README](../README.md)

[baseline.json](../baseline.json) is the original October 2, 2026 planning snapshot. It is preserved byte-for-byte in this documentation update. Its English identifiers and descriptive strings are explained here in both languages; this guide does not change executable configuration, pins, approval records, or JSON schema.

**Use the [October 2 experiment report](trial-report-2026-10-02.en.md) for actual results.** Two Pods were created and later stopped, not terminated. The final limited chain completed `selftest → net → venv`; CUDA build, full model-byte verification, loading, and inference remain untested. Two 80GB Volumes remain. The original `resources_created: false` and approval booleans describe the earlier planning phase; they are neither current operational status nor authority for future actions.

## Document identity and status

| Field | Value and meaning |
| --- | --- |
| `schema_version` | `1`: schema version of this historical record |
| `checked_at_utc` | `2026-10-02`: date the planning source was checked; not the final stop timestamp |
| `status` | `planning_only_no_gpu_run`: pretrial planning status, superseded for operational results by the dated report |

## Upstream source

| Field | Value and meaning |
| --- | --- |
| `repository` | `https://github.com/moruku36/qwen-multimodal-colab` |
| `commit` | `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`: pinned upstream source |
| `notebook` | `Qwen-Q8-Chat-Colab.ipynb`: current chat entry point |
| `notebook_git_blob` | `5e40a89f0253356c9c8d72698a93508f4d30d8aa`: exact target Notebook blob |
| `merged_pr` | `35`: upstream PR merged October 1, 2026 |
| `original_notebook_git_blob` | `15836f710296a2d6bbc665bcddd169424716679d`: recorded original Notebook blob, retained separately from the selected chat Notebook |

## Runtime defaults

- `engine`: `llama.cpp`; `commit`: `4da6337767f973e2b4d0797e5b323d77d8565e4a`
- `image_digest` and `python_lock`: `null` at planning time. The later report records an observed first-Pod image digest and implementation lock hash with evidence limits; null does not mean those later records do not exist
- `chat_only: true`: exclude image-generation/editing components
- `server_host: "127.0.0.1"`, `server_port: 8012`: loopback model API, not a public inference endpoint
- `chat_context_baseline: 32768`, `chat_context_initial_smoke: 8192`: separate baseline and initial-smoke settings; neither is a completed GPU result
- `thinking: false`, `asr_device: "cpu"`, `asr_model: "small"`, `share: false`: planned non-secret runtime settings

## Model references

These are publisher metadata and planned immutable references, not evidence that all model bytes were downloaded. Both entries have `download_verified: false` and an `apache-2.0` license label. Check the pinned revisions' actual LICENSE/NOTICE and upstream conditions before use or redistribution. Model weights are not stored in this repository.

| Field | Chat model | Vision projector |
| --- | --- | --- |
| `role` | `chat` | `vision_projector` |
| `repo` | `huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF` | `ggml-org/Qwen3.8-27B-GGUF` |
| `file` | `Huihui-Qwen3.8-27B-abliterated-UD-DW-Q8_K_L.gguf` | `mmproj-Qwen3.8-27B-Q8_0.gguf` |
| `file_change_commit` | `ff733b88376282017b7f4675d6d96536c6ffa712` | `97c30c65c8d9a3e73f9fdfb50f1d1a669e9a2827` |
| Size metadata | `size_display: "27.3 GB"` | `size_bytes: 629247008` |
| `sha256_publisher` | `fb8413d0b5cec5ad7055e49630a986518ba90fdacc95a337aa535e8ff5bf0d16` | `2e968a6af97ce35d8971890b257b9b7edabf20ad91450501fa53162a19ee33eb` |

The file-change commits identify source-page references. An implementation must pin and verify appropriate immutable revisions; the [final report](trial-report-2026-10-02.en.md) links the implementation's exact pins.

## Notes translated and explained

1. Publisher metadata is not local download verification
2. File-change commits are source-page references; the implementation must pin and validate appropriate immutable revisions
3. Template digest and dependency lock were unset until implementation; this is a historical statement, with later evidence in the report
4. This file authorizes no billing, deployment, automatic stop, or termination
5. Disposable Pod Volume storage is the default, not seven-day Network Volume retention
6. Every new experiment needs a new plan; repeated fresh setup can cost more than short-term retention
7. Account-wide deletion is excluded, as are existing/shared resources and externally saved reports

## Experiment lifecycle fields

| Field | Historical value and interpretation |
| --- | --- |
| `policy` | `new_plan_then_experiment_then_verified_external_report_then_delete_experiment_resources`: intended lifecycle; actual deletion is still pending |
| `next_experiment_requires_new_plan` | `true` |
| `default_gpu` | `A100 80GB` |
| `max_session_hours` | `2`: original attended-session limit, not a new authorization |
| `default_storage.pod_volume_gb` | `80` |
| `default_storage.container_disk_gb` | `20` |
| `default_storage.network_volume_gb` | `0`: no independent Network Volume by default |
| `default_storage.retain_between_experiments` | `false`: original default; actual intermediate work was subsequently retained, with ongoing costs |
| `estimated_two_hour_cost_usd_excluding_tax` | `3.21`: plan estimate for two A100 hours with immediate teardown after verified export; not actual trial spending |
| `total_budget_preference_usd_approximate` | `10`: original preference. A later $10 total cap was approved specifically for the recorded trial, including storage, taxes, and fees |
| `spending_approved`, `deposit_approved` | `false`: planning-time record; not a claim that the later documented paid trial lacked approval, and not permission for future spending |
| `automatic_hard_budget_cap` | `false`: no automatic hard cap was implemented |
| `resources_created` | `false`: planning-time record. The final report documents two actual Pods |
| `external_export_and_readback_required_before_deletion` | `true`: export and verification outside the resources being deleted |
| `reproduction_metadata_required` | `true` |
| `deletion_scope` | `only_resources_created_for_the_specific_experiment`: exclude pre-existing/shared resources and the account |
| `irreversible_deletion_requires_action_time_exact_target_confirmation` | `true` |
| `execution_or_deletion_authorized_by_plan` | `false`: plan agreement does not itself authorize execution or deletion |
| `include_fresh_boot_download_build_export_time_in_each_trial` | `true`: include all these stages in time/cost estimates |

For the later $1.71 GPU estimate, ongoing approximately $1.07/day combined storage, untested stages, and unresolved Billing reconciliation, use the report rather than editing this historical snapshot into an approval document.
