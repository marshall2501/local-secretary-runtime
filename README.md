# Local Secretary Runtime

Personal Local Secretary AI の実装リポジトリ。目標は **記憶・調査・計画・安全な実行・検証・記録・継続フォローアップ** を一つの依頼で完了できる秘書AIです。

[設計repoの新・目標構成図](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/assets/secretary-core-vnext.png) · [Secretary Core vNext](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/01_Architecture/SecretaryCore-vNext.md) · [最小プロトタイプ計画](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/03_Workflows/PrototypeVerticalSlice.md)

## 現状（実装済みと目標を区別）

現在：非破壊の `setup-runtime.ps1`、`doctor.ps1`、環境テンプレート、PC別プロファイル、独立 PostgreSQL Compose、Memory / 永続 Task の初期 SQL、バックアップ・新規 DB への復元スクリプト。Secretary Core、Memory Write Service、承認の実行時検証、MCP Gateway、Ollama/n8n 連携は今後の実装です。

目標構成：
- **Python Secretary Core**：対話、Planner、永続Task Manager、Memory Engine、Research Engine、Tool Executor、Policy & Approval、Model Router。
- **正本**：PostgreSQL＋Source原本。SQL/全文検索を優先し、必要に応じてベクトル検索。
- **連携**：MCP、PC Agent、各種API。n8nは定期処理・Webhook・通知。
- **推論**：ローカルLLM（Ollama等）＋必要に応じたクラウドLLM。
- **承認**：AIの未確定な記憶更新はPending Claimsへ。外部操作の実行承認は独立して管理。

## セットアップ（Windows / PowerShell）

先に `.env.example` のパスを確認してください。デフォルトは `D:\AI` です。初回の安全確認：

```powershell
cd D:\AI\projects\local-secretary-runtime
.\scripts\setup\setup-runtime.ps1 -WhatIf
.\scripts\setup\setup-runtime.ps1
.\scripts\doctor\doctor.ps1
```

`setup-runtime.ps1` は不足フォルダだけを作り、`.env` がない場合のみ `.env.example` からコピーします。既存ファイルを上書きしません。Git/Docker/Pythonの有無を調べ、Node.js/OllamaがなければTODOとして表示します。ソフトウェアの自動インストール、Docker起動、外部repoの変更はしません。`-RunDoctor` を指定するとセットアップ後に既存doctorを起動します。Windows実機では未検証なので、最初に `-WhatIf` を確認してください。

`doctor.ps1` は現在固定の `D:\AI` パスを使用しています。サブPCなど別パスでセットアップする場合は、doctorの可変パス対応が必要です。

## PostgreSQL を開始する

GitHub でマージされた変更は、ローカルの `D:\AI\projects\local-secretary-runtime` で取得します。`D:\AI` はその親フォルダーです。
`main` ブランチで `git pull --ff-only origin main` を実行してください。変更の取得と、以下のコンテナ起動・DB 初期化は別の操作です。

Docker Desktop の Linux containers を使用します。既存セットアップ後、runtime repo 内で：

```powershell
.\scripts\db\postgres.ps1 -Action Setup
.\scripts\db\postgres.ps1 -Action Start
.\scripts\db\postgres.ps1 -Action Migrate
.\scripts\db\postgres.ps1 -Action Doctor
```

専用 project `local-secretary-runtime-db`、専用 volume/network、`127.0.0.1:55432` を使用。起動前に競合を検査します。既存 `.env` は変更せず、DB 用 `.env.postgres` と `secrets/` は Git 対象外です。

[設計との対応、ポート変更、権限の境界、バックアップ・復元、テスト手順](docs/postgres.md) を参照してください。`doctor.ps1 -Postgres` で DB 診断も追加できます。

DB 基盤は Windows / Docker Desktop の隔離テスト環境で検証済みです（PowerShell 5.1 で再起動・バックアップ復元・ポート競合拒否・マイグレーション改変検知を確認）。`D:\AI` の通常運用環境への導入は別途必要です。

## セキュリティと別プロジェクト保護

`.env`、APIキー、実データ、DB本体、Docker volume、モデル、ログ、バックアップをGitへ入れないでください。`yt-topic-search` は同じPC上で稼働する**独立した別プロジェクト**です。構築時に他のCompose project、コンテナ、volume、portを変更しません。

## Python Secretary API（試作段階）

専用PostgreSQL上の記憶・タスクを扱う、localhost限定・Bearer認証付きPython APIのMVPを追加しました。
現時点の機能は現在のClaimとEntityの検索、未レビュー候補の提案、タスク作成・一覧・中断・再開です。
未確定候補の承認・正本への反映、Source取り込み、外部操作、タスク自動実行はまだできません。

**管理者DBユーザーをAPIへ渡さず**、読み取り・候補提案・タスク更新・監査追加に限定した専用ログインを作ってください。
[Python APIの日本語セットアップと機能・制限](docs/python-api.md)に、専用ロール作成・venv・起動・架空データでの動作確認を記載しています。
Python APIの依存ライブラリとオフラインテストは追加済みですが、利用者のメインPCでの統合検証は未実施です。

## Memory Review（人間専用のローカルCLI・試作段階）

未確定なPending Claimから正本Claimへ反映する、人間専用のレビューCLIを追加しました。
独立した `secretary_reviewer` のDBログインを使用し、通常のPython APIには承認権限を与えません。
Entityとユーザー発言のSourceメタデータ作成、候補一覧、承認・却下・要修正、承認時の
`unverified` Claim追加、memory_writeの承認記録、監査記録を扱います。
**元のファイル・全文のアーカイブ、真偽の検証、重複や訂正の解決は未実装**です。

マイグレーション`003_memory_review.sql`を適用し、手動でレビュー専用ログインを作成してから利用します。
実行コマンドと機能制限は [`docs/memory-review.md`](docs/memory-review.md) に記載しています。
レビューログインをAIやAPIサーバーに設定しないでください。

## ③ 記憶参照：SQL-first 検索（試作段階）

`GET /memory/search` は既存のEntity/Claim/Issue/Hypothesis/Sourceメタデータを
PostgreSQLから直接取得する**読み取り専用**エンドポイントです。
未検証のClaimとHypothesisを区別し、通常は有効期間内のClaimを表示。
`include_history=true` で過去のClaimも検索可能です。
`q` を省略すれば対象の全件を `total` と `limit/offset` で列挙でき、
RAGの類似上位件数に依存しません。外部Web調査や出典原本の検索は未実装です。

手順と制約は [`docs/memory-search.md`](docs/memory-search.md) を参照。

## 次の実装

Source Archive、Memory Write Service / API、実行時の承認検証、永続Task ManagerとSecretary Core最小APIを追加し、記憶→調査→計画→承認→安全な実行→検証→記録→フォローアップを通したテストを行います。今回の DB 基盤だけで自律実行や承認ポリシーの完成とはしません。
