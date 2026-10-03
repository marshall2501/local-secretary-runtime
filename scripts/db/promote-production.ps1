[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BackupPath,
    [switch]$SkipSettings
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$dbScript = Join-Path $root 'scripts/db/postgres.ps1'
$settingsTransfer = Join-Path $root 'scripts/db/runtime_settings_transfer.py'
$provisionRuntime = Join-Path $root 'scripts/db/provision_daily_runtime.py'
$python = Join-Path $root '.venv/Scripts/python.exe'
$envFile = Join-Path $root '.env.postgres'
$adminSecret = Join-Path $root 'secrets/postgres-password.txt'
$runtimeSecret = Join-Path $root 'secrets/secretary-daily-runtime-password.txt'
$BackupPath = [IO.Path]::GetFullPath($BackupPath)

foreach ($needed in @($dbScript,$settingsTransfer,$provisionRuntime,$python,$envFile,$adminSecret,$BackupPath)) {
    if (-not (Test-Path -LiteralPath $needed -PathType Leaf)) {
        throw "Required promotion input is missing: $needed"
    }
}
$portLine = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($portLine.Count -ne 1) { throw 'Cannot determine Secretary PostgreSQL port.' }
$port = [int]($portLine[0] -replace '^LSA_DB_PORT=', '')

$containerIds = @(docker compose --project-name local-secretary-runtime-db --project-directory (Join-Path $root 'docker') --env-file $envFile -f (Join-Path $root 'docker/compose.postgres.yml') ps -q secretary-postgres)
if ($LASTEXITCODE -ne 0 -or $containerIds.Count -ne 1) { throw 'Expected one owned Secretary PostgreSQL container.' }
$id = $containerIds[0]

$before = (& docker exec $id psql -X -q -A -t -U secretary_admin -d secretary -v ON_ERROR_STOP=1 -c "SELECT count(*)::text || '|' || max(version) FROM secretary.schema_migrations;") | Out-String
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect live migration history.' }
$parts = $before.Trim() -split '\|',2
if ($parts.Count -ne 2 -or $parts[0] -ne '4' -or $parts[1] -notlike '004_*') {
    throw "Live promotion requires production migrations 001-004 only; found $($before.Trim())."
}

$remote = '/tmp/secretary-promotion-verify.dump'
try {
    docker cp $BackupPath "$($id):$remote" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy trusted backup for verification.' }
    docker exec $id pg_restore --list $remote | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Trusted backup failed pg_restore --list verification.' }
} finally {
    docker exec $id rm -f $remote | Out-Null
}

& $dbScript -Action Migrate
if ($LASTEXITCODE -ne 0) { throw 'Live production schema migration failed.' }

if (-not $SkipSettings) {
    & $python $settingsTransfer --source-port $port --target-port $port --secret-file $adminSecret
    if ($LASTEXITCODE -ne 0) { throw 'Runtime settings transfer dry-run failed.' }
    & $python $settingsTransfer --source-port $port --target-port $port --secret-file $adminSecret --commit
    if ($LASTEXITCODE -ne 0) { throw 'Runtime settings transfer commit failed.' }
}

& $python $provisionRuntime --port $port --admin-secret-file $adminSecret --runtime-secret-file $runtimeSecret
if ($LASTEXITCODE -ne 0) { throw 'Daily runtime login provisioning failed.' }

$after = (& docker exec $id psql -X -q -A -t -U secretary_admin -d secretary -v ON_ERROR_STOP=1 -c "SELECT count(*)::text || '|' || max(version) FROM secretary.schema_migrations;") | Out-String
if ($LASTEXITCODE -ne 0 -or $after.Trim() -notlike '8|008_runtime_privileges.sql') {
    throw "Unexpected live migration state after promotion: $($after.Trim())"
}

Write-Host ''
Write-Host 'PASS: live secretary schema promoted to migration 008.'
if ($SkipSettings) {
    Write-Host 'Runtime settings: skipped by request.'
} else {
    Write-Host 'Runtime settings: copied from isolated DB.'
}
Write-Host 'Daily runtime login provisioned. Launcher cutover is a separate explicit step.'
