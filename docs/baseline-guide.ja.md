# baseline計画スナップショットの解説

[English](baseline-guide.en.md) | 日本語 | [README](../README.ja.md)

[baseline.json](../baseline.json) は2026-10-02当初の計画スナップショット。今回の文書更新ではバイト単位で変更しない。英語の識別子と説明文を英日ガイドで解説し、実行設定、固定値、承認記録、JSON schemaは変更しない。

**実際の結果は[10月2日の実験レポート](trial-report-2026-10-02.ja.md)を参照する。** 2つのPodが作成され、後に停止したが、Terminateは未実施。最終限定試行で `selftest → net → venv` が完了し、CUDAビルド、モデル全バイト検証、ロード、推論は未実施。80GB Volume2つが残る。元の `resources_created: false` や承認のbooleanは初期計画時点の値であり、現在の状態や将来の操作権限を示さない。

## 文書の識別と状態

| 項目 | 値と意味 |
| --- | --- |
| `schema_version` | `1`: 歴史的記録のschemaバージョン |
| `checked_at_utc` | `2026-10-02`: 計画の資料確認日。最終停止時刻ではない |
| `status` | `planning_only_no_gpu_run`: 実験前の計画状態。現在の実施結果は日付付きレポートが優先 |

## 上流ソース

| 項目 | 値と意味 |
| --- | --- |
| `repository` | `https://github.com/moruku36/qwen-multimodal-colab` |
| `commit` | `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`: 固定した上流ソース |
| `notebook` | `Qwen-Q8-Chat-Colab.ipynb`: 現在のチャット入口 |
| `notebook_git_blob` | `5e40a89f0253356c9c8d72698a93508f4d30d8aa`: 対象Notebookの正確なblob |
| `merged_pr` | `35`: 2026-10-01にマージ済みの上流PR |
| `original_notebook_git_blob` | `15836f710296a2d6bbc665bcddd169424716679d`: 選択したチャットNotebookとは別に記録した元Notebookのblob |

## runtimeの既定値

- `engine`: `llama.cpp`、`commit`: `4da6337767f973e2b4d0797e5b323d77d8565e4a`
- `image_digest` と `python_lock`: 計画時点では `null`。後のレポートに1台目の観測image digestと実装lock hash、その証拠の限界を記録している。nullは後の記録がないという意味ではない
- `chat_only: true`: 画像生成・編集コンポーネントを除外
- `server_host: "127.0.0.1"`、`server_port: 8012`: ループバックのmodel APIであり、公開推論endpointではない
- `chat_context_baseline: 32768`、`chat_context_initial_smoke: 8192`: 基準と初回smokeを別設定にする。どちらもGPU実測完了を表す値ではない
- `thinking: false`、`asr_device: "cpu"`、`asr_model: "small"`、`share: false`: 計画上の非秘密runtime設定

## モデル参照

これらは公開者のmetadataと固定予定参照であり、モデル全バイト取得の証拠ではない。両方とも `download_verified: false`、license表示は `apache-2.0`。利用・再配布前に固定revisionの実際のLICENSE/NOTICEと派生元条件を確認する。モデル本体はリポジトリに保存しない。

| 項目 | チャットモデル | 画像投影器 |
| --- | --- | --- |
| `role` | `chat` | `vision_projector` |
| `repo` | `huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF` | `ggml-org/Qwen3.8-27B-GGUF` |
| `file` | `Huihui-Qwen3.8-27B-abliterated-UD-DW-Q8_K_L.gguf` | `mmproj-Qwen3.8-27B-Q8_0.gguf` |
| `file_change_commit` | `ff733b88376282017b7f4675d6d96536c6ffa712` | `97c30c65c8d9a3e73f9fdfb50f1d1a669e9a2827` |
| サイズ情報 | `size_display: "27.3 GB"` | `size_bytes: 629247008` |
| `sha256_publisher` | `fb8413d0b5cec5ad7055e49630a986518ba90fdacc95a337aa535e8ff5bf0d16` | `2e968a6af97ce35d8971890b257b9b7edabf20ad91450501fa53162a19ee33eb` |

file-change commitはソースページの参照。実装では適切な不変revisionを固定・検証する必要があり、[最終レポート](trial-report-2026-10-02.ja.md)から実装の正確なpinsを参照できる。

## notesの翻訳と説明

1. 公開者metadataはローカルダウンロードの検証ではない
2. file-change commitはソースページ参照であり、実装では適切な不変revisionを固定・検証する
3. template digestと依存lockは実装まで未設定だった。歴史的な記述であり、後の証拠はレポートにある
4. このファイルは課金、deployment、自動停止、Terminateを許可しない
5. 標準は使い捨てPod Volumeであり、Network Volumeの7日保持ではない
6. 新しい実験には新計画が必要。毎回の構築が短期保持より高くなる場合もある
7. アカウント全体の削除は禁止対象。既存・共有資源と外部保存済みレポートも削除対象外

## 実験ライフサイクルの項目

| 項目 | 当初の値と解釈 |
| --- | --- |
| `policy` | `new_plan_then_experiment_then_verified_external_report_then_delete_experiment_resources`: 想定するサイクル。実際の削除は未完了 |
| `next_experiment_requires_new_plan` | `true` |
| `default_gpu` | `A100 80GB` |
| `max_session_hours` | `2`: 当初の有人セッション上限であり、新たな承認ではない |
| `default_storage.pod_volume_gb` | `80` |
| `default_storage.container_disk_gb` | `20` |
| `default_storage.network_volume_gb` | `0`: 標準では独立Network Volumeを使わない |
| `default_storage.retain_between_experiments` | `false`: 元の標準。実際には後で途中成果を保持し、保管料が続く |
| `estimated_two_hour_cost_usd_excluding_tax` | `3.21`: A100を2時間使い、保存検証後すぐ撤収する計画値。実際の試行支出ではない |
| `total_budget_preference_usd_approximate` | `10`: 当初の希望。後に記録された試行に限って、保管料・税・手数料込み総額$10が承認された |
| `spending_approved`, `deposit_approved` | `false`: 計画時の記録。後の有料試行が未承認だったという主張でも、今後の支出許可でもない |
| `automatic_hard_budget_cap` | `false`: 自動ハード上限は未実装 |
| `resources_created` | `false`: 計画時の記録。最終レポートでは実際の2つのPodを記録 |
| `external_export_and_readback_required_before_deletion` | `true`: 消すリソースの外へ保存して読み戻し確認が必要 |
| `reproduction_metadata_required` | `true` |
| `deletion_scope` | `only_resources_created_for_the_specific_experiment`: 既存・共有資源とアカウントを除外 |
| `irreversible_deletion_requires_action_time_exact_target_confirmation` | `true` |
| `execution_or_deletion_authorized_by_plan` | `false`: 計画への同意だけでは実行・削除を承認しない |
| `include_fresh_boot_download_build_export_time_in_each_trial` | `true`: 全工程を時間・費用見積に含める |

後のGPU費概算$1.71、続く保管料合計約$1.07/日、未検証工程、未完了のBilling照合はレポートで確認し、歴史的スナップショットを承認文書に書き換えない。
