# Compatibility launcher. Canonical launcher: scripts/ui/launch_daily_pkb.ps1
[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$target=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\scripts\ui\launch_daily_pkb.ps1'))
& $target
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
