[CmdletBinding()]
param()
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
$verifier = Join-Path $root 'scripts/db/verify_production_runtime.py'

foreach ($needed in @(
    $envFile,$adminSecret,$python,
    $initializer,$settingsTransfer,$provisionRuntime,$verifier
)) {
    if (-not (Test-Path -LiteralPath $needed -PathType Leaf)) {
        throw "Required rehearsal input is missing: $needed"
    }
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

$database = 'secretary_rebuild_rehearsal_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
$exists = & docker exec $container psql -X -A -t -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_database WHERE datname='$database';"
if ($LASTEXITCODE -ne 0 -or ($exists | Out-String).Trim() -ne '0') {
    throw "Disposable rehearsal database already exists: $database"
}

$created = $false
try {
    & docker exec $container createdb -U secretary_admin --template=template0 --owner=secretary_admin $database
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create disposable rehearsal database.' }
    $created = $true

    & $initializer -Database $database

    & $python $settingsTransfer --source-port $livePort --target-port $livePort --source-database secretary_pkb_proto_20260927 --target-database $database --secret-file $adminSecret --commit
    if ($LASTEXITCODE -ne 0) { throw 'Runtime settings transfer rehearsal failed.' }

    & $python $provisionRuntime --port $livePort --database $database --admin-secret-file $adminSecret --runtime-secret-file $runtimeSecret
    if ($LASTEXITCODE -ne 0) { throw 'Daily runtime provisioning rehearsal failed.' }
    if (-not (Test-Path -LiteralPath $runtimeSecret -PathType Leaf)) {
        throw 'Daily runtime provisioning did not create the runtime secret.'
    }

    & $python $verifier --target-port $livePort --live-port $livePort --runtime-secret-file $runtimeSecret --target-database $database --settings-source-port $livePort --admin-secret-file $adminSecret
    if ($LASTEXITCODE -ne 0) { throw 'Fresh production runtime rehearsal failed.' }

    Write-Host ''
    Write-Host '=== Fresh production rehearsal ==='
    [pscustomobject]@{
        Database = $database
        OldSecretaryUntouched = $true
        LegacyDataRestored = $false
        SettingsOnlyTransferred = $true
        RuntimeVerified = $true
    } | Format-List
    Write-Host 'PASS: fresh current-schema database works with settings-only transfer. No legacy PKB/Finance/Task data was restored.'
} finally {
    if ($created) {
        & docker exec $container dropdb -U secretary_admin --if-exists --force $database
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to remove disposable rehearsal database: $database"
        }
    }
}
