# Apply prototype-only 006 in an existing isolated DB and run fictional PC/RC corrections.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$worktree = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$main = 'D:\AI\projects\local-secretary-runtime'
$db = 'secretary_pkb_proto_20260927'
$role = 'secretary_pkb_proto_writer_20260927'
$adminSecret = Join-Path $main 'secrets\postgres-password.txt'
$writerSecret = Join-Path $worktree 'secrets\pkb-proto-writer-password.txt'
$python = Join-Path $main '.venv\Scripts\python.exe'
$envFile = Join-Path $main '.env.postgres'
$migration = Join-Path $worktree 'pkb_proto\sql\006_pkb_proto_corrections.sql'
if (!(Test-Path -LiteralPath $adminSecret -PathType Leaf) -or
    !(Test-Path -LiteralPath $writerSecret -PathType Leaf) -or
    !(Test-Path -LiteralPath $python -PathType Leaf) -or
    !(Test-Path -LiteralPath $migration -PathType Leaf)) {
    throw 'A required existing secret, Python interpreter, or migration is missing.'
}
$envLines = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($envLines.Count -ne 1) { throw 'Existing runtime PostgreSQL port cannot be resolved.' }
$port = [int]($envLines[0] -replace '^LSA_DB_PORT=', '')
$ids = @(docker ps -q --filter 'label=com.docker.compose.project=local-secretary-runtime-db' --filter 'label=com.docker.compose.service=secretary-postgres')
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) { throw 'Expected one owned PostgreSQL container.' }
$container = $ids[0]
$details = @(docker inspect $container | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $details.Count -ne 1 -or $details[0].State.Health.Status -ne 'healthy') {
    throw 'Owned PostgreSQL container is unhealthy.'
}
$bindings = @($details[0].NetworkSettings.Ports.'5432/tcp')
if ($bindings.Count -ne 1 -or $bindings[0].HostIp -ne '127.0.0.1' -or
    $bindings[0].HostPort -ne "$port") { throw 'Unexpected PostgreSQL port binding.' }
$count = & docker exec $container psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM secretary.schema_migrations;'
if ($LASTEXITCODE -ne 0) { throw 'Isolated schema inspection failed.' }
$count = ($count | Out-String).Trim()
if ($count -notin @('5','6')) { throw "Expected 5 or 6 migrations in isolated DB; found $count." }
$sha = (Get-FileHash -LiteralPath $migration -Algorithm SHA256).Hash.ToLowerInvariant()
if ($count -eq '5') {
    $roleCount = & docker exec $container psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_roles WHERE rolname='$role';"
    if ($LASTEXITCODE -ne 0 -or ($roleCount | Out-String).Trim() -ne '1') {
        throw 'Dedicated writer role was not provisioned by the prior successful smoke test.'
    }
    $sql = @'
BEGIN;
DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927'
     OR (SELECT count(*) FROM secretary.schema_migrations) <> 5 THEN
    RAISE EXCEPTION 'Refusing an unexpected DB or migration state';
  END IF;
END $guard$;
'@
    $sql += [Environment]::NewLine +
        [IO.File]::ReadAllText($migration) + [Environment]::NewLine +
        "INSERT INTO secretary.schema_migrations(version,sha256) VALUES ('006_pkb_proto_corrections.sql','$sha');" +
        [Environment]::NewLine + 'COMMIT;'
    $filename = 'pkb-correction-' + [guid]::NewGuid().ToString('N') + '.sql'
    $temp = Join-Path ([IO.Path]::GetTempPath()) $filename
    $remote = '/tmp/' + $filename
    try {
        [IO.File]::WriteAllText($temp, $sql, (New-Object Text.UTF8Encoding($false)))
        & docker cp $temp ($container + ':' + $remote) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Staging isolated migration failed.' }
        & docker exec $container psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remote
        if ($LASTEXITCODE -ne 0) { throw 'Isolated correction migration failed.' }
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp }
        & docker exec $container rm -f $remote | Out-Null
    }
}
$actual = & docker exec $container psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT sha256 FROM secretary.schema_migrations WHERE version='006_pkb_proto_corrections.sql';"
if ($LASTEXITCODE -ne 0 -or ($actual | Out-String).Trim() -ne $sha) {
    throw 'Migration 006 does not match the checked-out prototype.'
}
Write-Host "PASS: migration 006 applied only to $db"
Push-Location $worktree
try {
    & $python -m unittest discover -s tests -p 'test_pkb*.py' -v
    if ($LASTEXITCODE -ne 0) { throw 'Offline PKB tests failed.' }
    & $python -m scripts.pkb.smoke_correction --admin-secret $adminSecret --writer-secret $writerSecret --port $port
    if ($LASTEXITCODE -ne 0) { throw 'Isolated correction smoke failed.' }
} finally {
    Pop-Location
}
