# RunPod trial record template

English | [日本語](experiment-template.ja.md)

Template status: blank and not executed. The fields below are for recording observations, not measured results. Copy this template to create a record for each trial date. Do not interpret these blank fields as a completed record of the 2026-10-02 trial. Do not include secrets, full conversations, personal information, authenticated URLs, or keys.

This repository is public. Keep exact Pod IDs and similar identifiers in a private operations ledger; use redactions or non-sensitive labels in the public version. Do not publish account balances, payment information, or private URLs for personal services.

## Trial status as of 2026-10-02

The actual trial used two Pods. Network checks failed in US-MD-1. In EUR-IS-1, selftest, network checks, venv preparation, and external artifact export and read-back verification succeeded. CUDA, model download, model loading, and inference were not tested. The approximately $1.71 GPU cost is an estimate, not a finalized invoice amount.

Both Pods are stopped, but each retains an 80GB Pod Volume. Storage costs approximately $0.022/hour each, or approximately $1.07/day combined; cleanup is incomplete. This document does not authorize a new trial or deletion. See the [2026-10-02 trial report](trial-report-2026-10-02.en.md) for details. The next trial requires a new plan and the necessary approvals.

## Plan for this trial

- Trial ID / plan link and commit:
- Previous report / hypothesis and changes for this trial:
- Pass/fail criteria / execution date / maximum duration:
- Record of paid-use approval / approved total spending limit including taxes and fees:
- Relationship to the overall budget preference of approximately $10 (the preference alone is not spending approval):
- External destination for outputs (outside the Pod and Volume to be deleted for this trial):
- Differences from the standard configuration and reasons (the original plan's initial standard was A10080GB, Pod Volume80GB, Container20GB, a maximum of 2 hours, and an estimated $3.21 excluding taxes and similar charges; these are not actual results or a finalized invoice amount):

## Configuration

- Execution date and time zone:
- Original source commit / Notebook blob:
- RunPod Notebook commit:
- Exact GPU name / VRAM / GPU count / data center:
- On-Demand/Spot / actual displayed hourly price:
- Template / image tag / digest:
- Python / pip / torch / CUDA toolkit / NVIDIA driver:
- llama.cpp commit / CUDA arch / build flags:
- Model repo / revision / file name / SHA256 / byte count:
- mmproj repo / revision / file name / SHA256 / byte count:
- Context / image limit / output limit / thinking / search / seed and other settings:
- Disk type / capacity / mount path / resources to delete after this trial:
- Dependency lock location:

## Time and resources

| Item | Measurement | Notes |
| --- | --- | --- |
| Pod creation to connection availability | Not measured | |
| Dependency install | Not measured | |
| llama.cpp build (for each new trial) | Not measured | |
| Model download (for each new trial) | Not measured | |
| Cold model load | Not measured | |
| Cached restart to availability within this trial | Not measured | Retaining the cache for the next trial is not the default |
| Warm short prompt time to first token, median/maximum | Not measured | The same 10 prompts |
| Warm short prompt completion time, median/maximum | Not measured | Also record the output token count |
| One image / multiple images | Not measured | Record dimensions and image count |
| Search On | Not measured | Separate search wait time |
| CPU ASR | Not measured | Input duration in seconds and errors |
| Baseline/peak VRAM | Not measured | Use nvidia-smi to include separate processes |
| Peak CPU RAM | Not measured | |
| Disk usage / free space | Not measured | |
| VRAM after 10 turns / after release | Not measured | |
| External export and verification of the report, results, and reproduction information | Not measured | Include in billed time if the GPU is running |
| Total time from startup through confirmed shutdown | Not measured | Do not exclude install/build/download/wait time |

## Functionality and safety

- Chat / image understanding / search / ASR:
- History saving during this trial and restoration after an app restart:
- Test of recreating a new environment from saved procedures: Not performed (to be covered by the next new plan)
- Rejection of unauthenticated access:
- Externally exposed ports and internal model API:
- Cancellation / regeneration / reload / relaunch:
- OOM, offload, automatic context reduction, and other errors:
- Japanese response quality and usability:

## Costs and shutdown

- Billing start time / time billing stop was confirmed:
- GPU usage duration and cost:
- Container/Volume/Network costs:
- Taxes / currency / fees:
- Other API costs:
- Session total / finalized invoice or provisional amount:
- RunPod operations performed after stopping the app:
- Confirmation of Pod stop/deletion:
- Remaining storage awaiting deletion confirmation and its costs (if any; cleanup is incomplete while unresolved):
- Next decision: A40 comparison under a new plan / verification of fixes under a new plan / return to Colab / no further trial

## External storage verification

- Report and measurement destination link / commit:
- Reproduction information destination (source, Notebook, model revision/hash, image digest, dependency lock, settings, build instructions, and test inputs):
- File list / byte counts / hashes and other checks:
- Date, time, and result of reading the files back from the external destination:
- Confirmation that secrets, authenticated URLs, personal information, and full raw conversations were removed:
- Content, destination, and approval for any exception retaining the original DB or attachments:

## Experiment resource ledger and deletion confirmation

Record only resources created for this experiment. Existing/shared resources, the account, and externally saved reports are excluded from deletion.

| Type | Exact ID and name | Evidence of creation for this trial / relationship | Data lost and impact | Record of immediate pre-action confirmation | Execution and verification time and result |
| --- | --- | --- | --- | --- | --- |
| Pod and its attached Container/Pod Volume | Not created | | DB, attachments, weights, cache, build artifacts, etc. | Not obtained | Not performed |
| Independent resources added as an exception | None | | | Not obtained | Not performed |

- After external storage verification, was immediate pre-action confirmation for irreversible deletion obtained with the target IDs and impact presented?
- Result of reconciling Pods/Storage/Billing against the ledger:
- Confirmation that no GPU/storage billing resources remain for this experiment:
- Unresolved issues, including unfinalized charges, pending deletion, or failed export:
- Cleanup complete / incomplete (with evidence):
- Link to a new plan for the next trial (state “not created” if there is none):

Use the same fixed prompts for comparisons and record changes one at a time. Do not omit failures or long cold starts to make performance appear better.
