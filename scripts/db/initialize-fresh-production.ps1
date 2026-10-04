[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^(secretary|secretary_rebuild_[a-z0-9_]{1,40})$')]
    [string]$Database
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$migrations = @(
    'db/migrations/001_memory.sql',
    'db/migrations/002_task_core.sql',
    'db/migrations/003_memory_review.sql',
    'db/migrations/004_prototype_tool_sources.sql',
    'db/migrations/005_pkb_runtime_schema.sql',
    'db/migrations/006_finance_runtime_schema.sql',
    'db/migrations/007_runtime_settings_schema.sql',
    'db/migrations/008_runtime_privileges.sql'
)
foreach ($relative in $migrations) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $relative) -PathType Leaf)) {
        throw "Missing production migration: $relative"
    }
}

$ids = @(docker ps -q --filter 'label=com.docker.compose.project=local-secretary-runtime-db' --filter 'label=com.docker.compose.service=secretary-postgres')
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) {
    throw 'Expected exactly one running Secretary PostgreSQL container.'
}
$container = $ids[0]
$inspect = @(docker inspect $container | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $inspect.Count -ne 1 -or $inspect[0].State.Health.Status -ne 'healthy') {
    throw 'Secretary PostgreSQL is not healthy.'
}

$exists = & docker exec $container psql -X -A -t -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_database WHERE datname='$Database';"
if ($LASTEXITCODE -ne 0 -or ($exists | Out-String).Trim() -ne '1') {
    throw "Target database does not exist: $Database"
}

$parts = New-Object 'System.Collections.Generic.List[string]'
$parts.Add('BEGIN;')
$guardSql = @'
DO $guard$
BEGIN
    IF current_database() <> '__TARGET_DATABASE__' THEN
        RAISE EXCEPTION 'Fresh production initializer connected to the wrong database';
    END IF;
    IF to_regnamespace('secretary') IS NOT NULL THEN
        RAISE EXCEPTION 'secretary schema already exists; refusing overwrite';
    END IF;
    IF (SELECT count(*) FROM pg_roles WHERE rolname IN
        ('secretary_reader','secretary_candidate_writer','secretary_memory_writer',
         'secretary_task_writer','secretary_audit_writer','secretary_review_writer')) <> 6 THEN
        RAISE EXCEPTION 'Existing cluster roles missing';
    END IF;
END $guard$;
CREATE SCHEMA secretary;
CREATE TABLE secretary.schema_migrations (
    version text PRIMARY KEY,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now()
);
'@
$parts.Add($guardSql.Replace('__TARGET_DATABASE__', $Database))

foreach ($relative in $migrations) {
    $path = Join-Path $root $relative
    $sql = [IO.File]::ReadAllText($path)

    # Group roles are cluster-global and already exist because the legacy
    # production DB is retained. Reuse them instead of recreating them.
    if ($relative -like 'db/migrations/002_*') {
        $sql = [regex]::Replace($sql, '(?m)^CREATE ROLE secretary_[a-z_]+ NOLOGIN;\r?\n', '')
    }
    if ($relative -like 'db/migrations/003_*') {
        $sql = [regex]::Replace($sql, '(?m)^CREATE ROLE secretary_review_writer NOLOGIN;\r?\n', '')
    }

    $parts.Add('-- BEGIN ' + $relative + [Environment]::NewLine + $sql + [Environment]::NewLine + '-- END ' + $relative)
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    $version = Split-Path -Leaf $relative
    $parts.Add("INSERT INTO secretary.schema_migrations(version, sha256) VALUES ('$version', '$hash');")
}
$parts.Add('COMMIT;')

$name = 'lsa-fresh-production-' + [guid]::NewGuid().ToString('N') + '.sql'
$localTmp = Join-Path ([IO.Path]::GetTempPath()) $name
$remoteTmp = '/tmp/' + $name
try {
    [IO.File]::WriteAllText($localTmp, ($parts -join [Environment]::NewLine), (New-Object Text.UTF8Encoding($false)))
    & docker cp $localTmp ($container + ':' + $remoteTmp)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy fresh production migration script.' }

    & docker exec $container psql -X -U secretary_admin -d $Database -v ON_ERROR_STOP=1 -f $remoteTmp
    if ($LASTEXITCODE -ne 0) {
        throw 'Fresh production schema initialization failed; transaction rolled back.'
    }

    $state = & docker exec $container psql -X -A -t -U secretary_admin -d $Database -v ON_ERROR_STOP=1 -c "SELECT count(*)::text || '|' || max(version) FROM secretary.schema_migrations;"
    if ($LASTEXITCODE -ne 0 -or ($state | Out-String).Trim() -ne '8|008_runtime_privileges.sql') {
        throw "Fresh production migration state is unexpected: $(($state | Out-String).Trim())"
    }

    Write-Host "PASS: $Database initialized from production migrations 001-008 with existing cluster roles reused."
} finally {
    if (Test-Path -LiteralPath $localTmp) {
        Remove-Item -LiteralPath $localTmp -Force
    }
    & docker exec $container rm -f $remoteTmp | Out-Null
}
