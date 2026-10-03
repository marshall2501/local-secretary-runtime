# Apply prototype-only Pending Claims storage/privileges to the existing isolated DB.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
$db = 'secretary_pkb_proto_20260927'
$file008 = Join-Path $root 'db\isolated\pkb_proto\008_pkb_proto_pending_intake.sql'
$file009 = Join-Path $root 'db\isolated\pkb_proto\009_pkb_proto_pending_privileges.sql'
$file010 = Join-Path $root 'db\isolated\pkb_proto\010_pkb_proto_pending_review_audit.sql'
$file011 = Join-Path $root 'db\isolated\pkb_proto\011_pkb_proto_pending_acceptance.sql'
$file012 = Join-Path $root 'db\isolated\pkb_proto\012_pkb_proto_pending_interpreter.sql'
$file013 = Join-Path $root 'db\isolated\pkb_proto\013_pkb_proto_entity_model.sql'
$file014 = Join-Path $root 'db\isolated\pkb_proto\014_pkb_proto_component_state.sql'
$file015 = Join-Path $root 'db\isolated\pkb_proto\015_pkb_proto_component_normalization.sql'
$file016 = Join-Path $root 'db\isolated\pkb_proto\016_pkb_proto_finance.sql'
$file017 = Join-Path $root 'db\isolated\pkb_proto\017_pkb_proto_core_task_privileges.sql'
$file019 = Join-Path $root 'db\isolated\pkb_proto\019_pkb_proto_memory_intake.sql'
$file020 = Join-Path $root 'db\isolated\pkb_proto\020_magi_llm_settings.sql'
$file021 = Join-Path $root 'db\isolated\pkb_proto\021_ollama_context_window.sql'
$file022 = Join-Path $root 'db\isolated\pkb_proto\022_ollama_generation_budget.sql'
$file023 = Join-Path $root 'db\isolated\pkb_proto\023_magi_retry_policy.sql'
$file024 = Join-Path $root 'db\isolated\pkb_proto\024_service_connections.sql'
$file025 = Join-Path $root 'db\isolated\pkb_proto\025_connection_auth_and_consumer_binding.sql'
$file026 = Join-Path $root 'db\isolated\pkb_proto\026_service_billing.sql'
$file018 = Join-Path $root 'db\isolated\pkb_proto\018_pkb_proto_core_audit_read.sql'
foreach ($file in @($file008, $file009, $file010, $file011, $file012, $file013, $file014, $file015, $file016, $file017, $file018, $file019, $file020, $file021, $file022, $file023, $file024, $file025, $file026)) {
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
$has015 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='015_pkb_proto_component_normalization.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 015 schema version.' }
if (($has015 | Out-String).Trim() -eq '0') {
    $hash015 = (Get-FileHash -LiteralPath $file015 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql015 = [IO.File]::ReadAllText($file015).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash015)
    $tmpName015 = 'pkb-component-normalization-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp015 = Join-Path ([IO.Path]::GetTempPath()) $tmpName015
    $remoteTmp015 = '/tmp/' + $tmpName015
    try {
        [IO.File]::WriteAllText($localTmp015, $sql015, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp015 ($id + ':' + $remoteTmp015) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 015 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp015
        if ($LASTEXITCODE -ne 0) { throw '015 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp015) { Remove-Item -LiteralPath $localTmp015 }
        & docker exec $id rm -f $remoteTmp015 | Out-Null
    }
} elseif (($has015 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 015 migration records.'
}

$verify015 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='015_pkb_proto_component_normalization.sql';"
if ($LASTEXITCODE -ne 0 -or ($verify015 | Out-String).Trim() -ne '1') {
    throw 'Component normalization verification failed.'
}

$has016 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='016_pkb_proto_finance.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 016 schema version.' }
if (($has016 | Out-String).Trim() -eq '0') {
    $hash016 = (Get-FileHash -LiteralPath $file016 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql016 = [IO.File]::ReadAllText($file016).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash016)
    $tmpName016 = 'pkb-finance-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp016 = Join-Path ([IO.Path]::GetTempPath()) $tmpName016
    $remoteTmp016 = '/tmp/' + $tmpName016
    try {
        [IO.File]::WriteAllText($localTmp016, $sql016, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp016 ($id + ':' + $remoteTmp016) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 016 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp016
        if ($LASTEXITCODE -ne 0) { throw '016 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp016) { Remove-Item -LiteralPath $localTmp016 }
        & docker exec $id rm -f $remoteTmp016 | Out-Null
    }
} elseif (($has016 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 016 migration records.'
}

$verify016 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='016_pkb_proto_finance.sql';"
$financeGrant = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.finance_transactions','SELECT,INSERT,UPDATE');"
if ($LASTEXITCODE -ne 0 -or ($verify016 | Out-String).Trim() -ne '1' -or ($financeGrant | Out-String).Trim() -ne 't') {
    throw 'Finance schema or privilege verification failed.'
}

$has017 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='017_pkb_proto_core_task_privileges.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 017 schema version.' }
if (($has017 | Out-String).Trim() -eq '0') {
    $hash017 = (Get-FileHash -LiteralPath $file017 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql017 = [IO.File]::ReadAllText($file017).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash017)
    $tmpName017 = 'pkb-core-task-privileges-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp017 = Join-Path ([IO.Path]::GetTempPath()) $tmpName017
    $remoteTmp017 = '/tmp/' + $tmpName017
    try {
        [IO.File]::WriteAllText($localTmp017, $sql017, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp017 ($id + ':' + $remoteTmp017) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 017 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp017
        if ($LASTEXITCODE -ne 0) { throw '017 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp017) { Remove-Item -LiteralPath $localTmp017 }
        & docker exec $id rm -f $remoteTmp017 | Out-Null
    }
} elseif (($has017 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 017 migration records.'
}

$verify017 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='017_pkb_proto_core_task_privileges.sql';"
$coreTaskGrant = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.tasks','SELECT,INSERT,UPDATE') AND has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.actions','SELECT,INSERT') AND has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.results','SELECT,INSERT') AND has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.audit_events','INSERT') AND NOT has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.approvals','INSERT,UPDATE,DELETE');"
if ($LASTEXITCODE -ne 0 -or ($verify017 | Out-String).Trim() -ne '1' -or ($coreTaskGrant | Out-String).Trim() -ne 't') {
    throw 'Secretary Core task privilege verification failed.'
}

$has018 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='018_pkb_proto_core_audit_read.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 018 schema version.' }
if (($has018 | Out-String).Trim() -eq '0') {
    $hash018 = (Get-FileHash -LiteralPath $file018 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql018 = [IO.File]::ReadAllText($file018).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash018)
    $tmpName018 = 'pkb-core-audit-read-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp018 = Join-Path ([IO.Path]::GetTempPath()) $tmpName018
    $remoteTmp018 = '/tmp/' + $tmpName018
    try {
        [IO.File]::WriteAllText($localTmp018, $sql018, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp018 ($id + ':' + $remoteTmp018) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 018 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp018
        if ($LASTEXITCODE -ne 0) { throw '018 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp018) { Remove-Item -LiteralPath $localTmp018 }
        & docker exec $id rm -f $remoteTmp018 | Out-Null
    }
} elseif (($has018 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 018 migration records.'
}

$verify018 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='018_pkb_proto_core_audit_read.sql';"
$auditReadGrant = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.audit_events','SELECT,INSERT') AND NOT has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.approvals','INSERT,UPDATE,DELETE');"
if ($LASTEXITCODE -ne 0 -or ($verify018 | Out-String).Trim() -ne '1' -or ($auditReadGrant | Out-String).Trim() -ne 't') {
    throw 'Secretary Core audit read privilege verification failed.'
}

$has019 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='019_pkb_proto_memory_intake.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 019 schema version.' }
if (($has019 | Out-String).Trim() -eq '0') {
    $hash019 = (Get-FileHash -LiteralPath $file019 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql019 = [IO.File]::ReadAllText($file019).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash019)
    $tmpName019 = 'pkb-memory-intake-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp019 = Join-Path ([IO.Path]::GetTempPath()) $tmpName019
    $remoteTmp019 = '/tmp/' + $tmpName019
    try {
        [IO.File]::WriteAllText($localTmp019, $sql019, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp019 ($id + ':' + $remoteTmp019) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 019 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp019
        if ($LASTEXITCODE -ne 0) { throw '019 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp019) { Remove-Item -LiteralPath $localTmp019 }
        & docker exec $id rm -f $remoteTmp019 | Out-Null
    }
} elseif (($has019 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 019 migration records.'
}


$has020 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='020_magi_llm_settings.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 020 schema version.' }
if (($has020 | Out-String).Trim() -eq '0') {
    $hash020 = (Get-FileHash -LiteralPath $file020 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql020 = [IO.File]::ReadAllText($file020).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash020)
    $tmpName020 = 'magi-llm-settings-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp020 = Join-Path ([IO.Path]::GetTempPath()) $tmpName020
    $remoteTmp020 = '/tmp/' + $tmpName020
    try {
        [IO.File]::WriteAllText($localTmp020, $sql020, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp020 ($id + ':' + $remoteTmp020) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 020 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp020
        if ($LASTEXITCODE -ne 0) { throw '020 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp020) { Remove-Item -LiteralPath $localTmp020 }
        & docker exec $id rm -f $remoteTmp020 | Out-Null
    }
} elseif (($has020 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 020 migration records.'
}

$verify020 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='020_magi_llm_settings.sql';"
$settingsGrant = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.llm_profiles','SELECT,INSERT,UPDATE,DELETE') AND has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.magi_member_assignments','SELECT,INSERT,UPDATE,DELETE');"
if ($LASTEXITCODE -ne 0 -or ($verify020 | Out-String).Trim() -ne '1' -or ($settingsGrant | Out-String).Trim() -ne 't') {
    throw 'MAGI LLM settings schema or privilege verification failed.'
}

$has021 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='021_ollama_context_window.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 021 schema version.' }
if (($has021 | Out-String).Trim() -eq '0') {
    $hash021 = (Get-FileHash -LiteralPath $file021 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql021 = [IO.File]::ReadAllText($file021).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash021)
    $tmpName021 = 'ollama-context-window-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp021 = Join-Path ([IO.Path]::GetTempPath()) $tmpName021
    $remoteTmp021 = '/tmp/' + $tmpName021
    try {
        [IO.File]::WriteAllText($localTmp021, $sql021, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp021 ($id + ':' + $remoteTmp021) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 021 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp021
        if ($LASTEXITCODE -ne 0) { throw '021 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp021) { Remove-Item -LiteralPath $localTmp021 }
        & docker exec $id rm -f $remoteTmp021 | Out-Null
    }
} elseif (($has021 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 021 migration records.'
}

$verify021 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='021_ollama_context_window.sql';"
$contextColumn = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.columns WHERE table_schema='secretary' AND table_name='llm_profiles' AND column_name='context_window_tokens';"
if ($LASTEXITCODE -ne 0 -or ($verify021 | Out-String).Trim() -ne '1' -or ($contextColumn | Out-String).Trim() -ne '1') {
    throw 'Ollama context-window migration verification failed.'
}

$has022 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='022_ollama_generation_budget.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 022 schema version.' }
if (($has022 | Out-String).Trim() -eq '0') {
    $hash022 = (Get-FileHash -LiteralPath $file022 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql022 = [IO.File]::ReadAllText($file022).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash022)
    $tmpName022 = 'ollama-generation-budget-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp022 = Join-Path ([IO.Path]::GetTempPath()) $tmpName022
    $remoteTmp022 = '/tmp/' + $tmpName022
    try {
        [IO.File]::WriteAllText($localTmp022, $sql022, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp022 ($id + ':' + $remoteTmp022) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 022 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp022
        if ($LASTEXITCODE -ne 0) { throw '022 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp022) { Remove-Item -LiteralPath $localTmp022 }
        & docker exec $id rm -f $remoteTmp022 | Out-Null
    }
} elseif (($has022 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 022 migration records.'
}

$verify022 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='022_ollama_generation_budget.sql';"
$generationColumn = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.columns WHERE table_schema='secretary' AND table_name='llm_profiles' AND column_name='ollama_num_predict';"
if ($LASTEXITCODE -ne 0 -or ($verify022 | Out-String).Trim() -ne '1' -or ($generationColumn | Out-String).Trim() -ne '1') {
    throw 'Ollama generation-budget migration verification failed.'
}

$has023 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='023_magi_retry_policy.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 023 schema version.' }
if (($has023 | Out-String).Trim() -eq '0') {
    $hash023 = (Get-FileHash -LiteralPath $file023 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql023 = [IO.File]::ReadAllText($file023).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash023)
    $tmpName023 = 'magi-retry-policy-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp023 = Join-Path ([IO.Path]::GetTempPath()) $tmpName023
    $remoteTmp023 = '/tmp/' + $tmpName023
    try {
        [IO.File]::WriteAllText($localTmp023, $sql023, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp023 ($id + ':' + $remoteTmp023) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 023 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp023
        if ($LASTEXITCODE -ne 0) { throw '023 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp023) { Remove-Item -LiteralPath $localTmp023 }
        & docker exec $id rm -f $remoteTmp023 | Out-Null
    }
} elseif (($has023 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 023 migration records.'
}

$verify023 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='023_magi_retry_policy.sql';"
$retryProfileColumn = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.columns WHERE table_schema='secretary' AND table_name='llm_profiles' AND column_name='retry_http_codes';"
$retryMemberColumn = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.columns WHERE table_schema='secretary' AND table_name='magi_member_assignments' AND column_name='retry_within_turn';"
if ($LASTEXITCODE -ne 0 -or ($verify023 | Out-String).Trim() -ne '1' -or ($retryProfileColumn | Out-String).Trim() -ne '1' -or ($retryMemberColumn | Out-String).Trim() -ne '1') {
    throw 'MAGI retry-policy migration verification failed.'
}

$has024 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='024_service_connections.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 024 schema version.' }
if (($has024 | Out-String).Trim() -eq '0') {
    $hash024 = (Get-FileHash -LiteralPath $file024 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql024 = [IO.File]::ReadAllText($file024).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash024)
    $tmpName024 = 'service-connections-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp024 = Join-Path ([IO.Path]::GetTempPath()) $tmpName024
    $remoteTmp024 = '/tmp/' + $tmpName024
    try {
        [IO.File]::WriteAllText($localTmp024, $sql024, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp024 ($id + ':' + $remoteTmp024) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 024 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp024
        if ($LASTEXITCODE -ne 0) { throw '024 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp024) { Remove-Item -LiteralPath $localTmp024 }
        & docker exec $id rm -f $remoteTmp024 | Out-Null
    }
} elseif (($has024 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 024 migration records.'
}

$verify024 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='024_service_connections.sql';"
$connectionTable = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='secretary' AND table_name='service_connections';"
$connectionColumn = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.columns WHERE table_schema='secretary' AND table_name='llm_profiles' AND column_name='connection_id';"
$connectionGrant = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.service_connections','SELECT,INSERT,UPDATE,DELETE');"
$unlinkedProfiles = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.llm_profiles WHERE connection_id IS NULL;"
if ($LASTEXITCODE -ne 0 -or ($verify024 | Out-String).Trim() -ne '1' -or ($connectionTable | Out-String).Trim() -ne '1' -or ($connectionColumn | Out-String).Trim() -ne '1' -or ($connectionGrant | Out-String).Trim() -ne 't' -or ($unlinkedProfiles | Out-String).Trim() -ne '0') {
    throw 'Service Connection migration verification failed.'
}

$has025 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='025_connection_auth_and_consumer_binding.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 025 schema version.' }
if (($has025 | Out-String).Trim() -eq '0') {
    $hash025 = (Get-FileHash -LiteralPath $file025 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql025 = [IO.File]::ReadAllText($file025).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash025)
    $tmpName025 = 'connection-auth-binding-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp025 = Join-Path ([IO.Path]::GetTempPath()) $tmpName025
    $remoteTmp025 = '/tmp/' + $tmpName025
    try {
        [IO.File]::WriteAllText($localTmp025, $sql025, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp025 ($id + ':' + $remoteTmp025) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 025 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp025
        if ($LASTEXITCODE -ne 0) { throw '025 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp025) { Remove-Item -LiteralPath $localTmp025 }
        & docker exec $id rm -f $remoteTmp025 | Out-Null
    }
} elseif (($has025 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 025 migration records.'
}

$verify025 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='025_connection_auth_and_consumer_binding.sql';"
$connectionTypeColumn = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.columns WHERE table_schema='secretary' AND table_name='service_connections' AND column_name='connection_type';"
$connectionAuthColumn = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.columns WHERE table_schema='secretary' AND table_name='service_connections' AND column_name='auth_data';"
$usageProfileTable = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='secretary' AND table_name IN ('provider_usage_profiles','service_billing_profiles');"
$usageProfileGrant = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT CASE WHEN to_regclass('secretary.service_billing_profiles') IS NOT NULL THEN has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.service_billing_profiles','SELECT,INSERT,UPDATE,DELETE') ELSE has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.provider_usage_profiles','SELECT,INSERT,UPDATE,DELETE') END;"
if ($LASTEXITCODE -ne 0 -or ($verify025 | Out-String).Trim() -ne '1' -or ($connectionTypeColumn | Out-String).Trim() -ne '1' -or ($connectionAuthColumn | Out-String).Trim() -ne '1' -or ($usageProfileTable | Out-String).Trim() -ne '1' -or ($usageProfileGrant | Out-String).Trim() -ne 't') {
    throw 'Connection auth / consumer binding migration verification failed.'
}

$has026 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='026_service_billing.sql';"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect 026 schema version.' }
if (($has026 | Out-String).Trim() -eq '0') {
    $hash026 = (Get-FileHash -LiteralPath $file026 -Algorithm SHA256).Hash.ToLowerInvariant()
    $sql026 = [IO.File]::ReadAllText($file026).Replace('PENDING_SHA_REPLACED_BY_APPLIER', $hash026)
    $tmpName026 = 'service-billing-' + [guid]::NewGuid().ToString('N') + '.sql'
    $localTmp026 = Join-Path ([IO.Path]::GetTempPath()) $tmpName026
    $remoteTmp026 = '/tmp/' + $tmpName026
    try {
        [IO.File]::WriteAllText($localTmp026, $sql026, (New-Object Text.UTF8Encoding($false)))
        & docker cp $localTmp026 ($id + ':' + $remoteTmp026) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stage 026 migration.' }
        & docker exec $id psql -X -q -U secretary_admin -d $db -v ON_ERROR_STOP=1 -f $remoteTmp026
        if ($LASTEXITCODE -ne 0) { throw '026 migration failed and was rolled back.' }
    } finally {
        if (Test-Path -LiteralPath $localTmp026) { Remove-Item -LiteralPath $localTmp026 }
        & docker exec $id rm -f $remoteTmp026 | Out-Null
    }
} elseif (($has026 | Out-String).Trim() -ne '1') {
    throw 'Unexpected duplicate 026 migration records.'
}

$verify026 = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='026_service_billing.sql';"
$billingProfileTable = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='secretary' AND table_name='service_billing_profiles';"
$legacyUsageProfileTable = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='secretary' AND table_name='provider_usage_profiles';"
$billingProfileGrant = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT has_table_privilege('secretary_pkb_proto_writer_20260927','secretary.service_billing_profiles','SELECT,INSERT,UPDATE,DELETE');"
$legacyCapabilityCount = & docker exec $id psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.service_connections WHERE 'provider_usage_read'=ANY(capabilities);"
if ($LASTEXITCODE -ne 0 -or ($verify026 | Out-String).Trim() -ne '1' -or ($billingProfileTable | Out-String).Trim() -ne '1' -or ($legacyUsageProfileTable | Out-String).Trim() -ne '0' -or ($billingProfileGrant | Out-String).Trim() -ne 't' -or ($legacyCapabilityCount | Out-String).Trim() -ne '0') {
    throw 'Service Billing migration verification failed.'
}

Write-Host 'PASS: PKB prototype migrations verified through 026 (Service Billing included).'







