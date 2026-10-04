[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BackupPath,
    [switch]$IncludeSettings,
    [switch]$IncludeDailyData
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$composeFile = Join-Path $root 'docker/compose.postgres.yml'
$composeDirectory = Split-Path $composeFile
$envFile = Join-Path $root '.env.postgres'
$service = 'secretary-postgres'
$secretFile = Join-Path $root 'secrets/postgres-password.txt'
$python = Join-Path $root '.venv/Scripts/python.exe'
$dataTransfer = Join-Path $root 'scripts/db/production_data_transfer.py'
$dataRestoreHelper = Join-Path $root 'scripts/db/restore-data-only.sh'
$runtimeRunner = Join-Path $root 'scripts/db/run_production_runtime_rehearsal.py'
$BackupPath = [IO.Path]::GetFullPath($BackupPath)

if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
    throw 'Supply an existing trusted custom-format secretary backup.'
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) { throw 'Missing .env.postgres.' }
if (-not (Test-Path -LiteralPath $secretFile -PathType Leaf)) { throw 'Missing local PostgreSQL secret.' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Missing runtime Python virtualenv.' }
$transferDailyData = [bool]($IncludeDailyData -or $IncludeSettings)
if ($transferDailyData -and -not (Test-Path -LiteralPath $dataTransfer -PathType Leaf)) { throw 'Missing production data transfer helper.' }
if (-not (Test-Path -LiteralPath $dataRestoreHelper -PathType Leaf)) { throw 'Missing shared data-only restore helper.' }
if (-not (Test-Path -LiteralPath $runtimeRunner -PathType Leaf)) { throw 'Missing strict production runtime rehearsal runner.' }

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
        $composeProject = $null
        if ($null -ne $details.Config.Labels) {
            $projectProperty = $details.Config.Labels.PSObject.Properties['com.docker.compose.project']
            if ($null -ne $projectProperty) {
                $composeProject = "$($projectProperty.Value)"
            }
        }
        if ($composeProject -ne $project) {
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
$sourcePortLine = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($sourcePortLine.Count -ne 1) { throw 'Cannot determine the live Secretary PostgreSQL port.' }
$sourcePort = [int]($sourcePortLine[0] -replace '^LSA_DB_PORT=', '')
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

    Invoke-Docker @('cp',$BackupPath,"$($containerId):$remoteBackup")
    Invoke-Compose @('exec','-T',$service,'pg_restore','--list',$remoteBackup) | Out-Null

    # Build the replacement from the schema required by the improved runtime.
    # The old production backup contributes data only; its historical schema and
    # migration state are not restored into the replacement.
    Invoke-Compose @('exec','-T',$service,'sh','/opt/secretary/scripts/migrate.sh')

    $schemaMigrationCount = [int](Invoke-Scalar "SELECT count(*) FROM secretary.schema_migrations;")
    $schemaLatest = Invoke-Scalar "SELECT max(version) FROM secretary.schema_migrations;"
    if ($schemaMigrationCount -ne 8 -or $schemaLatest -ne '008_runtime_privileges.sql') {
        throw "Fresh replacement schema is unexpected. Found count=$schemaMigrationCount latest=$schemaLatest"
    }

    $schemaDataTables = @(
        'entities','sources','pending_claims','claims','issues','hypotheses','decisions',
        'tasks','task_steps','task_dependencies','task_step_dependencies',
        'approvals','actions','results','audit_events',
        'entity_relations','pkb_input_receipts','pkb_correction_receipts',
        'pkb_pending_intake','pkb_memory_intakes','pkb_memory_candidate_receipts',
        'finance_import_batches','finance_accounts','finance_categories',
        'finance_transactions','finance_transaction_versions',
        'llm_profiles','magi_member_assignments','service_connections',
        'service_billing_profiles'
    )
    foreach ($table in $schemaDataTables) {
        if ($table -notmatch '^[a-z_][a-z0-9_]*$') { throw "Unsafe table identifier: $table" }
        $count = [long](Invoke-Scalar "SELECT count(*) FROM secretary.$table;")
        if ($count -ne 0) { throw "Fresh replacement schema unexpectedly contains data in $table." }
    }

    # Preserve the old operational rows without restoring the old schema or its
    # migration history. The shared helper removes only schema_migrations TABLE DATA
    # from the archive TOC, then restores the remaining data through pg_restore -L.
    Invoke-Compose @(
        'exec','-T',$service,'sh','/opt/secretary/scripts/restore-data-only.sh',
        'secretary',$remoteBackup
    )

    $afterRestoreMigrationCount = [int](Invoke-Scalar "SELECT count(*) FROM secretary.schema_migrations;")
    $afterRestoreLatest = Invoke-Scalar "SELECT max(version) FROM secretary.schema_migrations;"
    if ($afterRestoreMigrationCount -ne $schemaMigrationCount -or $afterRestoreLatest -ne $schemaLatest) {
        throw 'Old production data restore changed replacement migration state.'
    }

    $trackedProductionTables = @(
        'entities','sources','pending_claims','claims','issues','hypotheses','decisions',
        'tasks','task_steps','task_dependencies','task_step_dependencies',
        'approvals','actions','results','audit_events'
    )
    $restoredProductionRows = 0L
    foreach ($table in $trackedProductionTables) {
        $restoredProductionRows += [long](Invoke-Scalar "SELECT count(*) FROM secretary.$table;")
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
        if ($count -ne 0) {
            throw "Legacy production backup unexpectedly populated post-004 table $table."
        }
    }

    $prototypeRole = Invoke-Scalar "SELECT count(*) FROM pg_roles WHERE rolname='secretary_pkb_proto_writer_20260927';"
    if ($prototypeRole -ne '0') { throw 'Prototype writer role leaked into rehearsal cluster.' }

    if ($transferDailyData) {
        & $python $dataTransfer --source-port $sourcePort --target-port $rehearsalPort --secret-file $secretFile --commit
        if ($LASTEXITCODE -ne 0) { throw 'Production daily data transfer rehearsal failed.' }
    }

    $runtimeArgs = @(
        $runtimeRunner,
        '--target-port', "$rehearsalPort",
        '--live-port', "$sourcePort",
        '--admin-secret-file', $secretFile
    )
    if ($transferDailyData) {
        $runtimeArgs += @('--settings-source-port', "$sourcePort")
    }
    & $python @runtimeArgs
    if ($LASTEXITCODE -ne 0) { throw 'Production daily runtime rehearsal failed.' }

    Write-Host ''
    Write-Host '=== Disposable promotion rehearsal ==='
    [pscustomobject]@{
        Project = $project
        HostPort = $rehearsalPort
        SchemaMigrationCount = $schemaMigrationCount
        SchemaLatestMigration = $schemaLatest
        LegacyProductionRowsRestored = $restoredProductionRows
        OldSchemaNotRestored = $true
        SchemaTablesInitiallyEmpty = $true
        SharedInternalRuntimeAccess = $true
        DailyDataTransferRehearsed = $transferDailyData
        ProductionRuntimeRehearsed = $true
    } | Format-List

    Write-Host 'PASS: fresh replacement schema built, legacy production data restored data-only, selected daily data transferred, and improved production runtime rehearsed.'
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
