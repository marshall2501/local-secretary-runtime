# Provenance-Origin: original
# Provenance-AI: AI-generated
# Vue日常UI移行中の実Task読取専用プレビュー。
# この入口は既存NiceGUIやAPIを置き換えず、8097に読取専用画面を追加起動する。
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
$portLines = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($portLines.Count -ne 1) { throw 'Expected exactly one LSA_DB_PORT setting.' }
$port = [int]($portLines[0] -replace '^LSA_DB_PORT=', '')
if ($port -lt 1024 -or $port -gt 65535) { throw 'Invalid database port.' }

# 普段のTaskを参照するため既存の日常DB接続先を使う。Secretの中身は表示・複製しない。
$values = @{
    LSA_DAILY_DB_MODE = 'production'
    LSA_DAILY_DB_NAME = 'secretary'
    LSA_PKB_DAILY_PORT = "$port"
    LSA_PKB_DAILY_SECRET = "$secret"
}
# 実行中だけ環境変数を上書きし、終了・例外時には呼び出し元の値へ戻す。
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
