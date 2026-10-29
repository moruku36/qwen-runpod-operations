# 作業の方法論・規律(トリガー表 — 当てる条件に当たったら従う)
各項目の「当てる条件」が発動トリガー。当たった瞬間に「やること」に従う。やることがファイル参照になっている項目は、その場で Read してから挙動を決める(要点をここに置かないのは、要点で済ませて誤動作するのを防ぐため)。

1. 事実・検証ステータス・数値の引用と文書作成
   - 当てる条件: README、docs/ 配下のガイド、技術記事、振り返りレポートで検証ステータス(C1/C2/C3等)、ベンチマーク数値、所要時間、コスト、自分の関与範囲を書こうとする直前
   - やること: _semantics/terminology-and-roles.md と _semantics/metrics-and-dates.md および docs/disciplines/writing-and-verification-guide.md を Read してから書く

2. 日英バイリンガル文書の同期更新
   - 当てる条件: *.ja.md または *.en.md の一方を編集・更新しようとするとき
   - やること: 対応する他言語版のファイルを Read し、検証ステータスや数値の定義が _semantics/ と一致しているか確認してから両方を同期する

3. 段階的構築
   - 当てる条件: 数百行規模の長い成果物(Markdown記事 / 設計書 / コード)を書こうとするとき
   - やること: docs/disciplines/incremental-construction.md を Read して従う

4. 設計判断・代替案比較の記録(ADR起票)
   - 当てる条件: 代替案比較を始めた瞬間 / 同じ問題に2回目のアプローチが頭をよぎった瞬間
   - やること: docs/disciplines/adr-howto.md を Read して記録する
