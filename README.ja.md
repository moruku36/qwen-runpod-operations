# Qwen RunPod 運用記録

[English](README.md) | 日本語

更新日: 2026-10-02。**初回の有料実験では接続確認、独立Python環境、小さな成果物の外部回収まで進んだ。CUDAビルドとQwenのGPU推論は未検証。** 実験用の2つのPodは16:05頃JSTまでに停止したが、80GBのPod Volumeが2つ残り、保管料は合計約$1.07/日。撤収と最終Billing照合は未完了。詳細は[実験レポート全文](docs/trial-report-2026-10-02.ja.md)を参照。

## 目的と現在の範囲

Colabのコンピュートユニット補充を待たずに利用できる候補として、RunPodを検証する記録。まず既存のGradio UI、llama.cpp、27B Q8_K_L、公式mmprojを維持する。テキストチャット、画像理解、Web検索、CPU ASR、履歴が評価予定の対象で、画像生成・編集は対象外。今回の実験だけでは、RunPodが速い・安い・移行完了とは判断できない。

当初計画ではSecure CloudのOn-Demand A100 80GBを1台、最大2時間の有人セッションで試し、その後に同条件のA40 48GB比較を提案した。A100は既存設計との差を減らすための選択であり、Q8に80GBが必須と実証されたためではない。ServerlessやOpen WebUI連携は別の将来課題とする。

10月2日に確認した公開掲載単価はA100 80GBが$1.59/時、A40 48GBが$0.49/時。実際の起動画面の価格と在庫を優先する。比較には起動・ダウンロード・ビルド・待機・回収・終了確認を含め、推論秒数だけを請求額とみなさない。

## 初回実験で確認したこと

- US-MD-1の1台目: GitHub接続が `No route to host` で失敗。根本原因の切り分けは未完了
- EUR-IS-1の2台目: 最終の `selftest → net → venv` が完了し、ハッシュ固定のpip導入と独立環境内の `pip check` が成功
- 小さなログ・成果物の外部回収とSHA256一致を確認。推論結果一式の完全な回収は未検証
- 手動準備は240秒でタイムアウト。後のpip導入は約297秒で成功
- CUDAビルド、モデル全量取得・ハッシュ照合、ロード、推論、性能、画像、検索、ASRは未検証
- 2つのPodは停止済みで、Terminateは未実施。GPU稼働時間からの概算は約$1.71で確定請求ではない。残る80GB Volume2つは合計約$0.04444/時
- 実試行では保管料・税・手数料を含む総額$10の上限が承認された。最終実費と正確な予算残額はBilling未照合。次の試行への承認ではない

## 実験のサイクル

**新しい計画書 → その承認範囲で実験 → レポート・結果・再現情報を使い捨て環境の外へ保存して読み戻し検証 → 削除対象と影響を確認 → 承認済みの実験リソースを削除 → 次の検証は新しい計画書から開始**とする。

当初の標準はPod Volume80GBとContainer Disk20GB。A100を2時間使用し、保存検証後すぐ撤収する場合の概算は税等別で$3.21。Network Volumeの7日保持は標準にしない。明示的な保存例外を合意しない限り、履歴DB、モデル、キャッシュ、ビルド物は使い捨てとし、レポートと非秘密の再現情報を残す。

今回はその予定の撤収まで進まず、停止中のVolume2つを保持している。Stopでは保管料は止まらない。不可逆削除は外部保存の検証後、対象と消えるデータを示して実行直前の確認を得る。アカウント、既存・共有資源、保存済みレポートは対象外。毎回の再取得・再ビルドが短期保持より高くなる場合もあるため、次の計画で比較する。自動ハード予算上限は未実装。

## 固定した基準

