[CmdletBinding()]
param(
    [ValidatePattern('^secretary_rebuild_[a-z0-9_]{1,40}$')]
    [string]$ReplacementDatabase
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$rebuild = Join-Path $PSScriptRoot 'rebuild-production.ps1'
if (-not (Test-Path -LiteralPath $rebuild -PathType Leaf)) {
    throw 'Missing fresh production rebuild workflow.'
}

Write-Warning 'promote-production.ps1 is retained only as a compatibility entrypoint. No dump restore is performed.'
if ($ReplacementDatabase) {
    & $rebuild -ReplacementDatabase $ReplacementDatabase
} else {
    & $rebuild
}
