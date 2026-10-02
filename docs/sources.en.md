# Primary sources and verification scope

English | [日本語](sources.ja.md)

Date of the verification record: 2026-10-02 UTC. The original planning research used read-only access through the connected GitHub service, Web research for public materials, and the cloud browser for the Gemini conversation and displayed Colab prices. This document preserves that research record and updates its verification scope to reflect the trial on the same day. Prices, specifications, and terms may change; recheck them on the execution date.

## Trial status and public scope on 2026-10-02

The actual trial used two Pods. GitHub connectivity failed in `US-MD-1`; after a restart in `EUR-IS-1`, selftest, net, the isolated venv, and artifact reconstruction and SHA256 verification succeeded. CUDA compilation, downloading and hash-verifying the model bytes, model loading, and inference were not performed. Both Pods are stopped but each retains an 80GB Pod Volume at approximately $0.022/hour, approximately $1.07/day combined. Cleanup is incomplete. Approximately $1.71 in GPU charges is an estimate, not a finalized billed amount. See the [trial report](trial-report-2026-10-02.en.md) for details. Historical plans and budget approvals do not authorize another launch or deletion; the next trial requires a new plan and the necessary approvals.

This repository is public, and `main` also contains the implementation merged after the trial; for the code that actually ran, refer to [immutable commit `69795d54490ffaeff62f18ca80f6181ea674bc16`](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16). The public documentation excludes account-specific balances, payment information, private URLs, and actual Pod IDs.

## Upstream project

- S1 [Q8 chat Notebook at the pinned commit](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/Qwen-Q8-Chat-Colab.ipynb): Old branch name, fixed Colab paths, models, share settings, and startup/shutdown handling
- S2 [Chat-only requirements](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/requirements-chat-colab.txt), [core requirements](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/requirements.txt): Dependency scope
- S3 [colab.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/colab.py), [config.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/config.py), [llama_server.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/backends/llama_server.py), [gpu_manager.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/gpu_manager.py), [ui_chat.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/ui_chat.py), [bench.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/bench.py): Basis for the portability review and measurement caveats
- S4 [Q8-first roadmap](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/docs/chat-only-roadmap.md), [PR35](https://github.com/moruku36/qwen-multimodal-colab/pull/35): Retain Q8, compare BF16 later, and GPU validation not yet performed. The PR reports CPU CI with 287 passed/2 skipped; those tests were not rerun as part of this plan
- S5 [VRAM measurements](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/docs/vram-measurements.md): Existing values are from Q4; Q8 is unmeasured

## Model distribution sources

- S6 [Huihui model card](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF), [target Q8 file](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/blob/main/Huihui-Qwen3.8-27B-abliterated-UD-DW-Q8_K_L.gguf), [file-change commit](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/commit/ff733b88376282017b7f4675d6d96536c6ffa712): 27.3GB, published SHA256, and displayed Apache-2.0 license. Model bytes have not been downloaded and hash-verified. Successful connectivity to the distribution endpoint does not prove a completed download
- S7 [ggml-org model card](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF), [target mmproj](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF/blob/main/mmproj-Qwen3.8-27B-Q8_0.gguf), [file-change commit](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF/commit/97c30c65c8d9a3e73f9fdfb50f1d1a669e9a2827): 629MB and a published hash. The raw pointer confirmed 629,247,008 bytes. The mmproj bytes have not been downloaded and hash-verified

## Official RunPod sources

- S8 [GPU Pricing](https://www.runpod.io/pricing), [AI server cost guide](https://www.runpod.io/articles/guides/ai-server-cost): Listed prices displayed as updated on 2026-09-27. The latter confirmed the Secure Cloud classification at the same prices. Public prices recorded during planning are not a guarantee of live availability or the quote at launch. See the trial report for actual launches and the hourly rate on the same day
- S9 [Pod pricing](https://docs.runpod.io/pods/pricing), [Billing](https://docs.runpod.io/accounts-billing/billing): Running charges, prepaid credit, non-refundability, automatic replenishment, and insufficient balances
- S10 [Storage options](https://docs.runpod.io/pods/storage/types): Container/Volume pricing, persistence, and differences in encryption features
- S11 [Network volumes](https://docs.runpod.io/storage/network-volumes): Pricing, independent persistence, and data-center constraints
- S12 [Manage Pods](https://docs.runpod.io/pods/manage-pods), [Zero GPU troubleshooting](https://docs.runpod.io/pods/troubleshooting/zero-gpus): Stop, Terminate, restart, and GPU shortages
- S13 [Connect to a Pod](https://docs.runpod.io/pods/connect-to-a-pod): Jupyter, authentication, and connection methods
- S14 [Expose ports](https://docs.runpod.io/pods/configuration/expose-ports), [SSH](https://docs.runpod.io/pods/configuration/use-ssh): HTTPS proxy and exposure scope, timeouts, and SSH requirements
- S15 [Templates overview](https://docs.runpod.io/pods/templates/overview): Differences between official and community templates. The exact image digest had not been selected in the first draft, but it was recorded from the trial's system logs. See the trial report for the actual digest and tag. Recording a digest does not establish successful CUDA compilation or inference
- S16 [Serverless pricing](https://docs.runpod.io/serverless/pricing): Startup, execution, and idle-timeout charges. Do not mix these with the Pods price table

## Colab and the starting point for the investigation

- S17 [Colab FAQ](https://research.google.com/colaboratory/faq.html): GPU and usage limits vary; resources are not guaranteed
- S18 [Colab signup](https://colab.research.google.com/signup): The research browser displayed GBP. Japanese subscription prices and GPU-specific CU consumption were not verified and were not used for a direct price comparison
- S19 [RunPod terms of service](https://www.runpod.io/legal/terms-of-service): Displayed as updated on 2026-03-24. Usage conditions and suspension rights apply; do not describe the service as free of terms
- User-shared Gemini conversation (non-public planning background; conversation URL omitted from the public version): Read in full as context for the investigation. It includes 72B/Q4 estimates, expectations for RunPod, and ideas for automatic shutdown. AI answers were not adopted as primary evidence of pricing, performance, or contractual terms

## Explicitly unverified items

- CUDA compilation, downloading and hash-verifying model bytes, model loading, inference, speed, VRAM, warm/cold start for model execution, and model quality. Do not confuse successful Pod startup, selftest, net, or venv with verification of these items
- Successful use of this Q8 model at 32k context on A40/L40S 48GB
- The user's Japanese Colab pricing and current A100 CU consumption rate
- Final invoiced charges and prices/regional availability for the next launch. Volume charges remain after stopping, and confirmation that no billable resources remain after deletion is incomplete
- End-to-end operation of the ported Notebook and GPU path. The image digest was recorded, and hash-verified dependency installation and `pip check` in the isolated venv succeeded, but the `source`, `build`, and `trial` stages were not run. Do not treat automatic shutdown as verified either

Estimates or plans do not turn these items into verified operation, completed migration, or completed cleanup.
