# RunPod 実行手順書（実装版）

状態: コードとCPUチェックまで完了。**Podは未作成**。支出・Pod起動・Terminate・キーや権限の変更は未実施。実行に使うコミットは、作業者が報告するコミットSHA（40桁）。この文書は自分自身のSHAを書けないため、SHAは別途受け取る。

## 実装したもの（要点）

| 項目 | 場所 | 内容 |
| --- | --- | --- |
| 固定値 | `qmc_runpod/pins.py` | 上流 `9e5ff82c…`、Notebook blob、llama.cpp `4da63377…`、モデル2つのrevisionとSHA256（`baseline.json`と一致するテストあり） |
| パス | `layout.py` | すべて `/workspace/qwen` 配下。`/content`・Drive・Colab Secretsは使わない |
| Gradio 6 | `chat_ui.py` | 上流 `ui_chat` は `gr.Chatbot(type="messages")` がTypeError。上流は変えず、構築中だけ `type` を外す |
| 起動の安全装置 | `launch.py` | 認証が両方そろわない／7860使用中／share／外部chat API／llama-serverが127.0.0.1以外、で起動を中止。起動後に未認証アクセスが拒否されることも確認 |
| 取得・ビルド | `models.py`, `llama.py` | 固定revisionの2ファイルだけ取得しSHA256照合。固定commitをビルド。nvccやarchを検出できなければ中止 |
| 測定 | `smoke.py` | load・最初の1件（cold）・warmを分離。出力上限を必ず明示 |
| 報告 | `report.py`, `trial.py`, `provenance.py` | 許可リスト方式。実行コミット、依存lockのhash、image・CUDA・ビルド情報、全チェックの pass/fail/skipped |
| Pod操作 | `podapi.py`, `scripts/pod.py` | REST v2。キーは扱わない。terminate/deleteの関数は存在しない |
| 依存 | `requirements-runpod.lock.txt` | gradio 6.29.0ほか85パッケージをハッシュ付きで固定 |

## Podへのコード配置（リポジトリはPublic）

1. 人がRunPodコンソールの **Connect** からJupyterに入る。パスワードなどはチャットに出さない。
2. Jupyterのターミナルで、報告された**コミットSHA**を指定して固定してcloneする（ブランチ名は使わない）。
   ```
   git clone https://github.com/moruku36/qwen-runpod-operations /workspace/qwen/ops
   cd /workspace/qwen/ops && git checkout --detach <40桁のSHA> && test "$(git rev-parse HEAD)" = "<40桁のSHA>" && git status --porcelain
   ```
   最後のコマンドが何も出力しなければ、作業ツリーはコミットと完全一致。同じ検証は `scripts/pod-bootstrap.sh <SHA>` でも行える。
3. `notebooks/Qwen-Q8-Chat-RunPod.ipynb` を開く。`notebooks/` フォルダからでもリポジトリルートからでも、Cell 1 が正しいルートを解決する（見つからなければ明示的に停止。`QMC_REPO_ROOT` で指定も可）。

## Notebookの実行順

- Cell 1: ルート解決、実行コミットとクリーン確認、上流を固定SHAで取得、ロックから依存導入と `pip check`
- Cell 2: 非秘密の環境、llama-serverのビルド（時間を記録）、モデル取得とSHA256照合
- Cell 3: ログイン値をgetpassで入力して起動。未認証アクセスが拒否されることを確認
- **Cell 4: 短い応答を1件だけ**。出力上限は64トークン（明示）。モデル起動時間は別に記録。これは動作確認で、性能の測定ではない
- Cell 4b（任意）: Cell 4の成功後に人が判断したときだけ。warm 10件（出力上限256）。coldとwarmは分けて記録
- Cell 5: 許可リストのレポートと `.tar.gz`
- Cell 6: アプリの停止（Podは止まらない）

性能基準（warm・短文・検索Off・thinkingOffの10試行で、初回tokenの中央値5秒以内、256トークン完了の中央値30秒以内）は**提案基準**で、保証ではない。warm 10件が正常に揃わない限り `perf_criteria` は `skipped` のまま。最初の1件（cold）は基準の評価に使わない。

## 作成（有料。承認後だけ）

