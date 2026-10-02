# Session execution checklist

English | [日本語](session-checklist.ja.md)

This is a blank checklist template for the next paid trial. Start with every item unchecked and check only items verified by the operator. The standard lifecycle is “new plan → experiment → external export and verification of results and reproduction information → deletion of that experiment's components.” The original was written before resource creation; consult the report below for the subsequent 2026-10-02 trial results. This unchecked list does not mean that the entire trial was unperformed, nor does it prove completion of any item. A budget preference of approximately $10 or agreement with this plan does not authorize spending, a deposit, or irreversible deletion.

This repository is public. Keep administrative records containing exact Pod IDs or payment information private, and remove balances, payment information, private URLs for personal services, and Pod IDs from public reports.

## Trial status as of 2026-10-02

The actual trial used two Pods. Network checks failed in US-MD-1. In EUR-IS-1, selftest, network checks, venv preparation, and external artifact export and read-back verification succeeded. CUDA, model download, model loading, and inference were not tested. The approximately $1.71 GPU cost is an estimate, not a finalized invoice amount.

Both Pods are stopped, but each retains an 80GB Pod Volume. Storage costs approximately $0.022/hour each, or approximately $1.07/day combined; cleanup is incomplete. This document does not authorize a new trial or deletion. See the [2026-10-02 trial report](trial-report-2026-10-02.en.md) for details. The next trial requires a new plan and the necessary approvals.

## Before startup

- [ ] Create a new plan with a trial ID, including the previous results, this trial's purpose, comparison conditions, and pass/fail criteria
- [ ] Obtain the user's approval for the execution date, an end time such as a maximum of 2 hours, a total USD spending limit including taxes and fees, and the external destination for outputs. Keep this separate from the overall preference of approximately $10
- [ ] Include startup, downloading the models again, rebuilding, result export, and shutdown verification in both billed time and the estimate. An automatic hard cap is not implemented
- [ ] Check the RunPod account, payment method, deposit amount, refund conditions, and whether automatic top-up is enabled. Obtain separate approval if a new agreement or new access must be created
- [ ] Record the original source SHA, Notebook blob, and model file names and hashes
- [ ] Finish preparing the RunPod entry point and dependency lock, and verify CPU tests
- [ ] Check Secure Cloud On-Demand, one GPU, the actual price, data center, RAM, and disk type/capacity
- [ ] The original plan's initial standard was one A100 80GB, Pod Volume80GB plus Container20GB, and a maximum of 2 hours at an estimated $3.21 (excluding taxes and similar charges; not actual results or a finalized invoice amount). Recheck prices in the next plan. Do not create a Network Volume
- [ ] Only if a future new plan explicitly selects a Network Volume, separately check retention cost, location, availability, and the deletion procedure
- [ ] Prepare an experiment resource ledger, excluding existing Pods, Volumes, and shared resources from deletion
- [ ] Enable authentication for Jupyter and Gradio. Enter secrets through an appropriate secret-management mechanism
- [ ] Prepare non-sensitive test prompts, an original image, and a short original audio recording in advance
- [ ] Do not spend GPU time planning the work. Keep a timer and the shutdown procedure ready

## Immediately after startup

- [ ] Record the Pod ID, startup time, price, and template/image digest. Do not record URLs or screenshots containing secrets
- [ ] Record the type, ID, name, and relationships of Pods/Volumes and other resources created for this experiment in the ledger. Do not confuse them with existing resources
- [ ] Use `nvidia-smi` to check the GPU name, VRAM, and driver. If the GPU differs from the plan, decide whether to stop before downloading models
- [ ] Verify that the CUDA toolkit, build tools, and Python are available and that `/workspace` is the intended storage area
- [ ] Ensure free space of approximately the total size of models not yet downloaded plus 20GB, and avoid duplicate copies at the download destination
- [ ] Verify that conversations, history, and attachments in Jupyter/Gradio cannot be viewed while logged out. Confirm that llama-server8012 is not exposed externally
- [ ] Download the two model files at fixed revisions and verify SHA256. Do not download the entire HF repositories

## Functionality and measurements

- [ ] Obtain an initial response to a short prompt at 8k with search Off and thinking Off
- [ ] Run the same 10 Japanese prompts and record time to first token, completion time, and output token count
- [ ] Verify that the known contents of one image can be described correctly and that the original image remains in history and can be displayed again
- [ ] Record a separate test at 32k. Do not hide OOM, automatic fallback, or CPU offload
- [ ] Check errors and memory growth with multiple images, a longer conversation, and 10 consecutive turns
- [ ] Verify search On with a non-sensitive query, including citations and handling of retrieval failures
- [ ] Verify CPU ASR with 10–20 seconds of original Japanese audio. Use the microphone over HTTPS with the user's permission
- [ ] Verify that Stop, Regenerate, Release GPU model, reload, and rerunning the launch cell do not create duplicate processes, duplicate VRAM allocations, or port conflicts
- [ ] Verify that history is restored after synchronization and an app restart within this trial. Carrying the Pod Volume into the next trial is not the default
- [ ] Treat recreation testing as the next new plan. Create a new environment from the saved fixed SHA, hashes, dependency lock, and procedures. Do not claim successful reproduction if it has not been tested
- [ ] Record GPU billing time from startup to confirmed Stop/Terminate, not only model inference time

