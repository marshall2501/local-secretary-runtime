# Isolated-only migration 007 plus original ten-episode intake smoke.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$worktree = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$main = 'D:\AI\projects\local-secretary-runtime'
$db = 'secretary_pkb_proto_20260927'
$migration = Join-Path $worktree 'pkb_proto\sql\007_pkb_proto_episodes.sql'
$python = Join-Path $main '.venv\Scripts\python.exe'
$writerSecret = Join-Path $worktree 'secrets\pkb-proto-writer-password.txt'
$envFile = Join-Path $main '.env.postgres'

foreach ($needed in @($migration, $python, $writerSecret, $envFile)) {
    if (-not (Test-Path -LiteralPath $needed -PathType Leaf)) {
        throw "Missing existing isolated prototype dependency: $needed"
    }
}
$lines = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($lines.Count -ne 1) { throw 'Existing runtime DB port not found.' }
$port = [int]($lines[0] -replace '^LSA_DB_PORT=', '')

$ids = @(docker ps -q --filter 'label=com.docker.compose.project=local-secretary-runtime-db' --filter 'label=com.docker.compose.service=secretary-postgres')
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) {
    throw 'Expected exactly one owned PostgreSQL container.'
}
$container = $ids[0]
$info = @(docker inspect $container | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $info.Count -ne 1 -or $info[0].State.Health.Status -ne 'healthy') {
    throw 'Expected healthy owned PostgreSQL container.'
}
$ports = @($info[0].NetworkSettings.Ports.'5432/tcp')
if ($ports.Count -ne 1 -or $ports[0].HostIp -ne '127.0.0.1' -or
    $ports[0].HostPort -ne "$port") { throw 'Unexpected PostgreSQL port binding.' }

$count = & docker exec $container psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM secretary.schema_migrations;'
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the isolated prototype DB.' }
$count = ($count | Out-String).Trim()
if ($count -notin @('6','7')) { throw "Expected 6 or 7 prototype migrations, found $count." }
$sha = (Get-FileHash -LiteralPath $migration -Algorithm SHA256).Hash.ToLowerInvariant()

if ($count -eq '6') {
    $sql = @'
BEGIN;
DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927'
    OR (SELECT count(*) FROM secretary.schema_migrations) <> 6 THEN
    RAISE EXCEPTION 'Refusing an unexpected database or migration state';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname='secretary_pkb_proto_writer_20260927'
  ) THEN
    RAISE EXCEPTION 'Dedicated prototype writer not found';
  END IF;
END $guard$;
'@
    $sql += [Environment]::NewLine + [IO.File]::ReadAllText($migration)
    $sql += [Environment]::NewLine +
        "INSERT INTO secretary.schema_migrations(version,sha256) VALUES ('007_pkb_proto_episodes.sql','$sha');" +
        [Environment]::NewLine + 'COMMIT;'
    $name = 'pkb-episodes-' + [guid]::NewGuid().ToString('N') + '.sql'
    $temp = Join-Path ([IO.Path]::GetTempPath()) $name
    $remote = '/tmp/' + $name
    try {
        [IO.File]::WriteAllText($temp, $sql, (New-Object Text.UTF8Encoding($false)))
        & docker cp $temp ($container + ':' + $remote) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not stage isolated migration.' }
        & docker exec $container psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remote
        if ($LASTEXITCODE -ne 0) { throw 'Isolated migration 007 failed.' }
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp }
        & docker exec $container rm -f $remote | Out-Null
    }
}
$actual = & docker exec $container psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT sha256 FROM secretary.schema_migrations WHERE version='007_pkb_proto_episodes.sql';"
if ($LASTEXITCODE -ne 0 -or ($actual | Out-String).Trim() -ne $sha) {
    throw 'The applied migration does not match the checked-out version.'
}
Write-Host "PASS: isolated migration 007 in $db"

Push-Location $worktree
try {
    & $python -m unittest discover -s tests -p 'test_pkb*.py' -v
    if ($LASTEXITCODE -ne 0) { throw 'Offline PKB tests failed.' }
    & $python -m scripts.pkb.smoke_episodes --writer-secret $writerSecret --port $port
    if ($LASTEXITCODE -ne 0) { throw 'Fictional episode intake smoke failed.' }
} finally {
    Pop-Location
}
