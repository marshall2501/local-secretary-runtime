[CmdletBinding()]
param(
    [ValidatePattern('^secretary_rebuild_[a-z0-9_]{1,40}$')]
    [string]$ReplacementDatabase
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$envFile = Join-Path $root '.env.postgres'
$adminSecret = Join-Path $root 'secrets/postgres-password.txt'
$runtimeSecret = Join-Path $root 'secrets/secretary-daily-runtime-password.txt'
$python = Join-Path $root '.venv/Scripts/python.exe'
$initializer = Join-Path $root 'scripts/db/initialize-fresh-production.ps1'
$settingsTransfer = Join-Path $root 'scripts/db/runtime_settings_transfer.py'
$provisionRuntime = Join-Path $root 'scripts/db/provision_daily_runtime.py'
$rehearsal = Join-Path $root 'scripts/db/promotion-rehearsal.ps1'

foreach ($needed in @(
    $envFile,$adminSecret,$runtimeSecret,$python,
    $initializer,$settingsTransfer,$provisionRuntime,$rehearsal
)) {
    if (-not (Test-Path -LiteralPath $needed -PathType Leaf)) {
        throw "Required rebuild input is missing: $needed"
    }
}

if (-not $ReplacementDatabase) {
    $ReplacementDatabase = 'secretary_rebuild_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
}
if ($ReplacementDatabase.Length -gt 63) {
    throw 'Replacement database name must not exceed 63 characters.'
}

$portLines = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($portLines.Count -ne 1) { throw 'Cannot determine the live Secretary PostgreSQL port.' }
$livePort = [int]($portLines[0] -replace '^LSA_DB_PORT=', '')
if ($livePort -lt 1024 -or $livePort -gt 65535) { throw 'Invalid live PostgreSQL port.' }

$ids = @(docker ps -q --filter 'label=com.docker.compose.project=local-secretary-runtime-db' --filter 'label=com.docker.compose.service=secretary-postgres')
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) {
    throw 'Expected exactly one running Secretary PostgreSQL container.'
}
$container = $ids[0]
$inspect = @(docker inspect $container | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $inspect.Count -ne 1 -or $inspect[0].State.Health.Status -ne 'healthy') {
    throw 'Secretary PostgreSQL is not healthy.'
}

$oldExists = & docker exec $container psql -X -A -t -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_database WHERE datname='secretary';"
if ($LASTEXITCODE -ne 0 -or ($oldExists | Out-String).Trim() -ne '1') {
    throw 'Existing secretary database is missing.'
}
$replacementExists = & docker exec $container psql -X -A -t -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_database WHERE datname='$ReplacementDatabase';"
if ($LASTEXITCODE -ne 0 -or ($replacementExists | Out-String).Trim() -ne '0') {
    throw "Replacement database already exists: $ReplacementDatabase"
}

# Prove the exact fresh-schema + settings-only path first in a disposable DB.
& $rehearsal

$created = $false
$completed = $false
try {
    & docker exec $container createdb -U secretary_admin --template=template0 --owner=secretary_admin $ReplacementDatabase
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create replacement database.' }
    $created = $true

    & $initializer -Database $ReplacementDatabase

    & $python $settingsTransfer --source-port $livePort --target-port $livePort --source-database secretary_pkb_proto_20260927 --target-database $ReplacementDatabase --secret-file $adminSecret --commit
    if ($LASTEXITCODE -ne 0) { throw 'Runtime settings transfer into replacement database failed.' }

    & $python $provisionRuntime --port $livePort --database $ReplacementDatabase --admin-secret-file $adminSecret --runtime-secret-file $runtimeSecret
    if ($LASTEXITCODE -ne 0) { throw 'Daily runtime provisioning on replacement database failed.' }

    $state = & docker exec $container psql -X -A -t -U secretary_admin -d $ReplacementDatabase -v ON_ERROR_STOP=1 -c "SELECT count(*)::text || '|' || max(version) FROM secretary.schema_migrations;"
    if ($LASTEXITCODE -ne 0 -or ($state | Out-String).Trim() -ne '8|008_runtime_privileges.sql') {
        throw "Replacement database migration state is unexpected: $(($state | Out-String).Trim())"
    }

    $completed = $true
    Write-Host ''
    Write-Host '=== Fresh production replacement ==='
    [pscustomobject]@{
        ReplacementDatabase = $ReplacementDatabase
        OldSecretaryPreserved = $true
        IsolatedSettingsSourcePreserved = $true
        LegacyDataRestored = $false
        SettingsOnlyTransferred = $true
        ReadyForGuiValidation = $true
    } | Format-List
    Write-Host 'PASS: fresh replacement database created without restoring legacy PKB/Finance/Task data.'
    Write-Host 'The existing secretary database has not been renamed or replaced.'
} finally {
    if ($created -and -not $completed) {
        & docker exec $container dropdb -U secretary_admin --if-exists --force $ReplacementDatabase
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Failed to remove incomplete replacement database $ReplacementDatabase."
        }
    }
}
