# Qwen RunPod 運用計画

更新日: 2026-10-02 / 状態: 計画・静的調査のみ。RunPod の契約、入金、GPU 起動、モデル取得、GPU テストは未実施。

## 結論

RunPod を試す価値はある。まずは **Secure Cloud の On-Demand A100 80GB を1台、最大2時間の有人セッション**で、最新の Q8 チャット Notebook を移植・検証する。その後、**A40 48GB**を同条件で比較し、応答速度と品質を保てるなら日常利用先にする。

最初から Serverless や Open WebUI 連携に広げず、既存の Gradio UI、llama.cpp、27B Q8_K_L、公式 mmproj を維持する。A100 を初回に選ぶ理由は既存設計との差を減らすためで、Q8 チャットに80GBが必須と実証されたためではない。

2026-10-02確認の RunPod 掲載単価は A100 80GB $1.59/時、A40 48GB $0.49/時。実際の起動画面の価格・在庫を優先する。**Colabより必ず安いとは言えない**。起動・取得・ビルド・操作待ち・結果の回収・終了確認も費用に含めて比較する。

## 毎回の実験サイクル

**新しい計画書 → 承認した範囲で実験 → レポート・結果・再現情報を使い捨て環境の外へ保存して検証 → 対象を確認して実験用コンポーネントを削除 → 次の検証は新しい計画書から開始**とする。

初回の標準は **Pod Volume 80GB＋Container Disk 20GB**。A100 80GBを最大2時間使用し、保存確認と削除時の承認後にTerminateする場合、概算は **$3.21（税等別）**。Network Volumeの7日保持は標準にしない。履歴DB・モデル・キャッシュは検証後に削除する対象とし、秘密や生会話を含まないレポートと再現情報を残す。

全体で約$10に収めたいという予算希望はあるが、支出・入金の承認は未取得。自動予算制限も未実装。毎回の再ダウンロード・再ビルドもGPU課金時間に入るため、間隔の短い連続試行でも毎回削除する方が必ず安いとは限らない。不可逆削除は対象ID・消えるデータ・保存確認を示し、その都度実行直前に確認する。アカウント全体は削除対象にしない。

## 今回固定する対象

- 元リポジトリ: [moruku36/qwen-multimodal-colab](https://github.com/moruku36/qwen-multimodal-colab)
- main の確認済み commit: `9e5ff82cf604dd3b0377de81b89e1f4aaa422947`
- 対象: [Qwen-Q8-Chat-Colab.ipynb](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/Qwen-Q8-Chat-Colab.ipynb)
- Notebook blob: `5e40a89f0253356c9c8d72698a93508f4d30d8aa`
- [PR35](https://github.com/moruku36/qwen-multimodal-colab/pull/35) は2026-10-01にマージ済み
- チャット、画像理解、Web検索、CPU音声認識、履歴を対象とする。画像生成・編集は対象外
- 旧 `Qwen-Multimodal-Colab.ipynb` は変更せず、今回の実行入口にはしない

## 読む順番

1. [運用計画と費用](docs/operations-plan.ja.md)
2. [Notebook の移植差分](docs/portability-review.ja.md)
3. [起動前から終了までのチェックリスト](docs/session-checklist.ja.md)
4. [測定結果の記録用テンプレート](docs/experiment-template.ja.md)
5. [確認した一次資料](docs/sources.ja.md)

`baseline.json` は今回確認したソースとモデルの識別情報。モデル本体、秘密情報、会話履歴、Notebook出力はこのリポジトリに置かない。

## 次の実装で作るもの

このリポジトリは運用計画の管理用で、現時点では RunPod 対応 Notebook や自動削除スクリプトを提供しない。次の実装では、元ソースの固定 SHA を利用する薄い RunPod Notebook、依存関係の固定、チャット専用測定、終了確認を作る。元リポジトリへの変更は別途扱う。

実行前には、試行ごとの計画、利用時間・総支出上限・成果の保存先・RunPod利用規約への同意を確定する。今回の更新は計画変更のみで、有料起動・入金・実データ削除の承認を意味しない。RunPodリソースは未作成。
