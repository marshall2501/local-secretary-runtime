# SOURCE_MAP — ソース構成と機能対応

更新: 2026-10-04  
参照runtime: `local-secretary-runtime main`（確認時 `6023bb67...`）

この資料は、Personal Local Secretary AI の **利用者から見える機能・Application責務・実装ソース** を対応付けるための索引です。

本資料は「どこに何が実装されているか」を示すものであり、機能が実機受入済みであることを意味しません。

- 大目標・製品要件: `local-secretary-ai/docs/PROJECT.md`
- 採用アーキテクチャ: `local-secretary-ai/docs/ARCHITECTURE.md`
- 実装・実機受入状況: `local-secretary-ai/docs/STATUS.md`
- 現在の作業: `local-secretary-ai/docs/PLAN.md`
- DB物理境界: [database-boundary.md](database-boundary.md)

---

## 1. 利用者機能と主な実装

| 利用者から見える処理 | Interface / Entry | 主なApplication / Domain | Infrastructure / 外部I/O |
|---|---|---|---|
| 日常Web GUI起動 | [../interfaces/web/app.py](../interfaces/web/app.py) | [../bootstrap/web_runtime.py](../bootstrap/web_runtime.py) | NiceGUI / PostgreSQL |
| TOP / 機能一覧 | [../interfaces/web/pages/top.py](../interfaces/web/pages/top.py), [../interfaces/web/pages/features.py](../interfaces/web/pages/features.py) | Web Runtime | read-only status |
| PKB登録・訂正・検索 | [../interfaces/web/pages/pkb.py](../interfaces/web/pages/pkb.py) | [../pkb/application/daily.py](../pkb/application/daily.py), PKB services | PostgreSQL |
| PKB Entity詳細 | [../interfaces/web/pages/entity.py](../interfaces/web/pages/entity.py) | PKB query / entity model | PostgreSQL |
| Pending確認 | [../interfaces/web/pages/pkb.py](../interfaces/web/pages/pkb.py) | [../pkb/pending_service.py](../pkb/pending_service.py) | PostgreSQL |
| Memory Intake | Web / RITSUKO | [../pkb/application/memory_intake.py](../pkb/application/memory_intake.py), [../pkb/memory_intake.py](../pkb/memory_intake.py) | PostgreSQL |
| RITSUKOへ自然言語依頼 | [../interfaces/web/pages/core.py](../interfaces/web/pages/core.py) | [../ritsuko/application/entry.py](../ritsuko/application/entry.py) | Task / MAGI / PKB / Capability |
| RITSUKO Task再開 | [../interfaces/web/pages/core.py](../interfaces/web/pages/core.py) | `RitsukoApplicationEntry.resume()` | PostgreSQL |
| RITSUKO Task履歴・trace | [../interfaces/web/pages/core_history.py](../interfaces/web/pages/core_history.py) | [../ritsuko/application/task_queries.py](../ritsuko/application/task_queries.py) | PostgreSQL |
| MAGI分析 | RITSUKO経由 | [../ritsuko/magi/](../ritsuko/magi/) | Ollama / OpenAI / Gemini等 |
| MAGI設定 | [../interfaces/web/pages/settings.py](../interfaces/web/pages/settings.py) | [../ritsuko/magi/settings.py](../ritsuko/magi/settings.py) | PostgreSQL |
| Service Connection設定 | [../interfaces/web/pages/settings.py](../interfaces/web/pages/settings.py) | [../integrations/connections/service_connections.py](../integrations/connections/service_connections.py) | PostgreSQL / Secret |
| 家計・資産 | [../interfaces/web/pages/finance.py](../interfaces/web/pages/finance.py) | [../capabilities/finance/application.py](../capabilities/finance/application.py) | PostgreSQL / CSV |
| MoneyForward CSV取込 | Finance画面 | [../capabilities/finance/finance_import.py](../capabilities/finance/finance_import.py) | CSV → PostgreSQL |
| 利用料金・契約 | [../interfaces/web/pages/service_billing.py](../interfaces/web/pages/service_billing.py) | [../capabilities/service_billing/service.py](../capabilities/service_billing/service.py) | OpenAI / Google Cloud等 |
| Web Research | RITSUKO / Capability | [../capabilities/web_research/application.py](../capabilities/web_research/application.py) | Web Search / HTTP |
| システム状態 / Debug | [../interfaces/web/pages/debug.py](../interfaces/web/pages/debug.py) | [../infrastructure/system_debug.py](../infrastructure/system_debug.py) | Git / Runtime / PostgreSQL read-only |
| 開発Workbench | [../interfaces/workbench/app.py](../interfaces/workbench/app.py) | Workbench helpers | LLM / PKB / Core検証 |
| Secretary REST API | [../interfaces/api/app.py](../interfaces/api/app.py) | [../application/read_service.py](../application/read_service.py), Task / Candidate services | FastAPI / PostgreSQL |
| 外部ChatGPT read-only MCP | [../interfaces/mcp/server.py](../interfaces/mcp/server.py) | Secretary API read boundary | HTTP → Secretary API |
| MCP互換entry | [../mcp_adapter/server.py](../mcp_adapter/server.py) | `interfaces.mcp.server`へ委譲 | stdio MCP |
| DB migration | [../scripts/db/migrate.sh](../scripts/db/migrate.sh) | migration管理 | PostgreSQL |
| DB Backup / Restore | [../scripts/db/postgres.ps1](../scripts/db/postgres.ps1) | DB運用 | pg_dump / pg_restore |
| DB再構築検証 | [../scripts/db/promotion-preflight.ps1](../scripts/db/promotion-preflight.ps1), [../scripts/db/promotion-rehearsal.ps1](../scripts/db/promotion-rehearsal.ps1) | clean rebuild rehearsal | PostgreSQL |
| Production DB再構築 | [../scripts/db/rebuild-production.ps1](../scripts/db/rebuild-production.ps1) | current schema + selected data → replacement DB | PostgreSQL |
| 旧昇格entry互換 | [../scripts/db/promote-production.ps1](../scripts/db/promote-production.ps1) | clean rebuildへ委譲 | PowerShell |

