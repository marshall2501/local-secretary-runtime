# SOURCE_MAP — ソース構成と機能対応

更新: 2026-10-15
参照runtime: current policy implementation `3de93850013c2e410815160e56c009344d889dbc` / GitHub isolated CI PASS。サブPC実機は未確認。過去のTask/Actionの受入は各Plan/STATUSを参照。

2026-10-15: Vue正式開発に向け、共通RITSUKO Task Compositionと、実DB-backed Task読取専用API/画面（別ポート8097、未実機）を追加。旧Vue隔離Labは8098で独立継続。GitHub型検査/隔離CI PASS。本番Taskの開始・回答・停止やGUI切替は未実装。
2026-10-16: Vue製品側 `interfaces/vue_daily/frontend/src/` をStage Aへ再編。薄い `App.vue`、共通 `app/SecretaryLayout.vue` と `shared/navigation.ts`、`shared/api/http.ts`、`shared/styles/base.css`、RITSUKO専用 `features/ritsuko/RitsukoPage.vue` / `api.ts` / `types.ts` / `useTasks.ts` / `components/{CurrentTask,TaskSidebar,TaskActivity}.vue` に責務を分離。既存Task一覧を右側、現在Taskと依頼入口を左中央へ配置。未移行機能ナビは従来NiceGUI `8093` を利用。**実Taskのread-only機能は維持、write機能は未実装**。GitHub CI/サブPC実機結果は対応する変更計画/STATUSを確認する。
2026-10-16 Stage B-1: `ritsuko/application/task_commands.py`にGUI非依存の非同期Taskコマンド受付を追加し、`interfaces/vue_daily/app.py`にDIで有効化するPOST受付・Operation参照を追加。通常8097はコマンド依存未注入で**read-only／POST 503**、実運用のDB・MAGI・Source・Policy設定は未接続。受付202はTask完了を意味しない。隔離テストは`tests/test_vue_task_commands.py`と`tests/test_vue_daily_api.py`。受入証拠は設計repoのVue変更計画/STATUS参照。
この資料は、Personal Local Secretary AI の **利用者から見える機能・Application責務・実装ソース** を対応付けるための現行実装索引です。

source directoryの役割・dependency direction・重要な配置原則の設計マスタは [`local-secretary-ai/docs/01_Architecture/RuntimeSourceArchitecture_実装ソース構造.md`](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/01_Architecture/RuntimeSourceArchitecture_%E5%AE%9F%E8%A3%85%E3%82%BD%E3%83%BC%E3%82%B9%E6%A7%8B%E9%80%A0.md) です。本資料は、その設計を**現在どのfileが実装しているか**を追跡します。

本資料は「どこに何が実装されているか」を示すものであり、機能が実機受入済みであることを意味しません。

2026-10-12: 環境別DB権限設定（Migration 009、Permission Label / Grant / Profile / Activation）、MAGIのContext送信Profile評価、Task単位の設定snapshot、設定GUI、合成データでのCI・使い捨てPostgreSQL検証をGitHubに追加。**実機の運用DB Migration・本人PKBを用いたCloud MAGI分析・外部Callerの環境識別は未受入**。従来Task／Migration未適用環境のGate挙動は保持する。

2026-10-12: state-driven Taskの開始・本人回答・Proposal ReviewがGUI非依存のApplication入口を経由するようになった。固定Action fixtureでは共通Operation定義、Task scopeによる低影響事前許可／高影響個別承認、PostgreSQL予約とreadbackを隔離CIで確認。**日常GUIのAction操作、実サービス連携、Production適用は未実施**。

- 大目標・製品要件: `local-secretary-ai/docs/PROJECT.md`
- 採用アーキテクチャ: `local-secretary-ai/docs/ARCHITECTURE.md`
- 実装・実機受入状況: `local-secretary-ai/docs/STATUS.md`
- 現在の作業: `local-secretary-ai/docs/PLAN.md`
- DB物理境界: [database-boundary.md](database-boundary.md)

---

## 1. 利用者機能と主な実装

