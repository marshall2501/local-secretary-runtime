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
$isolatedDb = 'secretary_pkb_proto_20260927'

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw 'Missing .env.postgres.'
}

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
    if ($LASTEXITCODE -ne 0) {
        throw "Read-only SQL failed for database $Database."
    }
    return @($values | ForEach-Object { "$_".Trim() } | Where-Object { $_ -ne '' })
}

$dbExists = @(Invoke-ReadOnlyLines -Database 'postgres' -Sql (
    "SELECT count(*) FROM pg_database WHERE datname='$isolatedDb';"
))
if ($dbExists.Count -ne 1 -or $dbExists[0] -ne '1') {
    throw "Required isolated database is missing: $isolatedDb"
}

$closureCtes = @"
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
relation_closure AS (
    SELECT r.id, r.subject_entity_id, r.object_entity_id, r.source_id
    FROM secretary.entity_relations r
    WHERE r.subject_entity_id IN (SELECT id FROM entity_closure)
       OR r.object_entity_id IN (SELECT id FROM entity_closure)
),
input_receipt_closure AS (
    SELECT r.*
    FROM secretary.pkb_input_receipts r
    WHERE r.source_id IN (SELECT id FROM candidate_sources)
       OR r.claim_id IN (SELECT id FROM claim_closure)
),
correction_receipt_closure AS (
    SELECT r.*
    FROM secretary.pkb_correction_receipts r
    WHERE r.source_id IN (SELECT id FROM candidate_sources)
       OR r.old_claim_id IN (SELECT id FROM claim_closure)
       OR r.new_claim_id IN (SELECT id FROM claim_closure)
),
source_closure(id) AS (
    SELECT id FROM candidate_sources
    UNION
    SELECT c.source_id
    FROM secretary.claims c
    JOIN claim_closure cc ON cc.id=c.id
    UNION
    SELECT r.source_id FROM relation_closure r
    UNION
    SELECT p.accepted_source_id
    FROM candidate_pending p
    WHERE p.accepted_source_id IS NOT NULL
    UNION
    SELECT r.source_id FROM input_receipt_closure r
    UNION
    SELECT r.source_id FROM correction_receipt_closure r
),
known_dev_sources AS (
    SELECT id
    FROM secretary.sources
    WHERE metadata->>'dataset_id'='pkb-p0-fictional-20260927'
       OR id='14000000-0000-0000-0000-0000000000f0'::uuid
       OR uri LIKE 'fixture://pkb-proto-%'
),
known_dev_claims AS (
    SELECT id, entity_id
    FROM secretary.claims
    WHERE source_id IN (SELECT id FROM known_dev_sources)
),
known_dev_entities AS (
    SELECT entity_id AS id FROM known_dev_claims
    UNION
    SELECT id
    FROM secretary.entities
    WHERE id IN (
        '14000000-0000-0000-0000-000000000001'::uuid,
        '14000000-0000-0000-0000-000000000002'::uuid
    )
),
known_dev_relations AS (
    SELECT id
    FROM secretary.entity_relations
    WHERE source_id IN (SELECT id FROM known_dev_sources)
       OR id IN (
          '14000000-0000-0000-0000-000000000101'::uuid,
          '14000000-0000-0000-0000-000000000102'::uuid
       )
)
"@

