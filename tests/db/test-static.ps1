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

    $initializerPath = Join-Path $root 'scripts/db/initialize-fresh-production.ps1'
    if (-not (Test-Path -LiteralPath $initializerPath -PathType Leaf)) {
        throw 'Missing fresh production initializer.'
    }
    $initializerText = Get-Content -LiteralPath $initializerPath -Raw
    foreach ($requiredMarker in @(
        'secretary_rebuild_',
        'Existing cluster roles missing',
        "db/migrations/008_runtime_privileges.sql",
        'CREATE ROLE secretary_[a-z_]+ NOLOGIN'
    )) {
        if (-not $initializerText.Contains($requiredMarker)) {
            throw "Fresh production initializer marker missing: $requiredMarker"
        }
    }
    if ($initializerText -match '(?i)pg_restore|pg_dump|BackupPath') {
        throw 'Fresh production initializer must not depend on dump/restore.'
    }

    $rehearsalPath = Join-Path $root 'scripts/db/promotion-rehearsal.ps1'
    if (-not (Test-Path -LiteralPath $rehearsalPath -PathType Leaf)) {
        throw 'Missing fresh production rehearsal tool.'
    }
    $rehearsalText = Get-Content -LiteralPath $rehearsalPath -Raw
    foreach ($requiredMarker in @(
        'secretary_rebuild_rehearsal_',
        'runtime_settings_transfer.py',
        'LegacyDataRestored = $false',
        'SettingsOnlyTransferred = $true',
        'dropdb'
    )) {
        if (-not $rehearsalText.Contains($requiredMarker)) {
            throw "Fresh production rehearsal marker missing: $requiredMarker"
        }
    }
    if ($rehearsalText -match '(?i)pg_restore|pg_dump|BackupPath|production_data_transfer') {
        throw 'Fresh production rehearsal must not restore legacy operational data.'
    }

    foreach ($promotionHelper in @(
        'scripts/db/transfer_common.py',
        'scripts/db/runtime_settings_transfer.py',
        'scripts/db/provision_daily_runtime.py',
        'scripts/db/verify_production_runtime.py',
        'scripts/db/initialize-fresh-production.ps1',
        'scripts/db/promotion-rehearsal.ps1',
        'scripts/db/rebuild-production.ps1',
        'scripts/db/promote-production.ps1'
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $promotionHelper) -PathType Leaf)) {
            throw "Missing production rebuild helper: $promotionHelper"
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
    if ($launcherText -notmatch '\$migrationSummary.*FROM secretary\.schema_migrations;') {
        throw 'Daily launcher migration summary must count rows from secretary.schema_migrations.'
    }

    $rebuildPath = Join-Path $root 'scripts/db/rebuild-production.ps1'
    $rebuildText = Get-Content -LiteralPath $rebuildPath -Raw
    foreach ($requiredMarker in @(
        'secretary_rebuild_',
        'promotion-rehearsal.ps1',
        'runtime_settings_transfer.py',
        'OldSecretaryPreserved = $true',
        'LegacyDataRestored = $false',
        'SettingsOnlyTransferred = $true'
    )) {
        if (-not $rebuildText.Contains($requiredMarker)) {
            throw "Fresh rebuild safety marker missing: $requiredMarker"
        }
    }
    if ($rebuildText -match '(?i)pg_restore|pg_dump|BackupPath|production_data_transfer') {
        throw 'Fresh rebuild must not depend on legacy data restore.'
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
        throw 'Production verifier must reject write probes against the live production database.'
    }
} finally { Pop-Location }
Write-Host 'PASS: repository PowerShell parsing, sensitive-path ignore rules, diff whitespace.'
