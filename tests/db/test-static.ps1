$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$errorsFound = @()
$scriptRoots = @(
    (Join-Path $root 'scripts'),
    (Join-Path $root 'tests')
)
Get-ChildItem -Path $scriptRoots -Recurse -Filter '*.ps1' -File | ForEach-Object {
    $tokens = $null
    $parseErrors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile(
        $_.FullName,
        [ref]$tokens,
        [ref]$parseErrors
    )
    $errorsFound += $parseErrors
}
if ($errorsFound.Count) { throw ($errorsFound | Out-String) }
Push-Location $root
try {
    foreach ($path in @('.env.postgres','secrets/postgres-password.txt','backups/test.dump','docker/volumes/test','models/test.gguf')) {
        git check-ignore -q -- $path
        if ($LASTEXITCODE -ne 0) { throw "Sensitive path is not ignored: $path" }
    }
    git diff --check
    if ($LASTEXITCODE -ne 0) { throw 'Whitespace validation failed.' }


    $promotionMigrations = @(
        'db/migrations/005_pkb_runtime_schema.sql',
        'db/migrations/006_finance_runtime_schema.sql',
        'db/migrations/007_runtime_settings_schema.sql',
        'db/migrations/008_runtime_privileges.sql'
    )
    foreach ($relative in $promotionMigrations) {
        $migrationPath = Join-Path $root $relative
        if (-not (Test-Path -LiteralPath $migrationPath -PathType Leaf)) {
            throw "Missing production promotion migration: $relative"
        }
        $migrationText = Get-Content -LiteralPath $migrationPath -Raw
        foreach ($banned in @(
            'secretary_pkb_proto_20260927',
            'secretary_pkb_proto_writer_20260927',
            'fixture://',
            'fictional_only',
            'pkb_episode_receipts',
            'provider_usage_profiles',
            '14000000-0000-0000-0000-'
        )) {
            if ($migrationText.Contains($banned)) {
                throw "Prototype/history marker '$banned' leaked into $relative"
            }
        }
        if ($migrationText -match '(?im)^\s*INSERT\s+INTO\s+') {
            throw "Production schema migration must not contain data INSERT statements: $relative"
        }
        if ($migrationText -match 'schema_migrations') {
            throw "Migration history is owned by scripts/db/migrate.sh: $relative"
        }
    }

    $preflightPath = Join-Path $root 'scripts/db/promotion-preflight.ps1'
    if (-not (Test-Path -LiteralPath $preflightPath -PathType Leaf)) {
        throw 'Missing production promotion preflight.'
    }
    $preflightText = Get-Content -LiteralPath $preflightPath -Raw
    if ($preflightText -match '(?im)^\s*(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b') {
        throw 'Production promotion preflight must remain read-only.'
    }


    $manifestPath = Join-Path $root 'scripts/db/promotion-manifest.ps1'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw 'Missing PKB production promotion manifest.'
    }
    $manifestText = Get-Content -LiteralPath $manifestPath -Raw
    if ($manifestText -match '(?im)^\s*(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b') {
        throw 'PKB production promotion manifest must remain read-only.'
    }
    if ($manifestText -notmatch 'BEGIN TRANSACTION READ ONLY') {
        throw 'PKB production promotion manifest must enforce read-only transactions.'
    }
    foreach ($sensitiveField in @(
        'raw_text',
        'citation',
        'evidence',
        'amount_jpy',
        'auth_data',
        'password',
        'access_token'
    )) {
        if ($manifestText -match ('(?i)\b' + [regex]::Escape($sensitiveField) + '\b')) {
            throw "PKB promotion manifest references sensitive value field: $sensitiveField"
        }
    }


    $entityReconciliationPath = Join-Path $root 'scripts/db/entity-reconciliation.ps1'
    if (-not (Test-Path -LiteralPath $entityReconciliationPath -PathType Leaf)) {
        throw 'Missing PKB entity reconciliation inspection.'
    }
    $entityReconciliationText = Get-Content -LiteralPath $entityReconciliationPath -Raw
    if ($entityReconciliationText -match '(?im)^\s*(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b') {
        throw 'PKB entity reconciliation inspection must remain read-only.'
    }
    if ($entityReconciliationText -notmatch 'BEGIN TRANSACTION READ ONLY') {
        throw 'PKB entity reconciliation inspection must enforce read-only transactions.'
    }
    foreach ($sensitiveField in @('raw_text','citation','evidence','amount_jpy','auth_data','password','access_token')) {
        if ($entityReconciliationText -match ('(?i)\b' + [regex]::Escape($sensitiveField) + '\b')) {
            throw "PKB entity reconciliation references sensitive value field: $sensitiveField"
        }
    }

    $rehearsalPath = Join-Path $root 'scripts/db/promotion-rehearsal.ps1'
    if (-not (Test-Path -LiteralPath $rehearsalPath -PathType Leaf)) {
        throw 'Missing disposable production promotion rehearsal tool.'
    }
    $rehearsalText = Get-Content -LiteralPath $rehearsalPath -Raw
    foreach ($requiredMarker in @(
        'local-secretary-test-promotion-',
        "pg_restore','-U','secretary_admin','-d','secretary'",
        "'--data-only'",
        "'--exclude-table-data=secretary.schema_migrations'",
        "/opt/secretary/scripts/migrate.sh",
        "down','--volumes"
    )) {
        if (-not $rehearsalText.Contains($requiredMarker)) {
            throw "Promotion rehearsal safety marker missing: $requiredMarker"
        }
    }
    if ($rehearsalText.Contains('local-secretary-runtime-db')) {
        throw 'Promotion rehearsal must never target the normal compose project.'
    }

    if ($rehearsalText -match '(?i)param\s*\(\s*\[string\[\]\]\s*\$Args\s*\)') {
        throw 'Promotion rehearsal must not shadow PowerShell automatic $Args in command wrappers.'
    }

    if ($rehearsalText -match [regex]::Escape("Labels.'com.docker.compose.project'")) {
        throw 'Promotion rehearsal must tolerate non-Compose containers without Compose labels under StrictMode.'
    }
    if ($rehearsalText -notmatch [regex]::Escape("PSObject.Properties['com.docker.compose.project']")) {
        throw 'Promotion rehearsal must inspect optional Compose labels safely.'
    }

    foreach ($promotionHelper in @(
        'scripts/db/transfer_common.py',
        'scripts/db/production_data_transfer.py',
        'scripts/db/runtime_settings_transfer.py',
        'scripts/db/provision_daily_runtime.py',
        'scripts/db/verify_production_runtime.py',
        'scripts/db/run_production_runtime_rehearsal.py',
        'scripts/db/rebuild-production.ps1',
        'scripts/db/promote-production.ps1'
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $promotionHelper) -PathType Leaf)) {
            throw "Missing production promotion helper: $promotionHelper"
        }
    }

    $runtimeProvisionText = Get-Content -LiteralPath (Join-Path $root 'scripts/db/provision_daily_runtime.py') -Raw
    if ($runtimeProvisionText -match 'PASSWORD\s+%s') {
        throw 'Role password DDL must not use psycopg bind placeholders.'
    }
    if ($runtimeProvisionText -notmatch 'sql\.Literal\(runtime_password\)') {
        throw 'Role password DDL must quote the generated secret with psycopg sql.Literal.'
    }
    $launcherText = Get-Content -LiteralPath (Join-Path $root 'scripts/ui/launch_daily_pkb.ps1') -Raw
    if ($launcherText -notmatch "\[ValidateSet\('isolated','production'\)\]") {
        throw 'Daily launcher must expose an explicit isolated/production cutover mode.'
    }
    if ($launcherText -notmatch '008_runtime_privileges.sql') {
        throw 'Production daily launcher must require migration 008.'
    }
    if ($launcherText -notmatch 'LSA_DAILY_DB_NAME') {
        throw 'Production daily launcher must pass the selected rebuilt database name to runtime.'
    }
    if ($launcherText -notmatch 'secretary_rebuild_') {
        throw 'Production daily launcher must allow an approved rebuilt production database.'
    }

    $rebuildPath = Join-Path $root 'scripts/db/rebuild-production.ps1'
    $rebuildText = Get-Content -LiteralPath $rebuildPath -Raw
    foreach ($requiredMarker in @(
        'secretary_rebuild_',
        "'--data-only'",
        "'--exclude-table-data=secretary.schema_migrations'",
        'production_data_transfer.py',
        'run_production_runtime_rehearsal.py',
        'OldSecretaryPreserved = $true'
    )) {
        if (-not $rebuildText.Contains($requiredMarker)) {
            throw "Clean rebuild safety marker missing: $requiredMarker"
        }
    }

    $tokens = $null
    $rehearsalParseErrors = $null
    $rehearsalAst = [Management.Automation.Language.Parser]::ParseFile(
        $rehearsalPath, [ref]$tokens, [ref]$rehearsalParseErrors
    )
    if ($rehearsalParseErrors.Count) { throw ($rehearsalParseErrors | Out-String) }
    $commands = @($rehearsalAst.FindAll(
        { param($node) $node -is [Management.Automation.Language.CommandAst] },
        $true
    ))
    $runnerCalls = @($commands | Where-Object {
        $_.Extent.Text -match '^&\s+\$python\s+@runtimeArgs$'
    })
    if ($runnerCalls.Count -ne 1) {
        throw 'Promotion rehearsal must invoke the strict runtime runner exactly once.'
    }
    $runnerOffset = $runnerCalls[0].Extent.StartOffset
    $successOffset = $rehearsalText.IndexOf('ProductionRuntimeRehearsed = $true')
    if ($successOffset -le $runnerOffset) {
        throw 'ProductionRuntimeRehearsed may only be reported after the runtime runner.'
    }
    $exitGuards = @($rehearsalAst.FindAll(
        {
            param($node)
            $node -is [Management.Automation.Language.IfStatementAst] -and
            $node.Extent.Text -match '\$LASTEXITCODE\s+-ne\s+0' -and
            $node.Extent.StartOffset -gt $runnerOffset -and
            $node.Extent.StartOffset -lt $successOffset
        },
        $true
    ))
    if ($exitGuards.Count -lt 1) {
        throw 'Runtime runner non-zero exit must stop rehearsal before success is reported.'
    }

    $dailyWebText = Get-Content -LiteralPath (Join-Path $root 'interfaces/web/app.py') -Raw
    if ($dailyWebText -notmatch 'resolve_daily_web_port\(\)') {
        throw 'Daily Web runtime must resolve the configured rehearsal port through the tested contract.'
    }

    $runtimeVerifyText = Get-Content -LiteralPath (Join-Path $root 'scripts/db/verify_production_runtime.py') -Raw
    if ($runtimeVerifyText -notmatch 'sys\.path\.insert\(0, str\(ROOT\)\)') {
        throw 'Direct production verifier execution must add the repository root to sys.path.'
    }
    if ($runtimeVerifyText -notmatch '_validate_disposable_target') {
        throw 'Production verifier must reject the configured live PostgreSQL port before write probes.'
    }
} finally { Pop-Location }
Write-Host 'PASS: repository PowerShell parsing, sensitive-path ignore rules, diff whitespace.'
