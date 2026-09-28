# Local development Web Workbench. Never installs packages or opens network ports.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$venv = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venv -PathType Leaf) {
    $python = $venv
    $pythonArgs = @()
} else {
    $python = 'py'
    $pythonArgs = @('-3.12')
}
& $python @pythonArgs -c 'import nicegui, fastapi; print("Web dependencies present")'
if ($LASTEXITCODE -ne 0) {
    throw 'NiceGUI is missing. Install requirements-web.txt explicitly in the chosen Python environment; this launcher does not install anything.'
}
Push-Location $root
try {
    & $python @pythonArgs -m pkb_proto.web_workbench
    if ($LASTEXITCODE -ne 0) { throw 'Local Workbench stopped with an error.' }
} finally {
    Pop-Location
}
