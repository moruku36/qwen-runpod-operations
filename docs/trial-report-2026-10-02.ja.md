# RunPod 初回実験レポート 2026年10月2日

[English](trial-report-2026-10-02.en.md) | 日本語 | [README](../README.ja.md)

対象: `moruku36/qwen-runpod-operations`  
記録範囲: 2026年10月2日 14:28〜16:05頃 JST（UTC+9）  
結論: **Podの起動・停止、接続確認、独立Python環境、ログと小さな成果物の回収まで進んだ。QwenのGPU推論には到達していない。**

## 1 今日できたこと

最終の限定試行では、`selftest → net → venv` が完了した。GitHub・PyPI・固定モデル配信先へ到達し、OS側のPythonパッケージと分離した環境で、ハッシュ固定の依存導入と `pip check` が通った。Pod外からログを読み、テストファイルをSHA256一致で回収する経路も確認できた。[試行ログ](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/trial-log-2026-10-02.ja.md)

16:05頃にGPUを停止した。2つのPodは削除せず、途中成果を保持している。**80GBのVolumeが2つ残り、保管料は合計約$0.0444/時、約$1.07/日で続く。** 当初計画の「回収確認後に実験用リソースを削除する」工程は未完了である。

| 検証項目 | 到達点 |
| --- | --- |
| A100 SXM 80GBの起動・停止 | 実施。コンソール表示とAPI報告を照合 |
| 通信 | 1台目はGitHub接続失敗。2台目は対象配信先への接続確認に成功 |
| Python依存環境 | 独立venv、ハッシュ固定導入、venv内の `pip check` に成功 |
| Jupyter kernel | 独立した検査で `qwen-venv` の登録を確認。画面の一覧・選択は未確認 |
| Pod外へのログ・成果物回収 | 状態行と小さな `selftest.txt` の回収・SHA256一致を確認 |
| CUDAビルドとモデル実行 | 未実施 |
| 性能・機能評価 | 未実施。速度、VRAM、32k、画像理解、Web検索、ASRは評価できない |

### 証拠の扱い

本書は、本人の端末出力・コンソール画面、Claudeの実行報告とGitHubの試行ログ、固定コミットの読み取り確認を突き合わせたもの。Podでの操作や75件と報告されたテストを、本書作成時に再実行してはいない。最終停止は会話内の報告と画面確認による追記であり、参照した16:03時点の試行ログにはまだ含まれていない。秒まで確認できた時刻以外は概数として扱う。

## 2 実験の経過

各PodはSecure CloudのA100 SXM 80GB × 1、GPU単価$1.59/時、Container Disk 20GB、Pod Volume 80GBで作成した。3回目は2台目の再開であり、新しい3台目は作成していない。

| 時刻 JST | 出来事と結果 |
| --- | --- |
| 14:28:02 | 1台目 をUS-MD-1に作成 |
| 14:35頃まで | GitHubへgit・curlとも接続失敗。IPv4でも `No route to host`。Hugging FaceのトップへのHEADは200 |
| 14:35:38〜40 | Stop要求がHTTP 200。14:35:40のシステム停止行をClaudeが確認。本人のコンソールでもCompute・Container storageが `Not running` |
| 14:50:42 | 2台目 をEUR-IS-1に作成 |
| 15:19頃まで | GitHub・PyPI・モデル配信先の接続確認成功。`213ad9ed…` のclean checkoutを本人が確認。Notebook Cell 1の依存導入後、OS側依存が原因で `pip check` 失敗。手動venv準備も240秒で打ち切り |
| 15:19:48 | 2台目をStop。HTTP 200、読み戻し `EXITED`。本人のコンソールでも停止確認 |
| 15:37:04〜05 | 2台目を再開。Start要求HTTP 200、`RUNNING` をClaudeが確認 |
| 15:57:02 | 本人がJupyterへ貼った1ブロックから、固定コミット `30d6c5d8…` で限定試行を開始。run IDは公開版では伏せる |
| 15:57:02 | `hello from the pod` と `selftest.txt` をログAPIから受信し、ファイルのSHA256一致をClaudeが確認 |
| 15:57:06 | `net` 成功。GitHubの3リポジトリ、PyPI、pythonhosted、固定GGUF・mmprojの配信先を確認 |
| 15:57:06〜16:02:28 | venv作成約24秒。ハッシュ固定のpip導入約297秒。venv内の `pip check` 成功 |
| 16:02:33〜35 | kernel登録後、別検査で `qwen-venv` がpresent。限定処理が完了 |
| 16:05頃 | 最終StopがHTTP 200、両Pod `EXITED` とClaudeが報告。最終共有画面では2台目のCompute・Container storageが `Not running`、80GB Volumeが残存 |

