# PostgreSQL runtime operations

この文書は `local-secretary-runtime` のPostgreSQL構築・migration・バックアップ・復元・安全境界を扱います。製品設計や実装済み範囲の正本ではありません。設計は `local-secretary-ai` の [`ARCHITECTURE`](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/ARCHITECTURE.md)、実証済み現在地は [`STATUS`](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/STATUS.md) を確認してください。

## 通常構成

| No. | 項目 | 値 |
| --- | --- | --- |
| 1 | Compose project | `local-secretary-runtime-db` |
| 2 | service | `secretary-postgres` |
| 3 | volume | `local-secretary-runtime-db_secretary_pgdata` |
| 4 | network | `local-secretary-runtime-db_secretary_db` |
| 5 | host接続 | `127.0.0.1:55432` 既定 |
| 6 | DB / 管理用user | `secretary` / `secretary_admin` |
| 7 | image | `postgres:17-bookworm` |

通常運用先はサブPC、メインPCは手動復旧先です。ポート等の実値は `.env.postgres` と起動中containerを正本として確認してください。

## Setup / Start / Migrate / Doctor

```powershell
cd D:\AI\projects\local-secretary-runtime
.\scripts\db\postgres.ps1 -Action Setup
.\scripts\db\postgres.ps1 -Action Start
.\scripts\db\postgres.ps1 -Action Migrate
.\scripts\db\postgres.ps1 -Action Doctor
```

`Setup` は不足している `.env.postgres` と `secrets/postgres-password.txt` を作成しますが、既存設定やSecretを上書きしません。SecretをGit、Issue、チャットへ貼らず、ローカルファイルのACLも保護してください。

`Start` は専用project・service・portの所有関係を確認し、競合時に別ポートへ勝手に変更したり他プロセスを停止したりしません。通常はComposeを直接操作せず `postgres.ps1` を使います。

`Doctor` はhealth、localhost binding、認証、migration履歴等を検査します。migration前に失敗する項目がある場合は `Start → Migrate → Doctor` の順で確認します。

## Migration / clean rebuild

稼働中DBに適用済みのmigration履歴は書き換えません。通常の差分変更では、現在のactive migration列へ新しいmigrationを追加します。

一方、構成整理などで永続化構造そのものを整理し直す場合は、旧migrationを新DBへ再演することを目的にしません。旧DBと旧migrationはrollback／履歴資産として保全し、現在採用schemaから別名のclean replacement DBを作り、必要データを選別移植できます。切替前に新DBで既存機能を確認し、旧DBは戻し先として残します。

`db/isolated/pkb_proto` は隔離PKB試験の履歴・回帰資産であり、通常production DBへ直接適用しません。

## 権限と操作境界

`secretary_admin` はDB作成・復旧・schema管理用です。通常のローカルRuntimeは共通のruntime loginからPKB / RITSUKO / Finance / MAGI設定 / Service Connection / Service Billing等の必要tableを利用できる構成を許容し、機能ごとのwriter role分割を成果として増やしません。

外部クライアントからのread/write、ChatGPT / MCP、外部変更Action、Secret、高影響操作は、Web/API/MCPの公開surface・認証とRITSUKO Policy / Approval / Executor等、その操作を実際に制御できる境界で制限します。DB roleの追加分割は具体的な運用上の必要性が出たとき再評価します。

`current_claims` は現在有効な記録を表しますが、すべてがverifiedであることを意味しません。有効時点、記録時点、supersedes、verification等を失わず扱ってください。

## Backup

```powershell
.\scripts\db\postgres.ps1 -Action Backup
```

既定ではGit除外対象の `backups/` を使用します。外部保存先を指定する場合は、例:

```powershell
.\scripts\db\postgres.ps1 -Action Backup -BackupPath 'D:\AI\data\backup\secretary-YYYYMMDD.dump'
```

`pg_dump --format=custom` の論理バックアップを使用します。ダンプには個人データが含まれ得るため、暗号化・アクセス制御・保存期間・別機器への退避を必要に応じて管理してください。稼働中のPostgreSQLデータディレクトリの単純コピーを論理バックアップの代用にしません。

## Restore drill

復元は既存運用DBを上書きせず、新しい `secretary_restore_*` DBへ行います。

```powershell
.\scripts\db\postgres.ps1 -Action Restore `
  -BackupPath 'D:\AI\data\backup\example.dump' `
  -RestoreDatabase secretary_restore_drill1
```

復元後はmigration版、主要テーブル件数、代表Task/Claim、必要なSource/原本との対応を復元元と照合します。新PCへ復旧する場合、クラスタ共通role・login・Secretはダンプだけでは完結しないため別途再構築・照合します。

## Tests

DB基盤の静的・隔離統合テスト入口:

```powershell
.\tests\db\test-static.ps1
.\tests\db\test-integration.ps1
```

隔離統合テストは専用projectと架空データを使い、所有権、migration、権限、再起動、backup/restore等の回帰を検査します。テスト成功は運用DBやサブPC統合の成功と同義ではありません。実機検証の現在地は設計repo `STATUS` に記録します。
