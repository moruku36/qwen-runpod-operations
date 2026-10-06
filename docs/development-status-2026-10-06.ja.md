# 10月6日の到達点：C1受理済み／C2 v5無効状態／C3未実施

[English](development-status-2026-10-06.en.md)

開発状況はこの記録を優先してください。10月2日のREADME記載と以前の提案書は、
各時点の履歴です。当時の料金・残高・リソース保持状況は現在の実行許可ではありません。
sourceの公開はprovider有効化や有料セッションの許可を意味しません。

| 工程 | 到達点 | 実証の範囲 |
| --- | --- | --- |
| C1 | CPU controller、bridge、永続authority、queue/status、gateway、OWUI dispatchのsourceを独立reviewで受理 | provider・SSH・結果は合成試験。運用・課金の実証ではありません |
| C2 v5 | 無効状態のローカル設置と競合対策を独立reviewで受理。OWUI planner修正、backend import/startup、gateway確認とv5再適用を完了 | private入力、credential facility、provider、journal、tunnelは未有効化 |
| C3 | 未実施 | 実reprovision、ready確認、chat、idle Terminate、運用budgetには別途具体的な有効化計画が必要 |

目標は、認証済みOpen WebUI → localhost gateway → private tunnel → 正式に受理された
要求が必要とする時だけRunPodを再作成 → ready確認後のOpenAI互換chat → 結果exportと
readback、個別許可を経たidle時Terminate → 次回要求で再作成、という流れです。
起動lock、有限queue、固定のabsolute deadlineとbudgetを維持し、restart・abort・切断・
retryで許可期限を延ばしません。model一覧やtitle/tag/follow-upは起動許可を発行できません。
Terminateにはimmutable ownership、exact allowlist、export検証、action単位のauthorityが
必要です。import・既定constructorは実効果を拒否し、credential探索やsecret loggingを行いません。

OWUI 0.11.4のplannerは公開routeのsignatureとverified-user依存を保持し、manual wrapperを
後続のmodule利用より前に定義します。legacy aliasとapp.stateはmanual contextを持たない
元のinternal pipelineへ接続します。background処理は起動・推論permitを発行できません。
設置したdisabled hookは別途安全なbindingを行うまでon-demand chatを拒否します。
public health成功、非認証models/status 401、未入力login画面を確認済みです。
account/session情報は取得していません。

private受理sourceは50ファイルと補助証跡27ファイルでした。source manifest SHA256は
`4f9a58c0862e668f53d61bb320a96b04469e8fc6d34d0ce3e5e3691f9d044ed2`。
受理時はCPU unit test 274件、planner regression 11件、v5 native/record確認43件がPASS。
backend依存import、OWUI 174 module import、startup/shutdownもPASSでした。
拡張したnonmigration module確認では190件PASS、optional SDK不足15件でbaselineと同じです。
全optional機能の導入成功を示すものではありません。これらは過去の受理記録であり、
今回の公開CIとは区別します。公開版はpath・fixture・試験入力の匿名化とhook保持の説明修正を
含むため、private受理packetとのbyte一致は主張しません。

公開対象はCPU source/test、buildとtrial間のartifact binding修正、固定native tool準備、
C2の再利用可能な復旧primitiveと無効なoperator templateです。
[復旧制約](../scripts/c2/README.md)を確認してください。templateにはmachine固有path・
ownerを含めず、そのまま設置できる受理済みbundleではありません。非公開bundleのGit履歴、
deployment manifest、ACL snapshot、private入力、生ログ、browser profile、user ID、鍵、
model/cacheは公開しません。CIは通常のCPU runnerと合成fixtureを使い、provider credentialを
使いません。別upstream checkoutを要するtestは不在なら明示的にskipし、成功とは数えません。

公開時のWindows確認は依存package不要のcontract suite 278件と追加planner regression 7件が
PASS。PowerShell 5.1のnative fixtureはmain CAS 15件、hook/排他作成17件がPASSでした。
最初のTemp fixtureでは継承ACLによる差を検出し、C2で必要な保護ACLの前提を合成fileに
限定して設定しました。stage済みblobを個人path/identity、literal UUID、秘密/token形式、
除外artifactについて検査し、一致なしでした。ローカルpytest導入は配布先とのTLS handshakeで
失敗したため、広いLinux pytest suiteはGitHub CPU CIで確認します。
公開CIではWindowsのsocket timer競合も検出しました。absolute request budgetの残り時間だけを
使うsocket待機がmonotonic deadlineの直前に切れると、generic 502になっていました。
公開transportではこれをrequest deadline 504へ正規化し、明示cancelを優先します。
retryやbudget延長は追加しません。fake connectionの決定的なregressionで両経路を確認します。
旧CLIのcredential静的制約を維持し、全packageのambient credential lookupをASTで確認します。
bridgeの認証は明示的な入力です。

既に行ったA100 80GB／27B GGUF診断はload **347.956秒**、context **8192**、
1要求・2文字回答に成功し、cleanup/結果回収PASSでした。これはcached診断です。
cold full build/downloadは**17.5分以上**で、fresh reprovisionのstartup時間は未計測です。
cached loadとCPU試験はC3の無人運用の証明ではありません。公開作業ではGPU・RunPodの
新しいlifecycle操作を行っていません。

C3の残作業は、利用者が明示入力する認証済みapp subject、別々のinference/control credential、
review済み保存facility、SSH identityとresourceごとのhost key、immutable ownershipとaction grant、
具体的なwork/idle/absolute budgetとexport/cleanup reserve、立会いでの実reprovision・ready・chat・
cancel・idle・Terminate確認です。既存停止resource、無関係のOWUI拡張と他アプリは対象外です。

Colabは既に[PR #35](https://github.com/moruku36/qwen-multimodal-colab/pull/35)でmerge済みの
[`9e5ff82cf604dd3b0377de81b89e1f4aaa422947`](https://github.com/moruku36/qwen-multimodal-colab/commit/9e5ff82cf604dd3b0377de81b89e1f4aaa422947)
を維持します。今回のRunPod成果によるColab source差分はなく、重複PRは作りません。