15:37の再開から15:57の実処理開始まで、約20分の待機時間もGPU課金時間に含まれる。最終画面には1台目は写っていないが、それ以前に停止を確認し、最終API報告でも `EXITED` だった。[時刻の一次記録](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/trial-log-2026-10-02.ja.md)

## 3 詰まった原因と分かったこと

### GitHubへの接続障害

1台目では通常接続とIPv4接続の両方が数ミリ秒で `No route to host` となった。一方、Hugging Faceのトップには到達した。2台目では同じGitHub接続障害は再現しなかった。

**根本原因は未確定。** ホスト・経路・一時障害などを切り分けていないため、US-MD-1全体の障害、`globalNetwork` の値、compliance表示の有無を原因とは断定できない。1台目のネットワークコマンド出力は当時のログAPIには残らず、本人の端末出力が根拠となる。次回は起動直後の接続確認と、その出力保存を先に行う。

### システムPythonとアプリ依存の混在

最初のCell 1は `/usr/bin/python` を使用していた。`/usr/lib/python3/dist-packages` にあるPyGObject 3.42.1がpycairoを要求し、`pip check` が失敗した。プロジェクトのlockにPyGObject・pycairoは含まれない。ここで観測した問題は、OS側パッケージを含む環境をアプリの依存検査に使ったことだった。

