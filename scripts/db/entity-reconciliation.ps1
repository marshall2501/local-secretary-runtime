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

function Invoke-ReadOnlyLines {
    param([string]$Database, [string]$Sql)
    $wrapped = 'BEGIN TRANSACTION READ ONLY;' + [Environment]::NewLine +
        $Sql + [Environment]::NewLine + 'COMMIT;'
    $values = @(& docker exec $id psql -X -q -A -t -U secretary_admin -d $Database -v ON_ERROR_STOP=1 -c $wrapped)
    if ($LASTEXITCODE -ne 0) { throw "Read-only SQL failed for database $Database." }
    return @($values | ForEach-Object { "$_".Trim() } | Where-Object { $_ -ne '' })
}

foreach ($required in @($operationalDb,$isolatedDb)) {
    $rows = @(Invoke-ReadOnlyLines -Database 'postgres' -Sql (
        "SELECT count(*) FROM pg_database WHERE datname='$required';"
    ))
    if ($rows.Count -ne 1 -or $rows[0] -ne '1') {
        throw "Required database is missing: $required"
    }
}

$isolatedSql = @"
WITH RECURSIVE
candidate_sources AS (
    SELECT id
    FROM secretary.sources
    WHERE uri LIKE 'fixture://daily-pkb/%'
       OR uri LIKE 'fixture://memory-intake/%'
),
candidate_memory_intakes AS (
    SELECT input_id, source_id
    FROM secretary.pkb_memory_intakes
    WHERE source_id IN (SELECT id FROM candidate_sources)
),
candidate_memory_receipts AS (
    SELECT r.*
    FROM secretary.pkb_memory_candidate_receipts r
    JOIN candidate_memory_intakes i ON i.input_id=r.input_id
),
candidate_pending AS (
    SELECT p.*
    FROM secretary.pkb_pending_intake p
    WHERE p.input_id LIKE 'daily-pkb-%'
       OR p.accepted_source_id IN (SELECT id FROM candidate_sources)
       OR p.memory_context->>'source_id' IN (
           SELECT id::text FROM candidate_sources
       )
),
seed_claim_ids AS (
    SELECT c.id
    FROM secretary.claims c
    WHERE c.source_id IN (SELECT id FROM candidate_sources)
    UNION
    SELECT p.accepted_claim_id
    FROM candidate_pending p
    WHERE p.accepted_claim_id IS NOT NULL
    UNION
    SELECT r.claim_id
    FROM candidate_memory_receipts r
    WHERE r.claim_id IS NOT NULL
    UNION
    SELECT unnest(r.derived_claim_ids)
    FROM candidate_memory_receipts r
    WHERE cardinality(r.derived_claim_ids) > 0
    UNION
    SELECT r.claim_id
    FROM secretary.pkb_input_receipts r
    WHERE r.source_id IN (SELECT id FROM candidate_sources)
    UNION
    SELECT r.old_claim_id
    FROM secretary.pkb_correction_receipts r
    WHERE r.source_id IN (SELECT id FROM candidate_sources)
    UNION
    SELECT r.new_claim_id
    FROM secretary.pkb_correction_receipts r
    WHERE r.source_id IN (SELECT id FROM candidate_sources)
),
claim_closure(id) AS (
    SELECT id FROM seed_claim_ids WHERE id IS NOT NULL
    UNION
    SELECT c.supersedes_id
    FROM secretary.claims c
    JOIN claim_closure cc ON cc.id=c.id
    WHERE c.supersedes_id IS NOT NULL
),
seed_entities AS (
    SELECT c.entity_id
    FROM secretary.claims c
    JOIN claim_closure cc ON cc.id=c.id
    UNION
    SELECT p.entity_id
    FROM candidate_pending p
    WHERE p.entity_id IS NOT NULL
),
entity_edges AS (
    SELECT subject_entity_id AS a, object_entity_id AS b
    FROM secretary.entity_relations
    WHERE valid_to IS NULL AND retracted_at IS NULL
    UNION ALL
    SELECT object_entity_id AS a, subject_entity_id AS b
    FROM secretary.entity_relations
    WHERE valid_to IS NULL AND retracted_at IS NULL
),
entity_closure(id) AS (
    SELECT entity_id FROM seed_entities
    UNION
    SELECT e.b
    FROM entity_edges e
    JOIN entity_closure ec ON ec.id=e.a
),
known_dev_sources AS (
    SELECT id
    FROM secretary.sources
    WHERE metadata->>'dataset_id'='pkb-p0-fictional-20260927'
       OR id='14000000-0000-0000-0000-0000000000f0'::uuid
       OR uri LIKE 'fixture://pkb-proto-%'
),
known_dev_entities AS (
    SELECT DISTINCT c.entity_id AS id
    FROM secretary.claims c
    WHERE c.source_id IN (SELECT id FROM known_dev_sources)
    UNION
    SELECT id
    FROM secretary.entities
    WHERE id IN (
        '14000000-0000-0000-0000-000000000001'::uuid,
        '14000000-0000-0000-0000-000000000002'::uuid
    )
)
SELECT
    e.id::text || '|' ||
    e.domain || '|' ||
    e.entity_type || '|' ||
    CASE
      WHEN e.id IN (
        '14000000-0000-0000-0000-000000000001'::uuid,
        '14000000-0000-0000-0000-000000000002'::uuid
      ) THEN 'fixed_component_fixture'
      WHEN e.id IN (SELECT id FROM known_dev_entities) THEN 'known_dev_entity'
      ELSE 'candidate_entity'
    END || '|' ||
    count(DISTINCT c.id)::text || '|' ||
    count(DISTINCT p.id)::text || '|' ||
    count(DISTINCT r.id)::text || '|' ||
    md5(lower(e.name))