1. `python scripts/pod.py plan --launch-at HH:MM` で作成案と見積を確認する（読み取りのみ）。固有のPod名（`qwen-trial-日付-時刻-4桁`）が出る。
2. 承認後、`plan` が出した名前を使って `python scripts/pod.py create --approve LAUNCH-PAID-POD --name <その名前> --launch-at HH:MM`。
3. 作成APIが**タイムアウト・接続エラー・5xx**になったら、課金中のPodが作られた可能性がある。ツールは「作成結果は不明」と表示して終了し、**自動で再試行しない**。次を確認する。
   - `python scripts/pod.py find <Pod名>`（読み取り。名前の完全一致で一覧）
   - コンソール（https://www.runpod.io/console/pods）で同じ名前を探す
   - 存在すれば停止（下記）。存在しないと確認できるまで `create` を再実行しない。
4. 4xxは「作成されなかった」確定の失敗として扱う。

## 停止（16:45目標）

`python scripts/pod.py stop <pod_id>`

- `POST /v2/pods/{id}/action` に `{"action":"stop"}` を送り、`GET /v2/pods/{id}` で読み戻す。
- 成功の条件は、`status == EXITED` **かつ** `cost` が存在して数値の0であること。`cost` の欠落・null・文字列・真偽値は0として扱わない。
- 通信エラーでも確認期限（既定5分）まで繰り返す。期限までに確認できなければ失敗として、**Pod IDとコンソールのStop手順**（Stop。Terminateではない）を必ず表示する。Podが `locked` ならコンソールで解除する。
- 停止後もPod Volume（80GB）の保管費が残る。Terminateはこの実装に入れておらず、不可逆なので別途確認する。

## 回収（16:30開始）

1. Cell 5が `/workspace/qwen/runs` に `report-*.json` と `.tar.gz`（MANIFEST付き）を作る。
2. Jupyterの画面からダウンロードし、外部に保存する。
3. 保存した `.tar.gz` を `python scripts/verify_bundle.py <file>` で読み戻す（ハッシュ・ファイル一覧・許可リスト・全チェックの存在）。OKになってから停止に進む。
4. レポートの `report_exported` は、Pod外で読み戻した結果で判断するためPod内では常に `skipped`。

### レポートの中身

実行コミットとクリーン判定、依存lockのSHA256、image tag（digestはPod内から取得できないため `unavailable_in_pod`）、nvccとドライバのCUDA、llama.cppのcommit・arch・runtime・ビルド秒数・今回ビルドしたか、GPU、モデルのrevisionと検証結果、設定、測定値（load・cold・warmを分離）、全チェック（pass/fail/skipped）。Podの `env`・ssh・runtime・APIレスポンス全体・設定全体は保存しない（名前を指定した項目だけを取り出す）。

## 見積（`plan` の出力）

`GPU単価 ×（起動から停止目標までの時間 + 停止完了までの余裕10分）` に、稼働中のContainer/Volume費用、**停止後に保管し続けるPod Volume費（既定24時間）**、**税・手数料などの余裕（既定20%。公表値ではない仮定）**を足す。

**これは見積であり、自動停止でも課金上限の保証でもない。** 超過を防ぐのは、人による停止確認と、Billingでの実額照合だけ。残高と自動チャージの有無はv2 APIに出ないので、コンソールで確認が必要。`create` は見積が指定の上限（既定$10）を超えると送信前に拒否するが、これも課金上限の仕組みではない。

## 必要な権限（変更なし）

呼ぶAPI: `GET /v2/catalog/gpus`、`GET /v2/catalog/datacenters`、`POST /v2/pods`、`GET /v2/pods`（`find`）、`GET /v2/pods/{id}`、`POST /v2/pods/{id}/action`（stopのみ）。任意で `GET /v2/billing/pods`。DELETE（terminate）、secrets、ssh-keys、templates、network volumes、serverless、clustersは不要。OpenAPI仕様にエンドポイント別の権限名はなく、コンソールでの確認が必要。作成・停止の書き込み権限は、実行するまで確認できない。

## 未解決事項

1. Jupyterのパスワードの取得場所（Connect画面にあるはずだが未確認）。チャットには出さない。
2. HFのrevisionが取得できるか（ここから`huggingface.co`に届かず未確認。SHA256照合が最終的な防波堤）。
3. llama.cppのCUDAビルド、モデルロード、GPUメモリ、32k、画像・検索・ASR、停止の読み戻しの実挙動は、Pod上でしか確認できない。
4. Pod Volumeはhost固定で、host障害でデータが消える（API仕様）。回収前に障害が起きれば失う。
5. 在庫は変動し、作成は在庫の予約ではない。
