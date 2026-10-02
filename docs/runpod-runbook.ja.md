# RunPod 実行手順書（実装版）

[English](runpod-runbook.en.md) | 日本語

最終状態: [2026-10-02 試行報告](trial-report-2026-10-02.ja.md)

**文書公開版について:** `main` は文書のみで、実行スクリプトやNotebookを含まない。以下の実装説明・コマンドは、[固定実装コミット](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16) `69795d54490ffaeff62f18ca80f6181ea674bc16` を別途checkoutした環境を前提とする。文書の公開は、新しい有料実行・再起動・Terminate・削除の承認ではない。次回は新しい計画と承認が必要。

**2026-10-02の最終状態:** 有料試行では2台のPodを使用。`US-MD-1` では外向き接続に失敗し、`EUR-IS-1` では `selftest`・`net`・`venv` とログ経由の小さな成果物の回収に成功した。CUDAビルド・モデル本体の取得とハッシュ検証・モデルロード・推論は未実施。最終実行報告では両Podが `EXITED`。各Podの停止時にコンソールのCompute・Container storageが `Not running` と確認され、16:05頃（JST）の最終共有画面は第2Podのみを示す。80GBのVolumeは各約$0.022/時で残り、2つで約$1.07/日の保管費が続く。後片付けは保留で、削除は未承認。

状態: 固定実装とCPUチェックに加え、2026-10-02の実Podで `selftest`・`net`・`venv` まで検証した。下記は実装の手順説明であり、未実施のCUDAビルド・モデル取得・推論の成功を保証しない。再現対象の実装SHAは `69795d54490ffaeff62f18ca80f6181ea674bc16`。文書のみの `main` ではこの手順を実行できない。

## 実装したもの（要点）

| 項目 | 場所 | 内容 |
| --- | --- | --- |
| 固定値 | [`qmc_runpod/pins.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/pins.py) | 上流 `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`、Notebook blob、llama.cpp `4da6337767f973e2b4d0797e5b323d77d8565e4a`、モデル2つのrevisionとSHA256（[`baseline.json`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/baseline.json)と一致するテストあり） |
| パス | [`layout.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/layout.py) | すべて `/workspace/qwen` 配下。`/content`・Drive・Colab Secretsは使わない |
| Gradio 6 | [`chat_ui.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/chat_ui.py) | 上流 `ui_chat` は `gr.Chatbot(type="messages")` がTypeError。上流は変えず、構築中だけ `type` を外す |
| 起動の安全装置 | [`launch.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/launch.py) | 認証が両方そろわない／7860使用中／share／外部chat API／llama-serverが127.0.0.1以外、で起動を中止。起動後に未認証アクセスが拒否されることも確認 |
| 取得・ビルド | [`models.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/models.py), [`llama.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/llama.py) | 固定revisionの2ファイルだけ取得しSHA256照合。固定commitをビルド。nvccやarchを検出できなければ中止 |
| 測定 | [`smoke.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/smoke.py) | load・最初の1件（cold）・warmを分離。出力上限を必ず明示 |
| 報告 | [`report.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/report.py), [`trial.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/trial.py), [`provenance.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/provenance.py) | 許可リスト方式。実行コミット、依存lockのhash、image・CUDA・ビルド情報、全チェックの pass/fail/skipped |
| Pod操作 | [`podapi.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/qmc_runpod/podapi.py), [`scripts/pod.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod.py) | REST v2。キーは扱わない。terminate/deleteの関数は存在しない |
| 依存 | [`requirements-runpod.lock.txt`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/requirements-runpod.lock.txt) | gradio 6.29.0ほか85パッケージをハッシュ付きで固定 |

## Podへのコード配置（リポジトリはPublic）

1. 人がRunPodコンソールの **Connect** からJupyterに入る。パスワードなどはチャットに出さない。
2. Jupyterのターミナルで、上記の**固定実装コミットSHA**を指定して固定してcloneする（ブランチ名は使わない）。**1行ずつ実行し、各行の結果を確認してから次へ進む**（`&&` でつながない）。
   ```
   git clone https://github.com/moruku36/qwen-runpod-operations /workspace/qwen/ops
   ```
   ```
   cd /workspace/qwen/ops
   ```
   ```
   git checkout --detach 69795d54490ffaeff62f18ca80f6181ea674bc16
   ```
   ```
   git rev-parse HEAD
   ```
   ```
   git status --porcelain
   ```
   `git rev-parse HEAD` が指定したSHAと完全に同じで、`git status --porcelain` が何も出力しなければ、作業ツリーはコミットと完全一致。同じ検証は `bash scripts/pod-bootstrap.sh <SHA>` でも行える（clone後に実行）。
