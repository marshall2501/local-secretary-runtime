#Requires -Version 5.1
<#
.SYNOPSIS
  Non-destructive bootstrap for Personal Local Secretary AI runtime.
.DESCRIPTION
  Creates only missing directories and a missing .env from .env.example.
  It does not install software, pull images, run containers, or alter other
  repositories. Existing .env / files are never overwritten.
.EXAMPLE
  .\scripts\setup\setup-runtime.ps1 -WhatIf
.EXAMPLE
  .\scripts\setup\setup-runtime.ps1 -RunDoctor
#>
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='Low')]
param(
    [string]$DataRoot,
    [string]$ModelsRoot,
    [switch]$RunDoctor
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RuntimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$ExamplePath = Join-Path $RuntimeRoot '.env.example'
$EnvPath = Join-Path $RuntimeRoot '.env'
$DoctorPath = Join-Path $RuntimeRoot 'scripts\doctor\doctor.ps1'
$script:Warnings = 0
$script:Errors = 0

function Info([string]$message) { Write-Host "[INFO] $message" -ForegroundColor Cyan }
function Ok([string]$message) { Write-Host "[OK]   $message" -ForegroundColor Green }
function Warn([string]$message) {
    $script:Warnings++
    Write-Host "[WARN] $message" -ForegroundColor Yellow
}
function Fail([string]$message) {
    $script:Errors++
    Write-Host "[NG]   $message" -ForegroundColor Red
}
function Get-TemplateValue([string]$key) {
    if (-not (Test-Path -LiteralPath $ExamplePath -PathType Leaf)) { return $null }
    $pattern = '^\s*' + [regex]::Escape($key) + '\s*=\s*(.*?)\s*$'
    foreach ($line in [IO.File]::ReadAllLines($ExamplePath)) {
        if ($line -match $pattern) { return $Matches[1].Trim('"').Trim("'") }
    }
    return $null
}
function Get-LocalValue([string]$key) {
    if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) { return $null }
    $pattern = '^\s*' + [regex]::Escape($key) + '\s*=\s*(.*?)\s*$'
    foreach ($line in [IO.File]::ReadAllLines($EnvPath)) {
        if ($line -match $pattern) { return $Matches[1].Trim('"').Trim("'") }
    }
    return $null
}
function Ensure-Directory([string]$path) {
    if (Test-Path -LiteralPath $path -PathType Container) { Ok "Already exists: $path"; return }
    if (Test-Path -LiteralPath $path) { Fail "Expected directory but found a file: $path"; return }
    if ($PSCmdlet.ShouldProcess($path, 'Create directory')) {
        try {
            New-Item -ItemType Directory -Path $path -Force -ErrorAction Stop | Out-Null
            Ok "Created: $path"
        } catch { Fail "Cannot create $path : $($_.Exception.Message)" }
    }
}
function Check-Tool([string]$name, [string]$cmd, [bool]$optional) {
    $found = Get-Command -Name $cmd -ErrorAction SilentlyContinue
    if ($found) { Ok "$name found: $($found.Source)" }
    elseif ($optional) { Info "TODO: $name not installed (optional at this stage)." }
    else { Warn "$name not found; install it before starting the prototype." }
}

Info "Local Secretary Runtime setup"
Info "Runtime root: $RuntimeRoot"
Info "Existing files and other Docker projects will not be changed."

if (-not (Test-Path -LiteralPath $ExamplePath -PathType Leaf)) {
    Fail "Missing required template: $ExamplePath"
} elseif (-not (Test-Path -LiteralPath $EnvPath)) {
    if ($PSCmdlet.ShouldProcess($EnvPath, 'Copy .env.example to .env')) {
        try {
            Copy-Item -LiteralPath $ExamplePath -Destination $EnvPath -ErrorAction Stop
            Ok 'Created local .env from .env.example; edit local values and secrets.'
        } catch { Fail "Cannot create .env: $($_.Exception.Message)" }
    }
} elseif (Test-Path -LiteralPath $EnvPath -PathType Leaf) {
    Ok '.env exists; preserved unchanged.'
} else {
    Fail '.env exists but is not a regular file.'
}

