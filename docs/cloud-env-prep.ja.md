# クラウド環境の準備結果 2026-10-02

[English](cloud-env-prep.en.md) | 日本語

最終状態: [2026-10-02 試行報告](trial-report-2026-10-02.ja.md)

**実装のmainへの統合について:** 試行後に実装（スクリプト・Notebook・依存lock・テスト）をmainへ統合した（[README](../README.ja.md)と[実装ガイド](implementation-guide.ja.md)を参照）。以下のコマンドは[固定実装コミット](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16) `69795d54490ffaeff62f18ca80f6181ea674bc16` から実行したもの。Podでの実行を再現するときは、ブランチ先端ではなくそのコミットをcheckoutする。文書の公開は、新しい有料実行・再起動・Terminate・削除の承認ではない。次回は新しい計画と承認が必要。

**2026-10-02の最終状態:** 有料試行では2台のPodを使用。`US-MD-1` では外向き接続に失敗し、`EUR-IS-1` では `selftest`・`net`・`venv` とログ経由の小さな成果物の回収に成功した。CUDAビルド・モデル本体の取得とハッシュ検証・モデルロード・推論は未実施。最終実行報告では両Podが `EXITED`。各Podの停止時にコンソールのCompute・Container storageが `Not running` と確認され、16:05頃（JST）の最終共有画面は第2Podのみを示す。80GBのVolumeは各約$0.022/時で残り、2つで約$1.07/日の保管費が続く。後片付けは保留で、削除は未承認。

**この節は有料試行前の履歴。** この準備段階にはGPU・有料リソースがなく、RunPodのAPIキー・OAuth・SSH設定も未設定だった。再現用スクリプトは [`scripts/setup-cloud-env.sh`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/setup-cloud-env.sh)。後の有料試行の結果で、当時の未確認事項の一部は更新された。

## 入ったもの

| 項目 | 結果 |
| --- | --- |
| runpodctl | v2.14.0（公式リリースの linux-amd64）。公式の `checksums_2.14.0_sha256.txt` と一致を確認して `/usr/local/bin` に配置 |
| 元リポジトリ | `9e5ff82cf604dd3b0377de81b89e1f4aaa422947` をcheckout。HEADとNotebook blob `5e40a89f0253356c9c8d72698a93508f4d30d8aa` が baseline.json と一致 |
| Python環境 | Python 3.11.15 の venv に [`requirements-chat-colab.txt`](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/requirements-chat-colab.txt) と dev 依存（gradio 6.29.0、huggingface_hub 1.33.0 ほか計86パッケージ）。`pip check` 問題なし |
| 既存ツール | git / cmake 3.28 / gcc 13 / make / jq / uv / node 22 / go |

## GPUなしチェックの結果

- 上流のCPUテスト: 288 passed, 1 skipped
- [`Qwen-Q8-Chat-Colab.ipynb`](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/Qwen-Q8-Chat-Colab.ipynb): nbformat 4、コードセル5つ、構文エラーなし
- 上流の `ruff check`: 7件（自動修正可）。今回は上流を変更しないので未対応

## 移植で直す場所（コード確認で確定）

- [`src/qmc/colab.py`](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/colab.py): `install_llama_cpp` の `/content/llama.cpp`、`/content/llama-bin`、`llama_cache_dir` の `/content/llama-bin-cache`、`QMC_DATA_DIR` の既定 `/content/qmc-data`
- [`src/qmc/config.py:149`](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/config.py#L149): `/content` があるときだけ Colab 用の保存先を選ぶ。RunPodでは `QMC_DATA_DIR` を明示する
- [`src/qmc/backends/llama_server.py:36`](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/backends/llama_server.py#L36): 探索候補に `/content/llama.cpp/...` がある。`QMC_LLAMA_BIN_DIR` を設定すれば回避できる
- Notebook Cell 1 の branch 追従と `/content` の `%cd`

## 準備段階で残っていた課題

1. **`api.runpod.io` がこの環境のネットワーク許可リストで拒否される**（CONNECT 403）。`huggingface.co` と llama.cpp の github.com も同様。`runpodctl` を動かすにも、モデル取得にも許可が必要。環境設定の Network access で、api.runpod.io と huggingface.co（と必要なら cdn-lfs 系）を許可するか、アクセスレベルを広げる
2. RunPodのAPIキー未設定（`~/.runpod/config.toml` の `apikey` は空）。`RUNPOD_API_KEY` を環境変数のシークレットとして渡すのが安全。チャットやGitへは出さない
3. SSH鍵の登録は未実施。必要なら本人の承認後
4. この環境にはCUDA/nvccがなく、llama.cppのCUDAビルド、モデルロード、GPU測定は検証不可（Pod上でのみ）
5. 支出・入金・Pod起動の承認は未取得。13:30〜15:30の試行は、上限額・保存先の承認後に開始


## 後の試行で更新されたこと

- 上記のネットワーク拒否・未設定・未承認という記述は、この無料準備段階の観測であり、以後も一切Podを起動していないという意味ではない。後の承認済み有料試行ではREST v2によるPod操作とログ読み取りを使用した
- `US-MD-1` からGitHubへの `No route to host` を観測した。`EUR-IS-1` ではGitHub・PyPI・固定モデル配信先への接続が成功した。地域固有または一時的な経路問題という説明は推測で、原因は確定していない
- 手動の独立venv導入は240秒の制限で中断した。後の時間制限を外した手順では、ハッシュ付き導入が約297秒で成功し、venv内の `pip check` と独立したkernel登録確認も成功した
- モデル配信先への到達確認は、モデル本体のダウンロード・SHA256照合・ロード・推論の検証ではない。CUDAビルドを含め、これらは未実施
- 新たな有料実行や削除へ進む前に、保存中Volumeの扱い、現時点の在庫・費用、所要時間、回収先、停止・確認方法を含む新しい計画を作り、必要な承認を取り直す

## 固定実装へのリンク

以下のファイルは実装の統合後は `main` にも含まれる。試行を正確に再現するときは、ブランチ先端ではなく、上記の固定コミットをcheckoutしてからコマンドを実行する。

- [`scripts/setup-cloud-env.sh`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/setup-cloud-env.sh)
