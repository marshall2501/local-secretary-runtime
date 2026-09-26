# Python Secretary API MVP（ローカル限定・未完成の縦断実装）

この段階は **Memoryの読み取り・未確定候補の提案と、タスクの作成・中断・再開** のみです。
Pending Claimの承認・正本への昇格、外部ツール実行、LLM、スケジューラ、Source取り込みは**未実装**。
PCだけでなく他のdomainにも同じエンドポイントを使えるようにしています。

## 前提と秘密情報

- 専用PostgreSQLが起動済みで、001/002マイグレーションが適用されていること。
- Python 3.12。
- 以下で作成する専用ログインには読み取り・未レビュー候補の追加・タスク更新・監査イベント追加だけを許可する。
  管理者アカウント `secretary_admin` をAPIへ渡すのは禁止。端末を共有する場合はOSアカウントのアクセス権を分離する。
- HTTPは127.0.0.1のみにbindし、Bearer token（32文字以上）を設定する。トークンは個人利用の試作向けの共通鍵。
  別マシン・インターネットへ公開しない。TLS、複数利用者の認証・アクセス制御、読み取りアクセス監査は未実装。
- APIは現在の運用DBへ書き込む。初期検証には架空のPC/タスク名などを使い、DBバックアップを取得しておく。

## 1. 専用DBログインの作成（一回だけ・手作業）

メインPCのPowerShellで、runtimeリポジトリ内から実行：

```powershell
cd D:\AI\projects\local-secretary-runtime
$container = docker ps -q --filter "label=com.docker.compose.project=local-secretary-runtime-db" --filter "label=com.docker.compose.service=secretary-postgres"
if (-not $container -or @($container).Count -ne 1) { throw "Expected exactly one secretary-postgres container" }
docker exec -it $container psql -X -U secretary_admin -d secretary
```

対話的な `psql` 端末で次を入力する（既に作成した場合、CREATE ROLEは繰り返さない）。

```sql
CREATE ROLE secretary_api LOGIN;
GRANT secretary_reader, secretary_candidate_writer,
      secretary_task_writer, secretary_audit_writer TO secretary_api;
```

次に **psql内だけで** `\password secretary_api` を実行。強い専用パスワードを2回入力する。
パスワードをSQL文、チャット、GitHub、コマンド履歴、Dockerコマンド引数へ貼らない。
`\q` でpsqlを終了する。専用ログインには `secretary_memory_writer` や管理者権限を与えない。

> `secretary_reader` は現時点では全テーブル読み取り可能です。アプリが第三者・クラウドやLANに開く設計にはなっていません。

## 2. Python依存ライブラリを仮想環境に導入

```powershell
cd D:\AI\projects\local-secretary-runtime
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r api\requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_api_static.py -v
```

## 3. 一時的なローカル環境変数を設定して起動

PowerShellのこのウィンドウ内で専用DBログインのパスワードを入力する。
URIエンコードするため、英数字以外も扱えます。

```powershell
$dbPassword = Read-Host "secretary_api password"
$encodedPassword = [uri]::EscapeDataString($dbPassword)
$env:LSA_API_DSN = "postgresql://secretary_api:$encodedPassword@127.0.0.1:55432/secretary"
$bytes = New-Object byte[] 48
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$env:LSA_API_TOKEN = [Convert]::ToBase64String($bytes)
Remove-Variable dbPassword, encodedPassword, bytes, rng
.\.venv\Scripts\python.exe -m uvicorn api.secretary_api:app --host 127.0.0.1 --port 8010 --no-access-log
```

`LSA_API_DSN` はこのPowerShellプロセスにのみ設定します。管理者DB認証情報と共用しないでください。
API tokenは再起動するたびに生成し直して構いません（クライアント側も新しいトークンに変更します）。
**このAPIへ秘密情報や実個人データを入力する前に、ローカルのファイアウォールとPCのログイン権限を確認してください。**

## 4. 最小動作確認

別のPowerShellから、API起動ウィンドウで設定したtokenを安全にクライアントへ渡して使用する
（チャットへ貼らない）。例：

```powershell
$token = Read-Host "Local API token"
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Uri http://127.0.0.1:8010/healthz
$task = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8010/tasks -Headers $headers -ContentType "application/json" -Body '{"request":"Example only: inspect a test PC","domain":"pc","completion_criteria":"Record the test result"}'
$task
Invoke-RestMethod -Uri http://127.0.0.1:8010/tasks -Headers $headers
```

`POST /tasks/{id}/pause` および `POST /tasks/{id}/resume` は
`{"expected_revision":0}` のように最新revisionを必要とします。再開しても**自動実行しません**。

`GET /entities?domain=pc` と `GET /claims/current?entity_id=UUID` は読み取り専用。
`POST /pending-claims` は既に存在する `entity_id` / `source_id` を参照する候補追加のみ。
候補のreview_statusは常にpendingで、APIからacceptedへ変更する手段はありません。
実際のSource登録とHuman Review / Memory Write Serviceは後続実装です。

## 検証範囲と次の実装

このPRにはオフラインのバリデーション/認証設定テストとGitHub Actionsの静的テストを含みます。
**このチャットからメインPCの稼働DBを操作したり、APIの実機動作を確認したわけではありません。**
DB接続・各権限・マイグレーション済みDBでのAPI統合テストは、ブランチの静的テスト成功後に実施してください。
次の作業は、管理者と分離したSource取り込み・Memory Review / Write Service、
タスクの永続checkpointと排他的取得、承認ポリシー、実行しない模擬ツールでの縦断検証です。
