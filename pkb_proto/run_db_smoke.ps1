# Provision a dedicated *isolated-only* login, then run the fictional PKB DB smoke test.
# Never grant membership in the production secretary_* groups to this login.
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
if (-not (Test-Path -LiteralPath $adminSecret -PathType Leaf)) {
    throw "Admin secret in the existing runtime checkout was not found."
}
$envFile = Join-Path $main '.env.postgres'
$envLines = @(Get-Content -LiteralPath $envFile |
    Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($envLines.Count -ne 1) { throw 'Unable to locate the existing PostgreSQL port.' }
$port = [int]($envLines[0] -replace '^LSA_DB_PORT=', '')
$python = 'py'
$pythonArgs = @('-3.12')
& $python @pythonArgs -c 'import psycopg'
if ($LASTEXITCODE -ne 0) {
    $venvPython = Join-Path $main '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw 'psycopg not available under Python 3.12 or the existing runtime venv.'
    }
    $python = $venvPython
    $pythonArgs = @()
    & $python -c 'import psycopg'
    if ($LASTEXITCODE -ne 0) { throw 'psycopg is missing in the runtime environment.' }
}
$ids = @(docker ps -q --filter 'label=com.docker.compose.project=local-secretary-runtime-db' --filter 'label=com.docker.compose.service=secretary-postgres')
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) {
    throw 'Expected exactly one running owned PostgreSQL container.'
}
$container = $ids[0]
$info = @(docker inspect $container | ConvertFrom-Json)
$ports = @($info[0].NetworkSettings.Ports.'5432/tcp')
if ($info[0].State.Health.Status -ne 'healthy' -or
    $ports.Count -ne 1 -or
    $ports[0].HostIp -ne '127.0.0.1' -or
    $ports[0].HostPort -ne "$port") {
    throw 'Container health or localhost port differs from the existing runtime configuration.'
}
$count = & docker exec $container psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM secretary.schema_migrations;'
if ($LASTEXITCODE -ne 0 -or ($count | Out-String).Trim() -ne '5') {
    throw 'Isolated DB does not contain all five prototype migrations.'
}
$exists = & docker exec $container psql -X -A -t -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_roles WHERE rolname='$role';"
if ($LASTEXITCODE -ne 0 -or ($exists | Out-String).Trim() -notin @('0','1')) {
    throw 'Unable to inspect dedicated test role.'
}
$roleExists = (($exists | Out-String).Trim() -eq '1')
if ($roleExists -and -not (Test-Path -LiteralPath $writerSecret -PathType Leaf)) {
    throw 'Prototype role already exists but the local password file is missing; refusing to take over an unknown role.'
}
if (-not (Test-Path -LiteralPath $writerSecret -PathType Leaf)) {
    $null = New-Item -ItemType Directory -Force (Split-Path $writerSecret)
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    [IO.File]::WriteAllText($writerSecret, [Convert]::ToBase64String($bytes), (New-Object Text.UTF8Encoding($false)))
}
$password = [IO.File]::ReadAllText($writerSecret).Trim()
if ($password.Length -lt 32 -or $password -notmatch '^[a-zA-Z0-9+/=]+$') {
    throw 'Invalid locally stored prototype password.'
}
$sql = @'
BEGIN;
DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF (SELECT count(*) FROM secretary.schema_migrations) <> 5 THEN
    RAISE EXCEPTION 'Missing prototype migration';
  END IF;
END $guard$;
'@
if (-not $roleExists) {
    $sql += [Environment]::NewLine +
        "CREATE ROLE $role LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$password';"
}
$sql += [Environment]::NewLine + @"
GRANT CONNECT ON DATABASE $db TO $role;
GRANT USAGE ON SCHEMA secretary TO $role;
GRANT SELECT ON secretary.entities, secretary.sources, secretary.claims, secretary.pkb_input_receipts TO $role;
GRANT INSERT ON secretary.sources, secretary.claims, secretary.pkb_input_receipts TO $role;
COMMIT;
"@
$filename = 'pkb-writer-setup-' + [guid]::NewGuid().ToString('N') + '.sql'
$localTmp = Join-Path ([IO.Path]::GetTempPath()) $filename
$remoteTmp = '/tmp/' + $filename
try {
    [IO.File]::WriteAllText($localTmp, $sql, (New-Object Text.UTF8Encoding($false)))
    & docker cp $localTmp ($container + ':' + $remoteTmp) | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot stage the isolated writer setup.' }
    & docker exec $container psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp
    if ($LASTEXITCODE -ne 0) { throw 'Dedicated writer setup failed.' }
} finally {
    if (Test-Path -LiteralPath $localTmp) { Remove-Item -LiteralPath $localTmp }
    & docker exec $container rm -f $remoteTmp | Out-Null
    $sql = $null
    $password = $null
}
Push-Location $worktree
try {
    & $python @pythonArgs -m pkb_proto.smoke_isolated --port $port --admin-secret $adminSecret --writer-secret $writerSecret
    if ($LASTEXITCODE -ne 0) { throw 'Isolated PostgreSQL smoke test failed.' }
} finally {
    Pop-Location
}
