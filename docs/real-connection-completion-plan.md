> Design record only. Current accepted C1, disabled C2 v5 and pending C3
> are described in [the October 6 record](development-status-2026-10-06.en.md).

# 実接続の残作業・完成条件の固定版 v1

作成: 2026-10-05 22:47 UTC。Phase3aは受入済み、Phase3bは境界修正後の再レビュー待ち。
この文書は追加の計画成果物であり、レビュー中の6ファイル、Phase3aの4ファイル、
以前の11ファイルを変更しない。実アクセスの適用や課金操作を認める文書ではない。

## 完成する機能と判定範囲

v1は受入済みのtext-only qwen-27bのJSON/SSE仕様を接続する。
経路は「認証済みOWUI backend → private localhost HTTP gateway → 検証済みprivate
tunnel → セッション所有Podのllama-server OpenAI互換chat」。OpenAIサービスを使わない。
モデル・状態照会は起動・推論・lease更新を起こさない。正当な手動chatだけが、事前に
承認されたsession grantの範囲で必要なPod再作成・準備・推論を要求する。idle/absolute
deadlineで推論を止め、検証済みexportと明示的な削除権限を満たしてTerminateし、次の
承認セッションで再作成する。既存の停止Pod、既存OWUI拡張、無関係の既存アプリは現作業対象外。

「コード完成」「実アクセス設定済み」「実証済み」を別の到達状態として記録する。
CPU/mockのPASSを本番完成と呼ばない。以下W0–W10がこの範囲の全残作業である。
以後見つかった問題は該当Wの未達・修正として管理し、未記載の必須条件を暗黙に追加
しない。新機能・対象変更を必要とする場合は、変更理由と完成条件の差分を先に提示する。

## 全残作業と完了の証拠

