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

Push-Location -LiteralPath $root
try {
    & $python -m bootstrap.secretary_server
    if ($LASTEXITCODE -ne 0) {
        throw "Secretary Server exited with code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
