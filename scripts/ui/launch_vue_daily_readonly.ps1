# Provenance-Origin: original
# Provenance-AI: AI-generated
# Vue日常UI移行中の実Task読取専用プレビュー。
# 明示起動のみ。NiceGUI/API/PVAを停止せず、DBへ変更操作を行わない。
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$python = Join-Path $root '.venv\Scripts\python.exe'
$envFile = Join-Path $root '.env.postgres'
$secret = Join-Path $root 'secrets\secretary-daily-runtime-password.txt'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Missing existing Python virtual environment.'
}
if (-not (Test-Path -LiteralPath $secret -PathType Leaf)) {
    throw 'Missing daily runtime secret file.'
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw 'Missing .env.postgres.'
}
$matches = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($matches.Count -ne 1) { throw 'Expected exactly one LSA_DB_PORT setting.' }
$port = [int]($matches[0] -replace '^LSA_DB_PORT=', '')
if ($port -lt 1024 -or $port -gt 65535) { throw 'Invalid database port.' }

# 実在する運用DB接続境界を再利用する。資格情報の値は表示しない。
$values = @{
    LSA_DAILY_DB_MODE = 'production'
    LSA_DAILY_DB_NAME = 'secretary'
    LSA_PKB_DAILY_PORT = "$port"
    LSA_PKB_DAILY_SECRET = "$secret"
}
$previous = @{}
foreach ($key in $values.Keys) {
    $previous[$key] = [Environment]::GetEnvironmentVariable(
        $key, [EnvironmentVariableTarget]::Process
    )
}
try {
    foreach ($key in $values.Keys) {
        [Environment]::SetEnvironmentVariable(
            $key, $values[$key], [EnvironmentVariableTarget]::Process
        )
    }
    Push-Location $root
    try {
        & $python -m interfaces.vue_daily.app
        if ($LASTEXITCODE -ne 0) {
            throw 'Vue daily read-only preview stopped with an error.'
        }
    } finally {
        Pop-Location
    }
} finally {
    foreach ($key in $previous.Keys) {
        [Environment]::SetEnvironmentVariable(
            $key, $previous[$key], [EnvironmentVariableTarget]::Process
        )
    }
}