3. [`notebooks/Qwen-Q8-Chat-RunPod.ipynb`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/notebooks/Qwen-Q8-Chat-RunPod.ipynb) を開く。`notebooks/` フォルダからでもリポジトリルートからでも、Cell 1 が正しいルートを解決する（見つからなければ明示的に停止。`QMC_REPO_ROOT` で指定も可）。

## Pod上の手順（独立したPython環境。システムPythonには入れない）

システムPython（`/usr/bin/python`）にはOSのパッケージ（例: PyGObject→pycairo）が入っており、そこへ入れて `pip check` すると、このプロジェクトと無関係な理由で失敗する。そのため、依存は `/workspace/qwen/venv` の独立venvに入れる。**1行ずつ実行する。**

```
python3 scripts/pod_run.py selftest
```
ログ経由の連絡路の確認（[Podへの接続方法](pod-remote-ops.ja.md)）。
```
python3 scripts/pod_run.py net
```
GitHub・PyPI・固定モデル2つの配信先を確認する。**失敗したらその場でStop。**
```
python3 scripts/pod_run.py all --bg
```
`net` → `source`（上流を固定SHAで取得）→ `venv`（独立venv、ハッシュ付き導入、venv内だけで `pip check`、kernel登録）→ `build`（llama-serverのCUDAビルドとモデルの取得・SHA256照合）を順に実行し、最初の失敗で止まる。時間制限はなく、すぐに戻る。
```
python3 scripts/pod_run.py status
```
進捗・チェック・ログの末尾。
```
python3 scripts/pod_run.py trial
```
`build` が成功した後に実行する。UIを起動し（ログイン値は `/workspace/qwen/secrets/gradio-login.txt`、画面には出さない）、**64トークン上限の応答を1件だけ**行い、レポートと `.tar.gz` を作ってログ経由でも送る。`--warm` でwarm 10件（任意）、`--hold-min N` でUIをN分維持。

Notebook（[`notebooks/Qwen-Q8-Chat-RunPod.ipynb`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/notebooks/Qwen-Q8-Chat-RunPod.ipynb)）を使う場合は、`venv` の後にkernel「Python (qwen-venv)」を選ぶ。Cell 1 はそのkernelでなければ止まる。性能基準の扱い（提案基準、warm 10件が揃うまで `perf_criteria` は `skipped`、最初の1件は評価に使わない）は変わらない。

CPUの事前検証では、mockで `selftest`、`source`、`venv`（実際のpip導入、約39秒）、`trial --mock`（UI・認証・応答・レポート・ログ経由の取り出し）まで通した。**Pod上のCUDAビルド・モデル本体の取得とSHA256照合・ロード・推論は未実施。** 実Podの独立venvでは、ハッシュ付き導入に約297秒かかり、venv内の `pip check` は成功した。240秒の制限を設けた先行手順はタイムアウトしていた。kernelの存在は独立した照会で確認したが、登録コマンドの終了コードを見ず成功状態を表示する実装上の制限が残る。Jupyter画面のkernel一覧は未確認。

## 作成 有料のため新しい計画の承認後だけ

1. `python scripts/pod.py plan --launch-at HH:MM` で作成案と見積を確認する（読み取りのみ）。固有のPod名（`qwen-trial-date-time-4digits`）が出る。
2. 承認後、`plan` が出した名前を使って `python scripts/pod.py create --approve LAUNCH-PAID-POD --name <name-from-plan> --launch-at HH:MM`。
3. 作成APIが**タイムアウト・接続エラー・5xx**になったら、課金中のPodが作られた可能性がある。ツールは「作成結果は不明」と表示して終了し、**自動で再試行しない**。次を確認する。
   - `python scripts/pod.py find <Pod-name>`（読み取り。名前の完全一致で一覧）
   - コンソール（https://www.runpod.io/console/pods）で同じ名前を探す
   - 存在すれば停止（下記）。存在しないと確認できるまで `create` を再実行しない。
4. 4xxは「作成されなかった」確定の失敗として扱う。

## 停止 当初の目標は2026-10-02 16:45 JST

`python scripts/pod.py stop <pod_id>`

- `POST /v2/pods/{id}/action` に `{"action":"stop"}` を送り、`GET /v2/pods/{id}` で読み戻す。
- **固定実装の成功判定**は、`status == EXITED` **かつ** `cost` が存在して数値の0であること。`cost` の欠落・null・文字列・真偽値は0として扱わない。
- **既知の制限:** 2026-10-02の試行では `EXITED` 後もAPIの `cost` は1.59のままだった。コンソールではCompute・Container storageが `Not running` と確認された。この数値だけではGPU稼働の継続を示さず、実装のゼロ費用判定は停止の確認に失敗した。停止状態と保管費は別に読み戻し、コンソールの表示も併用する。原因は未確定で、この文書公開ではコードを変更していない
- 通信エラーでも確認期限（既定5分）まで繰り返す。期限までに確認できなければ失敗として、**Pod IDとコンソールのStop手順**（Stop。Terminateではない）を必ず表示する。Podが `locked` ならコンソールで解除する。
- 停止後もPod Volume（80GB）の保管費が残る。Terminateはこの実装に入れておらず、不可逆なので別途確認する。

