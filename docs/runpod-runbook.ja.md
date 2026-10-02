# RunPod 実行手順書（2026-10-02 実装版）

状態: コードとCPUチェックまで完了。**Podは未作成**。支出・Pod起動・Terminate・キーや権限の変更は未実施。

## 実装したもの

| 項目 | 場所 | 内容 |
| --- | --- | --- |
| 固定値 | `qmc_runpod/pins.py` | 上流 `9e5ff82c…`、Notebook blob、llama.cpp `4da63377…`、モデル2つのrevisionとSHA256（`baseline.json`と一致するテストあり） |
| パス・環境 | `layout.py` | すべて `/workspace/qwen` 配下。`/content`・Drive・Colab Secretsは使わない |
| Gradio 6対応 | `chat_ui.py` | 上流 `ui_chat` は `gr.Chatbot(type="messages")` でTypeError（Gradio 6.29.0で再現）。上流を変えず、構築中だけ `type` 引数を外す |
| 起動の安全装置 | `launch.py` | 認証情報が両方そろわない／7860使用中／share有効／外部chat API／llama-serverが127.0.0.1以外、のいずれかで起動を中止。ポートの自動変更はしない |
| モデル取得 | `models.py` | 固定revisionで2ファイルだけ取得し、サイズ（既知分）とSHA256を照合。検証済みのパスだけをllama-serverに渡す |
| llama.cpp | `llama.py` | 固定commitを `/workspace` にビルド。nvccやGPU archを検出できなければ中止（推測ビルドなし） |
| 測定 | `smoke.py` | chat専用（画像生成なし）。数値だけ記録 |
| 報告 | `report.py` | 許可リスト方式。未知のキー・秘密らしい文字列・長いトークンは書き込み前に拒否 |
| Pod操作 | `podapi.py`, `scripts/pod.py` | REST v2。キーは扱わず、環境側の付与に任せる。terminate/deleteの関数は存在しない |
| 依存 | `requirements-runpod.lock.txt` | gradio 6.29.0など85パッケージをハッシュ付きで固定。新規venvで `--require-hashes` の導入と `pip check` を確認済み |
| Notebook | `notebooks/Qwen-Q8-Chat-RunPod.ipynb` | 薄い6セル。ログイン値はgetpassで入力し、保存しない |

## CPUで確認したこと

- `pytest tests`: 27件合格（上流のテストは288件合格のまま、上流は無変更）
- mockモードの実UIをChromiumで操作: ログイン → 送信 → mock応答の表示まで成功、ページエラーなし（`scripts/e2e_mock_ui.py`）
- 未ログインの `/config` は401/403、ログイン後は200
- 画像生成backendは未登録（`manager.get("image")` が KeyError）

## 実行と回収の経路

1. **作成案**（`python scripts/pod.py plan`、読み取りのみ）: Secure、A100 80GB×1、Container Disk 20GB、`/workspace` のPod Volume 80GB、公式イメージ `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`（公式テンプレート `runpod-torch-v240` と同じ）、Jupyter有効、SSH無効、Network Volumeなし、env・秘密なし。
2. **作成**（有料。承認後だけ）: `scripts/pod.py create --approve LAUNCH-PAID-POD`。見積が予算（既定$10）を超えると送信前に中止する。
3. **Podへのコード配置**: Pod内で `git clone` できるのはリポジトリが公開の場合だけ。非公開なら、GitHubの「Download ZIP」をJupyterでアップロードして展開する（`scripts/make-pod-bundle.sh` でも作れる）。
4. **実行**: ZIPの展開先でNotebookを上から実行。初回は8kコンテキスト・検索Off。ログイン用のユーザー名とパスワードは実行時に入力する。
5. **回収**: Notebook最後のセルが許可リスト済みの `report-*.json` と `.tar.gz`（MANIFEST付き）を `/workspace/qwen/runs` に作る。Jupyterの画面からダウンロードして外部に保存する。
6. **読み戻し**: 保存した `.tar.gz` に対し `python scripts/verify_bundle.py <file>`（ハッシュ・ファイル一覧・許可リスト）。OKになってから停止する。

