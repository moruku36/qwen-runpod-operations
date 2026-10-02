# ClaudeがPod内を扱う接続方法

[English](pod-remote-ops.en.md) | 日本語

最終状態: [2026-10-02 試行報告](trial-report-2026-10-02.ja.md)

**文書公開版について:** `main` は文書のみで、実行スクリプトやNotebookを含まない。以下の実装説明・コマンドは、[固定実装コミット](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16) `69795d54490ffaeff62f18ca80f6181ea674bc16` を別途checkoutした環境を前提とする。文書の公開は、新しい有料実行・再起動・Terminate・削除の承認ではない。次回は新しい計画と承認が必要。

**2026-10-02の最終状態:** 有料試行では2台のPodを使用。`US-MD-1` では外向き接続に失敗し、`EUR-IS-1` では `selftest`・`net`・`venv` とログ経由の小さな成果物の回収に成功した。CUDAビルド・モデル本体の取得とハッシュ検証・モデルロード・推論は未実施。最終実行報告では両Podが `EXITED`。各Podの停止時にコンソールのCompute・Container storageが `Not running` と確認され、16:05頃（JST）の最終共有画面は第2Podのみを示す。80GBのVolumeは各約$0.022/時で残り、2つで約$1.07/日の保管費が続く。後片付けは保留で、削除は未承認。

目的: 人がコマンドやログを仲介する負担を減らし、ClaudeがPod内の進捗・診断結果・成果を受け取れるようにする。**今回の文書公開では、認証・権限・ネットワーク設定を変更していない。** 案Aは2026-10-02の承認済み試行で検証済み。案B・Cは設定しておらず、新しい権限や接続方法は必要な承認があるまで使わない。

## 試行時の環境と確認事項

- ClaudeがPodに対して使えるのは、REST v2（`api.runpod.io`）だけ。認証は環境側が付与し、Claudeはキーを見ない。試行時の認証ではPodsの作成・取得・stop・一覧・**ログの読み取り**まで成功した。
- Claudeの環境は外向きHTTPSのプロキシ経由。許可リストにないホスト（例: `docs.runpod.io`、`huggingface.co`）は拒否される。Podへの直接のTCP接続（SSH等）は想定できない。
- `GET /v2/pods/{id}/logs` は `source=container|system` を選べる読み取りAPI（SSE）。停止中のPodにもシステム行（作成・停止）は残る。初回の通常のコンテナコマンド出力は空だったが、後の案Aの試験ではPID 1の標準出力へ書いた行を受信できた。

## 案A（推奨・新しい権限なし）: ログ経由の連絡路