| No. | 利用者から見える処理 | Interface / Entry | 主なApplication / Domain | Infrastructure / 外部I/O |
| --- | --- | --- |---|---|
| 1 | 日常Web GUI起動 | [../interfaces/web/app.py](../interfaces/web/app.py) | [../bootstrap/web_runtime.py](../bootstrap/web_runtime.py) | NiceGUI / PostgreSQL |
| 2 | TOP / 機能一覧 | [../interfaces/web/pages/top.py](../interfaces/web/pages/top.py), [../interfaces/web/pages/features.py](../interfaces/web/pages/features.py) | Web Runtime | read-only status |
| 3 | PKB登録・訂正・検索 | [../interfaces/web/pages/pkb.py](../interfaces/web/pages/pkb.py) | [../pkb/application/daily.py](../pkb/application/daily.py), PKB services | PostgreSQL |
| 4 | PKB Entity詳細 | [../interfaces/web/pages/entity.py](../interfaces/web/pages/entity.py) | PKB query / entity model | PostgreSQL |
| 5 | Pending確認 | [../interfaces/web/pages/pkb.py](../interfaces/web/pages/pkb.py) | [../pkb/pending_service.py](../pkb/pending_service.py) | PostgreSQL |
| 6 | Memory Intake | Web / RITSUKO | [../pkb/application/memory_intake.py](../pkb/application/memory_intake.py), [../pkb/memory_intake.py](../pkb/memory_intake.py) | PostgreSQL |
| 7 | RITSUKOへ自然言語依頼（旧Core＋state-driven） | [../interfaces/web/pages/core.py](../interfaces/web/pages/core.py) | [../ritsuko/application/entry.py](../ritsuko/application/entry.py)（旧Core）／[../ritsuko/application/task_runtime.py](../ritsuko/application/task_runtime.py)→[../ritsuko/core/observation_loop.py](../ritsuko/core/observation_loop.py)（state-driven） | Task / MAGI / PKB / Web / Finance |
| 8 | RITSUKO Task再開 | [../interfaces/web/pages/core.py](../interfaces/web/pages/core.py) | `RitsukoApplicationEntry.resume()` | PostgreSQL |
| 9 | RITSUKO Task履歴・trace | [../interfaces/web/pages/core_history.py](../interfaces/web/pages/core_history.py) | [../ritsuko/application/task_queries.py](../ritsuko/application/task_queries.py) | PostgreSQL |
| 10 | MAGI分析 | RITSUKO経由 | [../ritsuko/magi/](../ritsuko/magi/) | Ollama / OpenAI / Gemini等 |
| 11 | MAGI設定 | [../interfaces/web/pages/settings.py](../interfaces/web/pages/settings.py) | [../ritsuko/magi/settings.py](../ritsuko/magi/settings.py) | PostgreSQL |
| 12 | Service Connection設定 | [../interfaces/web/pages/settings.py](../interfaces/web/pages/settings.py) | [../integrations/connections/service_connections.py](../integrations/connections/service_connections.py) | PostgreSQL / Secret |
| 13 | 家計・資産 | [../interfaces/web/pages/finance.py](../interfaces/web/pages/finance.py) | [../capabilities/finance/application.py](../capabilities/finance/application.py) | PostgreSQL / CSV |
| 14 | MoneyForward CSV取込 | Finance画面 | [../capabilities/finance/finance_import.py](../capabilities/finance/finance_import.py) | CSV → PostgreSQL |
| 15 | 利用料金・契約 | [../interfaces/web/pages/service_billing.py](../interfaces/web/pages/service_billing.py) | [../capabilities/service_billing/service.py](../capabilities/service_billing/service.py) | OpenAI / Google Cloud等 |
| 16 | Web Research | RITSUKO / Capability | [../capabilities/web_research/application.py](../capabilities/web_research/application.py) | Web Search / HTTP |
| 17 | システム状態 / Debug | [../interfaces/web/pages/debug.py](../interfaces/web/pages/debug.py) | [../infrastructure/system_debug.py](../infrastructure/system_debug.py) | Git / Runtime / PostgreSQL read-only dashboard |
| 18 | DB ER図 | [../interfaces/web/pages/debug_er.py](../interfaces/web/pages/debug_er.py) | [../application/schema_diagram.py](../application/schema_diagram.py) | PostgreSQL schema metadata / Mermaid / optional ER providers |
| 19 | 開発Workbench | [../interfaces/workbench/app.py](../interfaces/workbench/app.py) | Workbench helpers | LLM / PKB / Core検証 |
| 20 | Secretary REST API | [../interfaces/api/app.py](../interfaces/api/app.py) | [../application/read_service.py](../application/read_service.py), Task / Candidate services | FastAPI / PostgreSQL |
| 21 | 外部ChatGPT read-only MCP | [../interfaces/mcp/server.py](../interfaces/mcp/server.py) | Secretary API read boundary | HTTP → Secretary API |
| 22 | MCP互換entry | [../mcp_adapter/server.py](../mcp_adapter/server.py) | `interfaces.mcp.server`へ委譲 | stdio MCP |
| 23 | DB migration | [../scripts/db/migrate.sh](../scripts/db/migrate.sh) | migration管理 | PostgreSQL |
| 24 | DB Backup / Restore | [../scripts/db/postgres.ps1](../scripts/db/postgres.ps1) | DB運用 | pg_dump / pg_restore |
| 25 | DB再構築検証 | [../scripts/db/promotion-preflight.ps1](../scripts/db/promotion-preflight.ps1), [../scripts/db/promotion-rehearsal.ps1](../scripts/db/promotion-rehearsal.ps1) | fresh schema + settings-only rehearsal | PostgreSQL |
| 26 | Fresh Production初期化 | [../scripts/db/initialize-fresh-production.ps1](../scripts/db/initialize-fresh-production.ps1) | migration 001-008 / existing cluster role reuse | PostgreSQL |
| 27 | Production DB再構築 | [../scripts/db/rebuild-production.ps1](../scripts/db/rebuild-production.ps1) | fresh schema + settings only → replacement DB | PostgreSQL |
| 28 | 旧昇格entry互換 | [../scripts/db/promote-production.ps1](../scripts/db/promote-production.ps1) | clean rebuildへ委譲 | PowerShell |
| 29 | 外部Action（隔離fixture、日常UI/write API非公開） | Application入口 [../ritsuko/application/task_runtime.py](../ritsuko/application/task_runtime.py)（オプションのAction Service） | [../ritsuko/application/external_actions.py](../ritsuko/application/external_actions.py)／[../ritsuko/application/external_action_policy.py](../ritsuko/application/external_action_policy.py) | [../infrastructure/postgres/external_action_repository.py](../infrastructure/postgres/external_action_repository.py) ＋ 固定loopback HTTP。使い捨てDB上の検証でありProduction利用の受入ではない |
| 30 | 環境別権限Profile・MAGI送信先の設定（GitHub＋隔離CIのみ） | [../interfaces/web/pages/settings.py](../interfaces/web/pages/settings.py)、[../interfaces/web/pages/core.py](../interfaces/web/pages/core.py) | [../ritsuko/application/context_policy.py](../ritsuko/application/context_policy.py)、[../ritsuko/magi/async_execution.py](../ritsuko/magi/async_execution.py) | [../infrastructure/postgres/runtime_policy_repository.py](../infrastructure/postgres/runtime_policy_repository.py)、[../db/migrations/009_runtime_policy_profiles.sql](../db/migrations/009_runtime_policy_profiles.sql)。運用DBには未適用 |
| 31 | RITSUKO Task Application共通組立（NiceGUI / Vue架空Port） | [../interfaces/web/pages/core.py](../interfaces/web/pages/core.py)、[../interfaces/vue_lab/task_service.py](../interfaces/vue_lab/task_service.py) | [../bootstrap/ritsuko_task_composition.py](../bootstrap/ritsuko_task_composition.py) → [../ritsuko/application/task_runtime.py](../ritsuko/application/task_runtime.py) | 既存MAGI/Task Port、隔離fixture（接続先を混在させない） |
| 32 | Vue日常UIの保存済みTask表示（Stage A：共有Shell・右Task一覧） | [../interfaces/vue_daily/app.py](../interfaces/vue_daily/app.py)、[../interfaces/vue_daily/frontend/src/App.vue](../interfaces/vue_daily/frontend/src/App.vue)、[../interfaces/vue_daily/frontend/src/app/SecretaryLayout.vue](../interfaces/vue_daily/frontend/src/app/SecretaryLayout.vue)、[../interfaces/vue_daily/frontend/src/features/ritsuko/RitsukoPage.vue](../interfaces/vue_daily/frontend/src/features/ritsuko/RitsukoPage.vue) | [../ritsuko/application/task_queries.py](../ritsuko/application/task_queries.py) / Vue `features/ritsuko/useTasks.ts` / `api.ts` | [../infrastructure/postgres/core_task_query_repository.py](../infrastructure/postgres/core_task_query_repository.py)。8097の明示起動・読取専用。新UIコードの受入はSTATUS参照 |
| 33 | RITSUKO 実Taskコマンド受付契約（Stage B-1、未実接続） | [../interfaces/vue_daily/app.py](../interfaces/vue_daily/app.py)（DI未設定は503） | [../ritsuko/application/task_commands.py](../ritsuko/application/task_commands.py) / [../ritsuko/application/task_runtime.py](../ritsuko/application/task_runtime.py) | 実DB・MAGI/Source/PolicyのComposition Root接続はB-2で実装。Operationはプロセス内一時情報でTask正本ではない |