修正版はCPython 3.11用のハッシュ付きlockを、system site-packagesを含まない独立venvへ導入する。最終試行ではensurepipが利用でき、venv内の `pip check` まで成功した。OS側の不足を場当たり的に足すより、検査対象を分離する方針が有効だった。[環境分離の実装](https://github.com/moruku36/qwen-runpod-operations/blob/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4/qmc_runpod/envsetup.py)

### 240秒のタイムアウト

手動の隔離環境準備は240秒で終了コード124となり、OPEN・KERNELの完了表示が出なかった。この出力だけでは、依存解決の破綻やpip自体の故障までは示さない。

後の試行ではpip導入だけで約297秒を要して成功した。**240秒の制限は、少なくとも今回観測した所要時間には不足していた。** ただし別実行のため、前の試行も継続すれば必ず成功したとまでは言えない。今後は工程ごとの制限、全体の費用・時間上限、進捗確認を分ける。今回の約5分を、次回の完了時間の保証にはしない。

### ログ経路と成功判定

PID 1の標準出力へ短い状態行とハッシュ付きデータを送る方法が、実PodのログAPIまで届くことを確認できた。回収した `selftest.txt` の期待SHA256は次のとおり。

`467e5a2b0ffc26ed3a065a9b8dbfbff233d6c978814172af05e215a2df910786`

この成功が証明するのはログ受信と小さなテストファイルの復元である。任意コマンドを外部から実行する機能や、実際の推論結果一式・大きなbundleの欠落なし回収は未実証。最初のコマンド投入は本人が行い、運転判断の連絡にも人の仲介が残った。

停止後には、CLIから既存の実行セッションへ報告確認用の読み取り依頼を送り、キューへの受理まで確認した。返答を自動で受け取る経路は未確認であり、Podの直接操作や自動修復を実証したものではない。

読み取り確認では、次の改善点も見つかった。

- `read_logs` はHTTPエラー以外の例外を握りつぶし、空・部分ログを返し得る
- 受信物がゼロでも受信スクリプトが終了コード0になり得る。新しいrun ID、期待イベント、期待ファイル、ハッシュ一致を別々に判定する必要がある
- kernel登録コマンドは `check=False` で、その終了コードを成功状態へ反映していない。今回は別のKernelSpec検査が根拠であり、`state=ok` の行だけを成功証拠にはできない
- stderrの出力やログの途切れだけで失敗を決めず、終了コード・例外・成果物・工程状態を合わせて判断する

[ログ取得コード](https://github.com/moruku36/qwen-runpod-operations/blob/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4/qmc_runpod/podapi.py) / [受信スクリプト](https://github.com/moruku36/qwen-runpod-operations/blob/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4/scripts/read_pod_logs.py) / [工程の実装](https://github.com/moruku36/qwen-runpod-operations/blob/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4/qmc_runpod/stages.py)

### 停止表示とAPIの不一致

Stop後にAPIのstatusが `EXITED` になり、コンソールでCompute・Container storageが停止しても、APIの `cost` は1.59のままだった。値の意味・更新タイミング・仕様との差は未解決であり、GPU継続課金の証拠にも、請求ゼロの証拠にもできない。

現行コードの「`EXITED` かつ `cost == 0`」だけでは今回の停止を確認できなかった。次回までに状態・システム停止ログ・コンソール表示を区別した停止判定へ見直し、実額はBillingで照合する。[現行手順書](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/runpod-runbook.ja.md)

## 4 費用

合意した実験予算は**保管料・税・手数料を含む総額$10以内**。この承認は当日の限定試行に対するものであり、次回の試行を自動で承認するものではない。

| 稼働区間 | 時間の目安 | GPU費の概算 |
| --- | --- | ---: |
| 1台目 14:28:02〜14:35:40 | 約7分38秒 | $0.20 |
| 2台目 14:50:42〜15:19:48 | 約29分06秒 | $0.77 |
| 2台目再開 15:37:05〜16:05頃 | 約28分 | $0.74 |
| 合計 | 約65分 | **約$1.71** |

上表は$1.59/時を稼働時間に掛けた**GPU費のみの概算**で、請求確定額ではない。稼働中のディスク費、停止後の保管料、税・手数料を含めていない。途中のアカウント画面だけでは、その後の試行を含む総費用を確定できない。途中に報告された累計約$1.20は最終累計として採用しない。**確定支出と$10予算の正確な残額はBilling未照合のため不明。**

停止中Volumeは公式単価$0.20/GB/月。80GB × 2を月720時間で換算すると、$0.04444/時、24時間で約$1.0667となる。画面の各約$0.022/時とも整合する。保持する限り費用が増えるため、明日に持ち越しても無料ではない。[RunPod公式料金](https://docs.runpod.io/pods/pricing)

## 5 残しているものと保存の限界

| 対象 | 最終の扱い |
| --- | --- |
| 1台目（US-MD-1） | 停止済み、80GB Volume保持、Terminate未実施 |
| 2台目（EUR-IS-1） | 停止済み、80GB Volume保持、Terminate未実施 |
| 2台目 `/workspace/qwen` | 旧checkoutと `isolated-*` の途中成果を保持する意図で停止 |
| 2台目 `/workspace/qwen-r3` | 最終コードと、`ws` 配下のvenv・ログ・状態等を保持する意図で停止 |
| Pod外 | GitHubのコード・試行記録、Claudeが回収・検証した小さなselftest成果物、本レポート |

停止後のファイル一覧を再読していないため、保持対象の完全性・再利用可否は未確認。モデル本体の取得完了や推論結果bundleの保存を主張できる段階ではない。

公式仕様では、通常のStopでContainer Diskは消去され、Volume Diskの `/workspace` は保持される。kernelの `--user` 登録はユーザーディレクトリ側にあるため、再開時に残るとは限らない。venvも参照先Pythonやイメージとの互換性を再確認する必要がある。Volumeはホストに依存する保管場所であり、外部バックアップの代わりにはしない。[ストレージ仕様](https://docs.runpod.io/pods/storage/types) / [StopとTerminate](https://docs.runpod.io/pods/manage-pods)

1台目のシステムログには「network volume」という文言もあったが、作成条件とコンソール単価に基づき、本書ではPod Volumeとして扱う。独立したNetwork Volumeの作成を確認したものではない。

最新の判断は途中成果の保持であり、新規リソース作成・再開・Terminateをこのレポートが許可するものではない。削除する場合は、必要な成果をPod外で読み戻したうえで、Pod ID・消えるデータ・保存先を示して実行直前の承認を取る。

## 6 次回の手順

**次回は新しい実験計画を作り、無料または既存のCPU環境で準備を終えてからGPUを再開する。**

1. **費用と保存を先に確定する**  
   Billingで実額と残額を確認する。残すVolumeと期間、回収対象、削除対象を決める。不要な保管を続けないよう、削除は対象を限定して別途承認を得る。
2. **GPUを使わずに再現性を整える**  
   kernel登録の終了コード、ログ受信の空・部分結果、停止判定を修正して検査する。依存lockとPython・イメージを固定し、再利用可能な環境や事前構築イメージを検討する。README・runbookの現在状態と、baselineの歴史的な計画スナップショットを区別する。今回のドキュメント更新ではその区別を明記し、baseline.jsonの値は変更していない。
3. **起動前に実行と回収の入口を用意する**  
   実行コミット、工程、run ID、実行開始方法、成果回収先、最大費用・停止条件を計画に書く。Jupyter直接操作やWebSocket接続は未検証であり、導入する場合の認証・権限・通信設定は別途判断する。SSHは今回設定していない。
4. **承認後のPod上では段階的に進める**  
   接続確認とログselftestを先行し、失敗なら停止。独立venv・kernelを検査してから、固定上流の `source`、CUDA `build`、固定モデルの全バイト取得とSHA256照合へ進む。今回は `source`・`build`・`trial`・`all` を最終限定試行で実行していない。
5. **最初の推論を小さく確認する**  
   loadとcoldを分け、まず64トークン上限で1件だけ確認する。warm 10件×256トークン等の測定や、32k・画像・検索・ASRへの拡張は、次の承認済み範囲に含まれる場合だけ行う。CPU mockの成功を実GPUの成功に読み替えない。
6. **回収と終了を実験の一部にする**  
   許可リスト済みreport・bundleをPod外へ保存し、ファイル一覧・ハッシュ・全チェックを読み戻す。Stopを実状態で確認し、Billingを照合する。保存が検証できた後、承認済み対象だけを削除する。

今回の収穫は、費用を使う前に固めるべき準備工程と、成功を確認する証拠が具体化したことにある。QwenのRunPod適性やColabとの費用・速度比較は、GPUビルドと実推論を完了してから判断する。

## 7 再現情報と参照先

- 運用リポジトリ: [moruku36/qwen-runpod-operations](https://github.com/moruku36/qwen-runpod-operations)
- 調査ブランチ: `claude/modest-knuth-8z1698`。このレポートの公開後に、文書、続いて実装をmainへ統合した。Podが実行したコードは引き続きコミット `30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4`
- 今回参照した試行ログのコミット: `69795d54490ffaeff62f18ca80f6181ea674bc16`。docs-onlyの変更で、Podが実行したコード（`30d6c5d…`）とコードは同一だがコミットとしては区別する
- 最終Pod実行コード: [`30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4`](https://github.com/moruku36/qwen-runpod-operations/commit/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4)
- 2回目に本人がclean checkoutを確認したコード: `213ad9ede6fb44becd5e642813093b4102f10edc`
- 最終コードの依存lock SHA256: `d9fb03b78fed58478ebe7e67023d93d331e5b9b8ffcba613f267d4599efdc8d0`。GitHubの固定コミットから取得したファイルを本書作成時に計算した値で、Pod内ファイルを再取得した値ではない
- 上流Notebook固定commit: `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`、Notebook blob: `5e40a89f0253356c9c8d72698a93508f4d30d8aa`
- llama.cpp固定commit: `4da6337767f973e2b4d0797e5b323d77d8565e4a`
- モデル予定: Huihui Qwen3.8 27B Q8_K_Lと公式Qwen3.8 27B mmproj。固定revision・予定SHA256は [`pins.py`](https://github.com/moruku36/qwen-runpod-operations/blob/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4/qmc_runpod/pins.py) に記録。HEAD 200は到達確認であり、モデル全量取得・ハッシュ検証済みを意味しない
- pythonhostedのHEAD 404は、要求したルートURLへのHTTP応答を確認したもの。パッケージ取得成功や対象ファイルの存在を示す判定とは区別する
- 1台目のシステムログに記録されたimage: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`、digest: `sha256:61a4aafb0094cd773f11eefa378929d5a687bd775febeb78eac62fc824141fb5`。2台目・再開後の実digestを独立取得した記録としては扱わない
- [試行ログ](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/trial-log-2026-10-02.ja.md)、[実行手順](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/runpod-runbook.ja.md)、[接続方式の設計](https://github.com/moruku36/qwen-runpod-operations/blob/69795d54490ffaeff62f18ca80f6181ea674bc16/docs/pod-remote-ops.ja.md)

本公開版ではPod識別子、アカウント残高、支払設定、秘密情報、認証値、会話履歴やモデル本体を掲載しない。これは初回実験の記録であり、新たな支出・接続権限変更・再開・削除への承認書ではない。
