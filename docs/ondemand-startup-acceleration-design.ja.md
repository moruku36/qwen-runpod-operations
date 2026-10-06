> Design record only. Current accepted C1, disabled C2 v5 and pending C3
> are described in [the October 6 record](development-status-2026-10-06.en.md).

# オンデマンド起動短縮の設計メモ

更新: 2026-10-06。将来候補のローカル設計メモ。現在のC1実装・C2設定を変更するものではない。
外部image公開、RunPod template登録、volume作成、GPU起動は行わない。C2は本人入力待ちを継続する。

## 時間の記録と計測境界

既存成果物を使った診断では、A100 80GB・27B GGUF・context8192で **model load 347.956秒（約5分48秒）**、
1要求・2文字回答成功、cleanup／結果回収PASSが報告されている。
**cold full build/downloadの17.5分以上とは別の記録**で、両者を同じ起動時間として扱わない。
17.5分以上はfresh再作成全体の実測値や確定上限ではなく、既存設計のcold準備に関する記録・計画上の目安である。

| 工程 | 現在確実に分かる範囲 | 未確認・未計測 |
| --- | --- | --- |
| Pod割当・image取得・OS起動 | model loadとは別の前段 | 各所要時間とキャッシュ効果 |
| 依存環境準備・CUDA build・GGUF取得／hash検証 | cached model loadと分離する工程。現C1 bootstrapはgit取得→CMake CUDA build→GGUF取得→SHA256検証 | 当該診断の個別実測値。現C1 bootstrapにpip install工程はない |
| model load | 現行計測コードは `manager.ensure("chat")` の開始から完了までを `load_s` とする | ファイル読込、GPU転送、CUDA初期化等の内部時間配分 |
| warmup | 現C1のserver起動argvは `--no-warmup` を指定 | 元診断が同じargvで動いたか、347.956秒に内部warmupが含まれるか。元の実測JSON／serverログは本checkoutで未確認 |
| 最初の回答 | load後に別計測し、後続のwarm測定とも分ける | 繰返しwarm性能。1要求の成功から起動短縮率は算定できない |

事前ビルドimageやモデル保管による **短縮幅は未計測**。
templateだけで347.956秒のmodel loadがなくなるとは確認できていない。

## 推論エンジンimage＋templateの候補

想定する構成は、互換性を確認したCUDA runtime・必要ライブラリ・固定commitからビルドした
`llama-server` を含むimageをdigestで固定し、そのimageと起動設定をRunPod templateで参照する形。
templateは設定の再利用、imageは準備済みファイルの供給を担う。model loadやGPUメモリ上の状態は別に扱う。

現在のC1はPod作成DTOの `imageName` にdigest固定imageを指定できる構造だが、初期値はCPU fixture用。
`templateId` を使う作成計画、独自template、事前ビルド推論エンジンimageは未導入。
現在のbootstrapは毎回新しいsession領域を作り、CUDA buildとGGUF取得を実行するため、
imageを指定するだけではこの工程を省けない。

将来の変更では、image内のエンジンを確認して利用するbootstrap経路を別途実装・レビューする。
GPU architecture、CUDA／driver／runtime ABI、llama commit、binary hash、model pinを一致させ、
sessionごとの所有権・認証・readiness確認を維持する。現在のqwen-27b text／context8192を前提とする。
事前ビルドでCUDA compileや環境準備を省ける可能性はあるが、image取得と実GPUでの初期化・loadは残る。

## モデル保管の選択肢

| 候補 | 再作成時に省ける可能性がある処理 | 残る処理・費用 |
| --- | --- | --- |
| エンジンimageのみ＋GGUFを毎回取得 | CUDA build、imageに含めた環境準備 | GGUF取得・hash検証・model load。継続モデル保管は不要だが、registry保管費等は別途確認 |
| GGUFもimageに同梱 | 個別のGGUF取得、CUDA build | 大きくなるimageの取得・registry保管／転送条件・model確認・load。host側image cacheの有無で効果が変わる |
| エンジンimage＋独立Network Volume等のモデルキャッシュ | CUDA build、cache hit時のGGUF再取得 | 待機中も継続保管費。接続／配置条件、容量、hash確認、model load。Podとは別の保持・削除台帳が必要 |
| Pod Volumeを保持してStopする比較案 | 同一Volume上の成果物を検証して再利用できればbuild／取得 | 停止中のVolume保管費、再開可能性、成果物互換性、model load。現在のidle時Terminate方針を変更する別案 |

現在の **idle時Terminate→次回再作成** では、通常のPod内キャッシュは次回の保管場所にならない。
独立キャッシュを選ぶ場合は、その資源の維持費・保持期間・削除対象をPodのcleanupと分けて設計する。
既存停止Podや既存Volumeをこのメモに基づいて起動・削除・転用しない。

## 費用比較と後続の確認

比較するのは、繰返し起動で省ける課金時間と、待機中の保管費・registry費・image作成更新費。
概算は「起動回数×実測した短縮時間×起動中のGPU／storage単価」と
「保持期間×キャッシュ保管単価＋その他費用」を同じ期間で比べる。
GPU割当からimage取得・準備・load・cleanupまでを計測し、料金の計上開始と請求実績も確認する。

現在の実容量・最新単価・利用頻度・image pull cache hit率が未確認なので、損益分岐や削減額は算定しない。
image／モデルcacheがあっても、GPUメモリへの再load時間をゼロと仮定しない。
現在の起動lock、元のUSD／絶対deadline、ready後idle期限、export確認、Pod所有権／terminate対象制限は維持する。
将来の有料比較は独立した承認と新しい数値予算が必要で、この文書は実行承認ではない。

## ローカル根拠

- [C1 activation packet](c1-production-activation-packet.md): cached load347.956秒とcold準備記録の分離。
- [smoke.py](../qmc_runpod/smoke.py): `load_model`、`first_response`、`warm_runs` の計測境界。
- [c1_ports.py](../qmc_runpod/c1_ports.py): `PodSpec.image` とPod作成 `imageName`。
- [execution_adapters.py](../qmc_runpod/execution_adapters.py): `bootstrap_script`、`server_argv`。
- [bootstrap_execution.py](../qmc_runpod/bootstrap_execution.py): 認証付きreadinessと起動成果物確認。

公開ドキュメントの追加確認は親側の別作業。本メモはローカルsourceと引継ぎ記録に基づく。
