# Apply existing 001-004 plus prototype-only 005 to an EMPTY isolated database.
# Run only from the prototype worktree. Never invoke the normal migrator on prod for 005.
[CmdletBinding()]
param(
    [ValidatePattern('^secretary_pkb_proto_[a-z0-9_]+$')]
    [string]$Database = 'secretary_pkb_proto_20260927'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$files = @(
    'db/migrations/001_memory.sql',
    'db/migrations/002_task_core.sql',
    'db/migrations/003_memory_review.sql',
    'db/migrations/004_prototype_tool_sources.sql',
    'pkb_proto/sql/005_pkb_proto_receipts.sql'
)
foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $file) -PathType Leaf)) {
        throw "Missing migration: $file"
    }
}
$ids = @(docker ps -q --filter 'label=com.docker.compose.project=local-secretary-runtime-db' --filter 'label=com.docker.compose.service=secretary-postgres')
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) {
    throw 'Expected exactly one running production PostgreSQL container.'
}
$container = $ids[0]
$info = @(docker inspect $container | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $info.Count -ne 1 -or $info[0].State.Health.Status -ne 'healthy') {
    throw 'PostgreSQL is not healthy.'
}
$ports = @($info[0].NetworkSettings.Ports.'5432/tcp')
if ($ports.Count -ne 1 -or $ports[0].HostIp -ne '127.0.0.1') {
    throw 'Unexpected PostgreSQL port exposure.'
}
# Verify actual creation, rather than relying on Write-Host output.
$exists = & docker exec $container psql -X -A -t -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "SELECT 1 FROM pg_database WHERE datname='$Database';"
if ($LASTEXITCODE -ne 0 -or ($exists | Out-String).Trim() -ne '1') {
    throw "Isolated DB $Database does not exist."
}
$parts = New-Object 'System.Collections.Generic.List[string]'
$parts.Add('BEGIN;')
$parts.Add(@'
DO $guard$
BEGIN
    IF current_database() !~ '^secretary_pkb_proto_[a-z0-9_]+$' THEN
        RAISE EXCEPTION 'Refusing a non-prototype database';
    END IF;
    IF to_regnamespace('secretary') IS NOT NULL THEN
        RAISE EXCEPTION 'Prototype schema already exists; refusing overwrite';
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
'@)
foreach ($file in $files) {
    $path = Join-Path $root $file
    $sql = [IO.File]::ReadAllText($path)
    # Group roles already exist globally (from the production DB); avoid CREATE ROLE conflicts.
    if ($file -like 'db/migrations/002_*') {
        $sql = [regex]::Replace($sql, '(?m)^CREATE ROLE secretary_[a-z_]+ NOLOGIN;\r?\n', '')
    }
    if ($file -like 'db/migrations/003_*') {
        $sql = [regex]::Replace($sql, '(?m)^CREATE ROLE secretary_review_writer NOLOGIN;\r?\n', '')
    }
    $parts.Add('-- BEGIN ' + $file + [Environment]::NewLine + $sql + [Environment]::NewLine + '-- END ' + $file)
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    $version = Split-Path -Leaf $file
    $parts.Add("INSERT INTO secretary.schema_migrations(version, sha256) VALUES ('$version', '$hash');")
}
$parts.Add('COMMIT;')
$filename = 'pkb-proto-' + [guid]::NewGuid().ToString('N') + '.sql'
$localTmp = Join-Path ([IO.Path]::GetTempPath()) $filename
$remoteTmp = '/tmp/' + $filename
try {
    [IO.File]::WriteAllText($localTmp, ($parts -join [Environment]::NewLine), (New-Object Text.UTF8Encoding($false)))
    & docker cp $localTmp ($container + ':' + $remoteTmp)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy isolated migration.' }
    & docker exec $container psql -X -U secretary_admin -d $Database -v ON_ERROR_STOP=1 -f $remoteTmp
    if ($LASTEXITCODE -ne 0) { throw 'Isolated migration failed; transaction rolled back.' }
    $count = & docker exec $container psql -X -A -t -U secretary_admin -d $Database -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM secretary.schema_migrations;'
    if ($LASTEXITCODE -ne 0 -or ($count | Out-String).Trim() -ne '5') {
        throw 'Migration count validation failed.'
    }
    Write-Host "PASS: $Database contains isolated schema versions 001-005"
} finally {
    if (Test-Path -LiteralPath $localTmp) { Remove-Item -LiteralPath $localTmp }
    & docker exec $container rm -f $remoteTmp | Out-Null
}
