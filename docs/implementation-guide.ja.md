# 実装ガイド: 2026-10-02に使ったコード

[English](implementation-guide.en.md) | 日本語

2026年10月2日に書き、使ったコードの一覧、各部分の役割、実際にどこで何を実行したか、未検証の項目をまとめる。新しい有料実行・再起動・権限変更・削除を承認するものではない。結果と費用は[試行レポート](trial-report-2026-10-02.ja.md)を参照する。

## コードの由来（コミット対応表）

| コミット | 内容 |
| --- | --- |
| `052cff2` | クラウド環境の準備（`scripts/setup-cloud-env.sh`）: runpodctlとCPUチェック |
| `c9c5763` | 最初のランチャー: 固定値、パス、Gradio 6アダプタ、起動の安全装置、モデル・llama.cppの補助、レポート、REST v2のPod操作、ハッシュ付き依存、CPUテスト |
| `213ad9e` | 強化: 停止確認、作成タイムアウトの扱い、見積、レポートの内容、リポジトリルートの解決、cold/warmの分離、固定コミットでの取得 |
| `30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4` | 独立venv、段階ごとの実行器、ログ連絡路、ネットワーク確認。**最終試行でPodが実行したコード** |
| `69795d54490ffaeff62f18ca80f6181ea674bc16` | 試行ログの更新のみ。コードは `30d6c5d…` と同一 |

実装ブランチは試行後にmainへ統合した。履歴は保存される（squashやrebaseなし）ので、上のコミットと他の文書のパーマリンクは有効なまま。Podでの実行を正確に再現するときは、ブランチ先端ではなく `30d6c5d…` をcheckoutする。

## リポジトリの構成

```
qmc_runpod/            ランチャーのパッケージ（上流の `qmc` を包む。上流は変更しない）
scripts/               コマンドラインのツール（Pod実行器、Pod API、ログ読み取り、bundle検査、取得）
notebooks/             Qwen-Q8-Chat-RunPod.ipynb（薄く、秘密なし）
tests/                 CPUテスト（`30d6c5d…` で75件合格）
requirements-runpod.in / .lock.txt   入力と、ハッシュ付きの固定一式（CPython 3.11、85以上のパッケージ）
baseline.json          試行前に記録した固定値（計画時のスナップショット）
```

## パッケージ `qmc_runpod/`

| モジュール | 役割 |
| --- | --- |
| `pins.py` | 上流のコミットとNotebook blob、llama.cppのコミット、2つのモデルファイル（repo、ファイル、revision、SHA256）、ポート。`baseline.json` と一致するテストあり |
| `layout.py` | `/workspace/qwen` 配下のPodのパス（`QMC_WORKSPACE` で変更可）と、秘密でない環境。`/content`・Driveは使わない |
| `chat_ui.py` | Gradio 6のアダプタ。上流の `ui_chat` は `gr.Chatbot` に `type="messages"` を渡し、Gradio 6.29.0はTypeErrorにする。上流のUIを構築する間だけこの引数を外す |
| `launch.py` | 閉じる側に倒れる起動: ログイン値が両方必要、ポートの自動変更なし、`share` なし、`llama-server` は `127.0.0.1`、外部chat APIなし。起動後に未認証アクセスが拒否されることも確認 |
| `models.py` | 固定revisionの2ファイルだけを取得し、使う前にサイズとSHA256を照合 |
| `llama.py` | 固定コミットの `llama-server` をワークスペースにビルド。GPU archを推測しない |
| `smoke.py` | chat専用の測定。load・最初の（cold）要求・warm実行を分け、出力上限（`max_tokens`）を明示。基準はwarm 10件が揃ったときだけ評価 |
| `report.py`, `trial.py`, `provenance.py` | 許可リスト方式のレポート（未知のキーや秘密らしい文字列は拒否）、コミット・lockのhash・image・CUDA・ビルドの事実、全チェックの pass/fail/skipped、manifest付きbundleと読み戻し検証 |
| `podapi.py` | 認証情報を扱わないREST v2クライアント。作成の安全装置と見積、検索できる固有のPod名、「作成結果は不明」の扱い、期限付きの読み戻しによる停止。**terminate/deleteの関数は存在しない** |
| `envsetup.py` | 独立venvの補助（system site-packagesなし、ハッシュ付き導入、venv内の `pip check`、kernel登録） |
| `stages.py` | 再開できる段階ごとの実行器: `selftest`、`net`、`source`、`venv`、`build`、`trial`。状態ファイル、ログ、Podの標準出力への転送 |
| `logchannel.py` | ログのストリームを通る、イベント行とSHA256付きの成果物チャンク |

