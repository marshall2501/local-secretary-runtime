# Local Secretary Runtime

Personal Local Secretary AI の実装リポジトリです。PC専用Botではなく、記憶・調査・計画・安全な実行・検証・記録・継続対応を、利用者の依頼を起点に扱う統合秘書AIを実装します。

設計・優先順位・検証済み現在地のマスタは `local-secretary-ai` 側です。

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
- **PKB**: PostgreSQLを構造化記憶のマスタとし、SQL-firstの検索、Memory Intake、訂正、履歴、Pending例外処理を実装。
- **API / UI**: 共通FastAPI、Vue（日常UI移行中）、NiceGUI（受入まで併存）、独立開発Workbench。
- **Service Connections**: LLM Providerや将来の外部／ローカルServiceについて、adapter / endpoint / credential参照 / 技術Capabilityを共通管理。Secret値そのものやRITSUKOの操作承認は別境界。
- **Runtime**: Docker、PowerShell、設定、診断、バックアップ・復元、テスト。

LLMの出力だけで記憶やTask完了を確定しません。RITSUKOと決定的なゲートが根拠・状態・権限・実行結果を検査します。明確な低リスク本人申告は条件を満たせばMemory Intakeから自動登録でき、曖昧さ・競合・高影響などはPendingへ送ります。外部操作の承認は記憶登録とは別です。

## 新Secretary Server（単一Host・実装初段、日常切替前）

- **正規Web API/Vue Host**: `interfaces/api/secretary_server.py`。Task受付／本人回答／Review／Stop・Resumeと独立PKB検索を同じOriginで提供。
- **単一起動Composition Root**: `bootstrap/secretary_server.py`。既存の運用`secretary` DB接続設定と共通Applicationを使用し、Python側から `python -m bootstrap.secretary_server` で起動できる実装。**ただし旧8097サーバーが起動中なら同じポートで二重起動できない**。起動を実施するのは受入・稼働切替時に本人と確認してから。
- **ログ**: コンソールと `logs/secretary.log` に同時記録し、10MiB・過去5本のローテーション。ミリ秒付き。旧8097/8099のポート別ログは旧Host専用であり新構造に使用しない。
- **隔離検証**: `tests/test_secretary_server.py`、Chromium fake serverを単一プロセス化した `interfaces/vue_daily/browser_fixture.py`。2026-10-18 [Vue/API/Chromium CI PASS](https://github.com/marshall2501/local-secretary-runtime/actions/runs/39080890696)。
- **未完了**: 8010別APIと外部PVA/MCP readの統合、PKB書込やFinance/設定等のAPI/画面、旧NiceGUI・旧Preview source撤去、通常ランチャーの正式切替・本人実機受入。**現行日常CMDはまだ旧入口へ向く**。

## 主な入口

- `interfaces/vue_daily/` / `interfaces/api/ritsuko_tasks.py` — 共通Task APIを使うVue日常UI移行先。PKBのSQL-first検索は `interfaces/api/pkb_read.py` と共通ReadService経由。記憶登録・訂正・Pendingは旧NiceGUIを継続。正式切替は本人実機受入後。
- `interfaces/web/app.py` / `scripts/ui/launch_daily_pkb.ps1` — 受入まで存続する旧NiceGUI localhost入口。接続先は通常DB `secretary`（旧隔離DB分岐なし）。
- `ritsuko/core/task_execution.py` — 単一のTask/Source/Result/Review実行Core。
- `ritsuko/magi/session_workflow.py` / `judgment_strategies.py` — 共通MAGI Sessionとstandard/Goal判断方式。
- `ritsuko/magi/async_execution.py` — Provider非同期通信、並列、timeout/cancel。旧Session転送入口は撤去。
- `integrations/connections/` — 共通Service Connection RegistryとSecret非保持の資格情報参照境界。
- `ritsuko/core/` / `ritsuko/tasks/` / `ritsuko/application/` — RITSUKO側のObservation、Task、共通依頼入口、resume/review境界。
- `pkb/` — Memory Intake、SQL-first検索、訂正、Pendingと決定的な書込み判断境界。
- `capabilities/` — Finance、Service Billing、Web Research等の再利用Capability。
- `api/secretary_api.py` — localhost限定のSecretary API。
- `scripts/db/postgres.ps1` — PostgreSQLのSetup / Start / Migrate / Doctor / Backup / Restore。
- `Launch-PKB-Web.cmd` — 開発Workbench。

### RITSUKO画面の起動（サブPC）

- **Vue Preview（8097）/ Task Commands（8099）のUvicornログ**: 起動・エラー・HTTPアクセスをLogRecordの実時刻からローカル日時 **ミリ秒3桁＋UTCオフセット** 付きで表示。**コンソール＋Git管理外の `logs/vue-readonly-8097.log` / `logs/vue-task-8099.log` の両方**へ出力する。各ファイルは10MiBでローテーションし、過去5本まで保持。サーバー名はログ行にも付加する。ファイルのマスキング等の追加開発は今回行わず、ローカル開発用の原因調査に利用する。次回Pythonサーバー起動から反映し、稼働中のサーバーを自動停止・再起動しない。

- **日常運用**: `Launch-Local-Secretary-AI.cmd`。Pythonコードが変わっても自動再起動しません。
- **開発中の自動リロード**: `Launch-Local-Secretary-AI-Dev.cmd`。従来と同じProduction DB/既存の起動前チェックを使い、ソース内の`*.py`変更でWebプロセスを自動再起動します。ブラウザは自動で増やさず、普段のRITSUKOのURLへアクセスしてください。初回のみ既存Workbenchを閉じて開発用を起動します。
- **注意**: これは無停止更新ではありません。MAGI/Actionが実行中の状態でGit pullやPython変更を行うとTaskが中断される可能性があります。Taskの終了を確認してからコードを更新してください。F5や実行途中のTask復元は別のR&D課題です。不安定なら開発用を終了し、日常運用用ランチャーで起動し直してください。

旧 `pkb_proto` の専用Migrationと隔離DB用Smoke/Launcher分岐はGit追跡ソースから撤去しました。現行Migrationは `db/migrations/` 一系列で、隔離検証でも同じschemaを使用します。独立Workbenchの架空入力検証は残ります。過去の削除内容はGit履歴から参照できます。

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
- `db/migrations/` を現行schemaの唯一のMigration正本とし、実DBへの適用は別の高影響操作承認で扱う。
- 実データ・運用DB・高影響操作では、設計repoのD-08に従いバックアップ、復元可能性、権限、承認を確認する。
- 同じPC上の独立プロジェクト `yt-topic-search` のCompose、container、network、volumeを変更・停止しない。
- GitHub反映、オフラインテスト、隔離DB試験、サブPC実機、統合動作、日常利用を区別し、未実証を完成と報告しない。

## 文書の扱い

runtime側の文書は現在使う運用方法・API境界・安全条件だけを扱います。過去の「次の作業」「当時は未実装」「特定モデルで成功」といった時点依存の記録は現行仕様として使わず、必要な実験知見は設計repoの `DevelopmentKnowledge`、現在地は `STATUS`、優先作業は `PLAN` をマスタとします。


## Copyright

© 2026 marshall2501. ライセンスは第三者権利の確認後に本リポジトリの `LICENSE` で明示します。現時点でMITの適用が完了したことを意味しません。
