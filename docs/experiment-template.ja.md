# RunPod試行記録テンプレート

状態: 未実施。以下は記録欄であり実測値ではない。コピーして実施日ごとの記録を作る。秘密、会話全文、個人情報、認証URL、キーを含めない。

## 構成

- 実施日とタイムゾーン:
- 元ソースcommit / Notebook blob:
- RunPod用Notebook commit:
- GPU正確な名称 / VRAM / 台数 / データセンター:
- On-Demand/Spot / 1時間の実際の表示価格:
- template / image tag / digest:
- Python / pip / torch / CUDA toolkit / NVIDIA driver:
- llama.cpp commit / CUDA arch / build flags:
- モデルrepo / revision / ファイル名 / SHA256 / byte数:
- mmproj repo / revision / ファイル名 / SHA256 / byte数:
- context / 画像上限 / 出力上限 / thinking / 検索 / seed等:
- disk種類 / 容量 / mount先 / 保存期限:
- 依存lockの保存先:

## 時間と資源

| 項目 | 実測 | 備考 |
| --- | --- | --- |
| Pod作成から接続可能まで | 未測定 | |
| 依存install | 未測定 | |
| llama.cpp初回build | 未測定 | |
| model download | 未測定 | |
| cold model load | 未測定 | |
| cached再起動から利用可能まで | 未測定 | |
| warm短文 初回token 中央値/最大 | 未測定 | 同一10prompt |
| warm短文 完了時間 中央値/最大 | 未測定 | 出力token数も記録 |
| 画像1枚/複数枚 | 未測定 | サイズ・枚数記録 |
| 検索On | 未測定 | 検索待ち時間を区別 |
| CPU ASR | 未測定 | 入力秒数・誤り |
| baseline/peak VRAM | 未測定 | nvidia-smiで別プロセス分も測定 |
| CPU RAM peak | 未測定 | |
| disk使用量/空き | 未測定 | |
| 10ターン後/解放後VRAM | 未測定 | |

## 機能と安全性

- チャット/画像理解/検索/ASR:
- 履歴保存と再作成時の復元:
- 未認証アクセスの拒否:
- 外部公開ポートと内部model API:
- 取消/再生成/再ロード/再launch:
- OOM、offload、context自動縮退、その他エラー:
- 日本語の回答品質と使い心地:

## 費用と終了

- 課金開始/停止確認時刻:
- GPU使用時間と料金:
- Container/Volume/Network料金:
- 税・通貨・手数料:
- その他API料金:
- セッション合計 / 請求確定か暫定か:
- アプリ終了後に実施したRunPod操作:
- Pod停止/削除の確認:
- 残存storageと期限:
- 次の判断: 継続 / A40比較 / 修正 / Colabへ戻す / 完全撤収

比較には同じ固定promptを使い、変更点を1項目ずつ記録する。速度がよく見えるよう、失敗や長いcold startを除外しない。
