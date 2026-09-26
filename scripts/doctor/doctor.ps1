# doctor.ps1
# Environment diagnostic script for Local Secretary Runtime.

$ErrorActionPreference = "Continue"

$Root = "D:\AI"
$RuntimeRoot = "D:\AI\projects\local-secretary-runtime"
$DesignRoot = "D:\AI\projects\local-secretary-ai"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "==== $Title ====" -ForegroundColor Cyan
}

function Test-Command {
    param(
        [string]$Name,
        [string]$Command
    )

    $cmd = Get-Command $Command -ErrorAction SilentlyContinue
    if ($cmd) {
        Write-Host "[OK] $Name found: $($cmd.Source)" -ForegroundColor Green
        return $true
    } else {
        Write-Host "[NG] $Name not found" -ForegroundColor Red
        return $false
    }
}

function Test-PathStatus {
    param([string]$Path)

    if (Test-Path $Path) {
        Write-Host "[OK] $Path" -ForegroundColor Green
    } else {
        Write-Host "[NG] $Path" -ForegroundColor Red
    }
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

Write-Section "Commands"

$hasGit = Test-Command "Git" "git"
$hasDocker = Test-Command "Docker" "docker"
$hasPython = Test-Command "Python" "python"
$hasNode = Test-Command "Node.js" "node"
$hasOllama = Test-Command "Ollama" "ollama"

Write-Section "Versions"

if ($hasGit) {
    git --version
}

if ($hasDocker) {
    docker --version
    docker compose version
}

if ($hasPython) {
    python --version
}

if ($hasNode) {
    node --version
}

if ($hasOllama) {
    ollama --version
}

Write-Section "Docker Status"

if ($hasDocker) {
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
} else {
    Write-Host "Docker not available. Skipped." -ForegroundColor Yellow
}

Write-Section "Git Status: design repo"

if (Test-Path "$DesignRoot\.git") {
    Push-Location $DesignRoot
    git status --short
    git branch --show-current
    git remote -v
    Pop-Location
} else {
    Write-Host "No git repository found at $DesignRoot" -ForegroundColor Yellow
}

Write-Section "Git Status: runtime repo"

if (Test-Path "$RuntimeRoot\.git") {
    Push-Location $RuntimeRoot
    git status --short
    git branch --show-current
    git remote -v
    Pop-Location
} else {
    Write-Host "No git repository found at $RuntimeRoot" -ForegroundColor Yellow
}

Write-Section "Ollama Models"

if ($hasOllama) {
    ollama list
} else {
    Write-Host "Ollama not available. Skipped." -ForegroundColor Yellow
}

Write-Section "Summary"

Write-Host "Doctor check completed."
Write-Host "Review [NG] items above before continuing."
