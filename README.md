# Local Secretary Runtime

Personal Local Secretary AI の実装リポジトリです。PC専用Botではなく、記憶・調査・計画・安全な実行・検証・記録・継続対応を、利用者の依頼を起点に扱う統合秘書AIを実装します。

設計・優先順位・検証済み現在地の正本は `local-secretary-ai` 側です。

- [START_HERE](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/START_HERE.md)
- [PROJECT](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/PROJECT.md)
- [ARCHITECTURE](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/ARCHITECTURE.md)
- [STATUS](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/STATUS.md)
- [PLAN](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/PLAN.md)
- [目標構成図](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/assets/secretary-core-vnext.svg)

`README.md` は実装状況の履歴や次の作業を管理しません。何が実証済みかは設計repoの `STATUS` と実機証拠を確認してください。

## このrepoの責務

- **RITSUKO / Secretary Core**: Task、状態遷移、権限、Observation、Action/Result、停止・再開、最終判断を管理するプログラム側の制御層。
- **MAGI**: MELCHIOR / BALTHASAR / CASPER の最大3スロットに、ローカルまたはクラウドLLMを割り当てて意味理解・分析・提案を行う交換可能な判断層。
- **PKB**: PostgreSQLを構造化記憶の正本とし、SQL-firstの検索、Memory Intake、訂正、履歴、Pending例外処理を実装。
- **API / UI**: FastAPI、NiceGUI、開発Workbench、日常用ポータル。
- **Runtime**: Docker、PowerShell、設定、診断、バックアップ・復元、テスト。

LLMの出力だけで記憶やTask完了を確定しません。RITSUKOと決定的なゲートが根拠・状態・権限・実行結果を検査します。明確な低リスク本人申告は条件を満たせばMemory Intakeから自動登録でき、曖昧さ・競合・高影響などはPendingへ送ります。外部操作の承認は記憶登録とは別です。

## 主な入口

- `pkb_proto/daily_pkb.py` / `pkb_proto/launch_daily_pkb.ps1` — localhostの日常用ポータル。現在のランチャーは隔離PKB DBと専用writerを明示的に検査します。
- `pkb_proto/magi_async.py` — state-driven MAGI通信と各provider adapter。
- `pkb_proto/magi_core_bridge.py` / `pkb_proto/magi_task_store.py` — RITSUKO側のObservation、Task永続化、resume/review境界。
- `pkb_proto/memory_intake.py` / `pkb_proto/ingestion_gate.py` — Memory Intakeと決定的な書込み判断境界。
- `pkb_proto/pending_service.py` — 例外Pendingの保存・確認処理。
- `api/secretary_api.py` — localhost限定のSecretary API。
- `scripts/db/postgres.ps1` — PostgreSQLのSetup / Start / Migrate / Doctor / Backup / Restore。
- `Launch-PKB-Web.cmd` — 開発Workbench。

`pkb_proto` というディレクトリ名は歴史的なものです。中には現行の検証・統合スライスも含まれます。各機能の採用状況や実機到達点はファイル名ではなく `STATUS` と実測で判断してください。

## PostgreSQL

通常のDB操作はruntime repo内から専用スクリプトを使います。

```powershell
cd D:\AI\projects\local-secretary-runtime
.\scripts\db\postgres.ps1 -Action Setup
.\scripts\db\postgres.ps1 -Action Start
.\scripts\db\postgres.ps1 -Action Migrate
.\scripts\db\postgres.ps1 -Action Doctor
```

既定のCompose projectは `local-secretary-runtime-db`、ホスト公開は `127.0.0.1` のみです。詳細は [`docs/postgres.md`](docs/postgres.md) を参照してください。

## API

`docker/compose.api.yml` はDBとは別のCompose project `local-secretary-runtime-api` としてAPIを起動し、DB専用networkへ接続します。秘密情報やDBデータはイメージに含めません。通常運用先はサブPC、メインPCは手動復旧先です。詳細は [`docs/portable-api.md`](docs/portable-api.md)。

`GET /memory/search` はPostgreSQLを直接読むSQL-firstの読み取り専用検索です。詳細は [`docs/memory-search.md`](docs/memory-search.md)。

## 安全条件

- `.env`、APIキー、Secret、個人原本、DBダンプ、個人ログをGitへ登録しない。
- PostgreSQL管理者資格情報をLLMや通常アプリへ渡さない。
- `pkb_proto/sql` の隔離試験用migrationを運用 `secretary` DBへ適用しない。
- 実データ・運用DB・高影響操作では、設計repoのD-08に従いバックアップ、復元可能性、権限、承認を確認する。
- 同じPC上の独立プロジェクト `yt-topic-search` のCompose、container、network、volumeを変更・停止しない。
- GitHub反映、オフラインテスト、隔離DB試験、サブPC実機、統合動作、日常利用を区別し、未実証を完成と報告しない。

## 文書の扱い

runtime側の文書は現在使う運用方法・API境界・安全条件だけを扱います。過去の「次の作業」「当時は未実装」「特定モデルで成功」といった時点依存の記録は現行仕様として使わず、必要な実験知見は設計repoの `DevelopmentKnowledge`、現在地は `STATUS`、優先作業は `PLAN` を正本とします。
