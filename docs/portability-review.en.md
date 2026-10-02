# Notebook RunPod portability review

English | [日本語](portability-review.ja.md)

The reviewed upstream commit is [`9e5ff82cf604dd3b0377de81b89e1f4aaa422947`](https://github.com/moruku36/qwen-multimodal-colab/tree/9e5ff82cf604dd3b0377de81b89e1f4aaa422947). The change specifications below were written before implementation and are retained as historical context. They do not establish that each item has been implemented or verified on actual hardware.

The subsequent implementation is preserved separately at immutable commit [`69795d54490ffaeff62f18ca80f6181ea674bc16`](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16). `main` of this public repository also contains the implementation, merged after the trial; references use links to the fixed commit that was executed. References to “current” or “upstream” below mean the upstream commit inspected for this review. Distinguish actual RunPod results using the status section and trial report below.

## Trial status as of 2026-10-02

The actual trial used two Pods. Network checks failed in US-MD-1. In EUR-IS-1, selftest, network checks, venv preparation, and external artifact export and read-back verification succeeded. CUDA, model download, model loading, and inference were not tested. The approximately $1.71 GPU cost is an estimate, not a finalized invoice amount.

Both Pods are stopped, but each retains an 80GB Pod Volume. Storage costs approximately $0.022/hour each, or approximately $1.07/day combined; cleanup is incomplete. This document does not authorize a new trial or deletion. See the [2026-10-02 trial report](trial-report-2026-10-02.en.md) for details. The next trial requires a new plan and the necessary approvals.

## Specifications to preserve

- Huihui Qwen3.8 27B, `Huihui-Qwen3.8-27B-abliterated-UD-DW-Q8_K_L.gguf`
- `mmproj-Qwen3.8-27B-Q8_0.gguf` from `ggml-org/Qwen3.8-27B-GGUF`
- llama.cpp commit `4da6337767f973e2b4d0797e5b323d77d8565e4a`
- Gradio chat-only UI, history, image understanding, web search, and CPU faster-whisper small
- No registration of image-generation backends, through `chat_only=True`
- One concurrent inference, thinking Off by default, and TTS Off. Treat the original context of 32,768 tokens as the final comparison condition, separately from startup smoke-test results at 8,192 tokens

## Cell-by-cell change specifications from the pre-implementation review

| Original location | Upstream behavior observed during review | RunPod change and verification specification |
| --- | --- | --- |
| Cell 1 | Clones/pulls with `BRANCH="feat/q8-chat-colab"`. The old branch name remains after the merge | Check out the upstream main commit above rather than following a branch tip. Verify the match with `git rev-parse HEAD`. Do not run pull automatically |
| Cell 1 | `/content/qwen-multimodal-colab`, `%cd` | Standardize on `/workspace/qwen/source`. Verify that the path is on the persistent volume |
| Cell 2 | Uses `requirements-chat-colab.txt` and Colab's torch/CUDA | Record Python/torch versions compatible with the official RunPod CUDA development template. Also check `nvcc --version`. Do not replace the GPU driver inside the Pod |
| Cell 2 | Adds `/content/.../src` to `sys.path` | Change to the checkout on RunPod. Verify that the Notebook kernel and pip use the same Python |
| Cell 2 | `colab.setup()` uses a Drive mount and Colab Secrets | Use the equivalent of `setup(use_drive=False)`. Secret loading should be a no-op outside Colab. Set necessary environment variables before startup |
| Cell 2 | The workdir/bin/cache of `install_llama_cpp()` default to `/content` | Move source, bin, and cache to `/workspace`. The workdir/local_bin arguments alone do not change the cache default; set it explicitly through a dedicated wrapper or equivalent |
| Cell 3 | `SHARE=False`, `PREFETCH=False`, search On | Keep sharing Off. Test the model alone with search Off first, then test search On. If using prefetch, limit it to chat=True,image=False |
| Cell 4 | `colab.launch(...profile="a100_80",chat_only=True)` | Use the corresponding profile for the initial A100 trial. Do not leave the A100 setting fixed when switching to A40 or another GPU. Select a profile automatically or prepare explicit comparison settings |
| Cell 4 | Colab-specific proxy URL correction | Verify Gradio streaming, attachments, and ASR through RunPod's HTTPS/port routing. Account for the 100-second proxy timeout; do not hold a long HTTP request open while waiting for model downloads or builds |
| Cell 5 | Only stops the app outside Colab | Distinguish “app shutdown” from “RunPod Stop/Terminate.” Do not display completion until Pod stop has been confirmed |

The Colab helper itself has branches that detect execution outside Colab; the absence of Drive alone does not make all functionality impossible. However, the original Notebook has fixed paths, so running it unchanged is not recommended. [S1 and S3](sources.en.md)

## Configuration locations

The following non-secret configuration proposal was prepared before implementation. This table alone does not establish that the settings were applied or verified in the implementation or on actual hardware.

| Setting | Proposal and caution |
| --- | --- |
| `QMC_DATA_DIR` | `/workspace/qwen/data` |
| `QMC_LOCAL_DB` | `/tmp/qmc/history.db`. Preserve the existing local DB plus mirror design |
| `HF_HOME` | `/workspace/qwen/hf-cache` |
| `QMC_LLAMA_BIN_DIR` | Equivalent to `/workspace/qwen/llama-bin/<commit>-<arch>-<runtime>` |
| `QMC_CHAT_CTX` | 8192 for startup verification, 32768 for comparison. Record changes |
| `QMC_ASR_DEVICE` / `QMC_ASR_MODEL` | `cpu` / `small` |
| `QMC_TTS` / `QMC_SHARE` | `false` / `false` |
| `QMC_AUTH_USER` / `QMC_AUTH_PASSWORD` | Dedicated login values. Never put actual values in Git |
| `QMC_CHAT_BASE_URL` | Unset. Use the local llama-server in the same Pod |

The reviewed `load_config` does not read `QMC_CHAT_ONLY`. Do not assume that setting a nonexistent environment variable enables chat-only mode; use an explicit argument such as `colab.launch(...chat_only=True)`.

## Dependencies and reproducibility

`requirements-chat-colab.txt` includes `requirements.txt` plus `huggingface_hub>=1.0`, `faster-whisper>=1,<2`, and `ipywidgets>=8,<9`. Core dependencies are Gradio6, Pillow, requests, numpy, ddgs, and pypdfium2, and they are not fully pinned. Do not install the image-generation `requirements-colab.txt` for the RunPod port. [S2](sources.en.md)

Checks identified as additional requirements before implementation; this is not a list of completed results:

1. Save the template name, image tag/digest, OS, Python, pip, torch, CUDA toolkit, host driver, and GPU compute capability
2. Verify llama.cpp initially at the SHA above. Use the GPU architecture detected on the actual machine; do not build A40 with A100's sm80 hardcoded. Do not copy an old CUDA binary to a different image/GPU without verification
3. After dependency resolution, save `pip freeze` and `pip check` and create a lock for recreation. Do not hide compatibility issues by simply upgrading to the latest versions
4. Record and verify the revision and SHA256 for both model files. The upstream `download_hf_file` has no revision argument, so a path must be added to download at a fixed revision and load those same verified files
5. Ensure reproduction from externally saved fixed SHAs, hashes, dependency locks, and procedures, rather than a hidden success dependent on `HF_HUB_OFFLINE` or preexisting caches. The default is to delete the Pod Volume after the trial. Recreation on a new Pod is part of the next new plan, with a budget and estimates for downloading and building again. Do not treat an unperformed reproduction test as successful

The model hashes in the implementation's fixed [`baseline.json`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/baseline.json) are values from public distribution pages, not hashes calculated after downloading the actual files. Recompute them from the downloaded bytes at execution time and verify that they match.

## Measurement cautions

Upstream `python -m qmc.bench` also loads the image backend and runs image generation and editing. Do not run it unchanged as a chat-only measurement command. For this trial, create procedures or a dedicated harness limited to text, image understanding, search, ASR, and history. [S3](sources.en.md)

Also use `nvidia-smi` to measure GPU memory. Because llama-server is a separate process, Python's `torch.cuda.max_memory_allocated()` alone cannot capture its usage. Separate initial load, reload, short prompts, long prompts, one image, multiple images, and 10 consecutive turns.

Large models on a network volume can make I/O a factor in cold start. Do not claim that startup is guaranteed “within seconds” solely because the network is fast. Record initial model download, initial build, model load, and warm time to first token separately.

## Implementation completion criteria in the original specification

- CPU tests and Notebook JSON/syntax checks pass
- The original Notebook/model settings have not been changed unintentionally
- Gradio requires authentication, and llama-server is not exposed outside the Pod
- Image-generation backends are neither registered nor downloaded
- Secrets are neither displayed nor saved
- The user can verify data synchronization, Pod stop, and remaining storage charges

When this review was originally prepared, the review work did not modify code, install dependencies, or execute tests. The subsequent implementation and limited on-Pod checks are documented at the fixed commit and in the trial report above. The successful CPU test record from upstream PR35 is existing evidence; by itself, it does not demonstrate success on RunPod.
