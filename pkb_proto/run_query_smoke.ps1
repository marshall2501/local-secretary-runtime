# Read-only PKB query acceptance on the existing isolated DB.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$worktree = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$main = 'D:\AI\projects\local-secretary-runtime'
$python = Join-Path $main '.venv\Scripts\python.exe'
$writerSecret = Join-Path $worktree 'secrets\pkb-proto-writer-password.txt'
$envFile = Join-Path $main '.env.postgres'
$db = 'secretary_pkb_proto_20260927'
if (!(Test-Path -LiteralPath $python -PathType Leaf) -or
    !(Test-Path -LiteralPath $writerSecret -PathType Leaf) -or
    !(Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw 'Expected existing virtualenv, prototype secret, and DB env file.'
}
$lines = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($lines.Count -ne 1) { throw 'Cannot read the existing dedicated DB port.' }
$port = [int]($lines[0] -replace '^LSA_DB_PORT=', '')
$ids = @(docker ps -q --filter 'label=com.docker.compose.project=local-secretary-runtime-db' --filter 'label=com.docker.compose.service=secretary-postgres')
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) { throw 'Expected one owned PostgreSQL container.' }
$id = $ids[0]
$details = @(docker inspect $id | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $details.Count -ne 1 -or
    $details[0].State.Health.Status -ne 'healthy') { throw 'DB container is unhealthy.' }
$bindings = @($details[0].NetworkSettings.Ports.'5432/tcp')
if ($bindings.Count -ne 1 -or $bindings[0].HostIp -ne '127.0.0.1' -or
    $bindings[0].HostPort -ne "$port") { throw 'Unexpected container port binding.' }
$count = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM secretary.schema_migrations;'
if ($LASTEXITCODE -ne 0 -or ($count | Out-String).Trim() -ne '6') {
    throw 'Expected previously validated isolated migrations 001-006.'
}
Push-Location $worktree
try {
    & $python -m unittest discover -s tests -p 'test_pkb*.py' -v
    if ($LASTEXITCODE -ne 0) { throw 'Offline PKB unit tests failed.' }
    & $python -m scripts.pkb.smoke_query --writer-secret $writerSecret --port $port
    if ($LASTEXITCODE -ne 0) { throw 'Isolated SQL-first query smoke failed.' }
} finally {
    Pop-Location
}
