# SOURCE_MAP — 現行Runtimeソース索引

更新: 2026-10-18（単一Secretary ServerにTask・PKB・Finance・Billing・接続設定・MAGI・Debug API/Vueを結線、正式起動切替は未受入）。実機稼働状態や受入結果は設計repoの [STATUS](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/STATUS.md) をマスタとし、この文書は**存在する実装の索引**とする。採用する責務境界は設計側の Runtime Source Architecture を参照。

## 1. 正式な目標実行経路

```text
本人 → Vue (interfaces/vue_daily/frontend/) → 単一Secretary Server (interfaces/api/secretary_server.py)
       → 共通RITSUKO FastAPI Router (interfaces/api/ritsuko_tasks.py)
       → Task Commands (ritsuko/application/task_commands.py)
       → Task Application (ritsuko/application/task_runtime.py)
       → 単一Task Executor (ritsuko/core/task_execution.py)
           ├─ Source/Action/Result Port (ritsuko/application/source_execution.py,
           │                        observation_sources.py)
           ├─ Task状態・証拠判定 (ritsuko/core/task_projection.py,
           │                       judgment_application.py, goal_evidence.py)
           └─ MAGI Session (ritsuko/magi/session_workflow.py)
                → strategy standard / llm (judgment_strategies.py)
                → Goal提案 (goal_decision.py / goal_contract.py)
                → Provider通信 (async_execution.py / client.py / integrations/)
       → 共通PostgreSQL (secretary)
```

RITSUKOがTask受付・Tool実行・Source/Result実在照合・永続状態・権限・停止再開を管理し、交換可能MAGIが意味判断・Goal/次の一手・根拠の十分性を提案する。standard/llmごとの別Task Executor・別DB・別画面状態マスタは作らない。

## 2. 機能と現在の入口

| 機能・責務 | 正規ソース | 備考 |
| --- | --- | --- |
| 正式Secretary Server（実装初段） | `interfaces/api/secretary_server.py`, `bootstrap/secretary_server.py` | Vue静的配信・Task操作/PKB検索の共通Hostを一プロセスへ構成。本人実機未受入、通常ランチャー未切替 |
| Product console/file log | `infrastructure/secretary_logging.py` | コンソール＋`logs/secretary.log`、ポート別ログ設計にしない |
| RITSUKO Task REST | `interfaces/api/ritsuko_tasks.py` | Web画面と他API Clientから再利用するTask Router |
| Vue日常画面 | `interfaces/vue_daily/frontend/src/`, `interfaces/api/secretary_server.py` | 同一Originの共通Task/PKB APIを呼ぶ。TOP状態カード、RITSUKO全状態履歴を追加。正式切替は本人受入待ち |
| Vue/REST PKB（検索・登録・訂正・Pending・Entity詳細） | `interfaces/api/pkb_read.py`, `interfaces/api/pkb_commands.py`, `interfaces/vue_daily/frontend/src/features/pkb/`, `pkb/application/daily.py`, `bootstrap/web_runtime.py` | Applicationは旧NiceGUIと共用。同一HostのEntity catalog/detailを既存PKB Repositoryから参照、Vueに現在値・履歴・関連を追加。隔離CIと本人実機受入は未了 |
| 共通Task受付・操作 | `ritsuko/application/task_commands.py`, `task_runtime.py` | start、answer、review、stop、resume |
| 共通実行・Source | `ritsuko/core/task_execution.py`, `ritsuko/application/source_execution.py` | 記録済みAction/Result IDを使用 |
| Task状態・Goal評価 | `ritsuko/core/task_projection.py`, `judgment_application.py`, `goal_evidence.py` | 提案だけでTool完了にしない |
| MAGI Session / Strategy | `ritsuko/magi/session_workflow.py`, `judgment_strategies.py` | standard/llm共通 |
| MAGI Goal判断契約 | `ritsuko/magi/goal_decision.py`, `goal_contract.py` | 判断候補・契約検証、実行状態Writerではない |
| MAGI Provider I/O | `ritsuko/magi/async_execution.py`, `client.py`, `integrations/llm/` | async、deadline/cancel、複数席 |
| MAGI/Task実行組立 | `bootstrap/magi_task_runtime.py`, `ritsuko_task_composition.py` | API・Vue・旧UIが共用 |
| 永続Task、照会 | `infrastructure/postgres/magi_task_repository.py`, `core_task_query_repository.py`, `task_repository.py` | 本人Task原文・履歴・停止／Review状態 |
| PKB独立機能 | `pkb/application/daily.py`, `memory_intake.py`, `infrastructure/postgres/pkb_*repository.py` | SQL-first、登録、訂正、Pending |
| Finance | `capabilities/finance/`, `infrastructure/postgres/finance_repository.py`, `interfaces/api/finance.py`, `interfaces/vue_daily/frontend/src/features/finance/` | 保存済み集計・月別/カテゴリ表・明細・取込履歴＋MoneyForward CSV差分Preview→明示Commitを同一Hostで提供、実DB受入前 |
| Service Billing / Connections / Debug | `interfaces/api/daily_overview.py`, `interfaces/vue_daily/frontend/src/features/system/`, `infrastructure/system_debug.py` | ProfileとConnection読書き／外部Usage明示参照／Runtime・DB・環境・API一覧を旧NiceGUIから独立 |
| MAGI LLM Profile・Member設定 | `interfaces/api/magi_settings.py`, `ritsuko/magi/settings.py`, `infrastructure/postgres/magi_settings_repository.py`, `interfaces/vue_daily/frontend/src/features/system/` | 同じ設定Repoで3席のProfile/Weight/Timeoutを管理。別MAGI Executorを作らない |
| Service接続・設定 | `integrations/connections/`, `infrastructure/postgres/service_connection_repository.py`, `magi_settings_repository.py` | SecretはAPI/Prompt等へ直接渡さない |
| Policy Profile | `ritsuko/application/context_policy.py`, `infrastructure/postgres/runtime_policy_repository.py` | verification-lan/production-lanは同一DBの切替設定 |
| Billing | `infrastructure/postgres/service_billing_settings_repository.py` | Service Connectionを参照 |
| NiceGUI（旧コード／撤去対象） | `interfaces/web/app.py`, `interfaces/web/pages/` | 新API＋Vueで能力を置換後に撤去。旧UIの保守は開発目標ではない |
| 旧Vue Host互換 | `interfaces/vue_daily/app.py`, `commands_preview.py` | 現在の旧ランチャー用。API実装は正式Secretary Hostに委譲、正式切替後撤去 |
| 8010 read契約の新Hostアダプタ | `interfaces/api/external_read.py`, `interfaces/api/secretary_server.py` | `/tasks`、`/memory/search`、`/entities`、`/claims/current`、`/experience/search`を新Hostへ結線。既存外部クライアントの接続切替は未実施 |
| 旧8010 REST | `interfaces/api/app.py`, `api/secretary_api.py` | 旧Containerはまだ稼働経路として残る。旧Task契約は撤去対象で新Executorへ採用しない |
| 独立開発Workbench | `interfaces/workbench/` | 架空入力の診断、旧運用DBの代替ではない |
| MCP | `interfaces/mcp/server.py` | 独立入口、責務と公開境界を維持 |

