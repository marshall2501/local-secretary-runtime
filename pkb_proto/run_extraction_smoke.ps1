# Read-only local extraction observation for the ten fictional PKB episodes.
# No user/private data, expected.json, production DB, or cloud endpoint is used.
[CmdletBinding()]
param([string]$Model = 'qwen3.5:9b')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$worktree = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = 'D:\AI\projects\local-secretary-runtime\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'The existing runtime virtualenv Python was not found.'
}
if ($Model -notmatch '^[A-Za-z0-9_.:/-]+$') { throw 'Invalid local model name.' }
try {
    $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 10
} catch {
    throw 'Local Ollama at 127.0.0.1:11434 is unavailable. No model download was attempted.'
}
$installed = @($tags.models | ForEach-Object { $_.name })
if ($installed -notcontains $Model) {
    Write-Host ('Installed local models: ' + ($installed -join ', '))
    throw "Required local model $Model was not found. No automatic download or cloud fallback."
}
Push-Location $worktree
try {
    & $python -m unittest discover -s tests -p 'test_pkb*.py' -v
    if ($LASTEXITCODE -ne 0) { throw 'Offline PKB tests failed.' }
    & $python -m pkb_proto.run_local_extraction --model $Model
    if ($LASTEXITCODE -ne 0) { throw 'Local fictional extraction run failed.' }
} finally {
    Pop-Location
}