---

## 2. 現在の主要ソース構成

<details>
<summary>主要source treeを表示</summary>

```text
local-secretary-runtime/
├─ application/
│  ├─ read_service.py
│  └─ schema_diagram.py
│
├─ ritsuko/
│  ├─ application/
│  │  ├─ entry.py
│  │  ├─ read_dispatch.py
│  │  ├─ task_queries.py
│  │  └─ task_records.py
│  ├─ core/
│  │  ├─ core_advisor.py
│  │  ├─ core_capabilities.py
│  │  ├─ core_coordinator.py
│  │  ├─ core_observation.py
│  │  ├─ core_ooda.py
│  │  ├─ core_synthesis.py
│  │  ├─ magi_bridge.py
│  │  ├─ observation_loop.py
│  │  └─ request_scope.py
│  ├─ magi/
│  │  ├─ protocol.py
│  │  ├─ client.py
│  │  ├─ dialogue.py
│  │  ├─ async_execution.py
│  │  ├─ settings.py
│  │  └─ transport_contract.py
│  └─ tasks/
│     ├─ service.py
│     └─ magi_task_store.py
│
├─ pkb/
│  ├─ application/
│  │  ├─ daily.py
│  │  └─ memory_intake.py
│  ├─ persistence.py              # PKB persistence Port
│  ├─ query_service.py
│  ├─ write_service.py
│  ├─ candidate_service.py
│  ├─ pending_service.py
│  ├─ correction_service.py
│  ├─ extraction_service.py
│  ├─ entity_model_service.py
│  ├─ episode_intake.py
│  ├─ memory_contracts.py
│  ├─ memory_extractor.py
│  ├─ memory_grounding.py
│  ├─ memory_intake.py
│  └─ memory_registry.py
│
├─ capabilities/
│  ├─ finance/
│  │  ├─ application.py
│  │  ├─ finance_import.py
│  │  └─ finance_preview.py
│  ├─ service_billing/
│  │  ├─ service.py
│  │  └─ settings.py
│  └─ web_research/
│     ├─ application.py
│     └─ web_research.py
│
├─ integrations/
│  ├─ connections/
│  │  ├─ service_connections.py
│  │  └─ credential_resolver.py
│  └─ llm/
│     └─ ollama_runtime.py
│
├─ interfaces/
│  ├─ web/
│  │  ├─ app.py
│  │  └─ pages/
│  │     ├─ top.py
│  │     ├─ features.py
│  │     ├─ pkb.py
│  │     ├─ entity.py
│  │     ├─ core.py
│  │     ├─ core_history.py
│  │     ├─ finance.py
│  │     ├─ service_billing.py
│  │     ├─ settings.py
│  │     ├─ debug.py
│  │     └─ debug_er.py
│  ├─ api/
│  │  └─ app.py
│  ├─ mcp/
│  │  └─ server.py
│  └─ workbench/
│     ├─ app.py
│     ├─ core.py
│     └─ ...
│
├─ bootstrap/
│  ├─ web_runtime.py
│  └─ api_runtime.py
│
├─ infrastructure/
│  ├─ postgres/
│  │  ├─ read_repository.py
│  │  ├─ task_repository.py
│  │  ├─ candidate_repository.py
│  │  ├─ core_execution_repository.py
│  │  ├─ core_task_query_repository.py
│  │  ├─ core_advisor_repository.py
│  │  ├─ entity_catalog_repository.py
│  │  ├─ pkb_repository.py
│  │  ├─ pkb_write_repository.py
│  │  ├─ pkb_query_repository.py
│  │  ├─ pkb_pending_repository.py
│  │  ├─ pkb_correction_repository.py
│  │  ├─ pkb_entity_repository.py
│  │  ├─ pkb_memory_repository.py
│  │  ├─ magi_task_repository.py
│  │  ├─ magi_settings_repository.py
│  │  ├─ finance_repository.py
│  │  ├─ service_connection_repository.py
│  │  ├─ service_billing_settings_repository.py
│  │  ├─ pkb_runtime.py
│  │  ├─ pkb_debug.py
│  │  └─ schema_introspection.py
│  ├─ schema_diagram/
│  │  └─ providers.py
│  ├─ async_runtime/
│  │  ├─ transport.py
│  │  └─ background_jobs.py
│  └─ system_debug.py
│
├─ config/
│  └─ runtime_database.py
│
├─ db/
│  ├─ migrations/                 # production migration
│  └─ isolated/
│     └─ pkb_proto/               # historical isolated migration
│
├─ scripts/
│  ├─ ui/
│  │  ├─ launch_daily_pkb.ps1
│  │  ├─ launch_gui.ps1
│  │  └─ launch_web_workbench.ps1
│  ├─ db/
│  │  ├─ postgres.ps1
│  │  ├─ migrate.sh
│  │  ├─ compare-runtime-databases.ps1
│  │  ├─ promotion-preflight.ps1
│  │  ├─ initialize-fresh-production.ps1
│  │  ├─ promotion-rehearsal.ps1
│  │  ├─ rebuild-production.ps1
│  │  ├─ promote-production.ps1
│  │  ├─ runtime_settings_transfer.py
│  │  └─ verify_production_runtime.py
│  └─ pkb/
│     └─ isolated/                # isolated regression / evidence
│
└─ tests/
   ├─ test_architecture_boundaries.py
   ├─ test_ritsuko_application_entry.py
   ├─ test_ritsuko_magi_protocol.py
   ├─ test_magi_observation_loop.py
   ├─ test_memory_intake.py
   ├─ test_pkb_daily.py
   ├─ test_finance_import.py
   ├─ test_service_billing.py
   ├─ test_service_connections.py
   ├─ test_system_debug.py
   ├─ test_schema_diagram.py
   ├─ test_read_service.py
   ├─ test_mcp_adapter_static.py
   └─ db/
```