## 停止（16:45目標）

- `python scripts/pod.py stop <pod_id>`: `POST /v2/pods/{id}/action` に `{"action":"stop"}` を送り、`GET /v2/pods/{id}` で `status == EXITED` かつ `cost == 0` を読み戻す。5分以内に確認できなければ失敗として、対象Pod IDとコンソール手順（Stop、Terminateではない）を出す。
- Podが `locked` だと停止できないので、コンソールで解除してから行う。
- 停止後も、Pod Volume（80GB）の保管費が残る。計画書の公開値 $0.20/GB/月 で約 $0.022/時（12時間で約$0.27）。Terminateで止まるが、不可逆なので別途確認する。Container Diskは再起動で消える前提で扱う。
- Terminateの機能はこの実装に入れていない。

## 料金見積（停止後の保管費を除く）

式は `GPU単価 × 時間 + Container 20GB と Volume 80GB の稼働中費用`。A100 80GBはSecureで$1.59/時（起動時の画面の価格を優先）。

| GPU稼働時間 | 概算 |
| --- | ---: |
| 2.0時間 | $3.21 |
| 2.5時間 | $4.01 |
| 3.0時間 | $4.81 |
| 3.5時間 | $5.61 |

$10の上限に達するのは約6.2時間（GPU課金のみで約6.3時間）。起動が14時台なら、16:45停止で2〜3時間程度に収まる。実際の請求は反映が遅れる。残高と自動チャージの有無はAPI（v2）からは見えないので、コンソールで確認が必要。

## 必要な権限

- 今回の経路で呼ぶAPI: `GET /v2/catalog/gpus`、`GET /v2/catalog/datacenters`、`POST /v2/pods`、`GET /v2/pods/{id}`、`POST /v2/pods/{id}/action`（stopのみ）。任意で `GET /v2/billing/pods`。
- 不要: DELETE（terminate）、secrets、ssh-keys、templates、network volumes、serverless、clusters。
- OpenAPI仕様には、エンドポイントごとの権限名が書かれていない（「キー作成時の権限範囲で、足りなければ403」とだけある）。具体的な設定名はコンソールで確認する必要があり、ここでは断定しない。
- 現在の認証での確認結果: `GET /v2/pods` は200（Podは0件）。**作成・停止の書き込み権限は未確認**。確認できるのは実際に作成・停止するときだけで、事前の安全な確認方法はない。
- Podの書き込み権限にはterminateも含まれるはず。そのため権限だけでは「停止のみ」に絞れず、コード側でterminateを持たない方針を取っている。

## 未解決事項

1. Jupyterのパスワードとログインを、人がどこで見るか（`startJupyter` が自動生成した値はAPIで返らない）。コンソールの接続画面で見えるはずだが未確認。確実にしたいなら、アカウントの秘密情報としてパスワードを登録する方法があるが、アカウントの変更になるので承認が必要。
2. リポジトリの公開/非公開。非公開ならZIPのアップロードが必要。
3. HFのrevision（各ファイルの最終変更commit）が取得できるか。ここからは `huggingface.co` に届かないため未確認。ハッシュ照合が最終的な防波堤。
4. llama.cppのCUDAビルド、モデルロード、GPUメモリ、32k、画像・検索・ASRは、Pod上でしか確認できない。
5. 停止の読み戻し（EXITEDとcost 0）の実挙動は、実Podでしか確認できない。
6. Pod Volume（`mounts.persistent`）はAPI仕様上「非推奨、ホスト固定、ホスト障害でデータ消失」。計画の標準だが、回収前に障害が起きれば失う。
7. 在庫は変動する。A100 80GBはPCIeがLOW、SXMがMEDIUM（確認時点）。作成は在庫の予約ではない。

## 次の承認が必要なもの

- GPUを有料で起動すること（時間・総額上限・停止時刻・保存先）
- 残高と自動チャージの確認（コンソール）
- Podのコード配置方法（clone か ZIP）
