[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^secretary_rebuild_[a-z0-9_]{1,40}$')]
    [string]$ReplacementDatabase
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$envFile = Join-Path $root '.env.postgres'
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw 'Missing .env.postgres.'
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

function Invoke-AdminScalar([string]$Database, [string]$Sql) {
    $value = & docker exec $container psql -X -A -t -U secretary_admin -d $Database -v ON_ERROR_STOP=1 -c $Sql
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL command failed against $Database."
    }
    return ($value | Out-String).Trim()
}

foreach ($database in @('secretary', $ReplacementDatabase)) {
    $escaped = $database.Replace("'", "''")
    $exists = Invoke-AdminScalar 'postgres' "SELECT count(*) FROM pg_database WHERE datname='$escaped';"
    if ($exists -ne '1') {
        throw "Required database is missing: $database"
    }
}

$replacementState = Invoke-AdminScalar $ReplacementDatabase "SELECT count(*)::text || '|' || max(version) FROM secretary.schema_migrations;"
if ($replacementState -ne '8|008_runtime_privileges.sql') {
    throw "Replacement database migration state is unexpected: $replacementState"
}

# Cutover deliberately refuses to kill sessions. The operator must stop daily GUI,
# API/tunnel processes or other clients first so no in-flight work is discarded.
foreach ($database in @('secretary', $ReplacementDatabase)) {
    $escaped = $database.Replace("'", "''")
    $connections = Invoke-AdminScalar 'postgres' "SELECT count(*) FROM pg_stat_activity WHERE datname='$escaped';"
    if ([int]$connections -ne 0) {
        throw "Active connections remain on $database ($connections). Stop clients and retry; this script will not terminate sessions."
    }
}

$legacy = 'secretary_legacy_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
if ($legacy.Length -gt 63) { throw 'Generated legacy database name exceeds PostgreSQL limit.' }
$legacyExists = Invoke-AdminScalar 'postgres' "SELECT count(*) FROM pg_database WHERE datname='$legacy';"
if ($legacyExists -ne '0') { throw "Legacy database target already exists: $legacy" }

$oldRenamed = $false
$newPromoted = $false

function Restore-DatabaseNames {
    if ($newPromoted) {
        try {
            & docker exec $container psql -X -A -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE secretary RENAME TO $ReplacementDatabase;"
            if ($LASTEXITCODE -ne 0) {
                Write-Warning 'Automatic rollback could not restore the replacement database name.'
                return
            }
            $script:newPromoted = $false
        } catch {
            Write-Warning 'Automatic rollback raised an error while restoring the replacement database name.'
            return
        }
    }
    if ($oldRenamed) {
        try {
            & docker exec $container psql -X -A -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE $legacy RENAME TO secretary;"
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Automatic rollback failed. Preserved old database remains named $legacy."
                return
            }
            $script:oldRenamed = $false
            Write-Warning 'Cutover failed; original database names were restored automatically.'
        } catch {
            Write-Warning "Automatic rollback raised an error. Preserved old database remains named $legacy."
        }
    }
}

try {
    & docker exec $container psql -X -A -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE secretary RENAME TO $legacy;"
    if ($LASTEXITCODE -ne 0) { throw 'Failed to preserve old secretary database under legacy name.' }
    $oldRenamed = $true

    & docker exec $container psql -X -A -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE $ReplacementDatabase RENAME TO secretary;"
    if ($LASTEXITCODE -ne 0) { throw 'Failed to promote validated replacement database.' }
    $newPromoted = $true

    $newState = Invoke-AdminScalar 'secretary' "SELECT count(*)::text || '|' || max(version) FROM secretary.schema_migrations;"
    if ($newState -ne '8|008_runtime_privileges.sql') {
        throw "Promoted secretary migration state is unexpected: $newState"
    }

    $counts = Invoke-AdminScalar 'secretary' @"
SELECT
  (SELECT count(*) FROM secretary.entities)::text || '|' ||
  (SELECT count(*) FROM secretary.claims)::text || '|' ||
  (SELECT count(*) FROM secretary.pkb_pending_intake WHERE review_status='pending')::text || '|' ||
  (SELECT count(*) FROM secretary.service_connections)::text || '|' ||
  (SELECT count(*) FROM secretary.llm_profiles)::text || '|' ||
  (SELECT count(*) FROM secretary.magi_member_assignments)::text || '|' ||
  (SELECT count(*) FROM secretary.service_billing_profiles)::text;
"@

    $legacyPresent = Invoke-AdminScalar 'postgres' "SELECT count(*) FROM pg_database WHERE datname='$legacy';"
    $replacementPresent = Invoke-AdminScalar 'postgres' "SELECT count(*) FROM pg_database WHERE datname='$ReplacementDatabase';"
    if ($legacyPresent -ne '1' -or $replacementPresent -ne '0') {
        throw 'Post-cutover database identity verification failed.'
    }
} catch {
    Restore-DatabaseNames
    throw
}

Write-Host ''
Write-Host '=== Production cutover ==='
[pscustomobject]@{
    ProductionDatabase = 'secretary'
    LegacyDatabase = $legacy
    ReplacementDatabaseNameRemaining = $false
    MigrationState = $newState
    RuntimeCounts = $counts
    OldSecretaryPreserved = $true
} | Format-List
Write-Host 'PASS: validated replacement database promoted to secretary; old production retained as rollback database.'
Write-Host 'No client sessions were terminated by the cutover script.'