FROM secretary.entities e
JOIN entity_closure ec ON ec.id=e.id
LEFT JOIN secretary.claims c
  ON c.entity_id=e.id AND c.id IN (SELECT id FROM claim_closure)
LEFT JOIN candidate_pending p ON p.entity_id=e.id
LEFT JOIN secretary.entity_relations r
  ON r.subject_entity_id=e.id OR r.object_entity_id=e.id
GROUP BY e.id,e.domain,e.entity_type,e.name
ORDER BY e.domain,e.entity_type,e.id;
"@

$isolatedRows = @()
foreach ($line in (Invoke-ReadOnlyLines -Database $isolatedDb -Sql $isolatedSql)) {
    $parts = @($line -split '\|', 8)
    if ($parts.Count -ne 8) { throw 'Invalid isolated reconciliation row.' }
    $isolatedRows += [pscustomobject]@{
        EntityId = $parts[0]
        Domain = $parts[1]
        EntityType = $parts[2]
        Classification = $parts[3]
        ClaimRefs = [long]$parts[4]
        PendingRefs = [long]$parts[5]
        RelationRefs = [long]$parts[6]
        NameFingerprint = $parts[7]
    }
}

$productionSql = @"
SELECT
  e.id::text || '|' ||
  e.domain || '|' ||
  e.entity_type || '|' ||
  md5(lower(e.name))
FROM secretary.entities e
WHERE e.retired_at IS NULL
ORDER BY e.domain,e.entity_type,e.id;
"@
$productionRows = @()
foreach ($line in (Invoke-ReadOnlyLines -Database $operationalDb -Sql $productionSql)) {
    $parts = @($line -split '\|', 4)
    if ($parts.Count -ne 4) { throw 'Invalid production entity row.' }
    $productionRows += [pscustomobject]@{
        EntityId = $parts[0]
        Domain = $parts[1]
        EntityType = $parts[2]
        NameFingerprint = $parts[3]
    }
}

$result = @()
foreach ($row in $isolatedRows) {
    $exact = @($productionRows | Where-Object {
        $_.Domain -eq $row.Domain -and
        $_.EntityType -eq $row.EntityType -and
        $_.NameFingerprint -eq $row.NameFingerprint
    })
    $domainType = @($productionRows | Where-Object {
        $_.Domain -eq $row.Domain -and $_.EntityType -eq $row.EntityType
    })
    $sameId = @($productionRows | Where-Object { $_.EntityId -eq $row.EntityId })
    $decision = if ($row.Classification -eq 'fixed_component_fixture') {
        'explicit_reconciliation_required'
    } elseif ($exact.Count -eq 1) {
        'exact_key_candidate'
    } elseif ($exact.Count -gt 1) {
        'ambiguous_exact_key'
    } else {
        'no_exact_production_entity'
    }
    $result += [pscustomobject]@{
        EntityId = $row.EntityId
        Domain = $row.Domain
        EntityType = $row.EntityType
        Classification = $row.Classification
        ClaimRefs = $row.ClaimRefs
        PendingRefs = $row.PendingRefs
        RelationRefs = $row.RelationRefs
        ExactProductionMatches = $exact.Count
        DomainTypeProductionCandidates = $domainType.Count
        SameIdInProduction = ($sameId.Count -gt 0)
        DecisionState = $decision
    }
}

Write-Host ''
Write-Host '=== PKB entity reconciliation candidates ==='
if ($result.Count) {
    $result | Format-Table -AutoSize
} else {
    Write-Host '(no entity candidates)'
}

Write-Host ''
Write-Host '=== Reconciliation summary ==='
$result |
    Group-Object Classification, DecisionState |
    ForEach-Object {
        [pscustomobject]@{
            Classification = $_.Group[0].Classification
            DecisionState = $_.Group[0].DecisionState
            Entities = $_.Count
            ClaimRefs = ($_.Group | Measure-Object ClaimRefs -Sum).Sum
            PendingRefs = ($_.Group | Measure-Object PendingRefs -Sum).Sum
            RelationRefs = ($_.Group | Measure-Object RelationRefs -Sum).Sum
        }
    } |
    Sort-Object Classification, DecisionState |
    Format-Table -AutoSize

$ready = @($result | Where-Object { $_.DecisionState -eq 'exact_key_candidate' }).Count
$requiresDecision = $result.Count - $ready
Write-Host ''
[pscustomobject]@{
    ExactMapCandidates = $ready
    ExplicitReconciliationRequired = $requiresDecision
    ProductionEntityCount = $productionRows.Count
} | Format-List

Write-Host 'PASS: entity reconciliation inspection completed in read-only transactions.'
Write-Host 'Entity names, claim values, source text, finance rows, and credential values were not printed.'
