# Qwen RunPod 運用計画

更新日: 2026-10-02 / 状態: 計画・静的調査のみ。RunPod の契約、入金、GPU 起動、モデル取得、GPU テストは未実施。

## 結論

RunPod を試す価値はある。まずは **Secure Cloud の On-Demand A100 80GB を1台、最大2時間の有人セッション**で、最新の Q8 チャット Notebook を移植・検証する。その後、**A40 48GB**を同条件で比較し、応答速度と品質を保てるなら日常利用先にする。

最初から Serverless や Open WebUI 連携に広げず、既存の Gradio UI、llama.cpp、27B Q8_K_L、公式 mmproj を維持する。A100 を初回に選ぶ理由は既存設計との差を減らすためで、Q8 チャットに80GBが必須と実証されたためではない。

2026-10-02確認の RunPod 掲載単価は A100 80GB $1.59/時、A40 48GB $0.49/時。実際の起動画面の価格・在庫を優先する。**Colabより必ず安いとは言えない**。初回構築、質問を考える時間、永続ディスクも費用に含めて比較する。

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

実行前には、利用時間・支出上限・保存期間・RunPod利用規約への同意を確定する。今日の計画作成は有料リソースの起動承認を意味しない。
