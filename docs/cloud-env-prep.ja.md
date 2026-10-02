# クラウド環境の準備結果（2026-10-02）

GPUなし・有料リソースなし。RunPodのAPIキー、OAuth、SSH設定は未設定。再現は `scripts/setup-cloud-env.sh`。

## 入ったもの

| 項目 | 結果 |
| --- | --- |
| runpodctl | v2.14.0（公式リリースの linux-amd64）。公式の `checksums_2.14.0_sha256.txt` と一致を確認して `/usr/local/bin` に配置 |
| 元リポジトリ | `9e5ff82c…` をcheckout。HEADとNotebook blob `5e40a89f…` が baseline.json と一致 |
| Python環境 | Python 3.11.15 の venv に `requirements-chat-colab.txt` と dev 依存（gradio 6.29.0、huggingface_hub 1.33.0 ほか計86パッケージ）。`pip check` 問題なし |
| 既存ツール | git / cmake 3.28 / gcc 13 / make / jq / uv / node 22 / go |

## GPUなしチェックの結果

- 上流のCPUテスト: 288 passed, 1 skipped
- `Qwen-Q8-Chat-Colab.ipynb`: nbformat 4、コードセル5つ、構文エラーなし
- 上流の `ruff check`: 7件（自動修正可）。今回は上流を変更しないので未対応

## 移植で直す場所（コード確認で確定）

- `src/qmc/colab.py`: `install_llama_cpp` の `/content/llama.cpp`、`/content/llama-bin`、`llama_cache_dir` の `/content/llama-bin-cache`、`QMC_DATA_DIR` の既定 `/content/qmc-data`
- `src/qmc/config.py:149`: `/content` があるときだけ Colab 用の保存先を選ぶ。RunPodでは `QMC_DATA_DIR` を明示する
- `src/qmc/backends/llama_server.py:36`: 探索候補に `/content/llama.cpp/...` がある。`QMC_LLAMA_BIN_DIR` を設定すれば回避できる
- Notebook Cell 1 の branch 追従と `/content` の `%cd`

## 残る課題

1. **`api.runpod.io` がこの環境のネットワーク許可リストで拒否される**（CONNECT 403）。`huggingface.co` と llama.cpp の github.com も同様。`runpodctl` を動かすにも、モデル取得にも許可が必要。環境設定の Network access で、api.runpod.io と huggingface.co（と必要なら cdn-lfs 系）を許可するか、アクセスレベルを広げる
2. RunPodのAPIキー未設定（`~/.runpod/config.toml` の `apikey` は空）。`RUNPOD_API_KEY` を環境変数のシークレットとして渡すのが安全。チャットやGitへは出さない
3. SSH鍵の登録は未実施。必要なら本人の承認後
4. この環境にはCUDA/nvccがなく、llama.cppのCUDAビルド、モデルロード、GPU測定は検証不可（Pod上でのみ）
5. 支出・入金・Pod起動の承認は未取得。13:30〜15:30の試行は、上限額・保存先の承認後に開始
