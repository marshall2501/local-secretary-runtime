# Apply prototype-only Pending Claims storage/privileges to the existing isolated DB.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$db = 'secretary_pkb_proto_20260927'
$file008 = Join-Path $root 'pkb_proto\sql\008_pkb_proto_pending_intake.sql'
$file009 = Join-Path $root 'pkb_proto\sql\009_pkb_proto_pending_privileges.sql'
$file010 = Join-Path $root 'pkb_proto\sql\010_pkb_proto_pending_review_audit.sql'
$file011 = Join-Path $root 'pkb_proto\sql\011_pkb_proto_pending_acceptance.sql'
$file012 = Join-Path $root 'pkb_proto\sql\012_pkb_proto_pending_interpreter.sql'
$file013 = Join-Path $root 'pkb_proto\sql\013_pkb_proto_entity_model.sql'
$file014 = Join-Path $root 'pkb_proto\sql\014_pkb_proto_component_state.sql'
foreach ($file in @($file008, $file009, $file010, $file011, $file012, $file013, $file014)) {
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

$has010 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='010_pkb_proto_pending_review_audit.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 010 schema version.' }
if (($has010 | Out-String).Trim() -eq '0') {
    $hash010 = (Get-FileHash -LiteralPath $file010 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql010 = [IO.File]::ReadAllText($file010).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash010)
    $tmpName010 = 'pkb-pending-review-audit-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp010 = Join-Path ([IO.Path]::GetTempPath()) $tmpName010
    $remoteTmp010 = '/tmp/' + $tmpName010
    try {
        [IO.File]::WriteAllText($localTmp010, $sql010, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp010 ($id + ':' + $remoteTmp010) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 010 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp010
        if ($LASTEXITCODE -ne 0) { throw '010 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp010) { Remove-Item -LiteralPath $localTmp010 }
        & docker exec $id rm -f $remoteTmp010 | Out-Null
    }
} elseif (($has010 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 010 migration records.'
}

$verify010 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='010_pkb_proto_pending_review_audit.sql';"
if ($LASTEXITCODE -ne 0 -or ($verify010 | Out-String).Trim() -ne '1') {
    throw 'Pending Claims review audit verification failed.'
}
$has011 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='011_pkb_proto_pending_acceptance.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 011 schema version.' }
if (($has011 | Out-String).Trim() -eq '0') {
    $hash011 = (Get-FileHash -LiteralPath $file011 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql011 = [IO.File]::ReadAllText($file011).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash011)
    $tmpName011 = 'pkb-pending-acceptance-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp011 = Join-Path ([IO.Path]::GetTempPath()) $tmpName011
    $remoteTmp011 = '/tmp/' + $tmpName011
    try {
        [IO.File]::WriteAllText($localTmp011, $sql011, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp011 ($id + ':' + $remoteTmp011) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 011 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp011
        if ($LASTEXITCODE -ne 0) { throw '011 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp011) { Remove-Item -LiteralPath $localTmp011 }
        & docker exec $id rm -f $remoteTmp011 | Out-Null
    }
} elseif (($has011 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 011 migration records.'
}

$verify011 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='011_pkb_proto_pending_acceptance.sql';"
if ($LASTEXITCODE -ne 0 -or ($verify011 | Out-String).Trim() -ne '1') {
    throw 'Pending Claims acceptance verification failed.'
}
$has012 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='012_pkb_proto_pending_interpreter.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 012 schema version.' }
if (($has012 | Out-String).Trim() -eq '0') {
    $hash012 = (Get-FileHash -LiteralPath $file012 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql012 = [IO.File]::ReadAllText($file012).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash012)
    $tmpName012 = 'pkb-pending-interpreter-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp012 = Join-Path ([IO.Path]::GetTempPath()) $tmpName012
    $remoteTmp012 = '/tmp/' + $tmpName012
    try {
        [IO.File]::WriteAllText($localTmp012, $sql012, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp012 ($id + ':' + $remoteTmp012) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 012 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp012
        if ($LASTEXITCODE -ne 0) { throw '012 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp012) { Remove-Item -LiteralPath $localTmp012 }
        & docker exec $id rm -f $remoteTmp012 | Out-Null
    }
} elseif (($has012 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 012 migration records.'
}

$verify012 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='012_pkb_proto_pending_interpreter.sql';"
if ($LASTEXITCODE -ne 0 -or ($verify012 | Out-String).Trim() -ne '1') {
    throw 'Pending Claims interpreter provenance verification failed.'
}
$has013 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='013_pkb_proto_entity_model.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 013 schema version.' }
if (($has013 | Out-String).Trim() -eq '0') {
    $hash013 = (Get-FileHash -LiteralPath $file013 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql013 = [IO.File]::ReadAllText($file013).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash013)
    $tmpName013 = 'pkb-entity-model-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp013 = Join-Path ([IO.Path]::GetTempPath()) $tmpName013
    $remoteTmp013 = '/tmp/' + $tmpName013
    try {
        [IO.File]::WriteAllText($localTmp013, $sql013, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp013 ($id + ':' + $remoteTmp013) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 013 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp013
        if ($LASTEXITCODE -ne 0) { throw '013 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp013) { Remove-Item -LiteralPath $localTmp013 }
        & docker exec $id rm -f $remoteTmp013 | Out-Null
    }
} elseif (($has013 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 013 migration records.'
}

$verify013 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='013_pkb_proto_entity_model.sql';"
if ($LASTEXITCODE -ne 0 -or ($verify013 | Out-String).Trim() -ne '1') {
    throw 'Compositional Entity model verification failed.'
}
$has014 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='014_pkb_proto_component_state.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 014 schema version.' }
if (($has014 | Out-String).Trim() -eq '0') {
    $hash014 = (Get-FileHash -LiteralPath $file014 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql014 = [IO.File]::ReadAllText($file014).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash014)
    $tmpName014 = 'pkb-component-state-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp014 = Join-Path ([IO.Path]::GetTempPath()) $tmpName014
    $remoteTmp014 = '/tmp/' + $tmpName014
    try {
        [IO.File]::WriteAllText($localTmp014, $sql014, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp014 ($id + ':' + $remoteTmp014) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 014 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp014
        if ($LASTEXITCODE -ne 0) { throw '014 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp014) { Remove-Item -LiteralPath $localTmp014 }
        & docker exec $id rm -f $remoteTmp014 | Out-Null
    }
} elseif (($has014 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 014 migration records.'
}

$verify014 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='014_pkb_proto_component_state.sql';"
if ($LASTEXITCODE -ne 0 -or ($verify014 | Out-String).Trim() -ne '1') {
    throw 'Component relation/current-state verification failed.'
}
Write-Host 'PASS: PKB prototype migrations verified through 014 (Pending + interpreter + Entity/Relation/Event/State component model).'





