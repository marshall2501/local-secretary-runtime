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
