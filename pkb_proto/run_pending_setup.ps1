# Apply prototype-only Pending Claims intake storage to the existing isolated DB.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$db = 'secretary_pkb_proto_20260927'
$file = Join-Path $root 'pkb_proto\sql\008_pkb_proto_pending_intake.sql'
if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'Missing 008 migration.' }

$ids = @(docker ps -q --filter 'label=com.docker.compose.project=local-secretary-runtime-db' --filter 'label=com.docker.compose.service=secretary-postgres')
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) { throw 'Expected one owned PostgreSQL container.' }
$id = $ids[0]
$info = @(docker inspect $id | ConvertFrom-Json)
$binding = @($info[0].NetworkSettings.Ports.'5432/tcp')
if ($info.Count -ne 1 -or $info[0].State.Health.Status -ne 'healthy' -or
    $binding.Count -ne 1 -or $binding[0].HostIp -ne '127.0.0.1') {
    throw 'Unexpected PostgreSQL health or network exposure.'
}
$current = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='008_pkb_proto_pending_intake.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect isolated schema version.' }
if (($current | Out-String).Trim() -eq '1') {
    Write-Host 'PASS: 008 already applied.'
    exit 0
}
$exists = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT to_regclass('secretary.pkb_pending_intake') IS NOT NULL;"
if ($LASTEXITCODE -ne 0 -or ($exists | Out-String).Trim() -ne 'f') {
    throw 'Pending table exists without the expected migration record; refusing automatic repair.'
}
$hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
$sql = [IO.File]::ReadAllText($file).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash)
$tmpName = 'pkb-pending-' + [guid]::NewGuid().ToString('N') + '.sql'
$localTmp = Join-Path ([IO.Path]::GetTempPath()) $tmpName
$remoteTmp = '/tmp/' + $tmpName
try {
    [IO.File]::WriteAllText($localTmp, $sql, (New-Object Text.UTF8Encoding($false)))
    & docker cp $localTmp ($id + ':' + $remoteTmp) | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 008 migration.' }
    & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp
    if ($LASTEXITCODE -ne 0) { throw '008 migration failed and was rolled back.' }
} finally {
    if (Test-Path -LiteralPath $localTmp) { Remove-Item -LiteralPath $localTmp }
    & docker exec $id rm -f $remoteTmp | Out-Null
}
$verify = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='008_pkb_proto_pending_intake.sql';"
if ($LASTEXITCODE -ne 0 -or ($verify | Out-String).Trim() -ne '1') { throw '008 verification failed.' }
Write-Host 'PASS: prototype Pending Claims intake storage applied.'
