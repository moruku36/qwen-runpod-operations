# ClaudeがPod内を扱う接続方法（整理。未設定・未変更）

目的: あなたがコマンドやログを仲介せず、ClaudeがPod内の実行・診断・成果の回収を行えるようにする。**このリポジトリでもあなたの環境でも、認証・権限・ネットワーク設定は何も変更していない。** 以下の選択肢は、承認があるまで使わない。

## 前提（確認済み）

- ClaudeがPodに対して使えるのは、REST v2（`api.runpod.io`）だけ。認証は環境側が付与し、Claudeはキーを見ない。現在のキーはPodsの作成・取得・stop・一覧・**ログの読み取り**まで通った。
- Claudeの環境は外向きHTTPSのプロキシ経由。許可リストにないホスト（例: `docs.runpod.io`、`huggingface.co`）は拒否される。Podへの直接のTCP接続（SSH等）は想定できない。
- `GET /v2/pods/{id}/logs` は `source=container|system` を選べる読み取りAPI（SSE）。停止中のPodにもシステム行（作成・停止）は残る。コンテナ側の行は、今回のPodでは空だった。

## 案A（推奨・新しい権限なし）: ログ経由の連絡路

Pod内の実行スクリプト（`scripts/pod_run.py`）が、短い状態行（`QMC|時刻|段階|内容`）と、小さな成果物（SHA256付きのbase64チャンク）を、Podの標準出力（PID 1）へ書く。ClaudeはREST v2の**ログ読み取りだけ**でそれを受け取る（`scripts/read_pod_logs.py <pod_id>`）。

- できること: 進捗・失敗・接続確認の結果の把握、レポート（許可リスト済みの `.tar.gz`）の回収。受け取った成果物はSHA256と許可リストで検証される。
- できないこと: Claudeが任意のコマンドを実行・診断すること。起動のきっかけ（Jupyterのターミナルへ `python3 scripts/pod_run.py all --bg` を1行貼る）は人が行う。
- 必要な権限: 現在のキー（Pods読み取り）で足りる。**追加の権限・ネットワーク設定は不要。**
- **未検証:** 「PID 1の標準出力への書き込みが、コンテナログとしてAPIから見えるか」は、実Podでまだ試していない。次のPodで、起動直後に `python3 scripts/pod_run.py selftest` を実行し、Claudeが `read_pod_logs.py` で `hello from the pod` と `selftest.txt` を受け取れるかを最初に確認する。見えなければ案Bへ進む。
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
| 状態 | 未検証（次のPodで `selftest`） | 未検証 | 非推奨 |

## 推奨手順

1. 次のPodで、まず案Aの `selftest` で連絡路を確認する（最初の1分）。通れば、そのまま `net` → `all` → `trial` を進め、Claudeが `read_pod_logs.py` で状況と成果物を受け取る。
2. 通らない、または任意のコマンドでの診断が必要になった場合に限り、案Bの設定（上の1〜3）を、あなたの判断で行う。
