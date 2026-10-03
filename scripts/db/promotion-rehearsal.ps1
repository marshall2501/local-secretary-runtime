[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BackupPath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$composeFile = Join-Path $root 'docker/compose.postgres.yml'
$composeDirectory = Split-Path $composeFile
$envFile = Join-Path $root '.env.postgres'
$service = 'secretary-postgres'
$secretFile = Join-Path $root 'secrets/postgres-password.txt'
$BackupPath = [IO.Path]::GetFullPath($BackupPath)

if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
    throw 'Supply an existing trusted custom-format secretary backup.'
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) { throw 'Missing .env.postgres.' }
if (-not (Test-Path -LiteralPath $secretFile -PathType Leaf)) { throw 'Missing local PostgreSQL secret.' }

$project = 'local-secretary-test-promotion-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$composeBase = @(
    'compose','--project-name',$project,'--project-directory',$composeDirectory,
    '--env-file',$envFile,'-f',$composeFile
)

function Invoke-Docker {
    param([string[]]$DockerArgs)
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed: $($DockerArgs -join ' ')" }
}

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    Invoke-Docker ($composeBase + $ComposeArgs)
}

function Snapshot-OtherContainers {
    $ids = @(Invoke-Docker @('ps','-aq'))
    $result = @()
    foreach ($containerId in $ids) {
        $details = (Invoke-Docker @('inspect',$containerId) | Out-String | ConvertFrom-Json)[0]
        if ($details.Config.Labels.'com.docker.compose.project' -ne $project) {
            $result += "$($details.Id)|$($details.State.Status)|$($details.State.StartedAt)|$($details.RestartCount)"
        }
    }
    return ($result | Sort-Object) -join [Environment]::NewLine
}

function Get-FreePort {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    } finally {
        $listener.Stop()
    }
}

function Invoke-Scalar {
    param([string]$Sql)
    $values = @(Invoke-Compose @('exec','-T',$service,'psql','-X','-q','-A','-t','-U','secretary_admin','-d','secretary','-v','ON_ERROR_STOP=1','-c',$Sql))
    if ($values.Count -ne 1) { throw 'Expected exactly one scalar result from rehearsal database.' }
    return "$($values[0])".Trim()
}

$beforeOthers = Snapshot-OtherContainers
$oldPort = $env:LSA_DB_PORT
$rehearsalPort = Get-FreePort
$env:LSA_DB_PORT = "$rehearsalPort"
$remoteBackup = '/tmp/secretary-promotion-rehearsal.dump'

