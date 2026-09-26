[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Setup','Start','Doctor','Migrate','Backup','Restore')][string]$Action,
    [ValidatePattern('^local-secretary-(runtime-db|test-[a-z0-9-]+)$')][string]$Project = 'local-secretary-runtime-db',
    [ValidateRange(1024,65535)][int]$Port = 55432,
    [string]$BackupPath,
    [ValidatePattern('^secretary_restore_[a-z0-9_]+$')][string]$RestoreDatabase
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$composeFile = Join-Path $repoRoot 'docker/compose.postgres.yml'
$composeDirectory = Split-Path $composeFile
$envFile = Join-Path $repoRoot '.env.postgres'
$secretFile = Join-Path $repoRoot 'secrets/postgres-password.txt'
$service = 'secretary-postgres'

function Invoke-Docker {
    param([string[]]$DockerArgs)
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE)." }
}
function Invoke-Compose {
    param([string[]]$ComposeArgs)
    Invoke-Docker (@('compose','--project-name',$Project,'--project-directory',$composeDirectory,
        '--env-file',$envFile,'-f',$composeFile) + $ComposeArgs)
}
function Test-OwnedResources {
    # Never adopt an existing resource just because its name happens to match.
    $ids = @(Invoke-Docker @('ps','-aq','--filter',"label=com.docker.compose.project=$Project"))
    foreach ($id in $ids) {
        $details = (Invoke-Docker @('inspect',$id) | Out-String | ConvertFrom-Json)[0]
        $labels = $details.Config.Labels
        $actualDir = $labels.'com.docker.compose.project.working_dir'
        if (-not $actualDir -or [IO.Path]::GetFullPath($actualDir) -ne $composeDirectory -or
            $labels.'com.docker.compose.service' -ne $service) {
            throw "Project $Project belongs to another checkout/service. Refusing to change it."
        }
    }
    foreach ($kind in @('volume','network')) {
        $suffix = if ($kind -eq 'volume') { 'secretary_pgdata' } else { 'secretary_db' }
        $name = "${Project}_$suffix"
        $names = @(Invoke-Docker @($kind,'ls','--format','{{.Name}}'))
        if ($names -contains $name) {
            $details = (Invoke-Docker @($kind,'inspect',$name) | Out-String | ConvertFrom-Json)[0]
            if ($null -eq $details.Labels -or $details.Labels.'com.docker.compose.project' -ne $Project) {
                throw "Unowned $kind $name exists. Refusing to adopt it."
            }
            # Orphaned data cannot be tied to this checkout. Require manual inspection.
            if ($ids.Count -eq 0) { throw "Orphaned $kind $name exists. Inspect ownership before recreating containers." }
        }
    }
}
function Get-ContainerId {
    $ids = @(Invoke-Compose @('ps','-q',$service))
    if ($ids.Count -ne 1) { throw 'Start the dedicated PostgreSQL service first.' }
    return $ids[0]
}
function Test-Database {
    $id = Get-ContainerId
    $details = (Invoke-Docker @('inspect',$id) | Out-String | ConvertFrom-Json)[0]
    if ($details.State.Health.Status -ne 'healthy') { throw 'PostgreSQL health check is not healthy.' }
    $bindings = @($details.NetworkSettings.Ports.'5432/tcp')
    if ($bindings.Count -ne 1 -or $bindings[0].HostIp -ne '127.0.0.1' -or
        $bindings[0].HostPort -ne "$script:effectivePort") { throw 'Unexpected port binding.' }
    # Readiness alone does not test authentication; this connects using the secret over TCP.
    Invoke-Compose @('exec','-T',$service,'sh','/opt/secretary/scripts/check-auth.sh')
}

