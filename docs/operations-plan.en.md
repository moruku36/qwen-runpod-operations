# Operations plan for trying Qwen Q8 on RunPod

English | [日本語](operations-plan.ja.md)

## Status as of 2026-10-02

This document preserves the **historical operations plan** written before the first trial, without omitting its detailed considerations. References below to the “first trial,” recommendations, and stages describe that original plan. Budget preferences and any past paid-use approval are historical; they do not authorize another launch, restart, or deletion. The next trial requires a new plan and the necessary approvals.

Two Pods were created on 2026-10-02. GitHub network connectivity failed in `US-MD-1`. After a restart in `EUR-IS-1`, selftest, net, the isolated venv, and artifact reconstruction and SHA256 verification succeeded. CUDA compilation, downloading and hash-verifying the model bytes, model loading, and inference were not performed; migration is not complete. Both Pods are stopped, but each retains an 80GB Pod Volume at approximately $0.022/hour, approximately $1.07/day combined. Cleanup is incomplete. Approximately $1.71 in GPU charges is an estimate from elapsed time and the hourly rate, not an invoiced total. See the [trial report](trial-report-2026-10-02.en.md) for details.

This repository is **public**, and `main` also contains the implementation merged after the trial; for the code that actually ran, refer to [immutable commit `69795d54490ffaeff62f18ca80f6181ea674bc16`](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16). Account-specific balances, payment information, private URLs, and actual Pod IDs are excluded from the public documentation. Citation numbers refer to the [primary sources](sources.en.md).

## Purpose and scope

Evaluate the existing Qwen chat environment as an option that can be used without waiting to replenish Colab compute units. Assess the effort required to start and shut down, the experience of Japanese chat and image understanding, and the actual cost of one working session. Begin with personal, single-user use and non-sensitive test data.

The baseline is the Q8 chat Notebook at `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`. The 72B/Q4, vLLM, and Open WebUI setup mentioned in the Gemini conversation is a different configuration; do not reuse its speed or cost estimates. BF16 is a candidate for a later comparison. Do not change either the model or the quantization method in the first trial. [S1–S5]

## Standard experiment cycle

1. **Plan:** Assign a trial ID and create a new plan that records the purpose, hypothesis, comparison conditions, pass/fail criteria, runtime, estimate, destination for results, and scope of resources to delete. State what changes from the previous trial and its results
2. **Run:** Create only the resources needed after paid use under that plan is approved. At creation, record each resource's type, ID, name, relationships, and whether it existed before the trial in a resource ledger
3. **Save and verify:** Save the report, measurements, non-secret logs, fixed source/model SHAs and hashes, environment/dependency locks, and reproduction instructions outside the Pod and Volumes to be deleted. By default, retain only non-sensitive materials suitable for public release in this public GitHub repository; exclude secrets, raw conversations, and account-specific information. If size, sensitivity, or another constraint requires a different destination, agree on the recipient, content, and access scope first. A copy inside the Pod does not count as completed preservation
4. **Confirm deletion:** Actually open the files at the external destination and confirm that the necessary results and reproduction information are present, using file counts, sizes, hashes, or similar checks to detect omissions. Then list only the resources created by this trial. For irreversible deletion, show the exact IDs, data that will be lost, and impact, and obtain the user's confirmation immediately before acting
5. **Clean up:** Delete only the confirmed targets and leave no disposable environment behind, including history databases, attachments, models, caches, and build outputs. Reconcile Pods, Storage, Billing, and the ledger to confirm that no billable experiment resources remain. Unverified or pending cleanup is not completed cleanup
6. **Next trial:** Start with a new plan rather than implicitly continuing the previous environment. Include cold start, model re-downloads, and build time in every new estimate

“Delete everything” applies only to components created for that experiment. Existing accounts, other Pods/Volumes, shared resources, this GitHub repository, and saved reports are excluded. Avoid unnecessarily creating API keys, Network Volumes, or standalone templates/images for the first trial. If an exception requires one, add it to the ledger and distinguish it from shared or pre-existing resources.

Preserving the history database or full raw conversations is not itself a goal. After verifying pass/fail criteria, treat them as deletion targets and retain the non-sensitive test inputs and anonymized results needed for reproduction in the report. If a database or attachment must exceptionally be retained, first confirm the destination, content, and access scope.

