# ポータブルなSecretary API（サブPC通常運用／メインPC予備稼働）

この段階では **既存PostgreSQLとは独立したAPI Composeプロジェクト**を追加します。
両PCで同じソースからDockerイメージを再ビルドできますが、DBの実データや認証情報は
イメージにもGitにも含まれません。移行時に別途バックアップ・復元します。
元のWindows上のvenv方式も引き続き利用できます。

## ファイルパス

- `api/Dockerfile`: APIのPython 3.12とソースをイメージ内に配置。
- `docker/compose.api.yml`: API専用Composeプロジェクト。DB専用Dockerネットワークだけに接続。
- `.env.api.example`: 秘密情報を含まないローカル設定テンプレート。
- `secrets/secretary-api-db-password.txt`、`secrets/secretary-api-token.txt`: Git除外の秘密情報。
- `docs/portable-api.md`: 本手順。

通常運用は**サブPC**、障害時の代替稼働先は**メインPC**。
いま稼働中のPostgreSQLとWindows上のPython APIは、すべてサブPCの実機で検証しています。
メインPCへの自動移行はしません。移行先でもGit・Docker Desktop（Linux containers）を用意します。

## 既存DBへの安全な接続

PostgreSQLは変更せず、従来どおり `local-secretary-runtime-db` で管理します。
APIは別プロジェクト名 `local-secretary-runtime-api` で起動し、DB専用ネットワーク
`local-secretary-runtime-db_secretary_db` に**外部ネットワークとして参加**します。
DBの `secretary-postgres:5432` をCompose内部DNSで利用するので
ホスト側 `127.0.0.1:55432` をコンテナから使う必要はありません。
ポート公開はホストの `127.0.0.1:8010` に限定します。

DBとAPIを別プロジェクトにした理由は、既存の `scripts/db/postgres.ps1` が
DB専用のコンテナ所有権を検査するためです。同じプロジェクトへAPIを追加すると
その安全検査がAPIを不明なサービスとして拒否します。
別Composeの `down` は対応するAPIだけに実行し、
DBの `down`・Volumeの削除を行わないでください。
`yt-topic-search` のCompose・ネットワーク・Volumeには触れません。

## 1. サブPCで準備

まず既存のDBが健康であることを確認してください。APIが既にWindows上で起動中なら
ポート `8010` が競合するので、**Ctrl+CなどでそのAPIだけ停止**します。
他プロジェクトのコンテナは停止しません。

```powershell
cd D:\AI\projects\local-secretary-runtime
git status
git pull --ff-only origin main
.\scripts\db\postgres.ps1 -Action Doctor
if (-not (Test-Path .env.api)) { Copy-Item .env.api.example .env.api }
if (-not (Test-Path secrets)) { New-Item -ItemType Directory secrets | Out-Null }
```

DBログイン `secretary_api` は以前作成したものを使い回せます。
以下の `secretary-api-db-password.txt` には **secretary_apiのパスワードのみ**を保存します。
DB管理者 `secretary_admin` のパスワードを入れてはいけません。
既存ファイルは上書きしません。

```powershell
if (-not (Test-Path secrets/secretary-api-db-password.txt)) {
  $sec = Read-Host "secretary_api DB password" -AsSecureString
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  try {
    $pass = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    [IO.File]::WriteAllText(
      (Join-Path (Get-Location) 'secrets/secretary-api-db-password.txt'),
      $pass, (New-Object Text.UTF8Encoding($false))
    )
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    Remove-Variable sec, ptr, pass -ErrorAction SilentlyContinue
  }
}
if (-not (Test-Path secrets/secretary-api-token.txt)) {
  $bytes = New-Object byte[] 48
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  [IO.File]::WriteAllText(
    (Join-Path (Get-Location) 'secrets/secretary-api-token.txt'),
    [Convert]::ToBase64String($bytes),
    (New-Object Text.UTF8Encoding($false))
  )
  Remove-Variable bytes, rng
}
```

`secrets/` のNTFS権限を現在のWindowsユーザー／管理者に限定してください。
Composeのファイル型Secretは **保存時の暗号化ではありません**。
サブPCからメインPCへ移行する際も安全な経路でバックアップ・設定・Secretを移します。
トークンとパスワードはチャットやリポジトリに貼らないこと。

## 2. Compose設定を確認しAPIを起動

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

**注意:** `docker compose ... up` は今回のAPIプロジェクトだけを対象とします。
既存のDBやそのスクリプトは変更しません。
外部ネットワークが存在しなければ起動に失敗します。DBを事前に起動してから行ってください。
APIは `secretary_api` という制限付きDBログインを検査し、
権限や秘密情報が不足していれば起動を拒否します。

## 3. 読み取り動作確認

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

以前作成したテスト用タスクのID・revisionが取得できることを確認してください。
ホスト側の `LSA_API_TOKEN` とコンテナ用tokenファイルは**別の値**です。
この段階ではAPIイメージ内にレビューCLIやDB管理者資格情報を入れていません。
Memory Reviewは引き続き信頼されたローカル端末から操作します。

## 4. 停止・更新とPC間移行

APIだけ停止：
```powershell
docker @composeArgs stop secretary-api
```

更新する場合は、コードを `git pull` した後 `docker @composeArgs up -d --build --wait`。
DBのバックアップ・新規DBへの復元手順は `docs/postgres.md`。
**Composeのソースをコピーしただけでは、DB volume内のデータは移りません。**
サブPCを正本とし、切替時はサブPCの書き込みを停止・バックアップ、
メインPCの新環境へ復元し、検索・タスク・記憶の件数やIDを照合してからメインを起動します。
正本DBの独立した2台同時書き込みは行いません。
DBダンプにはクラスタ共通ロールとパスワードを含まないため、
新PCではmigrationによるNOLOGINロール作成後に `secretary_api` と
`secretary_reviewer` のログイン・権限を**手動で再作成**する必要があります。

### 未完了

- サブPC上での実際のコンテナ起動とAPI統合試験、別PCへの復元訓練。
- 原本アーカイブとバックアップ時点の整合性・Secretを含めた移行ツール。
- GPU/ローカルLLMのPC別選択、Docker以外の `doctor.ps1` のハードコード解消。
- 自動切替や2台DB間の同期は目標外。当面は手動の移行・切替。