Pod内の実行スクリプト（[`scripts/pod_run.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod_run.py)）が、短い状態行（`QMC|time|stage|content`）と、小さな成果物（SHA256付きのbase64チャンク）を、Podの標準出力（PID 1）へ書く。ClaudeはREST v2の**ログ読み取りだけ**でそれを受け取る（`scripts/read_pod_logs.py <pod_id>`）。

- できること: 進捗・失敗・接続確認の結果の把握、レポート（許可リスト済みの `.tar.gz`）の回収。受け取った成果物はSHA256と許可リストで検証される。
- できないこと: Claudeが任意のコマンドを実行・診断すること。起動のきっかけ（Jupyterのターミナルへ `python3 scripts/pod_run.py all --bg` を1行貼る）は人が行う。
- 必要な権限: 現在のキー（Pods読み取り）で足りる。**追加の権限・ネットワーク設定は不要。**
- **実Podで検証済み（2026-10-02、EUR-IS-1）:** `python3 scripts/pod_run.py selftest` により、PID 1の標準出力へ書いた `hello from the pod` と `selftest.txt` をlogs API経由で受信し、復元ファイルのSHA256一致を確認した。`net`・`venv` の進捗も取得できた。ただし本番推論レポートの回収は未実施。将来の承認済み実行でも、起動直後にこの自己テストを行う。見えなければ診断し、案Bを使うには別途必要な承認を得る。
- 開発環境では、ログ行のエンコード・復元・改ざん検出、`trial --mock` の成果物を連絡路から取り出す流れまで、テストで確認済み。

## 案B（ClaudeがPod内で実行）: JupyterのAPI（RunPodのHTTPSプロキシ経由）

`https://<pod_id>-8888.proxy.runpod.net` のJupyter REST/WebSocket API（ターミナル・kernel）をClaudeが使い、コマンド実行・診断・ファイル取得を直接行う。

あなたの側で決める必要があるもの（私は変更しない）:

1. **ネットワーク許可:** Claudeの環境の Network access に、`*.proxy.runpod.net`（または対象Podのホストだけ）を追加する。現在は未許可のはず（未検証）。
2. **Jupyterの認証情報をClaudeの環境に渡す:** パスワードはPodごとに自動生成され、コンソールで見える。チャットには出さず、環境のシークレットとして設定する（Podを作り直すたびに更新が必要）。固定の値にするには、RunPodのアカウントの秘密情報を作成し、作成リクエストの `env` に参照を入れる必要がある。それにはキーに **Secretsの書き込み権限**が要り、現在のツールは `env` を送らない作りなので、コード変更と承認が要る。
3. **WebSocketの通過:** 環境のプロキシでWSが通るかは未検証。

リスク: JupyterのシェルはPodの全権限を意味する。Pod内にはPod専用のRunPodキー（`runpodctl`用。公式READMEの記述。そのPodだけを対象とする）があるはずで、`env` の出力などは扱わない運用にする。

## 案C: SSH（推奨しない）

アカウントへのSSH公開鍵の登録（`/v2/account/ssh-keys` の書き込み権限）、`startSsh: true`、直接接続用の `22/tcp`（公開IP）が必要。Claudeの環境からの生のTCP接続は、HTTPSプロキシ経由の制約で通らない見込み。除外する。

## 認証・権限のまとめ（変更はしない）

| 項目 | 案A | 案B | 案C |
| --- | --- | --- | --- |
| RunPodキーの権限 | 現在のまま（Pods読み書き＋ログ読み取り） | 固定パスワードを使うならSecrets書き込みが追加で必要 | SSH鍵の書き込みが必要 |
| Claude環境のネットワーク許可 | 不要（`api.runpod.io` は許可済み） | `*.proxy.runpod.net` が必要 | 生TCPが必要（現実的でない） |
| 環境のシークレット | 不要 | Jupyterのパスワード | 秘密鍵 |
| 人が行う操作 | Jupyterに1行貼る | 接続情報の設定のみ | 多い |
| 状態 | 実Podで `selftest`・ログ・小さな成果物の回収を検証済み | 未検証・未設定 | 非推奨・未設定 |

## 次回の承認済み試行の推奨手順

1. まず保存中Volumeの後片付け方針と、新しい試行の費用・停止目標・回収先を決める。現在の停止済みPodを再開する場合も、新しい計画と必要な承認を得る
2. 案Aの `selftest` で連絡路を再確認する（最初の1分を目安）。通れば `net` を実行する。`all` → `trial` はCUDAビルド・モデル本体取得・推論を含む未検証の後続工程なので、その作業範囲と費用の承認後に進める。Claudeは `read_pod_logs.py` で状況と成果物を受け取る
3. 案Aが通らない、または任意のコマンドによる診断が必要な場合だけ、案Bのネットワーク・認証・WebSocketの条件を確認し、設定変更や認証情報の受け渡しに必要な承認を得る

案Aは一方向のログ・成果物回収経路であり、任意のコマンドを遠隔実行できる接続ではない。長い導入中はログのthrottleにより進捗行が間引かれる場合がある。

## 固定実装へのリンク

以下のファイルは文書のみの `main` には含まれない。上記の固定コミットをcheckoutしてからコマンドを実行する。

- [`scripts/pod_run.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/pod_run.py)
- [`scripts/read_pod_logs.py`](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/scripts/read_pod_logs.py)