When the first draft was written, only planning documents existed and no RunPod resources had been created. For the actual trial and retained resources, see the 2026-10-02 update above and the trial report. Agreement with the plan is separate from authorization to deposit funds, launch paid resources, or irreversibly delete data. This document does not enable automatic execution or an automatic hard spending cap.

## Recommended configuration

| Item | First-trial proposal | Rationale and caveats |
| --- | --- | --- |
| Service | RunPod Pods / Secure Cloud / On-Demand | Supports step-by-step checks in a Notebook. Avoid interruptible Spot instances for the first trial |
| GPU | A100 80GB × 1 | The original Notebook's target. Q8 measurements are not yet available |
| Alternative comparison | A40 48GB × 1 | Less expensive. There is nominal capacity beyond the approximately 28GB of weights and projector, but speed and headroom for long contexts and images need testing |
| Template | Choose an official RunPod PyTorch/CUDA development image at launch | Verify Python 3.10 or later, Jupyter, nvcc, CMake/Ninja, and a C++ build environment. Record the image digest as well as the tag |
| Storage during the trial | Pod Volume 80GB, `/workspace` | Retain models, build outputs, history mirrors, and experiment records only during that trial. Terminate deletes it, so save and verify results externally first |
| Container Disk | Plan for 20GB and adjust to the selected template's requirements | Temporary OS-side storage. Do not treat it as a destination that survives stopping or recreating the Pod |
| CPU / RAM | Planning guideline of 8 vCPU and at least 64GB RAM | Provides room for builds and CPU ASR. This is not a guaranteed minimum requirement; record the actual allocation and peak RSS |
| Entry point | Authenticated Jupyter and Gradio | `SHARE=False`. Do not publish unauthenticated URLs |
| Inference | llama.cpp in the same Pod | Use the existing GGUF/mmproj unchanged; do not combine this with a vLLM migration |
| First-trial duration | Attended session of at most 2 hours | Includes all setup, downloads, checks, and shutdown. Approve the spending limit before launch |

**The standard is a disposable Pod Volume for each trial.** Save and verify results externally, obtain target-specific deletion confirmation, and then Terminate the Pod. Retaining an 80GB Network Volume for 7 days remains only a nonstandard option for a future comparison. For several trials close together, compare storage retention costs with the GPU cost of repeated downloads and builds; do not create it until it is explicitly selected in a new plan. [S8–S12]

## GPU candidates and capacity assessment

| GPU | Nominal VRAM | Listed rate USD/hour | Treatment in this plan |
| --- | ---: | ---: | --- |
| A100 PCIe or SXM | 80GB | 1.59 | Baseline candidate for the first trial. Record PCIe/SXM; do not assume identical performance |
| A40 | 48GB | 0.49 | Lower-cost candidate. Decide whether to use it after verifying Q8 chat-only operation |
| L40S | 48GB | 1.09 | Candidate for a speed comparison with A40. Speed remains unknown until measured |
| RTX 4090 | 24GB | 0.74 | Not selected for keeping this Q8 model fully resident on the GPU |
| A100 | 40GB | Outside this estimate | Chat-only may be physically feasible, but it is unsupported in the upstream README. Not a first-trial candidate |

These are public Secure Cloud list prices checked on 2026-10-02, not a guaranteed quote or availability. [S8]

The target GGUF is approximately 27.3GB, and mmproj is 629,247,008 bytes, approximately 0.629GB. Their approximately 27.9GB total is disk size, not runtime VRAM. Context, image tokens, work buffers, and other allocations add to VRAM use. Do not select a 24GB card on price alone. A40/L40S 48GB are candidates for validation, not configurations guaranteed to work at 32k context. [S6–S7]

Limit the first startup check to 8k context and at most one image, then increase progressively to the original 32k setting after success. Save 8k and 32k as separate measurements; do not combine them into one performance result. Do not apply the old Q4 measurement of approximately 22GB, or a “fixed VRAM saving” from excluding image generation, to Q8. [S4–S5]

## Storage design

80GB is a planned capacity, not a measured requirement. It allows approximately 28GB for weights, 10–20GB for the Python environment, ASR, and builds, several GB for logs, tests, and history, plus headroom during downloads and for future use. Do not download entire model repositories. Download only the specified GGUF and mmproj files. Do not retain duplicate model copies in the HF cache and manual copies. Before the initial download, aim for free space of at least the total size of the missing models plus 20GB; record actual use after downloading the weights as well.

