# Daily PKB Web UI prototype. Uses only the existing isolated fictional DB.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $root '.venv\Scripts\python.exe'
$envFile = Join-Path $root '.env.postgres'
$secret = Join-Path $root 'secrets\pkb-proto-writer-password.txt'
$db = 'secretary_pkb_proto_20260927'
$role = 'secretary_pkb_proto_writer_20260927'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Expected the existing runtime virtualenv.'
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw 'Missing .env.postgres.'
}
if (-not (Test-Path -LiteralPath $secret -PathType Leaf)) {
    throw 'Missing isolated PKB writer secret. Run the already validated prototype setup/smoke path first.'
}

& $python -c 'import nicegui, fastapi, psycopg'
if ($LASTEXITCODE -ne 0) {
    throw 'Required Python packages are missing. Install the existing runtime/web requirements explicitly; this launcher installs nothing.'
}

$lines = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($lines.Count -ne 1) { throw 'Cannot determine the existing PostgreSQL localhost port.' }
$port = [int]($lines[0] -replace '^LSA_DB_PORT=', '')
if ($port -lt 1024 -or $port -gt 65535) { throw 'Invalid PostgreSQL port.' }

$ids = @(docker ps -q --filter 'label=com.docker.compose.project=local-secretary-runtime-db' --filter 'label=com.docker.compose.service=secretary-postgres')
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) { throw 'Expected one owned PostgreSQL container.' }
$info = @(docker inspect $ids[0] | ConvertFrom-Json)
$bindings = @($info[0].NetworkSettings.Ports.'5432/tcp')
if ($info.Count -ne 1 -or $info[0].State.Health.Status -ne 'healthy' -or
    $bindings.Count -ne 1 -or $bindings[0].HostIp -ne '127.0.0.1' -or
    $bindings[0].HostPort -ne "$port") {
    throw 'PostgreSQL health or localhost binding is not the expected runtime configuration.'
}
$count = & docker exec $ids[0] psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='015_pkb_proto_component_normalization.sql';"
if ($LASTEXITCODE -ne 0 -or ($count | Out-String).Trim() -ne '1') {
    throw 'The isolated PKB database is missing migration 015. Run .\pkb_proto\run_pending_setup.ps1 first.'
}
$userExists = & docker exec $ids[0] psql -X -A -t -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_roles WHERE rolname='$role';"
if ($LASTEXITCODE -ne 0 -or ($userExists | Out-String).Trim() -ne '1') {
    throw 'Dedicated isolated PKB writer role is missing.'
}

$env:LSA_PKB_DAILY_PORT = "$port"
$env:LSA_PKB_DAILY_SECRET = $secret
Push-Location $root
try {
    & $python -m pkb_proto.daily_pkb
    if ($LASTEXITCODE -ne 0) { throw 'Daily PKB Web UI stopped with an error.' }
} finally {
    Remove-Item Env:LSA_PKB_DAILY_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:LSA_PKB_DAILY_SECRET -ErrorAction SilentlyContinue
    Pop-Location
}