try {
    if (@(Invoke-Docker @('ps','-aq','--filter',"label=com.docker.compose.project=$project")).Count -ne 0) {
        throw 'Unexpected pre-existing rehearsal project.'
    }

    Invoke-Compose @('up','-d','--wait','--wait-timeout','120',$service)
    $containerIds = @(Invoke-Compose @('ps','-q',$service))
    if ($containerIds.Count -ne 1) { throw 'Expected one rehearsal PostgreSQL container.' }
    $containerId = $containerIds[0]

    $roleSql = @"
CREATE ROLE secretary_reader NOLOGIN;
CREATE ROLE secretary_candidate_writer NOLOGIN;
CREATE ROLE secretary_memory_writer NOLOGIN;
CREATE ROLE secretary_task_writer NOLOGIN;
CREATE ROLE secretary_audit_writer NOLOGIN;
CREATE ROLE secretary_review_writer NOLOGIN;
"@
    Invoke-Compose @('exec','-T',$service,'psql','-X','-U','secretary_admin','-d','postgres','-v','ON_ERROR_STOP=1','-c',$roleSql)

    Invoke-Docker @('cp',$BackupPath,"$($containerId):$remoteBackup")
    Invoke-Compose @('exec','-T',$service,'pg_restore','--list',$remoteBackup) | Out-Null
    Invoke-Compose @('exec','-T',$service,'pg_restore','-U','secretary_admin','-d','secretary','--single-transaction','--exit-on-error','--no-owner','--no-acl',$remoteBackup)

    $beforeVersionCount = [int](Invoke-Scalar "SELECT count(*) FROM secretary.schema_migrations;")
    $beforeLatest = Invoke-Scalar "SELECT max(version) FROM secretary.schema_migrations;"
    if ($beforeVersionCount -ne 4 -or $beforeLatest -notlike '004_*') {
        throw "Rehearsal requires a pre-promotion backup at production migrations 001-004. Found count=$beforeVersionCount latest=$beforeLatest"
    }

    $trackedTables = @('entities','sources','pending_claims','claims','tasks','task_steps','approvals','actions','results','audit_events')
    $beforeCounts = @{}
    foreach ($table in $trackedTables) {
        if ($table -notmatch '^[a-z_][a-z0-9_]*$') { throw "Unsafe table identifier: $table" }
        $beforeCounts[$table] = [long](Invoke-Scalar "SELECT count(*) FROM secretary.$table;")
    }

    Invoke-Compose @('exec','-T',$service,'sh','/opt/secretary/scripts/migrate.sh')

    foreach ($version in @(
        '005_pkb_runtime_schema.sql',
        '006_finance_runtime_schema.sql',
        '007_runtime_settings_schema.sql',
        '008_runtime_privileges.sql'
    )) {
        $applied = Invoke-Scalar "SELECT count(*) FROM secretary.schema_migrations WHERE version='$version';"
        if ($applied -ne '1') { throw "Required production promotion migration missing after rehearsal: $version" }
    }

    foreach ($table in $trackedTables) {
        $after = [long](Invoke-Scalar "SELECT count(*) FROM secretary.$table;")
        if ($after -ne $beforeCounts[$table]) {
            throw "Schema promotion changed pre-existing row count for $table."
        }
    }

    foreach ($table in @(
        'entity_relations','pkb_input_receipts','pkb_correction_receipts',
        'pkb_pending_intake','pkb_memory_intakes','pkb_memory_candidate_receipts',
        'finance_import_batches','finance_accounts','finance_categories',
        'finance_transactions','finance_transaction_versions',
        'llm_profiles','magi_member_assignments','service_connections',
        'service_billing_profiles'
    )) {
        $count = [long](Invoke-Scalar "SELECT count(*) FROM secretary.$table;")
        if ($count -ne 0) { throw "Schema-only rehearsal unexpectedly populated $table." }
    }

    $roleCheck = Invoke-Scalar @"
SELECT count(*)
FROM pg_roles
WHERE rolname IN (
  'secretary_finance_writer',
  'secretary_magi_settings_writer',
  'secretary_connection_writer',
  'secretary_billing_writer'
) AND NOT rolcanlogin;
"@
    if ($roleCheck -ne '4') { throw 'Production promotion group-role creation failed in rehearsal cluster.' }

    $prototypeRole = Invoke-Scalar "SELECT count(*) FROM pg_roles WHERE rolname='secretary_pkb_proto_writer_20260927';"
    if ($prototypeRole -ne '0') { throw 'Prototype writer role leaked into rehearsal cluster.' }

    Write-Host ''
    Write-Host '=== Disposable promotion rehearsal ==='
    [pscustomobject]@{
        Project = $project
        HostPort = $rehearsalPort
        BeforeMigrationCount = $beforeVersionCount
        BeforeLatestMigration = $beforeLatest
        AfterMigrationCount = [int](Invoke-Scalar "SELECT count(*) FROM secretary.schema_migrations;")
        AfterLatestMigration = Invoke-Scalar "SELECT max(version) FROM secretary.schema_migrations;"
        ExistingRowCountsPreserved = $true
        NewSchemaTablesEmpty = $true
        ProductionGroupRolesPresent = $true
    } | Format-List

    Write-Host 'PASS: backup restored and production schema promotion rehearsed in a disposable PostgreSQL project.'
    Write-Host 'Live secretary database and existing container state were not changed.'
} finally {
    try {
        $env:LSA_DB_PORT = "$rehearsalPort"
        Invoke-Compose @('down','--volumes')
    } finally {
        $env:LSA_DB_PORT = $oldPort
    }
    if ((Snapshot-OtherContainers) -ne $beforeOthers) {
        throw 'Other container IDs, status, start times, or restart counts changed during rehearsal.'
    }
}
