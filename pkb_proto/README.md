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

## 追記：DB書込みスライス（GitHubのみ・未実機）

`write_service.py` と `sql/005_pkb_proto_receipts.sql` を追加。対象は `secretary_pkb_proto_*` という名前の**専用隔離DB**と `fixture://` の架空入力だけ。既存 `secretary` DBや実データは拒否する。入力IDごとの一意な領収記録・Source・単一Claimを1トランザクションで保存する。現在は対象の既知Entityに対する明示的な初回更新・交換のみを候補とし、既存Claimとの競合、訂正、曖昧な入力、外部情報はレビューへ回す。DB競合確認・引用の字面照合だけで意味的な正しさが保証されるわけではない。

まず `python -m unittest discover -s tests -p "test_pkb*.py" -v` によりオフライン試験を実施し、次段階で専用DB上の挿入・冪等再送・ロールバック・権限を実証する。既存の001〜004を更新せず、この実験専用005は運用DBに適用しない。

## 隔離DB実機スモーク試験（準備済み、未実施）

既に`secretary_pkb_proto_20260927`に001〜005を適用したサブPCで、`./pkb_proto/run_db_smoke.ps1`を実行。まず専用の`secretary_pkb_proto_writer_20260927`ログインとローカル除外対象の秘密ファイルを作り、**隔離DBに対してのみ**SELECT/INSERTを付与。既存`secretary_*`の広いグループロールは付与しない。実験用の架空メインPC Entityを作成し、`smoke_isolated.py`が初回Claim登録・再送・同一ID別内容拒否・Source/状態確認・意図的失敗のロールバック・運用DBのClaims読取拒否を確認する。通常の`secretary`DBに001〜005を適用しない。実測結果はサブPC出力を受領するまで未確認。

## 訂正スライス：PC／RC対象の明示的な訂正（実装済み、DB実機未検証）

- `correction_service.py`: 元の本人申告ClaimのIDを明示し、「旧Entityではなく新Entity」という原文がある場合だけ、**元Claimの撤回・新Claimの追加・訂正Source・訂正領収記録を同一トランザクション**で処理。元Claimと元Sourceを削除せず、訂正は`supersedes_id`で連結する。別日時の更新イベントは同じpredicateでも共存を許す。曖昧さ、別値変更、未検証の訂正先、同日時の衝突はレビューへ。
- `sql/006_pkb_proto_corrections.sql`: 既存隔離DBだけに適用する追加テーブルと、専用実験用ロールに限定したClaimの撤回列更新権限。**運用DBには適用しない**。
- `run_correction_smoke.ps1`: 既存サブPCの実験DBに006を安全確認後適用し、既存18件＋訂正用単体テストと、PC／RC両方の訂正、再送、訂正前後の「当時知っていた記録」、別の更新の保持を確認。実際の結果は本人のサブPC出力が出るまで未実証。

この段階の訂正は既知Claim IDと既知Entity IDを指定する**サービス内部の限定機能**であり、利用者が自然言語だけで訂正対象を特定できる完成したPKBではない。また`driver_updated`・`servo_updated`は履歴イベントであり、現在のドライバー・サーボの確定属性を推定するものではない。

## SQL-first検索スライス（GitHub実装、サブPC未試験）

`query_service.py`はUUID指定の対象・domain・predicate・発生時点の範囲・知識時点（`known_at`）・履歴の有無で**正本Claimを限定SQL検索**する。過去の撤回について当時の記録状態と現在保存されている状態を混同しないよう`status_at_cutoff`を別に返す。出典URI／citation・Source ID、総件数／ページを同じ読み取りトランザクションで取得する。0件なら`status=ok,total=0`で、DB障害は例外として区別。複数のページ取得要求を通じた固定スナップショットは未実装。

`tests/test_pkb_query.py`と`smoke_query.py`を追加し、サブPCで001〜006とPC／RC訂正試験を成功した既存隔離DBを使用する`run_query_smoke.ps1`を用意した。この新しいコードの実機結果は**未受領**。まだ原本10 Episode全件の投入・正解8問・自然言語質問・UIは未対応。
