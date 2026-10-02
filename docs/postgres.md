# PostgreSQL runtime operations

この文書は `local-secretary-runtime` のPostgreSQL構築・migration・バックアップ・復元・安全境界を扱います。製品設計や実装済み範囲の正本ではありません。設計は `local-secretary-ai` の [`ARCHITECTURE`](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/ARCHITECTURE.md)、実証済み現在地は [`STATUS`](https://github.com/marshall2501/local-secretary-ai/blob/main/docs/STATUS.md) を確認してください。

## 通常構成

| 項目 | 値 |
| --- | --- |
| Compose project | `local-secretary-runtime-db` |
| service | `secretary-postgres` |
| volume | `local-secretary-runtime-db_secretary_pgdata` |
| network | `local-secretary-runtime-db_secretary_db` |
| host接続 | `127.0.0.1:55432` 既定 |
| DB / 管理用user | `secretary` / `secretary_admin` |
| image | `postgres:17-bookworm` |

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

## Migration

`Migrate` は `db/migrations/NNN_*.sql` を順番に適用し、適用履歴とSHA-256を確認します。適用済みmigrationを編集しないでください。変更は新しいmigrationとして追加します。

失敗した未適用migrationはトランザクション境界で扱い、既存の適用済み履歴を書き換えて帳尻を合わせません。スキーマ変更前は影響に応じてバックアップ・復元可能性を確認してください。

`pkb_proto/sql` は隔離PKB試験用であり、運用 `secretary` DBのmigrationディレクトリではありません。

## 権限

DBには読み取り、候補、記憶更新、Task更新、監査等の責務を分離したroleがあります。通常アプリへ `secretary_admin` を渡さず、用途に必要な最小権限のloginを使います。

記憶書込みの可否、Pending確認、外部操作の承認は別の責務です。DB roleが存在するだけで、LLM出力や外部Actionの実行許可が成立するわけではありません。

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