| ID | 作業と必要な接続 | 無料コードで完了する証拠 | 実情報・設定で止まる点 |
| --- | --- | --- | --- |
| W0 | 実行環境を固定: OWUIの版/起動方式/host namespace/サービス名、対象ユーザーと認証済みmanual/task route、モデルserver pin、ポートとstate/export候補を1枚にする。 | 依存するinterface/設定案/rollback案に未解決値が残らない。秘密値は含めない。 | 実OWUIの非秘密runtime情報がこのcheckoutにはない。親の既知情報/許可済みread-only環境から取得し、不足分だけ確認する。 |
| W1 | 本番用HTTP入口: /v1/models、chat JSON/SSE、非更新status/readinessと、別権限のcontrol入口。OWUI推論用認証と制御権限を分離する。 | 無効初期値。隔離loopbackで厳格HTTP framing/auth/error/rate/concurrency、pre-header status、post-header bounded error/close、各write deadline、shutdownを通す。ログに入力/出力/認証値なし。 | 実listen/host bridge、既存OWUI接続レコード編集、常駐化/再起動には具体的承認が必要。候補19180は未使用確認前。 |
| W2 | OWUI操作hook: 認証済みの対象ユーザーの手動sendを内部intentに変換し、title/tag/follow-up等のbackgroundを分離する。RID/session/payloadを内部で結合する。 | 対象版のroute adapterをmock user/taskで検証。clientのmetadata/header/task名だけではpermitを発行できない。推論用bearerだけでもcreate/deleteできない。 | 版・routeが分からないと対象版adapterの完成を判定できない。実plugin/backendへの配置、認証context/secret/権限設定は後の承認対象。 |
| W3 | on-demand request orchestrator: 承認された手動intent → ABSENTならgrant内で一度create → bootstrap/load → READY → bound inference。有限queue/cancel/retry/drop/statusを結ぶ。 | CPU fakeのcold/cached/failure/unknown-create/restartを全経路で実行。重複createなし、catalog/background起動なし、取消後推論なし。期限・capが再試行で更新されない。 | 実create/startはgrantと対象ID/構成を確定・承認するまでdefault-deny。保持中Podを自動再利用しない。 |
| W4 | 永続authority: grant・usage・effect intent・ownership・approval消費とcheckpointをatomicに保存/復元し、単一controller所有lockを設ける。 | workspace内のsynthetic grant/clock/検証器でcrash前後/二重起動/rollback/コピーcheckpoint/UTC逆行を試験。ledgerなしのJSON復元で権限を得ない。spent/expiry/idempotencyは巻き戻らない。 | 本物のtrust root、named service identity、保護state/key保存先/ACL、clock/recovery方針の適用は承認が必要。単なるJSON署名だけをrollback防止と呼ばない。 |
| W5 | 実provider port: RunPod管理要求の生成・response allowlist・所有権検証・不確定操作reconcileをmock controllerから分離したdefault-deny dispatcherに接続する。 | HTTP fakeでcreate/read/reconcile/start/deleteの要求/応答/timeoutを検証。exact account/session/resource/action対象以外は送らない。unknown create/deleteはblind retryしない。 | 実account credential、provider keyが提供する権限範囲、現行API/schema、指定GPU/構成/率は実接続前に確認・承認。実API呼出しは現権限で行わない。 |
| W6 | bootstrap/tunnel/readiness: 固定model/server pin・private port・alias qwen-27b・必要auth、SSH forwardingとidentity proofをtyped portとして接続する。 | 不活性なcommand/request planとfake SSH/serverでhost fingerprint/Pod/endpoint/model一致、download/hash/build/loading/failure/deadlineを確認。health成功とcatalog存在を混同しない。 | 実SSH鍵設置/known_hosts保存/転送方式/remote bootstrap、19181→Pod-loopback8080、モデル/認証設定は対象を確定した後の承認が必要。 |
| W7 | 実時間/cost/idle watchdog: provisioningからcleanupまでの経過と価格・storage等をgrant内に計上し、work/idle/absolute/cleanup reserveを監視する。 | fake rate/clock/ledgerで起動待ち・推論・無応答・exportに同じcapを適用。stream/comment/status/reconnectがlease更新をしない。期限到達後は新作業を送らない。 | synthetic chat .01USDを実課金と扱わない。現行率、総USD cap、UTC expiry、cleanup reserveと失敗時対応は有料試行前に承認。provider障害で消失未確認なら課金停止を断言しない。 |
| W8 | export/readback/Terminate: セッション外へ結果・再現情報をexportしSHAを読み戻し、exact-owned Pod/action approvalを満たして削除・消失確認する。 | fake artifact/providerでexport失敗/改ざん/共有target/期限切れ/既使用approval/Terminate応答消失を検証。承認待ち・未確認cleanupをstatusへ正確に出し、別Podを消さない。 | 実export保存先/readbackアクセスと個別削除権限が必要。完全自動idle削除を有効にするなら条件付き事前承認policyがexact Pod/export/actionへ収束する設計を別途明示承認する。policyなしは承認待ちで止める。 |
| W9 | 梱包・統合・独立review: 上記を一つの不活性アプリに接続し、OWUI-shaped client→gateway→upstream→cleanupまでのfixture flowを揃える。 | import/default/CLIで外部I/O・credential読取なし。設定schema/起動手順/停止/rollback/pinsを固定。冷起動/queue/restart/stop/期限/JSON/SSE/budget/削除の合成flowと秘密非出力がPASS、独立review blockingなし。 | 実OWUIの配置/起動方法が確定するまでdeployment設定を完成扱いにしない。push/merge/公開/本番installは別承認。 |
| W10 | 承認されたprivate実接続の1セッションを実証して、本番経路の完成を判定する。 | W0–W9の成果・差分・hash・合成証跡と一括activation計画を先にreviewする。 | 認証/権限/トンネル/実GPU/本番設定を適用する地点から現権限外。具体的承認後だけ実行。models/background無課金、cold/cachedの別計測、chat/cancel/restart/idle/export/許可済み削除/実billing回収を確認する。 |

W3の起動待ちはcold build/downloadが17.5分超だった事実に合わせて、grantの起動予算内で
設計する。READY後のupstream HTTP期限（現在上限30秒）と起動待ち期限を混同しない。
Phase3bの5秒permitはREADY時のdispatch直前に発行する。待機中の元手動intentは同じ
session/payload/deadlineに固定し、status pollが新しい操作権限を生まないようにする。