## 回収 当初の開始目標は2026-10-02 16:30 JST

1. Cell 5が `/workspace/qwen/runs` に `report-*.json` と `.tar.gz`（MANIFEST付き）を作る。
2. Jupyterの画面からダウンロードし、外部に保存する。
3. 保存した `.tar.gz` を `python scripts/verify_bundle.py <file>` で読み戻す（ハッシュ・ファイル一覧・許可リスト・全チェックの存在）。OKになってから停止に進む。
4. レポートの `report_exported` は、Pod外で読み戻した結果で判断するためPod内では常に `skipped`。

### レポートの中身

実行コミットとクリーン判定、依存lockのSHA256、image tag（digestはPod内から取得できないため `unavailable_in_pod`）、nvccとドライバのCUDA、llama.cppのcommit・arch・runtime・ビルド秒数・今回ビルドしたか、GPU、モデルのrevisionと検証結果、設定、測定値（load・cold・warmを分離）、全チェック（pass/fail/skipped）。Podの `env`・ssh・runtime・APIレスポンス全体・設定全体は保存しない（名前を指定した項目だけを取り出す）。

## 見積（`plan` の出力）

`GPU単価 ×（起動から停止目標までの時間 + 停止完了までの余裕10分）` に、稼働中のContainer/Volume費用、**停止後に保管し続けるPod Volume費（既定24時間）**、**税・手数料などの余裕（既定20%。公表値ではない仮定）**を足す。

**これは見積であり、自動停止でも課金上限の保証でもない。** 超過を防ぐのは、人による停止確認と、Billingでの実額照合だけ。残高と自動チャージの有無はv2 APIに出ないので、コンソールで確認が必要。`create` は見積が指定の上限（既定$10）を超えると送信前に拒否するが、これも課金上限の仕組みではない。

## 必要な権限 今回の文書公開では変更なし

呼ぶAPI: `GET /v2/catalog/gpus`、`GET /v2/catalog/datacenters`、`POST /v2/pods`、`GET /v2/pods`（`find`）、`GET /v2/pods/{id}`、`POST /v2/pods/{id}/action`（stopのみ）。任意で `GET /v2/billing/pods`。DELETE（terminate）、secrets、ssh-keys、templates、network volumes、serverless、clustersは不要。OpenAPI仕様にエンドポイント別の権限名はなく、コンソールでの確認が必要。事前には作成・停止の書き込み権限を実行せずには確認できなかったが、試行では成功した。将来の環境や別の認証にも同じ権限があると仮定しない。

## 未解決事項

1. 試行では人がJupyterへ接続した。新しいPodごとの認証・接続手順は確認が必要で、パスワードはチャットやGitへ出さない。
2. `EUR-IS-1` から固定モデル配信先への接続は成功したが、モデル本体の取得とSHA256照合は未実施。到達確認をrevision・モデル内容の完全検証とは扱わない。
3. llama.cppのCUDAビルド、モデルロード、GPUメモリ、32k、画像・検索・ASRは未実施。停止の読み戻しでは上記のAPI `cost` 差異を確認した。
4. Pod Volumeはhost固定で、host障害でデータが消える（API仕様）。回収前に障害が起きれば失う。
5. 在庫は変動し、作成は在庫の予約ではない。


## 次回の新しい計画に含める事項

- 残る80GB Volume 2つの保存・回収・削除方針。Terminateや削除は未承認のため実施しない
- 最新在庫・GPU単価・起動中ストレージ費・停止後保管費と、合計支出の上限。過去の$1.59/時や$10の上限を新たな承認として流用しない
- 約5分の依存導入、未検証のCUDAビルドとモデル本体取得を含む所要時間、回収開始と停止期限
- `selftest` → `net` → 承認された後続工程という段階的な実行。接続失敗時はそこでStop
- 実装の停止判定制限を踏まえた、API・コンソール・必要な課金確認の方法。新たな実行・再起動には新しい承認を得る

## 固定実装へのリンク

以下のファイルは文書のみの `main` には含まれない。上記の固定コミットをcheckoutしてからコマンドを実行する。

- [`scripts/pod.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod.py)
- [`scripts/pod_run.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod_run.py)
- [`scripts/pod-bootstrap.sh`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod-bootstrap.sh)
- [`scripts/verify_bundle.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/verify_bundle.py)
- [`notebooks/Qwen-Q8-Chat-RunPod.ipynb`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/notebooks/Qwen-Q8-Chat-RunPod.ipynb)