</details>

### Persistence境界（現行）

Productionの業務/Application codeは、DB driver・SQL・cursor・transactionを直接所有せず、業務上意味のあるPort / Repository contractを介して永続化を利用します。具象PostgreSQL adapterの生成・bindingは `bootstrap` が担当します。

```text
interfaces / RITSUKO
  ↓
Application / Domain
  ↓ Port / Repository contract
bootstrap
  ↓ binds
infrastructure/postgres/*Repository
  ↓
PostgreSQL
```

主な対応:

| No. | Owner | Port / 上位contract | PostgreSQL adapter |
| --- | --- | --- |---|
| 1 | PKB | `pkb/persistence.py` と各PKB service | `infrastructure/postgres/pkb_repository.py` + `pkb_*_repository.py` |
| 2 | RITSUKO Task / MAGI | `ritsuko/tasks/magi_task_store.py`, `ritsuko/magi/settings.py` | `magi_task_repository.py`, `magi_settings_repository.py` |
| 3 | Finance | `capabilities/finance/` | `finance_repository.py` |
| 4 | Service Connection | `integrations/connections/service_connections.py` | `service_connection_repository.py` |
| 5 | Service Billing settings | `capabilities/service_billing/settings.py` | `service_billing_settings_repository.py` |

`pkb/episode_intake.py` はhistorical isolated fixture importerであり、通常Production business pathのPersistence Port化対象から除外しています。再流入防止は `tests/test_architecture_boundaries.py` が検査します。

