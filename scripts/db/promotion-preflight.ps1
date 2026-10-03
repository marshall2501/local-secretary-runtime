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
$composeArgs = @(
    'compose','--project-name',$Project,'--project-directory',$composeDirectory,
    '--env-file',$envFile,'-f',$composeFile,'ps','-q',$service
)
$ids = @(& docker @composeArgs)
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) {
    throw 'Expected exactly one owned Secretary PostgreSQL container.'
}
$id = $ids[0]
$details = @(docker inspect $id | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $details.Count -ne 1 -or
    $details[0].State.Health.Status -ne 'healthy') {
    throw 'Secretary PostgreSQL container is not healthy.'
}

function Invoke-Lines {
    param([string]$Database, [string]$Sql)
    $values = @(& docker exec $id psql -X -A -t -U secretary_admin -d $Database -v ON_ERROR_STOP=1 -c $Sql)
    if ($LASTEXITCODE -ne 0) { throw "Read-only SQL failed for database $Database." }
    return @($values | ForEach-Object { "$_".Trim() } | Where-Object { $_ -ne '' })
}

function Invoke-Scalar {
    param([string]$Database, [string]$Sql)
    $values = @(Invoke-Lines -Database $Database -Sql $Sql)
    if ($values.Count -ne 1) { throw "Expected one scalar row from database $Database." }
    return $values[0]
}

foreach ($required in @($operationalDb, $isolatedDb)) {
    $exists = Invoke-Scalar -Database 'postgres' -Sql (
        "SELECT count(*) FROM pg_database WHERE datname='$required';"
    )
    if ($exists -ne '1') { throw "Required database is missing: $required" }
}

$sharedIdTables = @(
    'entities','sources','pending_claims','claims','issues','hypotheses','decisions',
    'tasks','task_steps','approvals','actions','results','audit_events'
)
$promotionTables = @(
    'entity_relations','pkb_input_receipts','pkb_correction_receipts',
    'pkb_pending_intake','pkb_memory_intakes','pkb_memory_candidate_receipts',
    'finance_import_batches','finance_accounts','finance_categories',
    'finance_transactions','finance_transaction_versions',
    'llm_profiles','magi_member_assignments','service_connections',
    'service_billing_profiles'
)

function Test-Table {
    param([string]$Database, [string]$Table)
    if ($Table -notmatch '^[a-z_][a-z0-9_]*$') { throw "Unsafe table identifier: $Table" }
    return (Invoke-Scalar -Database $Database -Sql (
        "SELECT to_regclass('secretary.$Table') IS NOT NULL;"
    )) -eq 't'
}

function Get-IdMap {
    param([string]$Database, [string]$Table)
    $map = @{}
    if (-not (Test-Table -Database $Database -Table $Table)) { return $map }
    $sql = "SELECT id::text || '|' || md5(to_jsonb(t)::text) FROM secretary.$Table t ORDER BY id;"
    foreach ($line in (Invoke-Lines -Database $Database -Sql $sql)) {
        $parts = $line -split '\|', 2
        if ($parts.Count -ne 2) { throw "Invalid fingerprint row for $Table." }
        $map[$parts[0]] = $parts[1]
    }
    return $map
}

Write-Host ''
Write-Host '=== Shared UUID collision summary ==='
$collisionRows = @()
foreach ($table in $sharedIdTables) {
    $target = Get-IdMap -Database $operationalDb -Table $table
    $source = Get-IdMap -Database $isolatedDb -Table $table
    $same = 0
    $equivalent = 0
    $conflicting = 0
    foreach ($key in $source.Keys) {
        if (-not $target.ContainsKey($key)) { continue }
        $same++
        if ($source[$key] -eq $target[$key]) { $equivalent++ } else { $conflicting++ }
    }
    $collisionRows += [pscustomobject]@{
        Table = $table
        Operational = $target.Count
        Isolated = $source.Count
        SameId = $same
        Equivalent = $equivalent
        Conflicting = $conflicting
    }
}
$collisionRows | Format-Table -AutoSize

function Get-KeyMap {
    param([string]$Database, [string]$Sql)
    $map = @{}
    foreach ($line in (Invoke-Lines -Database $Database -Sql $Sql)) {
        $parts = $line -split '\|', 2
        if ($parts.Count -ne 2) { throw 'Invalid natural-key fingerprint row.' }
        if (-not $map.ContainsKey($parts[0])) { $map[$parts[0]] = @() }
        $map[$parts[0]] += $parts[1]
    }
    return $map
}

$entitySql = @"
SELECT md5(domain || chr(31) || entity_type || chr(31) || lower(name)),
       id::text
FROM secretary.entities
WHERE retired_at IS NULL
ORDER BY 1,2;
"@
$sourceSql = @"
SELECT md5(uri), id::text
FROM secretary.sources
ORDER BY 1,2;
"@

