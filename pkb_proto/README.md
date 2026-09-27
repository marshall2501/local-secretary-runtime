# PKB自前プロトタイプ：最初の実装単位

**状態：GitHub実装のみ、サブPC未検証。** 現在のブランチは運用DB・既存APIを変更しない。

## 目的

単独PKBの自然言語登録・訂正・検索に向け、LLMの抽出提案をそのまま正本へ書き込まないための、再利用可能な入力ゲートを先に実装する。これはP0で観測した誤った対象帰属と出典にない抽出への初期対策であり、意味的正確性を保証するものではない。

- `pkb_proto/ingestion_gate.py`：入力ID、Source、原文、記録時点・発生時点、引用位置、既知Entity別名、訂正対象、外部資料、機密性を機械検査。
- 出力は `auto_candidate`（次のDB競合検査へ進めるだけ）、`review`、`reject`。**実際のDB登録や訂正はまだ行わない。**
- 本人の明確な低リスク申告だけを自動登録候補へ。第三者資料・曖昧な対象・高影響・訂正は別途判断。LLMの解釈が原文に意味的に合うかは、追加の検証と実機試験が必要。
- `tests/test_pkb_ingestion_gate.py`：Python標準ライブラリの単体試験。

### 最初の隔離試験（サブPC）

```powershell
cd D:\AI\projects\local-secretary-runtime
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_pkb_ingestion_gate.py" -v
```

標準ライブラリのみ使用するので、依存追加は不要。初回は使用するPythonの存在を確認して適切なパスで実行する。新たなDB、ポート、Docker、クラウドモデルは不要。

## 後続の実装

1. `InputReceipt`と記憶候補を隔離PostgreSQLに保存するAPIおよび専用最小権限ロールを追加。入力IDで冪等登録し、許可された通常の本人申告をトランザクションで自動登録。
2. 訂正サービスを実装。旧Claimと新Claim、訂正した日時、事実の有効日時を区別。PC・RC別の過去と現在の検索で誤帰属を防ぐ。
3. SQL-firstの履歴／時点／全件検索と出典表示を共通APIに追加。元のP0の架空10件・8問・訂正2件を自動受入試験へ。
4. 最小のローカルUIから自然言語で登録・検索・訂正できることを確認。その後にSecret・個人データを扱う運用DBへ段階移行。

設計正本：[PersonalKnowledgeBase](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/02_Database/PersonalKnowledgeBase.md)／[Memory-vNext](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/02_Database/Memory-vNext.md)。

**安全条件：** 本番`secretary` DBはこの段階では読み書きしない。テストは架空データのみ。既存`yt-topic-search`、Cognee、Graphiti環境へ干渉しない。実データ・本番DB更新に進む時はD-08に従って直近バックアップ、復元可能性、権限、戻し方を確認する。
