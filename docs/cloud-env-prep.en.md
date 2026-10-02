# Cloud environment preparation results 2026-10-02

English | [日本語](cloud-env-prep.ja.md)

Final status: [Trial report for 2026-10-02](trial-report-2026-10-02.en.md)

**Documentation-only publication:** `main` contains documentation, without executable scripts or the notebook. Implementation descriptions and commands below require a separate checkout of the [immutable implementation snapshot](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16), commit `69795d54490ffaeff62f18ca80f6181ea674bc16`. Publishing these documents does not authorize a new paid run, restart, Terminate, or deletion. A fresh plan and authorization are required before another run.

**Final status on 2026-10-02:** The paid trial used two Pods. Outbound connectivity failed on `US-MD-1`; `selftest`, `net`, `venv`, and retrieval of a small artifact through logs succeeded on `EUR-IS-1`. CUDA compilation, downloading and hashing the complete model files, model loading, and inference were not performed. The final execution report recorded both Pods as `EXITED`. Compute and Container storage were confirmed as `Not running` in the console at each Pod's stop; the final shared screen around 16:05 JST shows only the second Pod. Each retained 80 GB Volume costs approximately $0.022/hour, approximately $1.07/day for both. Cleanup is pending and deletion is not authorized.

**This section records the preparation phase before the paid trial.** At that stage, no GPU or paid resources were in use, and RunPod API credentials, OAuth, and SSH had not been configured. The reproduction script is [`scripts/setup-cloud-env.sh`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/setup-cloud-env.sh). The later paid trial updated some of the uncertainties recorded at that time.

## Installed components

| Component | Result |
| --- | --- |
| runpodctl | v2.14.0, official linux-amd64 release. Verified against the official `checksums_2.14.0_sha256.txt` and installed in `/usr/local/bin` |
| Upstream repository | Checked out `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`. HEAD and notebook blob `5e40a89f0253356c9c8d72698a93508f4d30d8aa` matched [`baseline.json`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/baseline.json) |
| Python environment | A Python 3.11.15 venv with [`requirements-chat-colab.txt`](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/requirements-chat-colab.txt) and development dependencies, including gradio 6.29.0 and huggingface_hub 1.33.0, 86 packages in total. `pip check` passed |
| Existing tools | git / cmake 3.28 / gcc 13 / make / jq / uv / node 22 / go |

## Results of checks without a GPU

- Upstream CPU tests: 288 passed, 1 skipped
- [`Qwen-Q8-Chat-Colab.ipynb`](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/Qwen-Q8-Chat-Colab.ipynb): nbformat 4, five code cells, no syntax errors
- Upstream `ruff check`: seven findings, all automatically fixable. They were left unchanged because modifying upstream was outside this work

## Porting changes identified through code inspection

- [`src/qmc/colab.py`](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/colab.py): `/content/llama.cpp` and `/content/llama-bin` in `install_llama_cpp`; `/content/llama-bin-cache` in `llama_cache_dir`; the `/content/qmc-data` default for `QMC_DATA_DIR`
- [`src/qmc/config.py:149`](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/config.py#L149): selects the Colab storage location only when `/content` exists. Set `QMC_DATA_DIR` explicitly on RunPod
- [`src/qmc/backends/llama_server.py:36`](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/backends/llama_server.py#L36): search candidates include `/content/llama.cpp/...`. Setting `QMC_LLAMA_BIN_DIR` avoids this dependency
- Notebook Cell 1: following a branch and using `%cd` under `/content`

## Open issues at the preparation stage

1. **The environment's network allowlist blocked `api.runpod.io`** with CONNECT 403. The same applied to `huggingface.co` and access to llama.cpp on github.com. Running `runpodctl` or downloading models required network permission. The proposed options were to permit api.runpod.io and huggingface.co, plus any required cdn-lfs hosts, in the environment's Network access settings, or broaden the access level
2. No RunPod API key had been configured; `apikey` in `~/.runpod/config.toml` was empty. The proposed secure route was an environment secret named `RUNPOD_API_KEY`, never a chat message or Git commit
3. No SSH key had been registered. Registration, if needed, required the user's authorization
4. This environment had no CUDA/nvcc, so llama.cpp CUDA compilation, model loading, and GPU measurements could only be validated on a Pod
5. Spending, adding funds, and starting a Pod had not been authorized. The proposed 13:30–15:30 trial would begin only after approval of a total spending limit and an artifact destination

## Updates from the later trial

- The statements about blocked networking, missing setup, and missing authorization above describe the earlier free preparation stage. They do not mean that no Pod was subsequently started. The later authorized paid trial used REST v2 Pod operations and log reading
- GitHub returned `No route to host` from `US-MD-1`. GitHub, PyPI, and the pinned model delivery endpoints were reachable from `EUR-IS-1`. A location-specific or temporary routing problem is an inference; the root cause was not established
- A manual isolated-venv installation was interrupted at its 240-second limit. A later attempt without that limit completed the hash-pinned installation in approximately 297 seconds; `pip check` inside the venv and a separate kernel-registration check also succeeded
- Reaching a model delivery endpoint does not validate downloading the complete model, SHA256 verification, loading, or inference. Those steps, including CUDA compilation, were not performed
- Before another paid run or deletion, prepare a fresh plan covering the retained Volumes, current capacity and prices, expected duration, artifact destination, and stop/verification procedure, and obtain the required authorization

## Immutable implementation links

These files are not present on documentation-only `main`. Check out the pinned commit above before using the commands.

- [`scripts/setup-cloud-env.sh`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/setup-cloud-env.sh)
