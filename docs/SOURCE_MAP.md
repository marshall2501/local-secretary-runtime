# SOURCE_MAP — 現行Runtimeソース索引

更新: 2026-10-17（旧Runtime資産撤去後のGitHub source構造）。実機稼働状態や受入結果は設計repoの [STATUS](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/STATUS.md) を正本とし、この文書は**存在する実装の索引**とする。採用する責務境界は設計側の Runtime Source Architecture を参照。

## 1. 正式な目標実行経路

```text
本人 → Vue (interfaces/vue_daily/frontend/)
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
| RITSUKO Task REST | `interfaces/api/ritsuko_tasks.py`, `interfaces/api/app.py` | Vue以外のAPI Hostからも利用できるRouter |
| Vue日常画面 | `interfaces/vue_daily/frontend/src/`, `interfaces/vue_daily/app.py` | 共通HTTP APIを呼ぶ。日常正式切替は本人受入待ち |
| 共通Task受付・操作 | `ritsuko/application/task_commands.py`, `task_runtime.py` | start、answer、review、stop、resume |
| 共通実行・Source | `ritsuko/core/task_execution.py`, `ritsuko/application/source_execution.py` | 記録済みAction/Result IDを使用 |
| Task状態・Goal評価 | `ritsuko/core/task_projection.py`, `judgment_application.py`, `goal_evidence.py` | 提案だけでTool完了にしない |
| MAGI Session / Strategy | `ritsuko/magi/session_workflow.py`, `judgment_strategies.py` | standard/llm共通 |
| MAGI Goal判断契約 | `ritsuko/magi/goal_decision.py`, `goal_contract.py` | 判断候補・契約検証、実行状態Writerではない |
| MAGI Provider I/O | `ritsuko/magi/async_execution.py`, `client.py`, `integrations/llm/` | async、deadline/cancel、複数席 |
| MAGI/Task実行組立 | `bootstrap/magi_task_runtime.py`, `ritsuko_task_composition.py` | API・Vue・旧UIが共用 |
| 永続Task、照会 | `infrastructure/postgres/magi_task_repository.py`, `core_task_query_repository.py`, `task_repository.py` | 本人Task原文・履歴・停止／Review状態 |
| PKB独立機能 | `pkb/application/daily.py`, `memory_intake.py`, `infrastructure/postgres/pkb_*repository.py` | SQL-first、登録、訂正、Pending |
| Finance | `capabilities/finance/`, `infrastructure/postgres/finance_repository.py` | 同じRuntime DB、共通read Capability |
| Service接続・設定 | `integrations/connections/`, `infrastructure/postgres/service_connection_repository.py`, `magi_settings_repository.py` | SecretはAPI/Prompt等へ直接渡さない |
| Policy Profile | `ritsuko/application/context_policy.py`, `infrastructure/postgres/runtime_policy_repository.py` | verification-lan/production-lanは同一DBの切替設定 |
| Billing | `infrastructure/postgres/service_billing_settings_repository.py` | Service Connectionを参照 |
| NiceGUI（暫定） | `interfaces/web/app.py`, `interfaces/web/pages/` | Vueで必要機能を本人受入後にのみ撤去 |
| 独立開発Workbench | `interfaces/workbench/` | 架空入力の診断、旧運用DBの代替ではない |
| MCP | `interfaces/mcp/server.py` | 独立入口、責務と公開境界を維持 |

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
| Vue/API/Chromium隔離 | `interfaces/vue_daily/frontend/e2e/`, `.github/workflows/vue-secretary-ui-lab.yml` |
| ローカルプローブ | `scripts/db/probe_magi_runtime_readonly.py`, `probe_runtime_policy_readonly.py` |
| Source静的・整合性 | `tests/db/test-static.ps1`, `tests/test_architecture_boundaries.py` |

隔離試験が利用するDBは**テスト専用の使い捨て環境**。日常RuntimeのDB選択肢ではない。GitHub CIのPASS、サブPCでの実PG+MAGI Task完走、Vueでの必要機能受入は別々に記録する。

## 4. 撤去済みの旧経路

2026-10-17 Level C計画に沿い、旧 `db/isolated/pkb_proto/`、`scripts/pkb/isolated/`、旧prototype smoke/昇格・設定移送・DB比較ツール、`ritsuko/core/observation_loop.py` 転送Shim、`ritsuko/magi/goal_loop.py`、`async_execution.py` のSession互換転送入口をGit追跡から除去。必要なコード経緯はGit履歴へ戻って参照する。旧NiceGUI、Service Policyの環境別Profile、Workbenchはこれらの死んだ互換経路とは区別して存続する。

本作業はGitHubソース・CIの再構成であり、本人の実DB、過去の実DB実体、Secret、稼働中サービスを変更していない。正式Vue切替と実機追加受入は未実施。