**2026-10-18 UI再棚卸**：旧NiceGUIのMermaid ER図と比較して、新固定4列SVGの視認性をFAILと判定。Vue `SchemaDiagramPage.vue` はnpm版Mermaidを遅延読み込みして既存Mermaid sourceをレンダリングする実装へ置換し、Debug/TOP/フォームとEntity/Task履歴/Finance表示もGitHubへ反映。**CIおよび本人実機受入は未確認**。このSource Mapは実装存在の索引でありPASS証跡ではない。Mermaidは外部ソースをコピーせずnpm依存として導入（upstream: `mermaid-js/mermaid`, `^11.17.2`, MIT License、利用場所: `SchemaDiagramPage.vue`）。同梱npm packageのLICENSEを保持し、独自コードのライセンス表記と混同しない。

## 3. DB / 検証 / 復旧

| 用途 | 現行の実装・入口 |
| --- | --- |
| 唯一の運用DB | `secretary` / `secretary_daily_runtime` / `infrastructure/postgres/pkb_runtime.py` |
| 唯一のMigration系列 | `db/migrations/001_memory.sql` ～ `009_runtime_policy_profiles.sql` |
| 通常DB管理・バックアップ・復元 | `scripts/db/postgres.ps1`, `scripts/db/migrate.sh` |
| 明示的なfresh schema検証 | `scripts/db/initialize-fresh-production.ps1` |
| 使い捨てPostgreSQL隔離検証 | `tests/db/test-integration.ps1`, `.github/workflows/postgres.yml` |
| PKB/Memory実PostgreSQL試験 | `tests/test_memory_intake_postgres.py`, `.github/workflows/pkb-memory-intake.yml` |
| 共通Task/Strategy縦断 | `tests/test_shared_judgment_task_pipeline.py`, `tests/db/verify-common-task-execution.py` |
| Vue/API/Chromium隔離 | `interfaces/vue_daily/frontend/e2e/`, `tests/test_secretary_server.py`, `tests/test_secretary_capability_views.py`, `tests/test_secretary_pkb_commands.py`, `tests/test_secretary_external_read.py`, `tests/test_vue_pkb_read.py`, `.github/workflows/vue-secretary-ui-lab.yml` |
| ローカルプローブ | `scripts/db/probe_magi_runtime_readonly.py`, `probe_runtime_policy_readonly.py` |
| Source静的・整合性 | `tests/db/test-static.ps1`, `tests/test_architecture_boundaries.py` |

隔離試験が利用するDBは**テスト専用の使い捨て環境**。日常RuntimeのDB選択肢ではない。GitHub CIのPASS、サブPCでの実PG+MAGI Task完走、Vueでの必要機能受入は別々に記録する。

## 4. 撤去済みの旧経路

2026-10-17 Level C計画に沿い、旧 `db/isolated/pkb_proto/`、`scripts/pkb/isolated/`、旧prototype smoke/昇格・設定移送・DB比較ツール、`ritsuko/core/observation_loop.py` 転送Shim、`ritsuko/magi/goal_loop.py`、`async_execution.py` のSession互換転送入口をGit追跡から除去。必要なコード経緯はGit履歴へ戻って参照する。旧NiceGUI、Service Policyの環境別Profile、Workbenchはこれらの死んだ互換経路とは区別して存続する。

本作業はGitHubソース・CIの再構成であり、本人の実DB、過去の実DB実体、Secret、稼働中サービスを変更していない。正式Vue切替と実機追加受入は未実施。
