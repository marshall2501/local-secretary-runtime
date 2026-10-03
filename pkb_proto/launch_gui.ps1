# One-time GUI launcher: subsequent runs can double-click Launch-PKB-GUI.cmd.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$fixture = Join-Path $repo 'interfaces\workbench\fixtures\episodes.json'
if (-not (Test-Path -LiteralPath $fixture -PathType Leaf)) {
    throw 'Bundled fictional episodes are missing.'
}
$runtimePython = 'D:\AI\projects\local-secretary-runtime\.venv\Scripts\python.exe'
$python = $null
if (Test-Path -LiteralPath $runtimePython -PathType Leaf) {
    & $runtimePython -c 'import tkinter'
    if ($LASTEXITCODE -eq 0) { $python = $runtimePython }
}
if ($null -eq $python) {
    & py -3.12 -c 'import tkinter'
    if ($LASTEXITCODE -ne 0) {
        throw 'Python with tkinter is required. No packages were installed or downloaded.'
    }
    $python = 'py'
}
Push-Location $repo
try {
    if ($python -eq 'py') {
        & py -3.12 -m interfaces.workbench.desktop
    } else {
        & $python -m pkb_proto.gui
    }
    if ($LASTEXITCODE -ne 0) { throw 'PKB GUI failed to start or exited with an error.' }
} finally {
    Pop-Location
}