### Persistence機能を追加するときの標準

新しい永続化機能は、原則として次の経路へ追加します。

```text
Application / Domain
  ↓ business-oriented Port / Repository contract
bootstrap
  ↓ binds
infrastructure/postgres/*Repository
  ↓
PostgreSQL
```

- Port / Repositoryはtable単位の薄いSQL wrapperではなく、ownerと業務操作・transaction boundaryを基準に置きます。
- PostgreSQL固有型、SQL、cursor、transaction primitive、JSONBやadvisory lock等の最適化はadapter内部へ閉じます。
- 将来別backendが必要になった場合も、既存Application / Domain / Interfaceの変更を原則増やさず、adapter、Composition Root binding、adapter-specific testの追加を中心に対応します。使う予定のない第2backend分岐は先行実装しません。
- 旧直接SQL pathや互換shimを残す場合は用途と削除条件を明示し、Productionに恒久的な二重Persistence pathを作りません。
- testはarchitecture boundary、Repository semantics、PostgreSQL integration、DB運用・role assertionの責務を区別します。

DB権限については、信頼されたローカルRuntime内部でfeatureごとにwriter roleを細分化すること自体を目的にしません。現行のshared runtime boundaryを基本とし、外部read/write/action、Secret、高影響操作はAPI / MCP / RITSUKO Policy / Approval等の適切な境界で制御します。新しいrole / grant境界は、具体的な脅威・運用要件・復旧性・使い勝手との釣り合いがある場合に評価します。

---

## 3. 日常Web GUI

画面全体のRoute・現行ワイヤーフレームは設計repoの [`UIOverview_画面構成.md`](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/01_Architecture/UIOverview_%E7%94%BB%E9%9D%A2%E6%A7%8B%E6%88%90.md) を参照します。本資料は画面と実装責務の対応を担当し、画面図そのものを重複管理しません。

起動:

```text
scripts/ui/launch_daily_pkb.ps1
  → bootstrap.web_runtime
  → interfaces.web.app
  → NiceGUI
```

画面登録は [../interfaces/web/app.py](../interfaces/web/app.py) が行い、実画面は `interfaces/web/pages/` に分離されています。

```text
TOP
 ├─ PKB
 ├─ RITSUKO
 ├─ 家計・資産
 ├─ 利用料金・契約
 ├─ システム状態 / デバッグ
 └─ 設定
```

`interfaces/web` はUIと利用者操作の入口を担当し、PostgreSQL実装そのものを所有しません。

具象PostgreSQL adapterの組み立ては [../bootstrap/web_runtime.py](../bootstrap/web_runtime.py) が担当します。

### Debug DB ER図

`/debug` はシステム状態ダッシュボードとして保ち、ER図の生成・Provider比較・表示密度切替は専用 `/debug/er` へ分離します。ダッシュボード側はER図の利用可否サマリーと詳細画面への入口だけを持ちます。

UIからSQLやsubprocessを直接実行せず、次の責務へ分離しています。

```text
interfaces/web/pages/debug.py
  ↓ link / status summary
interfaces/web/pages/debug_er.py
  ↓
application/schema_diagram.py
  ↓
bootstrap/web_runtime.py
  ↓
infrastructure/postgres/schema_introspection.py
  ↓
infrastructure/schema_diagram/providers.py
```

Native + Mermaidは現在のDaily Runtime DBからschema metadataだけをread-only取得します。business row dataは読みません。SchemaCrawler / tbls / ERAlchemyは比較用Provider slotとして存在しますが、外部依存と安全なcredential handoffの受入前は自動実行・自動installしません。

---

## 4. RITSUKO

自然言語の秘書依頼の共通Application Entryは、

[../ritsuko/application/entry.py](../ritsuko/application/entry.py)

です。

基本経路:

```text
User
 ↓
interfaces/web/pages/core.py
 ↓
RitsukoApplicationEntry
 ↓
Task / Context
 ↓
RITSUKO Core
 ├─ Observation
 ├─ Capability判断
 ├─ MAGI問い合わせ
 ├─ Action / Result
 └─ Task状態・最終判断
 ↓
PostgreSQL / PKB / Capability
```

RITSUKO内部の主な責務:

| No. | 責務 | 実装 |
| --- | --- | --- |
| 1 | Core coordination | `ritsuko/core/core_coordinator.py` |
| 2 | OODA / 制御 | `ritsuko/core/core_ooda.py` |
| 3 | Observation | `ritsuko/core/core_observation.py` |
| 4 | Observation Loop | `ritsuko/core/observation_loop.py` |
| 5 | Capability判断 | `ritsuko/core/core_capabilities.py` |
| 6 | MAGI接続 | `ritsuko/core/magi_bridge.py` |
| 7 | 結果統合 | `ritsuko/core/core_synthesis.py` |
| 8 | Task保存 | `ritsuko/tasks/` |
| 9 | Application Entry | `ritsuko/application/entry.py` |

