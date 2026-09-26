$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$errorsFound = @()
Get-ChildItem (Join-Path $root 'scripts'),(Join-Path $root 'tests') -Recurse -Filter '*.ps1' | ForEach-Object {
    $tokens = $null; $parseErrors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$tokens,[ref]$parseErrors)
    $errorsFound += $parseErrors
}
if ($errorsFound.Count) { throw ($errorsFound | Out-String) }
Push-Location $root
try {
    foreach ($path in @('.env.postgres','secrets/postgres-password.txt','backups/test.dump','docker/volumes/test','models/test.gguf')) {
        git check-ignore -q -- $path
        if ($LASTEXITCODE -ne 0) { throw "Sensitive path is not ignored: $path" }
    }
    git diff --check
    if ($LASTEXITCODE -ne 0) { throw 'Whitespace validation failed.' }
} finally { Pop-Location }
Write-Host 'PASS: PowerShell parsing, sensitive-path ignore rules, diff whitespace.'
