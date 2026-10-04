[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BackupPath,
    [switch]$SkipSettings
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($SkipSettings) {
    throw 'The legacy partial-promotion path is retired. Clean rebuild always restores the daily runtime data needed by the current system.'
}

$rebuild = Join-Path $PSScriptRoot 'rebuild-production.ps1'
if (-not (Test-Path -LiteralPath $rebuild -PathType Leaf)) {
    throw 'Missing clean production rebuild workflow.'
}

Write-Warning 'promote-production.ps1 is retained only as a compatibility entrypoint. Running clean rebuild instead.'
& $rebuild -BackupPath $BackupPath
if ($LASTEXITCODE -ne 0) {
    throw 'Clean production rebuild failed.'
}
