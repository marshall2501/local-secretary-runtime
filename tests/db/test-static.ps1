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
            throw "Missing current migration: $relative"
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

    # Migration 009 intentionally seeds only synthetic policy catalog defaults.
    # Unlike 005-008, narrowly scoped INSERTs are part of its owner-configurable
    # bootstrap; it must not mutate existing production records.
    $policyMigrationPath = Join-Path $root 'db/migrations/009_runtime_policy_profiles.sql'
    if (-not (Test-Path -LiteralPath $policyMigrationPath -PathType Leaf)) {
        throw 'Missing policy profile migration 009.'
    }
    $policyMigrationText = Get-Content -LiteralPath $policyMigrationPath -Raw
    foreach ($required in @(
        'CREATE TABLE permission_labels',
        'CREATE TABLE policy_profiles',
        'CREATE TABLE policy_grants',
        'CREATE TABLE policy_activations',
        'INSERT INTO permission_labels',
        'INSERT INTO policy_profiles',
        'INSERT INTO policy_grants',
        'INSERT INTO policy_activations'
    )) {
        if (-not $policyMigrationText.Contains($required)) {
            throw "Policy migration 009 marker missing: $required"
        }
    }
    if ($policyMigrationText -match '(?im)^\s*(DELETE|UPDATE|TRUNCATE|DROP)\b') {
        throw 'Policy migration 009 must not rewrite existing production data.'
    }


    # The only tracked SQL migration series for runtime and disposable tests.
    foreach ($retiredPath in @(
        'db/isolated/pkb_proto',
        'scripts/pkb/isolated',
        'ritsuko/core/observation_loop.py',
        'ritsuko/magi/goal_loop.py',
        'scripts/db/promotion-rehearsal.ps1',
        'scripts/db/rebuild-production.ps1',
        'scripts/db/runtime_settings_transfer.py'
    )) {
        if (Test-Path -LiteralPath (Join-Path $root $retiredPath)) {
            throw "Retired runtime path still exists: $retiredPath"
        }
    }
    foreach ($path in @(
        'config/runtime_database.py',
        'infrastructure/postgres/pkb_runtime.py',
        'scripts/ui/launch_daily_pkb.ps1',
        'infrastructure/postgres/finance_repository.py'
    )) {
        $text = Get-Content -LiteralPath (Join-Path $root $path) -Raw
        if ($text -match 'secretary_pkb_proto_20260927|LSA_DAILY_DB_MODE') {
            throw "Legacy production DB selector remains in $path"
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
        "db/migrations/009_runtime_policy_profiles.sql",
        'CREATE ROLE secretary_[a-z_]+ NOLOGIN'
    )) {
        if (-not $initializerText.Contains($requiredMarker)) {
            throw "Fresh production initializer marker missing: $requiredMarker"
        }
    }
    if ($initializerText -match '(?i)pg_restore|pg_dump|BackupPath') {
        throw 'Fresh production initializer must not depend on dump/restore.'
    }

    $runtimeProvisionText = Get-Content -LiteralPath (Join-Path $root 'scripts/db/provision_daily_runtime.py') -Raw
    if ($runtimeProvisionText -match 'PASSWORD\s+%s') {
        throw 'Role password DDL must not use psycopg bind placeholders.'
    }
    if ($runtimeProvisionText -notmatch 'sql\.Literal\(runtime_password\)') {
        throw 'Role password DDL must quote the generated secret with psycopg sql.Literal.'
    }
    $launcherText = Get-Content -LiteralPath (Join-Path $root 'scripts/ui/launch_daily_pkb.ps1') -Raw
    if ($launcherText -match '\\$DatabaseMode|LSA_DAILY_DB_MODE|secretary_pkb_proto_20260927') {
        throw 'Daily launcher must not select the retired prototype database.'
    }
    if ($launcherText -notmatch 'secretary_daily_runtime') {
        throw 'Daily launcher must use the canonical runtime login.'
    }
    if ($launcherText -notmatch '008_runtime_privileges.sql') {
        throw 'Production daily launcher must remain compatible with migration 008 until 009 is explicitly applied.'
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
