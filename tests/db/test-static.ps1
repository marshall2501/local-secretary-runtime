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
} finally { Pop-Location }
Write-Host 'PASS: repository PowerShell parsing, sensitive-path ignore rules, diff whitespace.'