if ($Action -eq 'Setup') {
    # Setup is intentionally separate from the old .env and never overwrites credentials.
    if (-not (Test-Path -LiteralPath $envFile)) {
        [IO.File]::WriteAllText($envFile, "LSA_DB_PORT=$Port`n", (New-Object Text.UTF8Encoding($false)))
    }
    if (-not (Test-Path -LiteralPath $secretFile)) {
        $null = New-Item -ItemType Directory -Force (Split-Path $secretFile)
        $bytes = New-Object byte[] 48
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        [IO.File]::WriteAllText($secretFile, [Convert]::ToBase64String($bytes), (New-Object Text.UTF8Encoding($false)))
    }
    Write-Host 'PostgreSQL config/secret ready; existing files preserved. No containers started.'
    return
}
if (-not (Test-Path $envFile) -or -not (Test-Path $secretFile)) { throw 'Run -Action Setup first.' }
$lines = @(Get-Content -LiteralPath $envFile | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') })
if ($lines.Count -ne 1 -or $lines[0] -notmatch '^LSA_DB_PORT=([0-9]+)$') {
    throw '.env.postgres must contain only LSA_DB_PORT=<port> (plus comments).'
}
$script:effectivePort = [int]$Matches[1]
if ($script:effectivePort -lt 1024 -or $script:effectivePort -gt 65535) { throw 'Invalid configured port.' }
if ($PSBoundParameters.ContainsKey('Port') -and $Port -ne $script:effectivePort) {
    throw '-Port is only used by Setup; edit .env.postgres to change an existing configuration.'
}
if ((Get-Item $secretFile).Length -lt 32) { throw 'Secret file is too short; use a strong local password.' }
$savedPort = $env:LSA_DB_PORT
try {
    # Shell variables otherwise override --env-file. Only this dedicated key is changed temporarily.
    $env:LSA_DB_PORT = "$script:effectivePort"
    Invoke-Compose @('config','--quiet')
    Test-OwnedResources
    switch ($Action) {
        'Start' {
            $existing = @(Invoke-Compose @('ps','-q',$service))
            if ($existing.Count -gt 0) {
                Test-Database
                Write-Host 'Already healthy; no recreation performed.'
                break
            }
            $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $script:effectivePort)
            $listener.Server.ExclusiveAddressUse = $true
            try { $listener.Start() } catch { throw "Local port $script:effectivePort is unavailable. Choose a free port in .env.postgres. No service was started." }
            finally { $listener.Stop() }
            Invoke-Compose @('up','-d','--wait','--wait-timeout','120',$service)
            Test-Database
            Write-Host 'Database started. Run -Action Migrate next.'
        }
        'Doctor' {
            Test-Database
            Invoke-Compose @('exec','-T',$service,'psql','-X','-U','secretary_admin','-d','secretary',
                '-v','ON_ERROR_STOP=1','-c','TABLE secretary.schema_migrations;')
            Write-Host "OK: $Project, localhost:$script:effectivePort, authenticated SQL and migration history."
        }
        'Migrate' {
            Test-Database
            Invoke-Compose @('exec','-T',$service,'sh','/opt/secretary/scripts/migrate.sh')
        }
        'Backup' {
            Test-Database
            if (-not $BackupPath) {
                $BackupPath = Join-Path $repoRoot ("backups/secretary-{0}-{1}.dump" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), [guid]::NewGuid().ToString('N').Substring(0,8))
            }
            $BackupPath = [IO.Path]::GetFullPath($BackupPath)
            if (Test-Path -LiteralPath $BackupPath) { throw 'Backup destination exists; refusing overwrite.' }
            $null = New-Item -ItemType Directory -Force (Split-Path $BackupPath)
            $id = Get-ContainerId
            $remoteFile = '/tmp/secretary-' + [guid]::NewGuid().ToString('N') + '.dump'
            $partial = $BackupPath + '.partial'
            if (Test-Path -LiteralPath $partial) { throw 'Partial backup destination already exists.' }
            try {
                Invoke-Compose @('exec','-T',$service,'pg_dump','-U','secretary_admin','-d','secretary',
                    '--format=custom','--no-owner','--no-acl','--file',$remoteFile)
                Invoke-Compose @('exec','-T',$service,'pg_restore','--list',$remoteFile) | Out-Null
                # docker cp preserves binary data even on Windows PowerShell 5.1.
                Invoke-Docker @('cp',"${id}:$remoteFile",$partial)
                Move-Item -LiteralPath $partial -Destination $BackupPath
            } finally {
                Invoke-Compose @('exec','-T',$service,'rm','-f',$remoteFile)
            }
            Write-Host "Backup created: $BackupPath"
        }
        'Restore' {
            Test-Database
            if (-not $BackupPath -or -not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) { throw 'Supply an existing trusted -BackupPath.' }
            if (-not $RestoreDatabase) { throw 'Supply a NEW -RestoreDatabase secretary_restore_<name>.' }
            if ($RestoreDatabase.Length -gt 63) { throw 'Restore database name must not exceed 63 characters.' }
            $id = Get-ContainerId
            $remoteFile = '/tmp/secretary-' + [guid]::NewGuid().ToString('N') + '.dump'
            try {
                Invoke-Docker @('cp',[IO.Path]::GetFullPath($BackupPath),"${id}:$remoteFile")
                Invoke-Compose @('exec','-T',$service,'pg_restore','--list',$remoteFile) | Out-Null
                # createdb fails if the name exists: no --clean, DROP, overwrite, or live DB target.
                Invoke-Compose @('exec','-T',$service,'createdb','-U','secretary_admin','--template=template0',$RestoreDatabase)
                Invoke-Compose @('exec','-T',$service,'pg_restore','-U','secretary_admin','-d',$RestoreDatabase,
                    '--single-transaction','--exit-on-error','--no-owner','--no-acl',$remoteFile)
                Invoke-Compose @('exec','-T',$service,'psql','-X','-U','secretary_admin','-d',$RestoreDatabase,
                    '-v','ON_ERROR_STOP=1','-c','TABLE secretary.schema_migrations;')
            } finally {
                Invoke-Compose @('exec','-T',$service,'rm','-f',$remoteFile)
            }
            Write-Host "Restored to $RestoreDatabase. Live secretary DB unchanged. Validate data before any planned cutover."
        }
    }
} finally { $env:LSA_DB_PORT = $savedPort }