RITSUKOは最終Action、Task状態、権限・停止・再開・完了を管理します。

MAGIの出力だけでTask完了を確定しません。

現行のread-only Observation Source経路は次です。

```text
MAGI information_requests
 ↓
ritsuko/application/observation_sources.py
 ↓ Source / privacy / availability / outbound query gate
 ├─ pkb      → 既存PKB read
 ├─ web      → capabilities/web_research
 └─ finance  → capabilities/finance
 ↓
ritsuko/core/observation_loop.py
 ↓
Action / Result / Source / Audit
 ↓
verified Observation
 ↓
MAGI evaluate_observation
```

PKB / Finance等のprivate・sensitive Observationを含む再分析はlocal-onlyです。Webへは、scope済みPKB結果から公開してよいと決定できる技術識別子だけを `public_query_terms` として渡せます。対象を一意に解決できないPKB要求はglobal Claim検索へ広げません。Files / task_history / external_service / pc_observationは現時点ではこの自動read経路へ未接続です。

### 外部Actionの現存コード（未受入の範囲を含む）

| No. | 担当 | 現存ソース | 現在の実証・未達 |
| --- | --- | --- | --- |
| 1 | 個別承認前提のfixture Action Application | [`ritsuko/application/external_actions.py`](../ritsuko/application/external_actions.py) | 登録済み模擬操作に限る。通常GUI/APIから未接続 |
| 2 | 先行Policy helper | [`ritsuko/application/external_action_policy.py`](../ritsuko/application/external_action_policy.py) | 別実装経路。共通Policyへの統合が必要 |
| 3 | Approval / Action / Result / Source / Audit SQL | [`infrastructure/postgres/external_action_repository.py`](../infrastructure/postgres/external_action_repository.py) | 既存schemaを使用。実PostgreSQL統合試験は未確認 |
| 4 | 固定loopback HTTP設定変更Adapter | [`integrations/connections/loopback_setting_adapter.py`](../integrations/connections/loopback_setting_adapter.py) | 使い捨てHTTP fixtureで試験済み。一般HTTP proxyではない |
| 5 | 隔離HTTPテスト | [`tests/test_external_actions.py`](../tests/test_external_actions.py) | MemoryStore + loopback HTTP、2026-10-12 mainのCI PASS |
| 6 | 先行Policyの単体テスト | [`tests/test_external_action_policy.py`](../tests/test_external_action_policy.py) | コード存在。CIの上記独立テストの実行は未確認 |

2026-10-12採用設計は[DesignDecisions D-29](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/00_Project/DesignDecisions_%E8%A8%AD%E8%A8%88%E5%88%A4%E6%96%AD.md)を参照。通常の低影響Actionは事前許可範囲で個別承認なし、高影響Actionは必要な本人承認後に実行する。現行コードは個別承認が必要なfixture操作だけを対象とし、**この採用設計全体を実装・受入済みとは扱わない**。

新規DB試験はproduction `db/migrations/`の現行schemaを使う一時PostgreSQL DBを基本とする。歴史的`db/isolated/pkb_proto/`の別migration系列へ新機能を追加せず、テストデータを運用DBへ昇格・移植する前提を作らない。

---

## 5. MAGI System

MAGIの実装は [../ritsuko/magi/](../ritsuko/magi/) に集約します。

```text
RITSUKO
 ↓
MAGI Protocol
 ↓
MELCHIOR / BALTHASAR / CASPER
 ↓
LLM Profile
 ↓
Service Connection
 ↓
Ollama / OpenAI / Gemini / ...
```

主要実装:

| No. | 責務 | ソース |
| ---: | --- | --- |
| 1 | RITSUKO-MAGI契約 | `ritsuko/magi/protocol.py` |
| 2 | Member呼出し | `ritsuko/magi/client.py` |
| 3 | async実行・全memberへ共通日時を送信 | `ritsuko/magi/async_execution.py` |
| 4 | transport契約 | `ritsuko/magi/transport_contract.py` |
| 5 | Profile / Assignment | `ritsuko/magi/settings.py` |
| 6 | Service Connection | `integrations/connections/service_connections.py` |
| 7 | Secret解決 | `integrations/connections/credential_resolver.py` |
| 8 | Ollama固有runtime | `integrations/llm/ollama_runtime.py` |
| 9 | HTTP async transport | `infrastructure/async_runtime/transport.py` |
| 10 | Task基準日時の生成・再開時読取 | `ritsuko/magi/runtime_context.py` |
| 11 | 同期／legacy MAGI入力 | `ritsuko/magi/dialogue.py` |
| 12 | Finance「先月」のTask基準月検証 | `ritsuko/application/observation_sources.py`, `interfaces/web/app.py` |
| 13 | MAGI Task checkpointの保存 | `infrastructure/postgres/magi_task_repository.py` |

MELCHIOR / BALTHASAR / CASPER はProvider名ではなく論理slotです。

---

## 6. PKB

