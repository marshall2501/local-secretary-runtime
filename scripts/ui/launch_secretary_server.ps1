# Provenance-Origin: original; Provenance-AI: AI-generated
# Canonical Secretary Server entry: Vue + Task/API + PKB on one process.
# The existing user-facing CMD is switched only at the accepted cutover.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Expected Secretary Python virtualenv; no packages installed automatically.'
}

# Fail visibly before creating a second server when the old 8097 listener exists.
# This does not stop, replace, or probe any other project's process.
$port = 8097
if ($env:LSA_SECRETARY_PORT) {
    $parsedPort = 0
    if (-not [int]::TryParse($env:LSA_SECRETARY_PORT, [ref]$parsedPort) -or $parsedPort -lt 1024 -or $parsedPort -gt 65535) {
        throw 'LSA_SECRETARY_PORT must be a local TCP port from 1024 to 65535.'
    }
    $port = $parsedPort
}
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "TCP port $port is already in use. Stop only the identified old Secretary Vue server before starting the unified server; no process was stopped."
}

Push-Location -LiteralPath $root
try {
    & $python -m bootstrap.secretary_server
    if ($LASTEXITCODE -ne 0) {
        throw "Secretary Server exited with code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
