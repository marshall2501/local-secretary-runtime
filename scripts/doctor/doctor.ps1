# doctor.ps1
# Environment diagnostic script for Local Secretary Runtime.

$ErrorActionPreference = "Continue"

$Root = "D:\AI"
$RuntimeRoot = "D:\AI\projects\local-secretary-runtime"
$DesignRoot = "D:\AI\projects\local-secretary-ai"

$script:OkCount = 0
$script:WarnCount = 0
$script:NgCount = 0
$script:TodoCount = 0

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "==== $Title ====" -ForegroundColor Cyan
}

function Add-OK {
    param([string]$Message)
    $script:OkCount++
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Add-WARN {
    param([string]$Message)
    $script:WarnCount++
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Add-NG {
    param([string]$Message)
    $script:NgCount++
    Write-Host "[NG] $Message" -ForegroundColor Red
}

function Add-TODO {
    param([string]$Message)
    $script:TodoCount++
    Write-Host "[TODO] $Message" -ForegroundColor Magenta
}

function Test-CommandStatus {
    param(
        [string]$Name,
        [string]$Command,
        [switch]$Optional
    )

    $cmd = Get-Command $Command -ErrorAction SilentlyContinue

    if (-not $cmd) {
        if ($Optional) {
            Add-TODO "$Name not found"
        } else {
            Add-NG "$Name not found"
        }
        return $false
    }

    Add-OK "$Name found: $($cmd.Source)"
    return $true
}

function Test-PathStatus {
    param([string]$Path)

    if (Test-Path $Path) {
        Add-OK $Path
    } else {
        Add-NG $Path
    }
}

function Test-VersionCommand {
    param(
        [string]$Name,
        [string]$Command,
        [string[]]$Arguments,
        [switch]$Optional
    )

    $cmd = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $cmd) {
        return $false
    }

    $output = & $Command @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String).Trim()

    if ($exitCode -ne 0 -or $text -match "was not found|Microsoft Store|install from the Microsoft Store") {
        Add-WARN "$Name command exists but is not usable: $($cmd.Source)"
        if ($text) {
            Write-Host $text -ForegroundColor DarkYellow
        }
        return $false
    }

    Add-OK "$Name version: $text"
    return $true
}

Write-Host "Local Secretary Runtime Doctor" -ForegroundColor Yellow
Write-Host "Generated at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

Write-Section "Folders"

$paths = @(
    $Root,
    "$Root\data",
    "$Root\data\raw",
    "$Root\data\import",
    "$Root\data\export",
    "$Root\data\processed",
    "$Root\data\backup",
    "$Root\models",
    "$Root\sandbox",
    "$Root\projects",
    "$Root\projects\playground",
    $DesignRoot,
    $RuntimeRoot,
    "$RuntimeRoot\scripts",
    "$RuntimeRoot\scripts\setup",
    "$RuntimeRoot\scripts\doctor",
    "$RuntimeRoot\docker",
    "$RuntimeRoot\n8n",
    "$RuntimeRoot\python",
    "$RuntimeRoot\config",
    "$RuntimeRoot\api",
    "$RuntimeRoot\tests"
)

foreach ($p in $paths) {
    Test-PathStatus $p
}

Write-Section "Environment Files"

$envExamplePath = Join-Path $RuntimeRoot ".env.example"
$envPath = Join-Path $RuntimeRoot ".env"
$mainProfilePath = Join-Path $RuntimeRoot "config\profiles\main-pc.example.env"
$subProfilePath = Join-Path $RuntimeRoot "config\profiles\sub-pc.example.env"

if (Test-Path $envExamplePath) {
    Add-OK ".env.example exists"
} else {
    Add-NG ".env.example is missing"
}

if (Test-Path $mainProfilePath) {
    Add-OK "main-pc example profile exists"
} else {
    Add-NG "main-pc example profile is missing"
}

if (Test-Path $subProfilePath) {
    Add-OK "sub-pc example profile exists"
} else {
    Add-NG "sub-pc example profile is missing"
}

if (Test-Path $envPath) {
    Add-OK ".env exists"
} else {
    Add-TODO ".env not created yet. Copy .env.example to .env and edit local values."
}

