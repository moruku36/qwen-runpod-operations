# NotebookのRunPod移植レビュー

[English](portability-review.en.md) | 日本語

確認対象は上流commit [`9e5ff82cf604dd3b0377de81b89e1f4aaa422947`](https://github.com/moruku36/qwen-multimodal-colab/tree/9e5ff82cf604dd3b0377de81b89e1f4aaa422947)。以下は実装前に作成した変更仕様を履歴として保持したもので、各項目の実装・実機検証の完了を示すものではない。

その後の実装は別の固定commit [`69795d54490ffaeff62f18ca80f6181ea674bc16`](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16)に保存されている。この公開リポジトリの`main`には試行後に実装も統合したが、実装参照は実行した固定commitへのリンクを使う。下記の「現行」「上流」は、このレビューで確認した上流commitを指す。現在のRunPodでの実施結果は次の状態欄と試行レポートで区別する。

## 2026-10-02時点の実施状況

実際の試行では2台のPodを使用した。US-MD-1ではネットワーク確認に失敗し、EUR-IS-1ではselftest、ネットワーク確認、venv準備、成果物の外部保存・読み戻し検証に成功した。CUDA、モデルのダウンロード・ロード・推論は未検証。GPU費用の約$1.71は見積であり、確定請求額ではない。

両Podは停止済みだが、各80GBのPod Volumeが残っている。保存費は各約$0.022/時、合計約$1.07/日で、撤収は未完了。新規試行や削除をこの文書で承認するものではない。詳細は[2026-10-02試行レポート](trial-report-2026-10-02.ja.md)を参照。次の試行には新しい計画と必要な承認が必要。

## 維持する仕様

- Huihui Qwen3.8 27B、`Huihui-Qwen3.8-27B-abliterated-UD-DW-Q8_K_L.gguf`
- `ggml-org/Qwen3.8-27B-GGUF` の `mmproj-Qwen3.8-27B-Q8_0.gguf`
- llama.cpp commit `4da6337767f973e2b4d0797e5b323d77d8565e4a`
- Gradioのチャット専用UI、履歴、画像理解、Web検索、CPU faster-whisper small
- `chat_only=True`による画像生成backendの未登録
- 同時推論1件、thinking既定Off、TTS Off。元コンテキスト32,768 tokenは最終比較条件とし、8,192 tokenの起動確認結果と分ける

## セルごとの変更仕様（実装前のレビュー）

| 元の場所 | レビュー時に上流で確認できた動作 | RunPod用の変更・確認仕様 |
| --- | --- | --- |
| Cell 1 | `BRANCH="feat/q8-chat-colab"`でclone/pullする。マージ後も旧branch名のまま | branch先端を追わず、上記main commitをcheckout。`git rev-parse HEAD`一致を検証。pullは自動実行しない |
| Cell 1 | `/content/qwen-multimodal-colab`、`%cd` | `/workspace/qwen/source`に統一。パスが永続volume上であることを確認 |
| Cell 2 | `requirements-chat-colab.txt`、Colabのtorch/CUDAを利用 | RunPod公式CUDA開発用テンプレートに適合するPython/torchを記録。`nvcc --version`も確認。GPUドライバはPod内で置換しない |
| Cell 2 | `sys.path`に`/content/.../src`追加 | RunPod上のcheckoutに変更。NotebookカーネルとpipのPythonが一致することを確認 |
| Cell 2 | `colab.setup()`はDriveマウントとColab Secretsを利用 | `setup(use_drive=False)`相当。Colab外ではSecrets読込はno-op。必要な環境変数を起動前に設定する |
| Cell 2 | `install_llama_cpp()`のworkdir/bin/cacheが`/content`既定 | ソース・bin・cacheを`/workspace`へ移す。workdir/local_bin引数だけではcache既定まで変わらないため、専用wrapper等で明示する |
| Cell 3 | `SHARE=False`、`PREFETCH=False`、検索On | 共有Offを維持。まず検索Offでモデル単体、その後Onを検証。prefetchする場合もchat=True,image=Falseに限定 |
| Cell 4 | `colab.launch(...profile="a100_80",chat_only=True)` | A100初回は対応するprofile。A40等へ変えるときにA100固定を残さない。profileを自動選択するか明示的に比較用設定を用意 |
| Cell 4 | Colab専用proxy URL補正 | RunPodのHTTPS/ポート経路でGradio streaming、添付、ASRを検証。100秒proxy timeoutに留意し、長いHTTP要求でモデル取得・ビルドを待たせない |
| Cell 5 | Colab外ではアプリを止めるだけ | 「アプリ終了」と「RunPod Stop/Terminate」を分ける。Pod停止を確認するまで終了表示を出さない |

Colab helper自体はColab外を判定する分岐があり、Driveがないだけで全機能が不可能になるわけではない。ただし元Notebookには固定パスがあるため無変更実行は推奨しない。[S1・S3](sources.ja.md)

## 設定の置き場所

次は実装前に整理した非秘密設定案。実装や実機でこれらの設定が適用・検証されたことを、この一覧だけから判断しない。

| 設定 | 案・注意 |
| --- | --- |
| `QMC_DATA_DIR` | `/workspace/qwen/data` |
| `QMC_LOCAL_DB` | `/tmp/qmc/history.db`。現行のローカルDB＋mirrorを維持 |
| `HF_HOME` | `/workspace/qwen/hf-cache` |
| `QMC_LLAMA_BIN_DIR` | `/workspace/qwen/llama-bin/<commit>-<arch>-<runtime>`相当 |
| `QMC_CHAT_CTX` | 起動確認8192、比較条件32768。変更を記録 |
| `QMC_ASR_DEVICE` / `QMC_ASR_MODEL` | `cpu` / `small` |
| `QMC_TTS` / `QMC_SHARE` | `false` / `false` |
| `QMC_AUTH_USER` / `QMC_AUTH_PASSWORD` | 専用のログイン値。実値はGitに入れない |
| `QMC_CHAT_BASE_URL` | 未設定。同じPodのlocal llama-serverを使う |

現行`load_config`には`QMC_CHAT_ONLY`を読み込む処理がない。存在しない環境変数を設定するだけでchat-onlyになるとは扱わず、`colab.launch(...chat_only=True)`等の明示引数を使う。

## 依存関係と再現性

`requirements-chat-colab.txt`は`requirements.txt`に加え`huggingface_hub>=1.0`、`faster-whisper>=1,<2`、`ipywidgets>=8,<9`。coreはGradio6系、Pillow、requests、numpy、ddgs、pypdfium2であり、完全固定ではない。RunPod移植で画像生成用`requirements-colab.txt`を入れない。[S2](sources.ja.md)

実装前に追加要件として挙げた確認（完了実績の一覧ではない）:

1. template名、image tag/digest、OS、Python、pip、torch、CUDA toolkit、host driver、GPU compute capabilityを保存
2. llama.cppは上記SHAで初回検証。GPU archは実機検出値を使い、A40をA100のsm80で固定ビルドしない。旧CUDAバイナリを異なるイメージ/GPUへ無検証コピーしない
3. 依存解決後に`pip freeze`と`pip check`を保存し、再作成用lockを作る。単に最新へ更新して互換性の問題を隠さない
4. モデル2ファイルはrevisionとSHA256を記録し検証。上流`download_hf_file`にはrevision引数がないため、固定revisionで取得して同じ検証済みファイルをロードする導線を追加する必要がある
5. `HF_HUB_OFFLINE`やキャッシュの有無に依存した隠れた成功ではなく、外部保存した固定SHA・hash・依存lock・手順で再現できるようにする。標準では試行後にPod Volumeを削除し、新規Podでの再現試験は次の新計画として予算・再取得/ビルド時間を見積もる。未実施の再現試験を成功と扱わない

固定した実装側の[`baseline.json`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/baseline.json)にあるモデルhashは公開配布ページの値で、実ファイルをダウンロードして計算した値ではない。実行時に取得bytesから再計算して一致を確認する。

## 測定で気をつけること

上流`python -m qmc.bench`は画像backendのロード・生成・編集まで実行する。現状のままchat-only測定コマンドとして実行しない。今回の測定はテキスト、画像理解、検索、ASR、履歴だけに限定した手順または専用harnessを作る。[S3](sources.ja.md)

GPUメモリは`nvidia-smi`も使う。llama-serverは別プロセスなので、Python側の`torch.cuda.max_memory_allocated()`だけでは使用量を捕捉できない。初回ロード、再ロード、短文、長文、画像1枚・複数枚、連続10ターンを分ける。

ネットワークvolume上の大きなモデルはI/Oがcold startに影響する。ネットワークが速いという理由だけで「数秒で必ず起動」とはしない。初回モデル取得、初回ビルド、モデルload、warm時の初回tokenを別々に記録する。

## 実装の完了条件（当初の仕様）

- CPUテストとNotebook JSON/構文チェックが通る
- 元Notebook/モデル設定を意図せず変更していない
- Gradioは認証必須、llama-serverはPod外へ公開されない
- 画像生成backendを登録・取得しない
- 秘密情報を表示・保存しない
- データ同期、Pod停止、保存課金の残りをユーザーが確認できる

このレビューの作成時点では、レビュー作業としてコード修正・インストール・テスト実行をしていなかった。その後の実装と限定的な実機確認は上記の固定commitおよび試行レポートに記載する。上流PR35のCPUテスト成功記録は既存の証拠であり、それ自体はRunPod上での成功を示さない。