$manifestSql = $closureCtes + @"
SELECT category || '|' || table_name || '|' || row_id || '|' || classification
FROM (
    SELECT
        'candidate'::text AS category,
        'sources'::text AS table_name,
        s.id::text AS row_id,
        CASE
            WHEN s.uri LIKE 'fixture://daily-pkb/%' THEN 'daily_safety_marker'
            WHEN s.uri LIKE 'fixture://memory-intake/%' THEN 'memory_intake_safety_marker'
            ELSE 'direct_candidate'
        END AS classification
    FROM secretary.sources s
    JOIN candidate_sources c ON c.id=s.id

    UNION ALL

    SELECT
        'closure','sources',s.id::text,
        CASE
            WHEN k.id IS NOT NULL THEN 'known_dev_dependency'
            ELSE 'referential_dependency'
        END
    FROM secretary.sources s
    JOIN source_closure c ON c.id=s.id
    LEFT JOIN candidate_sources d ON d.id=s.id
    LEFT JOIN known_dev_sources k ON k.id=s.id
    WHERE d.id IS NULL

    UNION ALL

    SELECT
        'closure','claims',c.id::text,
        CASE
            WHEN c.source_id IN (SELECT id FROM known_dev_sources)
                THEN 'known_dev_dependency'
            ELSE 'selected_claim'
        END
    FROM secretary.claims c
    JOIN claim_closure cc ON cc.id=c.id

    UNION ALL

    SELECT
        'closure','entities',e.id::text,
        CASE
            WHEN e.id IN (
                '14000000-0000-0000-0000-000000000001'::uuid,
                '14000000-0000-0000-0000-000000000002'::uuid
            ) THEN 'fixed_component_dependency'
            WHEN e.id IN (SELECT id FROM known_dev_entities)
                THEN 'known_dev_dependency'
            ELSE 'selected_entity'
        END
    FROM secretary.entities e
    JOIN entity_closure ec ON ec.id=e.id

    UNION ALL

    SELECT
        'closure','entity_relations',r.id::text,
        CASE
            WHEN r.id IN (
                '14000000-0000-0000-0000-000000000101'::uuid,
                '14000000-0000-0000-0000-000000000102'::uuid
            ) THEN 'fixed_component_dependency'
            WHEN r.id IN (SELECT id FROM known_dev_relations)
                THEN 'known_dev_dependency'
            ELSE 'selected_relation'
        END
    FROM relation_closure r

    UNION ALL

    SELECT
        'candidate','pkb_pending_intake',p.id::text,'pending_user_candidate'
    FROM candidate_pending p

    UNION ALL

    SELECT
        'candidate','pkb_memory_intakes',i.input_id::text,'memory_intake_candidate'
    FROM candidate_memory_intakes i

    UNION ALL

    SELECT
        'closure','pkb_memory_candidate_receipts',r.input_id::text,
        'memory_receipts_for_input'
    FROM candidate_memory_receipts r
    GROUP BY r.input_id

    UNION ALL

    SELECT
        'closure','pkb_input_receipts',r.input_id,'input_receipt_dependency'
    FROM input_receipt_closure r

    UNION ALL

    SELECT
        'closure','pkb_correction_receipts',r.input_id,'correction_receipt_dependency'
    FROM correction_receipt_closure r

    UNION ALL

    SELECT
        'known_fixture','sources',s.id::text,'positive_dev_fixture'
    FROM secretary.sources s
    JOIN known_dev_sources k ON k.id=s.id

    UNION ALL

    SELECT
        'known_fixture','entities',e.id::text,'positive_dev_fixture'
    FROM secretary.entities e
    JOIN known_dev_entities k ON k.id=e.id

    UNION ALL

    SELECT
        'known_fixture','entity_relations',r.id::text,'positive_dev_fixture'
    FROM secretary.entity_relations r
    JOIN known_dev_relations k ON k.id=r.id
) manifest
ORDER BY category, table_name, row_id;
"@

$manifestRows = @()
foreach ($line in (Invoke-ReadOnlyLines -Database $isolatedDb -Sql $manifestSql)) {
    $parts = @($line -split '\|', 4)
    if ($parts.Count -ne 4) {
        throw 'Invalid manifest row.'
    }
    $manifestRows += [pscustomobject]@{
        Category = $parts[0]
        Table = $parts[1]
        Id = $parts[2]
        Classification = $parts[3]
    }
}

Write-Host ''
Write-Host '=== PKB promotion referential-closure manifest (IDs only) ==='
if ($manifestRows.Count) {
    $manifestRows | Format-Table -AutoSize
} else {
    Write-Host '(no candidate rows)'
}

Write-Host ''
Write-Host '=== Manifest counts ==='
$manifestRows |
    Group-Object Category, Table, Classification |
    ForEach-Object {
        [pscustomobject]@{
            Category = $_.Group[0].Category
            Table = $_.Group[0].Table
            Classification = $_.Group[0].Classification
            Count = $_.Count
        }
    } |
    Sort-Object Category, Table, Classification |
    Format-Table -AutoSize