| Location | Contents | Loss and preservation treatment |
| --- | --- | --- |
| `/workspace/qwen/source` | Source pinned to a SHA | Can be fetched again, but do not leave unsaved changes behind |
| `/workspace/qwen/hf-cache` | The two specified model files and ASR cache | Reused only within that Pod Volume. Delete after the trial and budget download time again for the next trial |
| `/workspace/qwen/llama-bin` | Build outputs for each CUDA architecture | Delete after the trial. Save the commit, CUDA version, GPU architecture, image digest, and build steps in an external report |
| `/workspace/qwen/data` | History database mirror and attachments | Excluded from Git. Use only non-sensitive tests; report the necessary results, then delete as a confirmed target |
| `/tmp/qmc/history.db` | Local working SQLite database during execution | Verify mirror synchronization and readback before shutdown. Do not treat it as already persisted |
| `/workspace/qwen/runs` | Measurements, dependency versions, and shutdown verification | Temporary storage. Save results excluding secrets and raw conversations to an external repository and verify readback before deleting the Pod |

In the official storage settings, selectable encryption at rest is available for Pod Volumes; the same feature is not available for Network Volumes. Do not treat a Network Volume as a confidential vault. If personal or business confidential data is needed, separately design encryption and storage. [S10]

Do not introduce direct SQLite operation on a network filesystem in the first trial. Keep the existing local database plus mirror arrangement, and limit testing to non-sensitive data for which losing changes since the last synchronization after a forced shutdown is acceptable. [S3]

## Cost estimates from the historical plan

The following planned amounts are in USD and exclude taxes, card fees, exchange-rate differences, and external search API fees. Monthly storage is prorated using 720 hours per month for comparison. This is not guaranteed to match the provider's monthly conversion or rounding exactly.

Published rates: Container Disk $0.10/GB/month while running; Pod Volume $0.10/GB/month while running and $0.20/GB/month while stopped; standard Network Volume $0.07/GB/month below 1TB. Other tiers, such as high-performance storage, are not included here. [S9–S12]

Formula for the standard Pod Volume option:

`Estimate = GPU rate × allocation hours + Container GB × 0.10 × running hours/720 + Pod Volume GB × (0.10 × running hours + 0.20 × stopped hours)/720`

For 2 hours on A100 followed by cleanup with 0 hours of stopped retention: `1.59 × 2 + 20 × 0.10 × 2/720 + 80 × 0.10 × 2/720 = 3.2078 USD ≈ $3.21`.

For the nonstandard Network Volume option, remove the Pod Volume term and add `Network GB × 0.07 × retention hours/720`. If the Pod is stopped while awaiting deletion confirmation, Pod Volume retention charges also accrue as applicable.

| Example | GPU cost | Disks and other storage | Estimated total |
| --- | ---: | ---: | ---: |
| **Standard: first A100 trial, 2 hours, Pod Volume 80GB + Container 20GB, Terminate immediately after verifying preservation and confirming deletion** | $3.18 | Approximately $0.03 | **Approximately $3.21** |
| A40 in a new next-trial plan, 2 hours, newly create and then clean up a Pod Volume + Container of the same sizes | $0.98 | Approximately $0.03 | Approximately $1.01. The 2 hours include rebuilding and downloading |
| Nonstandard comparison: A100, 2 hours, retain 80GB Network for 7 days, Container 20GB | $3.18 | $1.31 + less than $0.01 | Approximately $4.49 |
| Nonstandard comparison: A40 for 20 hours/month, retain 80GB Network for 1 month | $9.80 | $5.60 + approximately $0.06 | Approximately $15.46/month |
| Nonstandard comparison: A100 for 20 hours/month, retain 80GB Network for 1 month | $31.80 | $5.60 + approximately $0.06 | Approximately $37.46/month |
| Leave Network 80GB for 1 month without GPU use | $0 | $5.60 | $5.60/month |
| Leave a stopped Pod with Pod Volume 80GB for 1 month without GPU use | $0 | $16.00 | $16.00/month |
| Forget to stop A100 for 24 hours | $38.16 | Additional disk charges | More than $38.16 |

