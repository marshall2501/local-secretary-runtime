# setup-local-secretary-ai.ps1
# Creates the recommended project structure under D:\AI

$Root = "D:\AI"

$dirs = @(
    "$Root\data\raw\chatgpt",
    "$Root\data\raw\notes",
    "$Root\data\raw\logs",
    "$Root\data\raw\screenshots",
    "$Root\data\import",
    "$Root\data\export",
    "$Root\data\processed",
    "$Root\data\backup",

    "$Root\models",
    "$Root\sandbox",

    "$Root\projects\playground",

    "$Root\projects\local-secretary-ai\docs\00_Project",
    "$Root\projects\local-secretary-ai\docs\01_Architecture",
    "$Root\projects\local-secretary-ai\docs\02_Database",
    "$Root\projects\local-secretary-ai\docs\03_Workflows",
    "$Root\projects\local-secretary-ai\docs\04_AI",
    "$Root\projects\local-secretary-ai\docs\05_Implementation",
    "$Root\projects\local-secretary-ai\docs\06_Research",
    "$Root\projects\local-secretary-ai\docs\07_Meetings",
    "$Root\projects\local-secretary-ai\docs\99_Archive",
    "$Root\projects\local-secretary-ai\prompts",
    "$Root\projects\local-secretary-ai\templates",
    "$Root\projects\local-secretary-ai\diagrams",
    "$Root\projects\local-secretary-ai\decisions",

    "$Root\projects\local-secretary-runtime\docker",
    "$Root\projects\local-secretary-runtime\n8n",
    "$Root\projects\local-secretary-runtime\python",
    "$Root\projects\local-secretary-runtime\scripts",
    "$Root\projects\local-secretary-runtime\config",
    "$Root\projects\local-secretary-runtime\api",
    "$Root\projects\local-secretary-runtime\tests"
)

foreach($d in $dirs){
    if(-not (Test-Path $d)){
        New-Item -ItemType Directory -Path $d -Force | Out-Null
        Write-Host "[CREATE] $d"
    } else {
        Write-Host "[EXISTS] $d"
    }
}

$files = @{
    "$Root\projects\local-secretary-ai\README.md"="# Local Secretary AI`n";
    "$Root\projects\local-secretary-ai\CHANGELOG.md"="# Changelog`n";
    "$Root\projects\local-secretary-ai\.gitignore"=@"
# Python
__pycache__/
*.pyc
.venv/

# Environment
.env
.env.*

# Logs
*.log

# Data
/data/raw/
/data/backup/
/data/export/
/data/processed/

# Models
/models/

# IDE
.vscode/
.idea/

# OS
Thumbs.db
.DS_Store
"@;
    "$Root\projects\local-secretary-runtime\README.md"="# Local Secretary Runtime`n";
    "$Root\projects\local-secretary-runtime\requirements.txt"="";
}

foreach($f in $files.Keys){
    if(-not (Test-Path $f)){
        $files[$f] | Set-Content -Path $f -Encoding UTF8
        Write-Host "[FILE] $f"
    }
}

Write-Host ""
Write-Host "Done."
Write-Host "Next:"
Write-Host "  cd D:\AI\projects\local-secretary-ai"
Write-Host "  git init"
Write-Host "  cd ..\local-secretary-runtime"
Write-Host "  git init"
