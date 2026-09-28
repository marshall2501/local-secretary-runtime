# Apply prototype-only Pending Claims storage/privileges to the existing isolated DB.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$db = 'secretary_pkb_proto_20260927'
$file008 = Join-Path $root 'pkb_proto\sql\008_pkb_proto_pending_intake.sql'
$file009 = Join-Path $root 'pkb_proto\sql\009_pkb_proto_pending_privileges.sql'
foreach ($file in @($file008, $file009)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing migration: $file" }
}

$ids = @(docker ps -q --filter 'label=com.docker.compose.project=local-secretary-runtime-db' --filter 'label=com.docker.compose.service=secretary-postgres')
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) { throw 'Expected one owned PostgreSQL container.' }
$id = $ids[0]
$info = @(docker inspect $id | ConvertFrom-Json)
$binding = @($info[0].NetworkSettings.Ports.'5432/tcp')
if ($info.Count -ne 1 -or $info[0].State.Health.Status -ne 'healthy' -or
    $binding.Count -ne 1 -or $binding[0].HostIp -ne '127.0.0.1') {
    throw 'Unexpected PostgreSQL health or network exposure.'
}

$has008 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='008_pkb_proto_pending_intake.sql';"
if ($LASTEXITCODE -ne 0 -or ($has008 | Out-String).Trim() -ne '1') {
    throw '008 Pending Claims intake migration must be applied first.'
}

$has009 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='009_pkb_proto_pending_privileges.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 009 schema version.' }
if (($has009 | Out-String).Trim() -eq '0') {
    $hash = (Get-FileHash -LiteralPath $file009 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql = [IO.File]::ReadAllText($file009).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash)
    $tmpName = 'pkb-pending-privileges-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp = Join-Path ([IO.Path]::GetTempPath()) $tmpName
    $remoteTmp = '/tmp/' + $tmpName
    try {
        [IO.File]::WriteAllText($localTmp, $sql, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp ($id + ':' + $remoteTmp) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 009 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp
        if ($LASTEXITCODE -ne 0) { throw '009 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp) { Remove-Item -LiteralPath $localTmp }
        & docker exec $id rm -f $remoteTmp | Out-Null
    }
} elseif (($has009 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 009 migration records.'
}

$grants = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.pkb_pending_intake','SELECT,INSERT,UPDATE');"
$verify009 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='009_pkb_proto_pending_privileges.sql';"
if ($LASTEXITCODE -ne 0 -or ($grants | Out-String).Trim() -ne 't' -or ($verify009 | Out-String).Trim() -ne '1') {
    throw 'Pending Claims privilege verification failed.'
}
Write-Host 'PASS: Pending Claims storage and writer privileges verified (008 + 009).'