$root = Get-LocalValue 'LSA_ROOT'
if (-not $root) { $root = Get-TemplateValue 'LSA_ROOT' }
if (-not $root) { $root = 'D:\AI' }
if (-not $DataRoot) { $DataRoot = Get-LocalValue 'LSA_DATA_DIR' }
if (-not $DataRoot) { $DataRoot = Get-TemplateValue 'LSA_DATA_DIR' }
if (-not $DataRoot) { $DataRoot = Join-Path $root 'data' }
if (-not $ModelsRoot) { $ModelsRoot = Get-LocalValue 'LSA_MODELS_DIR' }
if (-not $ModelsRoot) { $ModelsRoot = Get-TemplateValue 'LSA_MODELS_DIR' }
if (-not $ModelsRoot) { $ModelsRoot = Join-Path $root 'models' }
$BackupRoot = Get-LocalValue 'LSA_BACKUP_DIR'
if (-not $BackupRoot) { $BackupRoot = Get-TemplateValue 'LSA_BACKUP_DIR' }
if (-not $BackupRoot) { $BackupRoot = Join-Path $DataRoot 'backup' }

Info "Data root: $DataRoot"
Info "Models root: $ModelsRoot"
# The doctor checks these directories. Keep them even if later code uses a
# different layout; no other project directories are created or removed.
$required = @(
    $DataRoot,
    (Join-Path $DataRoot 'raw'),
    (Join-Path $DataRoot 'import'),
    (Join-Path $DataRoot 'export'),
    (Join-Path $DataRoot 'processed'),
    $BackupRoot,
    $ModelsRoot,
    (Join-Path $root 'sandbox'),
    (Join-Path $root 'projects'),
    (Join-Path $root 'projects\playground'),
    (Join-Path $RuntimeRoot 'scripts\setup'),
    (Join-Path $RuntimeRoot 'scripts\doctor'),
    (Join-Path $RuntimeRoot 'docker'),
    (Join-Path $RuntimeRoot 'n8n'),
    (Join-Path $RuntimeRoot 'python'),
    (Join-Path $RuntimeRoot 'config'),
    (Join-Path $RuntimeRoot 'api'),
    (Join-Path $RuntimeRoot 'tests')
)
foreach ($path in ($required | Select-Object -Unique)) { Ensure-Directory $path }

foreach ($profile in @('main-pc.example.env','sub-pc.example.env')) {
    $path = Join-Path $RuntimeRoot ("config\profiles\" + $profile)
    if (Test-Path -LiteralPath $path -PathType Leaf) { Ok "Profile template exists: $profile" }
    else { Warn "Missing profile template: $path" }
}
Check-Tool 'Git' 'git' $false
Check-Tool 'Docker' 'docker' $false
Check-Tool 'Python' 'python' $false
Check-Tool 'Node.js' 'node' $true
Check-Tool 'Ollama' 'ollama' $true
$designRepo = Join-Path $root 'projects\local-secretary-ai'
if (Test-Path -LiteralPath $designRepo -PathType Container) { Ok "Design repo directory: $designRepo" }
else { Warn "Design repo not found at $designRepo; clone separately if needed." }

if ($RunDoctor) {
    if ($WhatIfPreference) {
        Info 'WhatIf: doctor execution skipped.'
    } elseif (Test-Path -LiteralPath $DoctorPath -PathType Leaf) {
        Info 'Running existing doctor.ps1 (read-only diagnostic).'
        & $DoctorPath
    } else {
        Warn "Doctor script not found: $DoctorPath"
    }
} else {
    Info 'Next: .\scripts\doctor\doctor.ps1'
}
Write-Host ("Setup complete. Warnings: {0}; Errors: {1}" -f $script:Warnings,$script:Errors)
if ($script:Errors -gt 0) { exit 1 }
