# RunPod Notebookガイド

[English](notebook-guide.en.md) | 日本語 | [README](../README.ja.md)

[commit 69795d54490ffaeff62f18ca80f6181ea674bc16のQwen-Q8-Chat-RunPod.ipynb](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/notebooks/Qwen-Q8-Chat-RunPod.ipynb)の人向けの説明とセルの役割を提供する。Notebookは試行後に変更せずmainへ統合した。このガイドはコードセルを複製・変更しない。固定Notebook内のコードコメントと識別子はその実装に従う。

**2026-10-02の状態:** 最終限定試行で完了したのは `selftest → net → venv` のみ。NotebookのCUDA・モデル・推論段階は実GPUで完了していない。2つのPodは停止中で保管領域が残る。次の実行には新しい承認済み計画が必要。[実験レポート](trial-report-2026-10-02.ja.md)を参照。

## 元の冒頭説明と適用範囲

実装Notebookの題名は「Qwen Q8 Chat on RunPod (chat-only)」。冒頭では `python scripts/pod_run.py all` により独立venvを準備し、kernel **Python (qwen-venv)** を選ぶ手順を記載している。Notebook自体からsystem Pythonへインストールしない。

**重要:** `all` は確認済みの限定工程を超える広い実行経路。最終試行では実行しておらず、本ガイドも実行を許可しない。[実行手順書](runpod-runbook.ja.md)と次の承認済み計画に従い、工程を個別に選ぶ。コマンドには実装を含むcheckoutが必要（統合後のmain、または厳密に再現するときは固定コミット）。

RunPod管理画面 → ConnectからPodのJupyterへ接続してNotebookを開く。リポジトリ直下または `notebooks/` から開始できる。Cell 1〜5を順に進め、Cell 4bは範囲内で必要な場合のみ実施し、最後にCell 6でアプリを終了する。認証値はNotebookに保存せず、実行時の非表示入力でGradioログインを設定する。

- Cell 4は出力上限64トークンの短い要求1件。smoke確認であり性能測定ではない
- 任意のCell 4bはCell 4成功後、実行者が承認範囲内で選んだ場合のみwarm測定を別に実施。warmの証拠がなければ性能確認は `skipped` のまま
- アプリを止めてもPodは止まらない。適用される承認に従い、外部から `python scripts/pod.py stop <pod_id>`（REST v2）または管理画面で停止する

## Cell 1 リポジトリの場所と独立kernel

環境、ハッシュ固定導入、`pip check` は `python scripts/pod_run.py venv` で準備し、Podのsystem Pythonへ入れない。PyGObject/pycairo等のOSパッケージをこの検査に混在させたことが前の失敗につながった。

リポジトリ検索は直下、`notebooks/`、明示した `QMC_REPO_ROOT` から動作し、`qmc_runpod/pins.py` と `requirements-runpod.lock.txt` を探す。venv外では続行しない。Kernel → Change kernelで **Python (qwen-venv)** を選び、存在しなければ先に準備する。

レポートのチェックはすべて `skipped` で始まる。運用commitとclean状態を記録し、sourceディレクトリを作成、固定上流commitを取得し、上流HEADとNotebook blobを照合する。不一致なら中断。venv内で `pip check` を実施し、lock SHA256、Pythonバージョン、環境パスを記録する。clean表示やCPUテストをGPU検証とみなさない。

## Cell 2 環境、CUDAビルド、固定モデル

context8192・Web検索offの非秘密環境値を設定する。context32768は後の別試行。固定commitのllama-serverを導入し、ビルド時間、CUDA architecture、runtime tag、Release設定、今回ビルドしたかを記録する。指定モデルを固定revisionから取得し、SHA256検証を記録する。

GPU割当時間、通信量、ディスク容量を消費し得る工程で、最終限定試行では未実行。HEAD応答だけではこのセルのモデル取得・バイト検証が完了したことにならない。

## Cell 3 認証付きチャット専用UI

`QMC_AUTH_USER` と `QMC_AUTH_PASSWORD` の両方を非表示入力し、両方がなければ起動しない。チャット専用アプリを起動して未認証アクセスの拒否を確認する。表示されるproxy URLは接続経路であり、それ自体が認証ではない。`SHARE=False` とループバック限定のmodel APIを維持し、認証値や実際の認証URLをレポート・Gitに入れない。

## Cell 4 cold要求1件のsmoke確認

モデルロードを別に計時した後、64トークン上限の短い要求1件を送り、GPUメモリを調べる。エラーがなくstream deltaが1件以上のときだけfirst responseをpassにする。このcold要求でwarm性能を判定しない。

## 任意のCell 4b warm測定

`first_response == pass` が必要。固定の日本語prompt10件を各256トークン上限で実行し、warm集計を記録する。正常なwarm10件がなければ性能基準は `not_evaluated`。未実施・失敗を成功と表示しない。

入力を変えると比較条件が変わるため、実行Notebookのpromptはバイト単位で変更しない。英語読者向けにも意味が分かるよう、ここでは内容を列挙する。

1. 日本の四季を50字で説明して。
2. 1から10までの和は？
3. Pythonでリストを逆順にする方法は？
4. 富士山の高さは？
5. 挨拶を一言。
6. Gitのcommitとpushの違いは？
7. 味噌汁の基本の作り方を3行で。
8. TCPとUDPの違いを一言で。
9. 今日の気分を一言で。
10. 素数とは？

## Cell 5 許可リスト方式のレポートと回収bundle

モデルload/coldの各測定値、出力上限、stream delta、finish reason、取得できた場合のfirst delta時間とVRAMを記録する。warm集計は存在する場合のみ追加。再現情報には運用commit、依存lock、モデル記録、GPU・ビルド、llama-server/Gradioバージョン、許可した設定（context、thinking off、検索off、CPU small ASR、TTS off、share off、chat-only）を含める。

runsディレクトリへJSONレポートと `.tar.gz` bundleを作成する。Jupyterからダウンロードし、Pod外でファイルを読み戻してmanifest/hashを照合した後に `report_exported` を確認する。ローカルパスの表示だけでは外部保存の検証にならない。小さなselftestの回収成功で実推論bundleの回収成功を主張しない。

## Cell 6 アプリの停止

`launch.stop_app(APP)` が止めるのはアプリとモデルのプロセスだけで、Pod停止・削除は行わない。外部のRunPod停止経路を使い、実状態、システム停止記録、管理画面のCompute/Container表示を調べる。実試行では `EXITED` と画面停止表示にもかかわらずAPIの `cost` が1.59のままだった。`cost == 0` だけを停止確認の条件にできない。実請求は別途Billingで照合する。

Stop後もPod Volumeと保管料は残る。Terminateは別操作でContainer/Pod Volumeのデータを不可逆に消すため、外部保存の検証と対象を示した実行直前承認が必要。本ガイドは起動、広い `all` 実行、再開、アクセス追加、削除を許可しない。