PKBはRITSUKOから独立して利用できるKnowledge機能です。

日常GUI経路:

```text
interfaces/web/pages/pkb.py
 ↓
pkb/application/
 ↓
PKB services
 ↓
PostgreSQL repository / connection
```

主な責務:

| No. | 機能 | 実装 |
| --- | --- | --- |
| 1 | 日常PKB操作 | `pkb/application/daily.py` |
| 2 | 厳密検索 | `pkb/query_service.py` |
| 3 | 登録 | `pkb/write_service.py` |
| 4 | Pending | `pkb/pending_service.py` |
| 5 | 訂正 | `pkb/correction_service.py` |
| 6 | Entity | `pkb/entity_model_service.py` |
| 7 | 抽出 | `pkb/extraction_service.py` |
| 8 | Memory Intake | `pkb/memory_intake.py` |
| 9 | Grounding | `pkb/memory_grounding.py` |
| 10 | Memory契約 | `pkb/memory_contracts.py` |

厳密条件・全件・履歴・時点・集計はPostgreSQL / SQLをマスタとします。

---

## 7. 独立Capability

RITSUKOは各Capabilityの実装を所有しません。

```text
RITSUKO ─┐
         ├→ Capability Application
GUI ─────┘
              ↓
         Domain / Adapter
```

### Finance

`capabilities/finance/`

MoneyForward CSV取込、version履歴、SQL-first集計・明細表示を担当します。

### Service Billing

`capabilities/service_billing/`

OpenAI / Google等の利用量・料金・契約・Credit・Limitを共通形式へ正規化します。

接続先そのものはService Connectionを参照します。

### Web Research

`capabilities/web_research/`

Web検索、取得、Source品質判定等を担当します。

**2026-10-13 GitHub隔離実装（サブPC実機未受入）:** 日常RITSUKOの`NEED_INFORMATION(source=web)`は、`interfaces/web/app.py::_execute_magi_source_request`から既存`ritsuko/application/observation_sources.py::web_query_from_request`の公開Query検査を通し、`ritsuko/application/context_policy.py::public_web_search_permission`でTaskのDB Profile snapshotの`web.search / remote` Grantを確認してから`ritsuko/magi/native_web.py::search_magi_web`へ渡す。OpenAI Responses `web_search`、Gemini `google_search`、Ollamaの1回のTool Calling（許可済みQueryのみ、実行はRITSUKOが管理）を並列に利用でき、出典／Provider別実行状況を1つの`web` Observationに集約して既存Task履歴へ戻す。出典がない、全員エラー等の時は同じ権限の内側で従来のbounded DDGSへfallbackする。

`web.search` Permission Labelは既存Policy Repositoryの`create_label`／`save_grant`を使ってDBに登録・割当可能だが、**今回のGitHub code反映だけでは運用DBのLabel/Grantは追加されない**。欠落、deny、未確認の場合は新Web経路がfail-closedとなる。既存`llm.send_context.private/sensitive`はWeb検索の許可へ代用しない。従来の直接Web Research UI／旧Coreを含む全入出力面のPolicy統一、本人のサブPC運用Profile設定、実クラウド検索、検索料金・根拠品質、GUIの最終完走は未受入である。

コード: [native_web.py](../ritsuko/magi/native_web.py) / [context_policy.py](../ritsuko/application/context_policy.py) / [observation_sources.py](../ritsuko/application/observation_sources.py) / [test_magi_native_web.py](../tests/test_magi_native_web.py)。


---

## 8. Service Connection

外部接続情報の共通管理:

[../integrations/connections/service_connections.py](../integrations/connections/service_connections.py)

```text
Consumer
  ├─ MAGI LLM Profile
  ├─ Service Billing Profile
  └─ 将来の外部Service
        ↓
Service Connection
        ↓
Adapter / Endpoint / Auth
```

Connectionと「そのConnectionをどの用途に使うか」は分離します。

Secret値はPrompt / Task / Audit / Gitへ流さないことを前提とします。

---

## 9. Secretary API / MCP

### REST API

```text
interfaces/api/app.py
 ↓
bootstrap/api_runtime.py
 ↓
Application Service
 ↓
Postgres Repository
 ↓
PostgreSQL
```

read系の共通Application Service:

[../application/read_service.py](../application/read_service.py)

PostgreSQL実装:

[../infrastructure/postgres/read_repository.py](../infrastructure/postgres/read_repository.py)

### MCP

canonical実装:

[../interfaces/mcp/server.py](../interfaces/mcp/server.py)

互換entry:

[../mcp_adapter/server.py](../mcp_adapter/server.py)

現在の外部MCPはPostgreSQLへ直接接続せず、

```text
ChatGPT
 ↓
MCP
 ↓
Secretary API external-read
 ↓
ReadService
 ↓
PostgresReadRepository
 ↓
PostgreSQL
```

を使用します。

公開read toolは現在、

- `tasks`
- `memory_search`

です。

write / control toolとは認証・Policy境界を分離します。

---

## 10. PostgreSQL / migration

通常Runtimeの構造化データの目標物理DB:

