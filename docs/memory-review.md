# Memory Review MVP — 出典登録・候補の審査・正本への反映

> この段階では、**人間がローカルで起動したレビューCLIだけ**がPending Claimを承認・却下できます。
> 通常の `secretary_api` は候補を追加できますが、承認・正本Claimへの反映はできません。
> 本機能はプロトタイプです。複数ユーザー認証、原文の完全保存、記憶の訂正・重複解消、
> 外部操作の承認、LLMの自律実行は未実装です。

## リポジトリ内のファイルパス

- `db/migrations/003_memory_review.sql` — 人間によるレビュー専用のDB権限。
- `scripts/memory/review.py` — エンティティ／Sourceメタデータ登録、候補一覧、レビューCLI。
- `api/secretary_api.py` — 既存の制限付きAPI。候補追加と現在のClaim検索に利用。
- `tests/test_memory_review.py` — 認証設定と取り消しのオフライン検証。

メインPC上の基点は `D:\AI\projects\local-secretary-runtime`。

## 0. バックアップ・マイグレーション

APIの新規コードは変えず、DBにレビュー用のグループロールを追加します。
SQLの適用は専用DB `secretary` に限ります。既存の
`secretary_restore_test1`、他プロジェクトのDB、コンテナ・ボリュームは変更しません。

```powershell
cd D:\AI\projects\local-secretary-runtime
git pull --ff-only origin main
.\scripts\db\postgres.ps1 -Action Backup
.\scripts\db\postgres.ps1 -Action Migrate
.\scripts\db\postgres.ps1 -Action Doctor
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_memory_review.py -v
```

※この手順はPRがマージされてから実行してください。実行前にバックアップ結果のパスを控え、
マイグレーション履歴に `003_memory_review.sql` が追加されたことを確認します。

## 1. 人間専用のレビュー用ログイン

`secretary_api` のパスワード・ロールは変更しません。
APIとは別の `secretary_reviewer` ログインを手動作成します。Dockerコンテナを特定：

```powershell
$container = @(docker ps -q --filter "label=com.docker.compose.project=local-secretary-runtime-db" --filter "label=com.docker.compose.service=secretary-postgres")
if ($container.Count -ne 1) { throw "Expected exactly one secretary-postgres container" }
docker exec -it $container[0] psql -X -U secretary_admin -d secretary
```

`psql` の中で（初回のみ）：

```sql
CREATE ROLE secretary_reviewer LOGIN;
GRANT secretary_memory_writer, secretary_review_writer,
      secretary_audit_writer TO secretary_reviewer;
```

続けて `\password secretary_reviewer` でレビュー専用の強いパスワードを非表示入力し、
`\q` で終了します。**管理者のパスワードを使い回さないでください。**
レビュー用ロールは信頼されたローカル処理のため、Entity/Source/Claimの登録と更新権限を持ちます。
LLM、APIサーバー、n8n、外部サービスには、このログインやパスワードを渡さないこと。

## 2. レビューCLIのローカル接続情報

別のPowerShellで、パスワードを非表示入力します（値をチャット・コマンド履歴に貼らない）。

```powershell
cd D:\AI\projects\local-secretary-runtime
$secure = Read-Host "secretary_reviewer password" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    Remove-Variable secure, ptr -ErrorAction SilentlyContinue
}
$env:LSA_REVIEW_DSN = "host=127.0.0.1 port=55432 dbname=secretary user=secretary_reviewer"
```

`PGPASSWORD` はこのPowerShellとその子プロセスにのみ渡しますが、環境変数は高度な
同一PCのプロセスから読み取られる可能性があります。共有PCや本番用途には、
OSで保護した資格情報保管庫などへの移行が必要です。

## 3. 架空データによる記憶の一連の検証

実際のPC構成や個人情報はまだ登録せず、次の架空データで試験します。

**(1) Entityを作成**

```powershell
.\.venv\Scripts\python.exe scripts\memory\review.py entity --name "架空テストPC" --entity-type device --domain pc --reviewer local_user
```

既存の同名・同domain・同種別のEntityがあれば、重複登録せずIDを表示します。

**(2) Sourceのメタデータを登録**

```powershell
.\.venv\Scripts\python.exe scripts\memory\review.py source --source-type user_statement --uri "local://test/statement-001" --citation "架空データ: テストPCのRAMは16GBとの発言" --reviewer local_user
```

この段階ではURIと引用文のメタデータだけがDBに保存され、
元の長文・ファイルは保存されません。後続のSource Archiveで原本保管を実装します。
それぞれ表示された `entity_id` と `source_id` を控えます。

**(3) APIへ未確定の候補を送信**

以前起動したAPIはそのままでも構いません。API接続用PowerShellで、
`$headers`、`$base` を既に設定している場合：

```powershell
$candidate = @{
  entity_id = "<ここにEntityのUUID>"
  source_id = "<ここにSourceのUUID>"
  claim_type = "attribute"
  predicate = "ram_gb"
  proposed_value = 16
  confidence = 0.8
  evidence = "架空のユーザー発言を基にしたテスト"
  extraction_model = "manual-fixture"
  prompt_version = "fixture-v1"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$base/pending-claims" -Headers $headers -ContentType "application/json" -Body ([Text.Encoding]::UTF8.GetBytes($candidate))
```

返った `id` は `pending_claim_id` です。**まだ正本のClaimではありません。**

**(4) レビュー用PowerShellから候補を確認**

```powershell
.\.venv\Scripts\python.exe scripts\memory\review.py pending
```

出典、対象Entity、提案値、抽出根拠を確認してから、承認する場合のみ：

```powershell
.\.venv\Scripts\python.exe scripts\memory\review.py review --id "<候補UUID>" --decision accepted --notes "架空データの承認テスト" --reviewer local_user
```

CLIが提示する `CONFIRM <候補UUID>` をそのまま入力しない限り、
候補と正本は変更されません。承認時は候補のstatus更新、
memory_write approval、`unverified` の正本Claim作成、監査イベントを
**同一DBトランザクション**でコミットします。人が承認しても、
真偽を別途検証していない候補は `verified` になりません。

不採用なら `--decision rejected`、修正が必要なら `--decision needs_edit`。
`needs_edit` は元候補を確定しません。修正版は新しい候補として再提案します。

**(5) APIから確認**

```powershell
Invoke-RestMethod "$base/claims/current?entity_id=<EntityのUUID>" -Headers $headers
```

`ram_gb: 16` が `origin=reviewed_extraction`、
`verification_status=unverified` として取得できることを確認します。
`verified_only=true` で取得すると、この未検証のClaimは除外されます。

## 保護範囲と制限

- 別ロールによって、候補追加APIには承認や正本書き込みの権限を与えません。
- レビューCLIはローカルで明示的な人の確認が必要です。外部操作の承認は扱いません。
- この段階はローカル試作であり、レビュー者の実本人認証、重複Claim処理、
  出典原本の保存・削除権限分離、監査ログの完全な改ざん防止は今後の課題です。
- DBの既存の `secretary_reviewer` ログインは信頼された書き込み権限を持つため、
  同じ資格情報をAPIサービスの環境変数に入れないでください。
- `yt-topic-search` のコンテナ・DB・データにはアクセスしません。
- Windows実機でのAPIとレビューCLIをつなぐ統合テストは、マージ後のローカル検証で行います。