## スクリプト

| スクリプト | 用途 |
| --- | --- |
| `scripts/pod_run.py` | Pod上で段階を実行（`selftest`、`net`、`source`、`venv`、`build`、`trial`、`all`、`status`、`emit`）。`--bg` で切り離して実行。pipとCUDAビルドに時間制限なし |
| `scripts/pod.py` | `plan`（読み取りのみ）、`create`（有料。承認トークンと計画の名前が必要）、`find`、`status`、`stop` |
| `scripts/read_pod_logs.py` | 読み取り専用APIでPodのログを読み、状態イベントだけを表示し、成果物を復元して検証 |
| `scripts/verify_bundle.py` | Pod外に保存したレポートbundleの読み戻し検査 |
| `scripts/pod-bootstrap.sh` | このリポジトリを40桁のSHAで取得し、作業ツリーがきれいなことを確認 |
| `scripts/e2e_mock_ui.py` | 手動のCPU確認: 実際のchat専用UIをmockバックエンドでChromiumから操作 |
| `scripts/setup-cloud-env.sh`, `scripts/make-pod-bundle.sh` | クラウド環境の準備、Pod用ファイルのtarball |

## テスト

`tests/` はCPUだけのテスト（pytest）。固定した上流のcheckoutが要る（`QMC_UPSTREAM_DIR` を設定。ない場合、それが必要なテストはスキップ）。内容は、固定値、ColabやDriveのパスがないこと、起動の安全装置、Gradio 6アダプタとログイン付きのmock chat専用UI、モデルの検証、許可リストのレポート、偽のHTTPによるPod APIの挙動（見積、不明な作成結果、停止確認、terminateがないこと）、Notebookのルート解決、cold/warmの分離、独立venvのコマンド、ネットワーク確認、ログ連絡路。CPUでの予行（`pod_run.py trial --mock`）は、連絡路を通して読み戻したbundleを作る。

## 実際に何をどこで実行したか

| 項目 | 場所 | 結果 |
| --- | --- | --- |
| CPUテスト、Chromiumでのmock UI、実際のpipによる独立venv（約39秒）、`trial --mock` | 開発環境 | 合格 |
| ログ連絡路を通した `selftest`、`net`、`venv` | 実Pod、`EUR-IS-1`、コミット `30d6c5d…` | 合格（イベント行、SHA256が一致する成果物、全ホストに到達、ハッシュ付き導入が約297秒、venv内の `pip check`、kernel登録は別の確認で確認） |
| REST v2での作成・停止・再開 | 実アカウント | 作成と停止は動作。停止後もAPIの `cost` は1.59のまま（下記） |

**Podで未実行:** `source`、`build`（CUDAコンパイルとモデル取得）、`trial`、Notebook、モデルのロード、推論、画像、検索、ASR、性能測定。

## 既知の問題と未解決事項

1. 停止したPodのAPIの `cost` が稼働時の値のまま、コンソールはNot running。停止確認（`EXITED` かつ数値の0）はこの口座では成立しない。基準を見直すまで、コンソールを第二の根拠にする。
2. `venv` ステージは、登録コマンドの終了コードにかかわらず `kernel=qwen-venv` と出す。kernelは別の確認で確認した。
3. 長い導入のログ行は間引かれるため、進捗が止まって見えることがある。
4. `start` はPodツールの関数ではなく、10月2日の再開は一時的なAPI呼び出しだった。Terminateは意図的に入れていない。
5. `ensurepip` の代替（ホストのpipに `--python`）と、連続実行の段階（chain）は段階として実装していない。最終試行は、`selftest`・`net`・`venv` を順に実行する短いシェルのブロックを使った。
6. CUDAビルド、モデル取得、推論、使い心地の基準は、新しく別途承認された計画で検証する。

## 公開リポジトリでの注意

このリポジトリは公開されている。秘密、認証URL、アカウント・支払情報、生会話、私的な識別子、モデル本体、Notebookの出力をコミットしない。レポートは許可リスト（`qmc_runpod/report.py`）を通す。