Write-Section "Commands"

$hasGit = Test-CommandStatus "Git" "git"
$hasDocker = Test-CommandStatus "Docker" "docker"
$hasPython = Test-CommandStatus "Python" "python"
$hasNode = Test-CommandStatus "Node.js" "node" -Optional
$hasOllama = Test-CommandStatus "Ollama" "ollama" -Optional

Write-Section "Versions"

if ($hasGit) {
    Test-VersionCommand "Git" "git" @("--version") | Out-Null
}

if ($hasDocker) {
    Test-VersionCommand "Docker" "docker" @("--version") | Out-Null
    Test-VersionCommand "Docker Compose" "docker" @("compose", "version") | Out-Null
}

if ($hasPython) {
    Test-VersionCommand "Python" "python" @("--version") | Out-Null
}

if ($hasNode) {
    Test-VersionCommand "Node.js" "node" @("--version") -Optional | Out-Null
}

if ($hasOllama) {
    Test-VersionCommand "Ollama" "ollama" @("--version") -Optional | Out-Null
}

Write-Section "Docker Status"

if ($hasDocker) {
    $containers = docker ps --format "{{.Names}}|{{.Status}}|{{.Ports}}" 2>&1

    if ($LASTEXITCODE -ne 0) {
        Add-WARN "docker ps failed"
        Write-Host ($containers | Out-String).Trim() -ForegroundColor DarkYellow
    } else {
        Write-Host "NAMES`tSTATUS`tPORTS"

        foreach ($line in $containers) {
            $parts = $line -split "\|", 3
            $name = $parts[0]
            $status = $parts[1]
            $ports = if ($parts.Count -ge 3) { $parts[2] } else { "" }

            Write-Host "$name`t$status`t$ports"

            if ($status -match "Restarting|Exited|unhealthy") {
                Add-WARN "Container attention needed: $name is $status"
            }
        }
    }
} else {
    Add-NG "Docker not available. Skipped docker status."
}

Write-Section "Git Status: design repo"

if (Test-Path "$DesignRoot\.git") {
    Push-Location $DesignRoot

    $status = git status --short
    $branch = git branch --show-current
    $remote = git remote -v

    Add-OK "Design repo branch: $branch"

    if ($status) {
        Add-WARN "Design repo has uncommitted changes"
        Write-Host $status -ForegroundColor DarkYellow
    } else {
        Add-OK "Design repo working tree clean"
    }

    Write-Host $remote

    Pop-Location
} else {
    Add-WARN "No git repository found at $DesignRoot"
}

Write-Section "Git Status: runtime repo"

if (Test-Path "$RuntimeRoot\.git") {
    Push-Location $RuntimeRoot

    $status = git status --short
    $branch = git branch --show-current
    $remote = git remote -v

    Add-OK "Runtime repo branch: $branch"

    if ($status) {
        Add-WARN "Runtime repo has uncommitted changes"
        Write-Host $status -ForegroundColor DarkYellow
    } else {
        Add-OK "Runtime repo working tree clean"
    }

    Write-Host $remote

    Pop-Location
} else {
    Add-WARN "No git repository found at $RuntimeRoot"
}

Write-Section "Ollama Models"

if ($hasOllama) {
    $models = ollama list 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host $models
    } else {
        Add-WARN "ollama list failed"
        Write-Host ($models | Out-String).Trim() -ForegroundColor DarkYellow
    }
} else {
    Add-TODO "Ollama not installed yet. Needed later for local LLM."
}

Write-Section "Summary"

Write-Host "OK:   $script:OkCount" -ForegroundColor Green
Write-Host "WARN: $script:WarnCount" -ForegroundColor Yellow
Write-Host "NG:   $script:NgCount" -ForegroundColor Red
Write-Host "TODO: $script:TodoCount" -ForegroundColor Magenta

if ($script:NgCount -gt 0) {
    Write-Host "Result: NG items must be fixed before continuing." -ForegroundColor Red
} elseif ($script:WarnCount -gt 0) {
    Write-Host "Result: Usable, but warnings should be reviewed." -ForegroundColor Yellow
} else {
    Write-Host "Result: Looks good." -ForegroundColor Green
}