---

## 2. 現在の主要ソース構成

```text
local-secretary-runtime/
├─ application/
│  └─ read_service.py
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
│  │     └─ debug.py
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
│  │  ├─ pkb_runtime.py
│  │  └─ pkb_debug.py
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
│  │  ├─ promotion-rehearsal.ps1
│  │  ├─ promote-production.ps1
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
   ├─ test_read_service.py
   ├─ test_mcp_adapter_static.py
   └─ db/
```

---

## 3. 日常Web GUI

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

| 責務 | 実装 |
|---|---|
| Core coordination | `ritsuko/core/core_coordinator.py` |
| OODA / 制御 | `ritsuko/core/core_ooda.py` |
| Observation | `ritsuko/core/core_observation.py` |
| Observation Loop | `ritsuko/core/observation_loop.py` |
| Capability判断 | `ritsuko/core/core_capabilities.py` |
| MAGI接続 | `ritsuko/core/magi_bridge.py` |
| 結果統合 | `ritsuko/core/core_synthesis.py` |
| Task保存 | `ritsuko/tasks/` |
| Application Entry | `ritsuko/application/entry.py` |

RITSUKOは最終Action、Task状態、権限・停止・再開・完了を管理します。

MAGIの出力だけでTask完了を確定しません。

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

| 責務 | ソース |
|---|---|
| RITSUKO-MAGI契約 | `ritsuko/magi/protocol.py` |
| Member呼出し | `ritsuko/magi/client.py` |
| async実行 | `ritsuko/magi/async_execution.py` |
| transport契約 | `ritsuko/magi/transport_contract.py` |
| Profile / Assignment | `ritsuko/magi/settings.py` |
| Service Connection | `integrations/connections/service_connections.py` |
| Secret解決 | `integrations/connections/credential_resolver.py` |
| Ollama固有runtime | `integrations/llm/ollama_runtime.py` |
| HTTP async transport | `infrastructure/async_runtime/transport.py` |

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

| 機能 | 実装 |
|---|---|
| 日常PKB操作 | `pkb/application/daily.py` |
| 厳密検索 | `pkb/query_service.py` |
| 登録 | `pkb/write_service.py` |
| Pending | `pkb/pending_service.py` |
| 訂正 | `pkb/correction_service.py` |
| Entity | `pkb/entity_model_service.py` |
| 抽出 | `pkb/extraction_service.py` |
| Memory Intake | `pkb/memory_intake.py` |
| Grounding | `pkb/memory_grounding.py` |
| Memory契約 | `pkb/memory_contracts.py` |

厳密条件・全件・履歴・時点・集計はPostgreSQL / SQLを正本とします。

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

DB境界と昇格手順の正本:

[database-boundary.md](database-boundary.md)

---

## 11. DB運用・昇格

主要entry:

| 処理 | 実装 |
|---|---|
| PostgreSQL Setup / Start / Doctor | `scripts/db/postgres.ps1` |
| production migration | `scripts/db/migrate.sh` |
| 2DB read-only比較 | `scripts/db/compare-runtime-databases.ps1` |
| promotion事前検査 | `scripts/db/promotion-preflight.ps1` |
| promotion rehearsal | `scripts/db/promotion-rehearsal.ps1` |
| production promotion | `scripts/db/promote-production.ps1` |
| production runtime検証 | `scripts/db/verify_production_runtime.py` |
| runtime設定移送 | `scripts/db/runtime_settings_transfer.py` |

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

| テスト | 主な対象 |
|---|---|
| `test_architecture_boundaries.py` | package依存境界、旧runtime path |
| `test_ritsuko_application_entry.py` | RITSUKO共通Application Entry |
| `test_ritsuko_magi_protocol.py` | RITSUKO-MAGI契約 |
| `test_magi_observation_loop.py` | MAGI → Observation → 再判断 |
| `test_async_transport.py` | async / timeout / cancel / retry |
| `test_magi_settings.py` | LLM Profile / Member Assignment |
| `test_memory_intake.py` | Memory Intake |
| `test_pkb_daily.py` | 日常PKB |
| `test_pkb_corrections.py` | 訂正 |
| `test_pkb_query.py` | SQL-first検索 |
| `test_finance_import.py` | Finance import |
| `test_service_billing.py` | Service Billing |
| `test_service_connections.py` | Service Connection |
| `test_system_debug.py` | Debug snapshot |
| `test_read_service.py` | 共通read Application |
| `test_mcp_adapter_static.py` | MCP read-only境界 |
| `tests/db/` | PostgreSQL / migration / privilege |

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

SOURCE_MAPは現在のソース索引であり、設計理由はDesignDecisions、実装・実機受入状況はSTATUS、現在選択中の作業はPLANへ記録します。
