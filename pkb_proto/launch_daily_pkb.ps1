# Daily PKB Web UI prototype. Uses only the existing isolated fictional DB.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $root '.venv\Scripts\python.exe'
$envFile = Join-Path $root '.env.postgres'
$runtimeEnvFile = Join-Path $root '.env'
$secret = Join-Path $root 'secrets\pkb-proto-writer-password.txt'
$db = 'secretary_pkb_proto_20260927'
$role = 'secretary_pkb_proto_writer_20260927'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Expected the existing runtime virtualenv.'
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw 'Missing .env.postgres.'
}
if (-not (Test-Path -LiteralPath $secret -PathType Leaf)) {
    throw 'Missing isolated PKB writer secret. Run the already validated prototype setup/smoke path first.'
}

& $python -c 'import nicegui, fastapi, psycopg, ddgs, httpx, anyio'
if ($LASTEXITCODE -ne 0) {
    throw 'Required Python packages are missing. Install the existing runtime/web requirements explicitly; this launcher installs nothing.'
}

$lines = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($lines.Count -ne 1) { throw 'Cannot determine the existing PostgreSQL localhost port.' }
$port = [int]($lines[0] -replace '^LSA_DB_PORT=', '')
if ($port -lt 1024 -or $port -gt 65535) { throw 'Invalid PostgreSQL port.' }

$ids = @(docker ps -q --filter 'label=com.docker.compose.project=local-secretary-runtime-db' --filter 'label=com.docker.compose.service=secretary-postgres')
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) { throw 'Expected one owned PostgreSQL container.' }
$info = @(docker inspect $ids[0] | ConvertFrom-Json)
$bindings = @($info[0].NetworkSettings.Ports.'5432/tcp')
if ($info.Count -ne 1 -or $info[0].State.Health.Status -ne 'healthy' -or
    $bindings.Count -ne 1 -or $bindings[0].HostIp -ne '127.0.0.1' -or
    $bindings[0].HostPort -ne "$port") {
    throw 'PostgreSQL health or localhost binding is not the expected runtime configuration.'
}
$count = & docker exec $ids[0] psql -X -A -t -U secretary_admin -d $db -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM secretary.schema_migrations WHERE version='024_service_connections.sql';"
if ($LASTEXITCODE -ne 0 -or ($count | Out-String).Trim() -ne '1') {
    throw 'The isolated PKB database is missing migration 024. Run .\pkb_proto\run_pending_setup.ps1 first.'
}
$userExists = & docker exec $ids[0] psql -X -A -t -U secretary_admin -d postgres -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_roles WHERE rolname='$role';"
if ($LASTEXITCODE -ne 0 -or ($userExists | Out-String).Trim() -ne '1') {
    throw 'Dedicated isolated PKB writer role is missing.'
}

$allowedRuntimeEnv = @(
    'OPENAI_API_KEY',
    'OPENAI_BASE_URL',
    'GEMINI_API_KEY',
    'GEMINI_BASE_URL',
    'OLLAMA_HOST',
    'LSA_OLLAMA_CONTEXT_TOKENS',
    'LSA_MAGI_OLLAMA_NUM_PREDICT',
    'LSA_MAGI_CLOUD_ENABLED',
    'LSA_MAGI_MELCHIOR_PROVIDER',
    'LSA_MAGI_MELCHIOR_MODEL',
    'LSA_MAGI_MELCHIOR_ENDPOINT',
    'LSA_MAGI_MELCHIOR_CREDENTIAL_ENV',
    'LSA_MAGI_MELCHIOR_ENABLED',
    'LSA_MAGI_MELCHIOR_WEIGHT',
    'LSA_MAGI_MELCHIOR_TIMEOUT_SECONDS',
    'LSA_MAGI_MELCHIOR_CONTEXT_TOKENS',
    'LSA_MAGI_CASPER_PROVIDER',
    'LSA_MAGI_CASPER_MODEL',
    'LSA_MAGI_CASPER_ENDPOINT',
    'LSA_MAGI_CASPER_CREDENTIAL_ENV',
    'LSA_MAGI_CASPER_ENABLED',
    'LSA_MAGI_CASPER_WEIGHT',
    'LSA_MAGI_CASPER_TIMEOUT_SECONDS',
    'LSA_MAGI_CASPER_CONTEXT_TOKENS',
    'LSA_MAGI_BALTHASAR_PROVIDER',
    'LSA_MAGI_BALTHASAR_MODEL',
    'LSA_MAGI_BALTHASAR_ENDPOINT',
    'LSA_MAGI_BALTHASAR_CREDENTIAL_ENV',
    'LSA_MAGI_BALTHASAR_ENABLED',
    'LSA_MAGI_BALTHASAR_WEIGHT',
    'LSA_MAGI_BALTHASAR_TIMEOUT_SECONDS',
    'LSA_MAGI_BALTHASAR_CONTEXT_TOKENS'
)

$previousRuntimeEnv = @{}
$loadedRuntimeEnv = [System.Collections.Generic.List[string]]::new()
$seenRuntimeEnv = @{}
$locationPushed = $false

try {
    if (Test-Path -LiteralPath $runtimeEnvFile -PathType Leaf) {
        foreach ($rawLine in Get-Content -LiteralPath $runtimeEnvFile) {
            $line = $rawLine.Trim()
            if (-not $line -or $line.StartsWith('#')) { continue }

            $separator = $line.IndexOf('=')
            if ($separator -lt 1) { continue }

            $name = $line.Substring(0, $separator).Trim()
            if ($allowedRuntimeEnv -notcontains $name) { continue }
            if ($seenRuntimeEnv.ContainsKey($name)) {
                throw "Duplicate allowed runtime environment variable in .env: $name"
            }
            $seenRuntimeEnv[$name] = $true

            $value = $line.Substring($separator + 1).Trim()
            $previousRuntimeEnv[$name] = [Environment]::GetEnvironmentVariable(
                $name, [EnvironmentVariableTarget]::Process
            )
            [Environment]::SetEnvironmentVariable(
                $name, $value, [EnvironmentVariableTarget]::Process
            )
            $loadedRuntimeEnv.Add($name)
        }
    }

    $env:LSA_PKB_DAILY_PORT = "$port"
    $env:LSA_PKB_DAILY_SECRET = $secret
    Push-Location $root
    $locationPushed = $true

    & $python -m pkb_proto.daily_pkb
    if ($LASTEXITCODE -ne 0) { throw 'Daily PKB Web UI stopped with an error.' }
} finally {
    Remove-Item Env:LSA_PKB_DAILY_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:LSA_PKB_DAILY_SECRET -ErrorAction SilentlyContinue

    foreach ($name in $loadedRuntimeEnv) {
        [Environment]::SetEnvironmentVariable(
            $name, $previousRuntimeEnv[$name], [EnvironmentVariableTarget]::Process
        )
    }

    if ($locationPushed) {
        Pop-Location
    }
}