$naturalRows = @()
foreach ($spec in @(
    @{ Name='entities(domain,type,name)'; Sql=$entitySql },
    @{ Name='sources(uri)'; Sql=$sourceSql }
)) {
    $target = Get-KeyMap -Database $operationalDb -Sql $spec.Sql
    $source = Get-KeyMap -Database $isolatedDb -Sql $spec.Sql
    $overlap = 0
    $differentIds = 0
    foreach ($key in $source.Keys) {
        if (-not $target.ContainsKey($key)) { continue }
        $overlap++
        $targetIds = @($target[$key])
        $sourceIds = @($source[$key])
        if (@($sourceIds | Where-Object { $targetIds -notcontains $_ }).Count -gt 0) {
            $differentIds++
        }
    }
    $naturalRows += [pscustomobject]@{
        Key = $spec.Name
        Overlap = $overlap
        DifferentIds = $differentIds
    }
}
Write-Host ''
Write-Host '=== Natural-key overlap summary (values are never printed) ==='
$naturalRows | Format-Table -AutoSize

Write-Host ''
Write-Host '=== Promotion table presence / row counts ==='
$tableRows = @()
foreach ($table in $promotionTables) {
    $sourceExists = Test-Table -Database $isolatedDb -Table $table
    $targetExists = Test-Table -Database $operationalDb -Table $table
    $sourceCount = if ($sourceExists) {
        [long](Invoke-Scalar -Database $isolatedDb -Sql "SELECT count(*) FROM secretary.$table;")
    } else { $null }
    $targetCount = if ($targetExists) {
        [long](Invoke-Scalar -Database $operationalDb -Sql "SELECT count(*) FROM secretary.$table;")
    } else { $null }
    $tableRows += [pscustomobject]@{
        Table = $table
        OperationalExists = $targetExists
        OperationalRows = $targetCount
        IsolatedExists = $sourceExists
        IsolatedRows = $sourceCount
    }
}
$tableRows | Format-Table -AutoSize

$episodeSources = if (Test-Table -Database $isolatedDb -Table 'pkb_episode_receipts') {
    [long](Invoke-Scalar -Database $isolatedDb -Sql 'SELECT count(*) FROM secretary.pkb_episode_receipts;')
} else { 0 }
$componentEntities = [long](Invoke-Scalar -Database $isolatedDb -Sql @"
SELECT count(*) FROM secretary.entities
WHERE id IN (
  '14000000-0000-0000-0000-000000000001'::uuid,
  '14000000-0000-0000-0000-000000000002'::uuid
);
"@)
$componentSource = [long](Invoke-Scalar -Database $isolatedDb -Sql @"
SELECT count(*) FROM secretary.sources
WHERE id='14000000-0000-0000-0000-0000000000f0'::uuid;
"@)
$componentRelations = if (Test-Table -Database $isolatedDb -Table 'entity_relations') {
    [long](Invoke-Scalar -Database $isolatedDb -Sql @"
SELECT count(*) FROM secretary.entity_relations
WHERE id IN (
  '14000000-0000-0000-0000-000000000101'::uuid,
  '14000000-0000-0000-0000-000000000102'::uuid
);
"@)
} else { 0 }
$datasetSources = [long](Invoke-Scalar -Database $isolatedDb -Sql @"
SELECT count(*) FROM secretary.sources
WHERE metadata->>'dataset_id'='pkb-p0-fictional-20260927';
"@)
$dailySafetySources = [long](Invoke-Scalar -Database $isolatedDb -Sql @"
SELECT count(*) FROM secretary.sources
WHERE uri LIKE 'fixture://daily-pkb/%';
"@)

Write-Host ''
Write-Host '=== Known fixture / legacy safety-marker classification ==='
[pscustomobject]@{
    BundledEpisodeReceipts = $episodeSources
    FixedComponentEntities = $componentEntities
    FixedComponentSources = $componentSource
    FixedComponentRelations = $componentRelations
    BundledDatasetSources = $datasetSources
    DailySafetyMarkedSources = $dailySafetySources
} | Format-List

if (Test-Table -Database $isolatedDb -Table 'finance_transactions') {
    $financeSummary = Invoke-Scalar -Database $isolatedDb -Sql @"
SELECT count(*)::text || '|' ||
       COALESCE(min(transaction_date)::text,'') || '|' ||
       COALESCE(max(transaction_date)::text,'')
FROM secretary.finance_transactions;
"@
    $parts = $financeSummary -split '\|', 3
    Write-Host ''
    Write-Host '=== Finance promotion summary ==='
    [pscustomobject]@{
        Rows = [long]$parts[0]
        FirstDate = $parts[1]
        LastDate = $parts[2]
    } | Format-List
}

if (Test-Table -Database $isolatedDb -Table 'service_connections') {
    $connectionSummary = Invoke-Scalar -Database $isolatedDb -Sql @"
SELECT count(*)::text || '|' ||
       count(*) FILTER (WHERE auth_data <> '{}'::jsonb)::text || '|' ||
       count(*) FILTER (WHERE connection_type='external_credentials')::text
FROM secretary.service_connections;
"@
    $parts = $connectionSummary -split '\|', 3
    Write-Host ''
    Write-Host '=== Connection promotion summary (secret values are never read) ==='
    [pscustomobject]@{
        Connections = [long]$parts[0]
        AuthConfigured = [long]$parts[1]
        ExternalCredentialConnections = [long]$parts[2]
    } | Format-List
}

Write-Host ''
Write-Host 'PASS: production promotion preflight completed read-only. No row values or secrets were printed.'