W4の脅威/clock/recovery modelを先に固定する。保護ledgerとapproval verifierをauthority
の根拠にし、任意checkpointだけを根拠にしない。サービス所有者自身の権限を超える
攻撃耐性や信頼できない時計に対する保証は、実装・環境が提供する範囲を明示し、根拠が
ない状態では継続課金操作をdenyして回復指示を要求する。

W8の自動化境界を曖昧にしない。人がexport後に個別承認する試行と、条件付き事前承認で
idle削除まで任せる運用は、同じ承認ではない。v1のコードは両者を明示できるようにし、
有効policyを確定する前に自動削除を有効にしない。

## 完成ゲート

- C0: Phase3b review受入、既存成果/計画scope/pinsが一致。現在125 CPU試験、既存smoke11、
  新binding smoke6がPASS。これをW1–W9全体の完成とは呼ばない。
- C1「無料コード完成」: W0–W9の非秘密baselineとコード/設定案/合成flowが完成し、
  independent reviewでblockingなし。入口・hook・永続authority・orchestrator・provider・
  tunnel/readiness・watchdog・export/cleanupの接続が全て試験可能である。
- C2「実設定完了」: 具体的activation計画が承認され、指定の認証/ACL/接続レコード/トンネル/
  state場所だけが設定されている。承認外の既存サービス・Pod・cloud providerは変更しない。
- C3「実接続実証」: grant内でW10を実施。JSON/SSE/ready/cancel/restart/期限/idle/export/
  許可済みTerminateと消失・billing確認を収集し、実証の失敗/未確認項目を隠さない。
- C4「継続運用可」: C3証跡をreviewし、以降のsession grant・cleanup policy・監視/回復手順を
  承認。最初の有料試行が通っただけで無制限な常時/自動課金権限を得ない。

## 今ユーザーに依頼すること

今、新たな課金・認証設定の承認や実key/SSH秘密鍵の提出は不要である。まず親が既に把握した
次の非秘密情報を共有する。許可済みread-onlyで取得できない不足分だけ、まとめて確認する。

1. OWUIの版、Windows直接/Docker/WSL等の起動方式、対象service/container名、backendの
   network namespace。既存設定値に秘密が含まれる場合は値を読まず接続レコード名だけ。
2. v1で操作する認証済みユーザー/ownerと、parentが把握するmanual send/background route。
   actual user ID等は必要最小限の内部設定とし、公開文書/ログに書かない。
3. 親が選定済みならstate/export場所・GPU構成・cold/cached方針・初回cleanup承認方式。
   未決定ならコード設計案に候補と差を示し、実設定の承認は後へ回す。

これらは対象版hookと最終deployment案を完成させるための情報確認であり、アクセス拡大の
承認ではない。情報が揃う間もW1/W3/W4/W5/W6/W7/W8の汎用コード・mock試験は進められる。

## コード完成後にまとめて依頼する操作

`private-live-access-approval.md`を、未解決placeholderのない一つのactivation計画へ仕上げる。
次を同じ計画の具体的操作として提示し、実行の直前に承認を求める。

- 指定OWUI接続レコードのbackupとbase URL/model/auth参照変更、対象版hookの配置・対象user。
  必要なら指定serviceだけの再起動。現在のlogin・chat・memory・Voiceを保持する。
- named gateway/controller identity、制御と推論を分離する認証、承認済みsecret注入方法、
  durable state/trust root/鍵保存先の必要ACL。値をrepo/ログ/URLに出さない。
- exact session-owned PodへのSSH user/key/host fingerprint/forwarding方式、local bindと転送先。
  Pod側の固定model/server/alias/private port/必要bearerとbootstrap。
- RunPod管理credentialの使用範囲と所有権allowlist、新GPU試行の構成・率・総USD・work/idle/
  UTC expiry・cleanup reserve・失敗時対応。OpenAIサービスとAutoPayの権限は求めない。
- export/readback保存先・保持範囲とcleanup方針。Terminate/deleteはverified exportとexact
  resource/actionを満たす個別承認、または別途明示承認された条件付きpolicyに限る。

公開・push/merge・他サービスへの権限追加はactivationの黙示的付属操作にしない。今の作業では
どの実設定も適用しない。現在の停止点は「秘密非読取でruntime情報を確定できない対象版hook」
と「C2以降の実アクセス/課金/削除」であり、単に小さい試験が終わったことを停止点にしない。