## Pass/fail decision

Mandatory conditions are no authentication gaps or secret leaks, successful chat and image understanding with the intended model, restoration of history, and the ability to stop GPU billing through the stop operation. Do not move into continued operation with unresolved fatal errors or OOM.

Provisional usability thresholds are a median time to first token of at most 5 seconds and a median completion time of at most 30 seconds for up to 256 output tokens, across 10 warm short-prompt tests with search Off and thinking Off. These are proposed criteria for judging usability, not performance predictions or guarantees. They may be adjusted before execution. Evaluate images, search, ASR, and cold start separately.

Adopt A40 only if it meets the mandatory conditions under the same settings, response satisfaction is acceptable, and the full cost from startup to shutdown is lower than A100. A lower hourly rate alone is insufficient.

## Failures and rollback

- Budget/time limit reached, missing authentication, or secret leak: interrupt new processing, export necessary data, and decide whether to stop the Pod. Stop also discards the Container Disk, so obtain immediate pre-action confirmation with the exact target and impact for an operation that causes irreversible loss. Do not continue using the system before correcting exposure settings
- OOM: return to concurrency 1, one image, and 8k, and record the reduction. If it recurs on A40, consider switching to A100 under the next new plan. Do not immediately launch an additional GPU or switch to Q4 without approval
- Startup failure/dependency mismatch: return to the fixed SHA and image/lock. Do not leave the GPU running while indiscriminately upgrading libraries
- Search service outage: model-only tests may continue, but do not mark search as passed
- Volume I/O/external export failure: do not Terminate. Save recoverable data and check what will be lost on stop and how to recover it. Do not mark pending deletion confirmation or unexported data as complete; record the remaining charges
- Insufficient availability: do not purchase another GPU without approval. Postpone or revise the estimate, accounting for the Volume's data-center constraints
- Returning to the original Colab environment: the original Notebook and repository were not modified, so use the existing procedure. Separately reconcile remaining RunPod Volumes and charges

## Save results and clean up after every trial

1. [ ] Stop new submissions and confirm that running work has completed or been canceled
2. [ ] Write a report covering successes, failures, unperformed items, measurements, costs, and the next decision against the trial plan
3. [ ] Save the source SHA, Notebook commit, model revision/hash, image digest, dependency lock, non-secret settings, build/reproduction procedures, and necessary test inputs. Exclude secrets, authenticated URLs, and full raw conversations
4. [ ] Save the report and necessary results and reproduction information outside the Pod and Volume to be deleted. A copy only inside the Pod does not count as completion
5. [ ] Actually open the files at the external destination link/commit and verify contents, file count, sizes, hashes, and similar checks. Even if a repeat experiment in another environment remains unperformed, verify that the materials are complete and readable
6. [ ] Treat the history DB and attachments as deletion targets after extracting necessary results into the report. If retaining originals as an exception, first confirm the destination, content, and access scope
7. [ ] Reconcile the experiment resource ledger with actual resources and list the exact type, ID, name, dependencies, and data that will be lost for each deletion target. Exclude existing/shared resources, the account, and externally saved materials
8. [ ] Show the user the external storage verification results and target list, and obtain immediate pre-action confirmation before irreversible deletion. Agreement with the policy at planning time does not authorize automatic deletion
9. [ ] Terminate the confirmed disposable Pod. Its DB, attachments, models, cache, build artifacts, and Container/Pod Volume will be lost. If temporarily deferring with Stop, account for Container loss and obtain the necessary confirmation
10. [ ] If independent Volumes or other resources were created for this trial as an exception, confirm each target separately before deleting it. Do not delete existing/shared resources or the entire account together
11. [ ] If temporary access was created specifically for this experiment, remove it within the approved scope without affecting shared keys. The initial standard does not pass the account API key to the Notebook
12. [ ] Reconcile Pods, Storage, and Billing against the ledger. Verify the deleted state, the end of GPU billing, and that no storage billing resources remain for this experiment. Add target IDs and verification times to the report
13. [ ] Reconcile actual session costs against Billing. If posting is delayed, use provisional amounts and identify unfinalized charges. If deletion confirmation is pending, record remaining resources and charges and do not state that cleanup is complete

## Before the next test

- [ ] Read the previous report and write the next hypothesis, changes, and pass/fail criteria in a new plan
- [ ] Re-estimate including cold start, model downloads, dependency installation, building, export, and cleanup time
- [ ] Do not implicitly carry over the previous environment or launch additional resources. Obtain paid-use approval for the new plan before experimenting
- [ ] Deleting everything after every trial is not necessarily cheapest for closely spaced trials. If changing the standard non-retention policy, compare it with short-term retention costs in the new plan
