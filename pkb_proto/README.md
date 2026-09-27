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

## 元のEpisode10件の無損失取込（GitHub実装・サブPC未試験）

旧P0の`episodes.json`だけをそのまま`fixtures/episodes.json`に取り込み、正解`expected.json`は実装／入力へ混ぜない。`episode_intake.py`は10件の領域、Source種別、本文、記録・発生時点、`fixture://`出典を事前検査し、原文を`sources.metadata`に保全する。新しい実験専用`007_pkb_proto_episodes.sql`でEpisode IDとSourceを一対一で関連付け、同一内容の再送は冪等とする。未知の原文の解釈はすべて`uninterpreted`として保留し、**この取り込みだけではClaimを作成しない**。

既存の隔離DBで006と訂正スモークが成功済みなら、`pkb_proto/run_episode_smoke.ps1`が007のSHAと専用DB名を検査して適用し、単体試験と元の10件・再送・外部推測資料の非昇格を確認する。8つの正解質問への回答試験は**この次**であり、本取込成功だけで「8問正解」や汎用自然言語抽出の完成とはしない。

## ローカルLLM抽出の読取専用スライス（GitHub登録、サブPC未試験）

`extraction_service.py` は `fixtures/episodes.json` の原文だけを対象に、候補の引用・値・Entity文字列が原文中に存在するか検証する。LLMによる意味理解の正しさは別に評価するため、**候補は一律review、DBへの書込みはゼロ**。外部資料、明示訂正、未確認を別の理由で保留する。`run_local_extraction.py` は `127.0.0.1:11434` のローカルOllamaだけを呼び、元10件から候補数・拒否理由を観測。**正解 `expected.json` は抽出プロンプトに入れない**。

サブPCでは `pkb_proto/run_extraction_smoke.ps1` が先にPKB単体テストとローカルモデルの存在を確認し、その後に架空データだけの観測試験を実行する。モデルがなければ停止し、勝手にダウンロードや外部APIへの送信をしない。結果を受領するまでは抽出性能未実証。

## 架空Episode用GUI検証ワークベンチ（2026-09-28、GitHub実装、実機未試験）

前段のCLI抽出は単体46件合格だが、サブPCのローカル`qwen3.5:9b`では元Episode10件とも`invalid_json`／候補ゼロだった。モデル本文・終了理由未収集なので原因は未確定。以後の反復診断は`Launch-PKB-GUI.cmd`をExplorerからダブルクリックして行う（最初のブランチ更新だけPowerShellが必要）。`pkb_proto/gui.py`がPython標準のTkinter画面を起動し、**架空入力のみ・Ollama localhostのみ・DB書込なし**で実験する。

GUI上ではモデル一覧更新、Episode1件／全10件、生成上限1100／2048／4096、推論モード自動／無効を選択できる。終了理由、生成tokens、本文長、JSON解析結果、候補・拒否理由と原文を画面上で比較し、架空データだけのJSON報告を保存できる。モデルに推論フィールドが含まれても内容は画面と報告に出さず、長さと有無だけを記録する。候補は引き続き一律reviewで、GUIからの正本昇格はできない。正解`expected.json`はモデルに渡さない。

これは**開発・検証用のGUI**であり、日常利用するPKBの自然言語登録／訂正／検索画面は別の後続段階。サブPCでの起動、既存モデルとの接続、反復診断の成功はユーザーの実測結果を受領するまで未検証。

### GUI追加改善：実行時間とJSONコピー（2026-09-28、GitHub登録／サブPC未検証）

1件ごとにモデル呼出し・検査が完了するまでの経過時間（秒）を表の「実行秒」に表示し、結果の詳細とエクスポートJSONにも`elapsed_seconds`を保存する。全件実行の終了時には今回の経過時間を画面下部へ表示する。エラーでもエラーまでの経過時間を画面に表示する。計測は`time.perf_counter()`によるGUI側の実測で、Ollama単体の推論時間とは区別する。

新設「結果JSONをコピー」ボタンは、既存「結果をJSON保存」と**同一内容**の架空結果レポート全件をクリップボードへコピーする。結果が0件ならコピーしない。モデルの推論本文と個人情報は扱わず、引き続き架空10件とローカルOllamaのみ。既存GUIを終了しGitHubから更新して再起動すると反映。新規オフライン試験とGUI実機操作の結果は未受領。

### 推論・入力・抽出を分離する5モード（2026-09-28、GitHub登録・サブPC未試験）

GUIの「検証」欄で「疎通：固定文字列」（出力`ABC123`）、「理解：原文復唱」（選択Episodeの原文を一字一句復唱）、「推論：簡単な計算」（リンゴ2個＋3個、期待する短答`5`）、「抽出：簡略」（対象と出来事だけの2項目JSON）、「抽出：現行」（これまでの5項目候補・原文照合）を選べる。先頭3モードはOllamaへ`format=json`を強制せず、回答をJSON不正と誤認しない。簡略抽出の構文合格を意味的な抽出精度の合格とは扱わず、元10 Episodeのgold正解はモデルに渡さない。

基本3モード・簡略抽出は1件ずつ実行可能、現行抽出のみ10件一括ボタンを使える。結果JSONに`mode`、`check`、`elapsed_seconds`、推論本文の有無・文字数、終了理由と回答本文を保存／コピーする（推論本文そのものは出力しない）。設定は既存GUIのモデル・生成上限・推論自動／無効を再利用する。現在の目的はモデル側と指示／検査側を**少ない実測で切り分けること**であり、抽出チューニングの継続やMAGI実装ではない。
