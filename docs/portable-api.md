# Secretary API — portable localhost deployment

`api/Dockerfile` と `docker/compose.api.yml` は、Secretary APIをDBとは別のCompose projectとして起動するための構成です。通常運用先はサブPC、メインPCは手動復旧先です。自動フェイルオーバーや2台の正本DBへの同時書込みは行いません。

## 構成

- DB Compose project: `local-secretary-runtime-db`
- API Compose project: `local-secretary-runtime-api`
- DB network: `local-secretary-runtime-db_secretary_db`
- API公開: `127.0.0.1:${LSA_API_PORT:-8010}`
- APIからDB: Compose内部DNS `secretary-postgres:5432`
- 非Secret設定: `.env.api`（`.env.api.example` から作成）
- Secret: `secrets/secretary-api-db-password.txt` / `secrets/secretary-api-token.txt`

DBとAPIを別projectにすることで、DB専用スクリプトの所有権検査とAPIのライフサイクルを分離します。API側の `down` / `stop` でDBや `yt-topic-search` を操作しないでください。

## 前提確認

```powershell
cd D:\AI\projects\local-secretary-runtime
git status
git pull --ff-only origin main
.\scripts\db\postgres.ps1 -Action Doctor
if (-not (Test-Path .env.api)) { Copy-Item .env.api.example .env.api }
if (-not (Test-Path secrets)) { New-Item -ItemType Directory secrets | Out-Null }
```

`secretary-api-db-password.txt` には制限付き `secretary_api` ログインのパスワードだけを保存し、`secretary_admin` の資格情報を入れないでください。SecretファイルはGit除外に加え、NTFS ACLも利用者本人と必要な管理者へ限定します。Composeのfile Secretは保存時暗号化ではありません。

API tokenがまだない場合の例:

```powershell
if (-not (Test-Path secrets/secretary-api-token.txt)) {
  $bytes = New-Object byte[] 48
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  [IO.File]::WriteAllText(
    (Join-Path (Get-Location) 'secrets/secretary-api-token.txt'),
    [Convert]::ToBase64String($bytes),
    (New-Object Text.UTF8Encoding($false))
  )
}
```

DBログインの作成・権限は現行migrationと運用手順に合わせて行い、管理者資格情報を通常APIへ渡さないでください。

## 起動

```powershell
$composeArgs = @(
  'compose', '--project-name', 'local-secretary-runtime-api',
  '--env-file', '.env.api', '-f', 'docker/compose.api.yml'
)
docker @composeArgs config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Invalid API Compose config' }

docker @composeArgs up -d --build --wait
if ($LASTEXITCODE -ne 0) { throw 'API failed to start' }
docker @composeArgs ps
```

外部DB networkが存在しない、制限付きDBログインやSecretが不正、ポートが競合する等の場合は起動を成功扱いにしません。

## 動作確認

```powershell
Invoke-RestMethod 'http://127.0.0.1:8010/healthz'
$token = [IO.File]::ReadAllText(
  (Join-Path (Get-Location) 'secrets/secretary-api-token.txt')
).Trim()
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod 'http://127.0.0.1:8010/tasks' -Headers $headers
Invoke-RestMethod 'http://127.0.0.1:8010/memory/search?limit=20' -Headers $headers
Remove-Variable token -ErrorAction SilentlyContinue
```

## 停止・更新

APIだけ停止:

```powershell
docker @composeArgs stop secretary-api
```

コード更新後は `docker @composeArgs up -d --build --wait` でAPI projectだけ再構築します。DB migrationは `scripts/db/postgres.ps1` の責務です。

## PC間の手動復旧

ComposeソースをコピーしてもDB volume、Secret、個人原本は移りません。復旧時は正本側の書込みを止め、DBをバックアップし、新しい環境へ復元し、migration・代表件数・Task/Claim等を照合してから切り替えます。独立した正本DBを2台で同時更新しません。

バックアップ・復元は [`postgres.md`](postgres.md) を参照してください。実機でどこまで確認済みかはこの文書へ固定せず、設計repo `STATUS` を正本とします。