```text
PostgreSQL
└─ secretary
   ├─ PKB
   ├─ RITSUKO
   ├─ Finance
   ├─ MAGI settings
   ├─ Service Connections
   └─ Service Billing
```

論理ownershipと物理DBは別概念です。

production migration:

```text
db/migrations/
```

歴史的な隔離PKB検証migration:

```text
db/isolated/pkb_proto/
```

`db/isolated/pkb_proto/` は検証・証拠資産であり、production `secretary` へ直接適用しません。

DB境界と昇格手順のマスタ:

[database-boundary.md](database-boundary.md)

---

## 11. DB運用・昇格

主要entry:

| No. | 処理 | 実装 |
| --- | --- | --- |
| 1 | PostgreSQL Setup / Start / Doctor | `scripts/db/postgres.ps1` |
| 2 | production migration | `scripts/db/migrate.sh` |
| 3 | 2DB read-only比較 | `scripts/db/compare-runtime-databases.ps1` |
| 4 | promotion事前検査 | `scripts/db/promotion-preflight.ps1` |
| 5 | fresh production初期化 | `scripts/db/initialize-fresh-production.ps1` |
| 6 | settings-only rehearsal | `scripts/db/promotion-rehearsal.ps1` |
| 7 | fresh replacement DB作成 | `scripts/db/rebuild-production.ps1` |
| 8 | production promotion互換entry | `scripts/db/promote-production.ps1` |
| 9 | production runtime検証 | `scripts/db/verify_production_runtime.py` |
| 10 | runtime設定移送 | `scripts/db/runtime_settings_transfer.py` |

本番DB変更と隔離検証は別の受入段階として扱います。

---

## 12. Composition Root

具象InfrastructureをApplicationへ接続する責務は `bootstrap/` に置きます。

### 日常Web

[../bootstrap/web_runtime.py](../bootstrap/web_runtime.py)

### API

[../bootstrap/api_runtime.py](../bootstrap/api_runtime.py)

基本dependency:

```text
interfaces
    ↓
application / domain
    ↑
bootstrap
    ↓
infrastructure
```

Application / DomainからConcrete Infrastructureへの逆依存を増やしません。

静的境界検査:

[../tests/test_architecture_boundaries.py](../tests/test_architecture_boundaries.py)

---

## 13. テスト対応

| No. | テスト | 主な対象 |
| --- | --- | --- |
| 1 | `test_architecture_boundaries.py` | package依存境界、旧runtime path |
| 2 | `test_ritsuko_application_entry.py` | RITSUKO共通Application Entry |
| 3 | `test_ritsuko_magi_protocol.py` | RITSUKO-MAGI契約 |
| 4 | `test_magi_observation_loop.py` | MAGI → Observation → 再判断 |
| 5 | `test_async_transport.py` | async / timeout / cancel / retry |
| 6 | `test_magi_settings.py` | LLM Profile / Member Assignment |
| 7 | `test_memory_intake.py` | Memory Intake |
| 8 | `test_pkb_daily.py` | 日常PKB |
| 9 | `test_pkb_corrections.py` | 訂正 |
| 10 | `test_pkb_query.py` | SQL-first検索 |
| 11 | `test_finance_import.py` | Finance import |
| 12 | `test_service_billing.py` | Service Billing |
| 13 | `test_service_connections.py` | Service Connection |
| 14 | `test_system_debug.py` | Debug snapshot |
| 15 | `test_read_service.py` | 共通read Application |
| 16 | `test_mcp_adapter_static.py` | MCP read-only境界 |
| 17 | `tests/db/` | PostgreSQL / migration / privilege |

代表的なPython回帰:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

PowerShell / repository静的検査:

```powershell
.\tests\db\test-static.ps1
```

---

## 14. 現在の境界ルール

ソースを変更するときは、少なくとも次の依存を維持します。

```text
PKB ─X→ RITSUKO
Capability ─X→ RITSUKO

Interfaces → Application
RITSUKO → Application / Capability contracts
Infrastructure → Application / Domain contracts

Application / Domain ─X→ Interfaces
Application / Domain ─X→ Concrete Infrastructure

MCP ─X→ PostgreSQL direct
Web UI ─X→ PostgreSQL direct
```

RITSUKO中心とは、すべての機能がRITSUKO経由という意味ではありません。

PKB、Finance、Service Billing等は独立利用可能なApplication Capabilityとして維持します。

---

## 15. 変更時の追跡ルール

機能・source構成を変更した場合は、次を確認します。

1. 利用者から見える入口はどこか
2. Interface / Application / Domain / Infrastructureのどの責務か
3. RITSUKO専用か、独立Capabilityか
4. 新しいDB read/write権限が必要か
5. 外部I/O / Secret / Cloud送信境界が増えていないか
6. Task / Action / Result / Source / Auditへ追跡すべき処理か
7. 対応する自動テストはどれか
8. 本資料の「機能 ↔ ソース」対応が古くなっていないか

SOURCE_MAPは現在の実装ソース索引です。directory責務・dependency boundaryは設計repoの `RuntimeSourceArchitecture_実装ソース構造.md`、設計理由はDesignDecisions、実装・実機受入状況はSTATUS、現在選択中の作業はPLANをマスタとします。