- 元リポジトリ: [moruku36/qwen-multimodal-colab](https://github.com/moruku36/qwen-multimodal-colab)
- 確認済み上流commit: `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`
- 対象: [Qwen-Q8-Chat-Colab.ipynb](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/Qwen-Q8-Chat-Colab.ipynb)
- Notebook blob: `5e40a89f0253356c9c8d72698a93508f4d30d8aa`
- [上流PR35](https://github.com/moruku36/qwen-multimodal-colab/pull/35) は2026-10-01にマージ済み
- 旧 `Qwen-Multimodal-Colab.ipynb` は変更せず、今回の実行入口にしない

[baseline.json](baseline.json) は**当初の計画スナップショット**であり、現在の稼働状態ではない。`planning_only_no_gpu_run`、`resources_created: false`、支出未承認の値は実験前の状態を示す。image/lockのnull値も後のレポートを置き換えない。ファイルは変更せず、[英日baselineガイド](docs/baseline-guide.ja.md)で各項目と歴史的な範囲を説明する。

## ドキュメント

各文書に内容の対応する英語版・日本語版がある。コマンド、パス、識別子、固定値は翻訳で変更しない。

| 内容 | English | 日本語 |
| --- | --- | --- |
| 初回実験の最終レポート | [Report](docs/trial-report-2026-10-02.en.md) | [実験レポート](docs/trial-report-2026-10-02.ja.md) |
| 当初の運用計画と費用 | [Plan](docs/operations-plan.en.md) | [運用計画](docs/operations-plan.ja.md) |
| Notebook移植差分 | [Review](docs/portability-review.en.md) | [移植差分](docs/portability-review.ja.md) |
| 起動から終了まで | [Checklist](docs/session-checklist.en.md) | [チェックリスト](docs/session-checklist.ja.md) |
| 空欄の試行記録 | [Template](docs/experiment-template.en.md) | [記録テンプレート](docs/experiment-template.ja.md) |
| 確認した一次資料 | [Sources](docs/sources.en.md) | [一次資料](docs/sources.ja.md) |
| 初期CPU・クラウド準備の記録 | [Preparation](docs/cloud-env-prep.en.md) | [クラウド準備](docs/cloud-env-prep.ja.md) |
| 実装版の実行手順 | [Runbook](docs/runpod-runbook.en.md) | [実行手順](docs/runpod-runbook.ja.md) |
| 遠隔操作の設計 | [Remote operations](docs/pod-remote-ops.en.md) | [遠隔操作](docs/pod-remote-ops.ja.md) |
| 10月2日の詳細な試行記録 | [Trial log](docs/trial-log-2026-10-02.en.md) | [試行ログ](docs/trial-log-2026-10-02.ja.md) |
| Notebookの説明とセル | [Notebook guide](docs/notebook-guide.en.md) | [Notebookガイド](docs/notebook-guide.ja.md) |
| baselineスナップショットの項目 | [Baseline guide](docs/baseline-guide.en.md) | [baseline解説](docs/baseline-guide.ja.md) |

## 実装との境界

今回のmain向け公開更新はドキュメントと元のbaselineを対象とし、**実装ブランチをマージしない**。実行可能なRunPod Notebook、スクリプト、依存lock、テストをmainに追加する更新ではない。

実装は別の[commit 69795d54490ffaeff62f18ca80f6181ea674bc16](https://github.com/moruku36/qwen-runpod-operations/tree/69795d54490ffaeff62f18ca80f6181ea674bc16)にある。コードは最終Pod実行commit [30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4](https://github.com/moruku36/qwen-runpod-operations/commit/30d6c5d842a61d5fcc1e4e82cd39d1479f7ed6f4)と同一で、後続変更は試行ログのみ。手順書のコマンドはその実装checkoutが必要で、文書だけのmainでは実行できない。[Notebookガイド](docs/notebook-guide.ja.md)では実行セルを複製・変更せず、人向けの説明を英日で提供する。

次の作業には、範囲、時間・総支出上限、外部保存先、承認を明記した新計画が必要。次の有料起動前に、空・部分ログの扱い、kernel登録の検査、停止時のAPI `cost` の不一致を見直す。文書の閲覧・公開だけで新規リソース、再開、アクセス権追加、削除を許可しない。

このリポジトリは公開されている。秘密、認証URL、アカウント・支払情報、生会話、私的な識別子、モデル本体、Notebook出力をコミットしない。匿名化レポートと許可リスト方式の再現情報を使う。
