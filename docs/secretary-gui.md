# Secretary GUI — 依頼・判断経過・証拠・回答（実験版）

## 概要

2026-09-27、最初の操作可能なローカルGUIを
scripts/secretary/secretary_gui.py に追加しました。
Python製 NiceGUI 3.17.1 を使用し、既存のSecretary Core / LangGraphを
実際に呼び出します。見た目だけのモックではなく、ブラウザから依頼を
受け、既存のcheckpointへ記録し、実際のOllama処理中に経過を更新します。

依頼本文・対象・領域・モデル、実行状態、統括役の判断、専門担当への指示、
Python側の結果検証、回答案の独立監査、資料の出典・未検証・模擬・
対象未紐付けフラグ、最終回答を表示します。
ask_user 時は本人への質問・回答フォームを表示し、元の依頼と同じ
ローカルTask IDで再開。過去依頼もローカルcheckpointから復元します。

## 実装場所

- scripts/secretary/secretary_gui.py: NiceGUI画面とバックグラウンドCore実行。
- scripts/secretary/secretary_gui_model.py: 履歴・イベント・証拠を表示向けに整理。
- scripts/secretary/secretary_core.py: GUIによる事前Task ID割当と質問条件の明示。
- tests/test_secretary_gui_model.py: 履歴、根拠フラグ、質問後の再開など5件。

## サブPCでの起動方法（一度に一操作ずつ）

まず既存の隔離worktreeだけ更新：

    git -C D:\AI\projects\secretary-framework-compare pull --ff-only origin experiment/orchestrator-framework-comparison

次に既存のLangGraph側仮想環境へGUIをインストール：

    D:\AI\projects\secretary-framework-compare\.venv-compare\Scripts\python.exe -m pip install "nicegui==3.17.1"

オフライン試験（Ollama・API不要）：

    D:\AI\projects\secretary-framework-compare\.venv-compare\Scripts\python.exe -m unittest discover -s D:\AI\projects\secretary-framework-compare\tests -p "test_secretary*.py" -v

GUI起動：

    D:\AI\projects\secretary-framework-compare\.venv-compare\Scripts\python.exe D:\AI\projects\secretary-framework-compare\scripts\secretary\secretary_gui.py

サブPCのブラウザで http://127.0.0.1:8091 を開き、依頼を送信。
処理が進むと判断・担当・証拠・回答が更新されます。
回答待ちになった場合、同じ依頼を開いて質問へ回答してください。

## 実記憶・安全性と限界

既定のfixtureは架空テストPC / pcのみ。live実記憶モードを使う場合は
サブPC上の既存制限付きAPIを起動し、環境変数
LSA_SECRETARY_API_TOKEN_FILE に既存トークンファイルのパスだけを指定。
トークンの中身、個人原本、DBダンプはチャットやGitに登録しないこと。
実Web調査は未接続なので、live researchは架空資料を代用せず失敗します。

本GUIは認証未実装で、127.0.0.1だけで起動します。
外部PCやスマホから公開する場合、認証・アクセス制御・HTTPSが別途必要です。
実PC変更、外部サービス操作、DBの永続Task/Action/Result更新は未実装。
ローカルUUIDとJSON checkpointは既存PostgreSQL Taskとは区別。
yt-topic-search のコード・DB・コンテナには触れません。

## 検証区分

- GitHubへのGUI実装と5つのGUIモデルテスト：反映済み。
- CI / サブPCテストの結果：未確認。
- 実ブラウザからQwen3へ依頼、質問への回答・再開：未実機検証。
- PostgreSQLと自動統合した本番秘書UI：未実装。


## 全文コピーできる検証用ログ（2026-09-27追加・サブPC未検証）

ユーザーの実機試験で、複数ターンの判断と証拠がスクリーンショットに
収まりきらないことが判明。各Task詳細の「取得した証拠」の**一番下**に
「検証用ログ（全文コピー）」を追加しました。閲覧向けのカード表示は維持します。

- コピー用テキスト欄：Task ID、元の依頼、対象・モード・モデル・状態、最新の
  追加情報・保留質問、保存済みの判断・委任・評価・監査・ユーザー回答の**全件**
  （内部イベントJSONを含む）、過去・現在の取得資料の**全件**
  （M1/M2/R1/R2、検索条件・出典・模擬・未検証・対象未紐付け、元レコードJSONを含む）、
  最終回答・残課題・実行エラーを出力。意図的な件数・文字数切捨てはありません。
- 「ログ全文をコピー」：ブラウザのクリップボードに1操作でコピー。
- 「詳細JSON」を開くとcheckpoint内部状態の完全なJSONを別欄に表示し、
  「JSON全文をコピー」からそのままコピーできます。
- 処理中のcheckpoint更新や依頼切替でも、同じ画面のテキスト出力を再生成します。
- 将来のTaskでは、統括役が質問した**原文**と、監査に却下された回答**原案**も
  判断イベントに残します。旧checkpointの過去質問の原文等、
  当時そもそも保存されていなかった情報を遡及的に復元するものではありません。
  旧Taskのcheckpointに残った最後の質問は表示可能です。
- イベントごとの発生時刻は現checkpointにないため、番号順で表示します。
  個々の日時があるように見せかけることはしません。

個人情報を含む可能性があるため、**実記憶モードでコピーした全文を外部共有する際は、
先に内容を確認してください。** APIトークンやDBダンプは含めず、専用のファイルや
GitHubへ勝手に送信しません。コピーボタンは閲覧中のブラウザだけを操作します。
現段階はGitHubへ実装・テストを反映したのみで、追加のサブPC試験は未確認です。