The original budget preference was approximately $10 overall, so the first-trial baseline was at most 2 hours and an estimated $3.21. These are historical planning figures, not the actual cost of a later trial or authorization for future spending. Approximately $10 is neither spending approval nor an automatic hard cap. Before launch, finalize and approve each trial's total limit, including taxes, fees, and costs incurred while verifying preservation and awaiting deletion, as well as the deposit amount and execution date. Do not automatically add another trial based only on remaining funds or rates. Prepaid deposits are separate from service consumption. The official Billing guide describes deposits starting at $10 and says credits cannot be refunded or withdrawn. Check the minimum deposit, refund terms, and whether automatic replenishment is enabled on the payment page before approving a payment. Do not use a zero balance as a shutdown mechanism; it may cause stopping and data loss.

RunPod Pods are billable while the GPU is running, including startup, waiting for answers, entering input, builds, downloads, and result retrieval. Disposable operation requires repeating these steps in the next trial, so the 2 hours are not inference-only time. Multiplying one answer's generation time by the hourly rate does not give the actual session cost. Serverless also includes initialization, idle timeout, and other billable time; do not describe it as charging only for a few seconds of inference. [S9,S16]

## How to compare with Colab

- RunPod advantages: Choose the GPU, disk, and image, making it easier to recreate an environment with pinned settings. The standard workflow aims to leave no experimental storage costs between uses
- Disposable-operation tradeoff: Every startup, model download, and build consumes GPU time and money. For consecutive trials, this is not necessarily cheaper than short-term retention. Update the next plan's estimate with the previous trial's measurements
- RunPod overhead: Initial porting, authentication and port management, storage costs, start/stop verification, and GPU availability must all be handled. It is not as simple as opening the original Notebook unchanged
- Colab advantages: The existing Notebook, Secrets, and Drive integration are convenient. Already-included unused compute units may reduce additional spending
- Colab constraints: GPU types, allocation, and usage limits vary, and purchased compute units do not guarantee GPU availability. This plan alone does not change the existing roadmap's verification schedule starting October 23

For a fair comparison, use the same model, hashes, llama.cpp, inputs, context, and output limits, and measure the amount billed from startup through safe shutdown. Calculate effective Colab cost as “price per CU at purchase × actual CUs consumed by that session.” Also state how any existing subscription cost is allocated.

Colab signup prices vary by region and currency. The research browser displayed GBP, so do not reuse those prices as current Japanese-account or USD prices. The Gemini conversation's “10–15 CU/hour on A100” and “$9.99 for 100 CU” are not measurements or quotes for this account. [S17–S18]

## Access and secrets

1. For the first trial, connect to authenticated Jupyter through the RunPod console. Give Gradio its own authentication and verify that conversations, history, files, and APIs are inaccessible when logged out
2. The HTTP proxy provides an HTTPS route; do not equate that with automatically authenticating anyone who knows the URL. Do not expose unauthenticated ports 7860/8888
3. Keep llama-server at `127.0.0.1:8012` with a per-start API key, as in the existing setup, and do not expose it externally. External API integration is a later step
4. Use `SHARE=False` and `QMC_SHARE=false`. Do not use public tunnels such as `gradio.live` in the first trial. If choosing SSH, follow the console's public-key and official connection instructions and minimize exposed ports
5. Do not pass a GitHub token or RunPod account API key to the first Notebook. They are unnecessary because the public upstream source can be fetched at a fixed SHA
6. Do not add HF_TOKEN if the HF models can be downloaded publicly. Only if needed, confirm its purpose and permissions and place it in a secret store such as RunPod Secrets. Never leave secret values in Notebooks, logs, `.env.example`, GitHub, or screenshots
7. Keep Web search Off for the initial local-model check, then test with non-sensitive search queries. Search sends queries to a third party. Keep TTS Off and use self-recorded audio for CPU ASR

The existing `AppConfig.public_dict()` excludes `auth_password` and the remote API key, but does not exclude the entire `github_token` value. Do not use that dictionary or a full environment-variable dump for experiment metadata. Record only an allowlist of GPU information, versions, and non-secret settings. [S3,S13–S15]

## Terms and models

The upstream code uses MIT. The public model pages for the target Huihui GGUF and ggml-org projector displayed Apache-2.0. When using them, also preserve LICENSE/NOTICE at the pinned revision and the terms of their upstream models, and check attribution obligations before redistribution. This repository does not include model weights. [S6–S7]