$unclassifiedSql = $closureCtes + @"
SELECT label || '|' || count_value::text
FROM (
    SELECT 'unclassified_entities' AS label, count(*) AS count_value
    FROM secretary.entities e
    WHERE e.id NOT IN (SELECT id FROM entity_closure)
      AND e.id NOT IN (SELECT id FROM known_dev_entities)

    UNION ALL

    SELECT 'unclassified_sources', count(*)
    FROM secretary.sources s
    WHERE s.id NOT IN (SELECT id FROM source_closure)
      AND s.id NOT IN (SELECT id FROM known_dev_sources)

    UNION ALL

    SELECT 'unclassified_claims', count(*)
    FROM secretary.claims c
    WHERE c.id NOT IN (SELECT id FROM claim_closure)
      AND c.id NOT IN (SELECT id FROM known_dev_claims)

    UNION ALL

    SELECT 'unclassified_relations', count(*)
    FROM secretary.entity_relations r
    WHERE r.id NOT IN (SELECT id FROM relation_closure)
      AND r.id NOT IN (SELECT id FROM known_dev_relations)

    UNION ALL

    SELECT 'unselected_pending_rows', count(*)
    FROM secretary.pkb_pending_intake p
    WHERE p.id NOT IN (SELECT id FROM candidate_pending)

    UNION ALL

    SELECT 'unselected_memory_intakes', count(*)
    FROM secretary.pkb_memory_intakes i
    WHERE i.input_id NOT IN (SELECT input_id FROM candidate_memory_intakes)

    UNION ALL

    SELECT 'unselected_input_receipts', count(*)
    FROM secretary.pkb_input_receipts r
    WHERE r.input_id NOT IN (SELECT input_id FROM input_receipt_closure)

    UNION ALL

    SELECT 'unselected_correction_receipts', count(*)
    FROM secretary.pkb_correction_receipts r
    WHERE r.input_id NOT IN (SELECT input_id FROM correction_receipt_closure)

    UNION ALL

    SELECT 'bundled_episode_receipts', count(*)
    FROM secretary.pkb_episode_receipts
) summary
ORDER BY label;
"@

$unclassifiedRows = @()
foreach ($line in (Invoke-ReadOnlyLines -Database $isolatedDb -Sql $unclassifiedSql)) {
    $parts = @($line -split '\|', 2)
    if ($parts.Count -ne 2) {
        throw 'Invalid summary row.'
    }
    $unclassifiedRows += [pscustomobject]@{
        Class = $parts[0]
        Count = [long]$parts[1]
    }
}

Write-Host ''
Write-Host '=== Remaining classification work ==='
$unclassifiedRows | Format-Table -AutoSize

$dependencySql = $closureCtes + @"
SELECT
  count(*) FILTER (
      WHERE id IN (
          '14000000-0000-0000-0000-000000000001'::uuid,
          '14000000-0000-0000-0000-000000000002'::uuid
      )
  )::text || '|' ||
  count(*) FILTER (WHERE id IN (SELECT id FROM known_dev_entities))::text
FROM entity_closure;
"@
$dependencyLine = @(Invoke-ReadOnlyLines -Database $isolatedDb -Sql $dependencySql)
if ($dependencyLine.Count -ne 1) {
    throw 'Invalid fixture-dependency summary.'
}
$dependencyParts = @($dependencyLine[0] -split '\|', 2)
if ($dependencyParts.Count -ne 2) {
    throw 'Invalid fixture-dependency summary fields.'
}

Write-Host ''
Write-Host '=== Candidate dependency flags ==='
[pscustomobject]@{
    FixedComponentEntityDependencies = [long]$dependencyParts[0]
    KnownDevEntityDependencies = [long]$dependencyParts[1]
} | Format-List

Write-Host ''
Write-Host 'PASS: PKB promotion manifest completed in read-only transactions.'
Write-Host 'No personal text, claim values, finance rows, or credential values were printed.'
