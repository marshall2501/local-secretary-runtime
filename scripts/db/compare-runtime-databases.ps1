[CmdletBinding()]
param(
    [ValidatePattern('^local-secretary-runtime-db$')]
    [string]$Project = 'local-secretary-runtime-db'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$composeFile = Join-Path $root 'docker/compose.postgres.yml'
$composeDirectory = Split-Path $composeFile
$envFile = Join-Path $root '.env.postgres'
$service = 'secretary-postgres'
$operationalDb = 'secretary'
$isolatedDb = 'secretary_pkb_proto_20260927'

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) { throw 'Missing .env.postgres.' }
$composeArgs = @('compose','--project-name',$Project,'--project-directory',$composeDirectory,'--env-file',$envFile,'-f',$composeFile,'ps','-q',$service)
$ids = @(& docker @composeArgs)
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) { throw 'Expected exactly one owned Secretary PostgreSQL container.' }
$id = $ids[0]
$details = @(docker inspect $id | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $details.Count -ne 1 -or $details[0].State.Health.Status -ne 'healthy') { throw 'Secretary PostgreSQL container is not healthy.' }

$dbSql = "SELECT datname FROM pg_database WHERE datname IN ('$operationalDb','$isolatedDb') ORDER BY datname;"
$dbList = @(& docker exec $id psql -X -A -t -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c $dbSql)
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect database list.' }
foreach ($required in @($operationalDb, $isolatedDb)) {
    if ($dbList -notcontains $required) { throw "Required comparison database is missing: $required" }
}

$ownershipRows = @(
    @{ Owner='PKB'; Table='entities' },
    @{ Owner='PKB'; Table='sources' },
    @{ Owner='PKB'; Table='pending_claims' },
    @{ Owner='PKB'; Table='claims' },
    @{ Owner='PKB'; Table='issues' },
    @{ Owner='PKB'; Table='hypotheses' },
    @{ Owner='PKB'; Table='decisions' },
    @{ Owner='PKB'; Table='entity_relations' },
    @{ Owner='PKB'; Table='pkb_input_receipts' },
    @{ Owner='PKB'; Table='pkb_correction_receipts' },
    @{ Owner='PKB'; Table='pkb_episode_receipts' },
    @{ Owner='PKB'; Table='pkb_pending_intake' },
    @{ Owner='PKB'; Table='pkb_memory_intakes' },
    @{ Owner='PKB'; Table='pkb_memory_candidate_receipts' },
    @{ Owner='RITSUKO'; Table='tasks' },
    @{ Owner='RITSUKO'; Table='task_steps' },
    @{ Owner='RITSUKO'; Table='task_dependencies' },
    @{ Owner='RITSUKO'; Table='task_step_dependencies' },
    @{ Owner='RITSUKO'; Table='approvals' },
    @{ Owner='RITSUKO'; Table='actions' },
    @{ Owner='RITSUKO'; Table='results' },
    @{ Owner='RITSUKO'; Table='audit_events' },
    @{ Owner='Finance'; Table='finance_import_batches' },
    @{ Owner='Finance'; Table='finance_accounts' },
    @{ Owner='Finance'; Table='finance_categories' },
    @{ Owner='Finance'; Table='finance_transactions' },
    @{ Owner='Finance'; Table='finance_transaction_versions' },
    @{ Owner='MAGI'; Table='llm_profiles' },
    @{ Owner='MAGI'; Table='magi_member_assignments' },
    @{ Owner='Connections'; Table='service_connections' },
    @{ Owner='ServiceBilling'; Table='service_billing_profiles' }
)

function Invoke-Scalar {
    param([string]$Database, [string]$Sql)
    $value = & docker exec $id psql -X -A -t -U secretary_admin -d $Database -v ON_ERROR_STOP=1 -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "SQL failed for database $Database." }
    return (($value | Out-String).Trim())
}

$rows = @()
foreach ($db in @($operationalDb, $isolatedDb)) {
    foreach ($item in $ownershipRows) {
        $table = $item.Table
        if ($table -notmatch '^[a-z_][a-z0-9_]*$') { throw "Unsafe table identifier: $table" }
        $exists = Invoke-Scalar -Database $db -Sql "SELECT to_regclass('secretary.$table') IS NOT NULL;"
        $count = $null
        if ($exists -eq 't') { $count = [long](Invoke-Scalar -Database $db -Sql "SELECT count(*) FROM secretary.$table;") }
        $rows += [pscustomobject]@{ Database=$db; Owner=$item.Owner; Table=$table; Exists=($exists -eq 't'); Rows=$count }
    }
}

Write-Host ''
Write-Host '=== Table ownership / row counts ==='
$rows | Format-Table -AutoSize
Write-Host ''
Write-Host '=== Migration history: operational ==='
& docker exec $id psql -X -P pager=off -U secretary_admin -d $operationalDb -v ON_ERROR_STOP=1 -c 'TABLE secretary.schema_migrations;'
if ($LASTEXITCODE -ne 0) { throw 'Unable to read operational migration history.' }
Write-Host ''
Write-Host '=== Migration history: isolated ==='
& docker exec $id psql -X -P pager=off -U secretary_admin -d $isolatedDb -v ON_ERROR_STOP=1 -c 'TABLE secretary.schema_migrations;'
if ($LASTEXITCODE -ne 0) { throw 'Unable to read isolated migration history.' }
Write-Host ''
Write-Host 'PASS: read-only runtime database inventory completed. No database changes were made.'