RunPod's terms, the law, and third-party rights still apply to an abliterated model. Do not adopt the Gemini descriptions of “complete freedom,” “no terms,” or “no intervention.” The actual terms specify prohibited uses and content, suspension rights, and other conditions. A human should review output from models with weaker safety filtering. Do not promise the complete privacy of privately owned GPU hardware. [S19]

Do not download or register the image-generation model Qwen-Image-2.1 in this trial. If adding it later, separately recheck its non-commercial research terms. Publishing an API, offering a commercial service, or sharing with outside users is outside this scope.

## Distinguishing shutdown actions and billing

| Action | GPU billing | Data treatment |
| --- | --- | --- |
| Close the browser, use UI Stop, or Release GPU model | Does not stop | The Pod may continue running |
| Call `colab.shutdown_runtime()` on RunPod | Does not stop | Only stops the application; it contains no operation to stop RunPod |
| Stop in the RunPod console | Stops compute charges | Container Disk is discarded; Pod Volume or Network Volume remains and storage charges continue. Save any necessary Container data externally first, and confirm the targets before an action involving irreversible loss |
| Terminate in the RunPod console | Ends that Pod's compute resources | Container/Pod Volume is lost. After verifying external preservation, show the Pod ID and data to be lost and obtain deletion confirmation immediately before acting. An independent Network Volume remains and continues accruing charges |
| Also delete the independent Network Volume | Ends its storage charges | Not created in the standard workflow. If exceptionally created for this experiment, verify external preservation and obtain target-specific deletion confirmation immediately before acting |

The official guide at the time of review describes both Stop and Terminate for Pods with Network Volumes. Do not rely on the old claim that a Pod with a Network Volume cannot be stopped. Verify the result in the actual console, Pod status, and billing view. A stopped Pod may be unable to restart if the same GPU is unavailable. A Network Volume may allow recreation on a compatible GPU in the same data center, but availability is not guaranteed. [S10–S12]

## Order for adding automatic shutdown

Begin with an attended session, track the end time using a separate clock, and stop through the console. A lone `time.sleep(300)` at the end of the Notebook cannot handle intermediate exceptions, kernel termination, browser disconnection, or resumed activity. Deleting data after five minutes is also dangerous.

If automating, design it in a later implementation stage:

- Separate the session's maximum elapsed time from idle time after the last completed operation
- Defer idle shutdown during inference, writes, or resumed use
- As a rule, first save and verify required data outside the disposable environment. Even Stop can discard Container data. Before irreversible actions such as Terminate or Volume deletion, show the target IDs and data to be lost, and obtain the user's confirmation immediately before acting. Do not perform automatic deletion based on blanket agreement at planning time
- Consider monitoring that still works if the Notebook process dies. Verify shutdown API success and Pod state externally
- Decide the management API key's permissions, storage location, and revocation method, and separately approve creation of any required access. Do not hard-code a full-permission account key in a Notebook
- Do not make automatic shutdown an acceptance criterion for the first trial; first establish reliable manual shutdown

## Stages and decisions

1. Planning: The stage covered by the first draft. Check sources, models, costs, and shutdown procedures
2. No-charge porting preparation: Create the new Notebook entry point, paths, authentication, dependency locks, and chat-only measurements, and run CPU tests. Changes to the upstream repository are a separate matter
3. First A100 trial: Finalize the trial plan, total spending limit, external destination for results, and execution date, then launch after paid use is approved. Record text, images, search, ASR, history, restart behavior, and billing after stopping
4. Report and cleanup: Save results and reproduction information externally, verify readback, and reconcile the experiment resource ledger with deletion targets. Clean up after deletion confirmation immediately before execution, and check for remaining billable resources
5. Next plan: Write a new plan using the previous results and actual costs. If comparing A40, treat it as a separate trial under the same model and conditions, and estimate its budget and environment-rebuild time. Launch additional resources only after the new plan is approved
6. Continuation decision: Use session cost, operator time, and satisfaction with answers to decide whether to continue Colab or use RunPod alongside it. For sporadic use a few questions at a time, measure cold start before considering Serverless as a separate project

Use the [checklist](session-checklist.en.md) for GPU-test completion criteria and failure handling. Do not fill in unmeasured values as successes.
