# 一次資料と確認範囲

確認日: 2026-10-02 UTC。GitHubは接続済みGitHubの読取、公開資料はWeb調査、Gemini会話とColab価格表示はクラウドブラウザで確認した。価格・仕様は将来変わるため実行日に再確認する。

## 元プロジェクト

- S1 [Q8チャットNotebook 固定commit](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/Qwen-Q8-Chat-Colab.ipynb): 旧branch名、Colab固定パス、モデル、share、起動/終了処理
- S2 [chat-only requirements](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/requirements-chat-colab.txt)、[core requirements](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/requirements.txt): 依存範囲
- S3 [colab.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/colab.py)、[config.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/config.py)、[llama_server.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/backends/llama_server.py)、[gpu_manager.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/gpu_manager.py)、[ui_chat.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/ui_chat.py)、[bench.py](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/src/qmc/bench.py): 移植レビューと測定上の注意の根拠
- S4 [Q8-first roadmap](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/docs/chat-only-roadmap.md)、[PR35](https://github.com/moruku36/qwen-multimodal-colab/pull/35): Q8維持、BF16後日比較、GPU検証未実施。PRに記載されたCPU CIは287 passed/2 skippedだが、本計画で再実行したものではない
- S5 [VRAM measurements](https://github.com/moruku36/qwen-multimodal-colab/blob/9e5ff82cf604dd3b0377de81b89e1f4aaa422947/docs/vram-measurements.md): 既存数値はQ4時点、Q8未測定

## モデルの配布元

- S6 [Huihuiモデルカード](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF)、[対象Q8ファイル](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/blob/main/Huihui-Qwen3.8-27B-abliterated-UD-DW-Q8_K_L.gguf)、[ファイル変更commit](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/commit/ff733b88376282017b7f4675d6d96536c6ffa712): 27.3GB、公開SHA256、Apache-2.0表示。モデル本体は未取得
- S7 [ggml-orgモデルカード](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF)、[対象mmproj](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF/blob/main/mmproj-Qwen3.8-27B-Q8_0.gguf)、[ファイル変更commit](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF/commit/97c30c65c8d9a3e73f9fdfb50f1d1a669e9a2827): 629MBと公開hash。raw pointerで629,247,008 bytesを確認

## RunPod公式

- S8 [GPU Pricing](https://www.runpod.io/pricing)、[AI server cost guide](https://www.runpod.io/articles/guides/ai-server-cost): 2026-09-27更新表示の掲載価格。後者でSecure Cloudという分類と同額を確認。実在庫・起動時見積は未確認
- S9 [Pod pricing](https://docs.runpod.io/pods/pricing)、[Billing](https://docs.runpod.io/accounts-billing/billing): 稼働課金、プリペイド、返金不可、自動補充、残高不足
- S10 [Storage options](https://docs.runpod.io/pods/storage/types): Container/Volumeの料金、永続性、暗号化機能の差
- S11 [Network volumes](https://docs.runpod.io/storage/network-volumes): 料金、独立永続化、データセンター制約
- S12 [Manage Pods](https://docs.runpod.io/pods/manage-pods)、[Zero GPU troubleshooting](https://docs.runpod.io/pods/troubleshooting/zero-gpus): Stop/Terminate/再開、GPU枯渇
- S13 [Connect to a Pod](https://docs.runpod.io/pods/connect-to-a-pod): Jupyterと認証、接続方法
- S14 [Expose ports](https://docs.runpod.io/pods/configuration/expose-ports)、[SSH](https://docs.runpod.io/pods/configuration/use-ssh): HTTPS proxyと公開範囲、timeout、SSH要件
- S15 [Templates overview](https://docs.runpod.io/pods/templates/overview): 公式/コミュニティtemplateの違い。今回の厳密なimage digestは未選定
- S16 [Serverless pricing](https://docs.runpod.io/serverless/pricing): 起動・実行・idle timeoutの費用。Podsの価格表と混ぜない

## Colabと検討の出発点

- S17 [Colab FAQ](https://research.google.com/colaboratory/faq.html): GPU/利用制限は変動、リソース保証なし
- S18 [Colab signup](https://colab.research.google.com/signup): 調査ブラウザではGBP表示。日本の契約価格・GPU別CU消費は未確認のため直接価格比較に使用していない
- S19 [RunPod利用規約](https://www.runpod.io/legal/terms-of-service): 2026-03-24更新表示。利用条件や停止権限があり、規約フリーと扱わない
- [ユーザー共有のGemini会話](https://share.gemini.google/kPN0Th0JnpV1): 検討背景として全文を確認。72B/Q4の試算、RunPodへの期待、自動終了の案が含まれる。AIの回答を価格・性能・契約条件の一次証拠として採用していない

## 未確認を明示する事項

- RunPodでの実起動、速度、VRAM、warm/cold start、モデルの品質
- A40/L40S 48GBで今回のQ8と32k contextを使った成功
- 本人の日本向けColab価格とA100の現在のCU消費率
- RunPodの本人アカウントの残高、決済方法、見積、地域在庫
- 実行用image digest、Python lock、移植Notebook、終了自動化

これらは推定や計画で補っており、動作検証済みと表現しない。
