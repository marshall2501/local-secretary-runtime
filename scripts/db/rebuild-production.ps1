[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BackupPath,
    [ValidatePattern('^secretary_rebuild_[a-z0-9_]{1,40}$')]
    [string]$ReplacementDatabase
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$composeFile = Join-Path $root 'docker/compose.postgres.yml'
$composeDirectory = Split-Path $composeFile
$envFile = Join-Path $root '.env.postgres'
$service = 'secretary-postgres'
$adminSecret = Join-Path $root 'secrets/postgres-password.txt'
$runtimeSecret = Join-Path $root 'secrets/secretary-daily-runtime-password.txt'
$python = Join-Path $root '.venv/Scripts/python.exe'
$dataTransfer = Join-Path $root 'scripts/db/production_data_transfer.py'
$runtimeRunner = Join-Path $root 'scripts/db/run_production_runtime_rehearsal.py'
$provisionRuntime = Join-Path $root 'scripts/db/provision_daily_runtime.py'
$BackupPath = [IO.Path]::GetFullPath($BackupPath)

foreach ($needed in @(
    $BackupPath,$composeFile,$envFile,$adminSecret,$python,
    $dataTransfer,$runtimeRunner,$provisionRuntime
)) {
    if (-not (Test-Path -LiteralPath $needed -PathType Leaf)) {
        throw "Required rebuild input is missing: $needed"
    }
}

if (-not $ReplacementDatabase) {
    $ReplacementDatabase = 'secretary_rebuild_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
}
if ($ReplacementDatabase.Length -gt 63) {
    throw 'Replacement database name must not exceed 63 characters.'
}

$portLines = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^LSA_DB_PORT=[0-9]+$' })
if ($portLines.Count -ne 1) { throw 'Cannot determine the live Secretary PostgreSQL port.' }
$livePort = [int]($portLines[0] -replace '^LSA_DB_PORT=', '')
if ($livePort -lt 1024 -or $livePort -gt 65535) { throw 'Invalid live PostgreSQL port.' }

function Invoke-Docker {
    param([string[]]$DockerArgs)
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed: $($DockerArgs -join ' ')"
    }
}

function Get-FreePort {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    } finally {
        $listener.Stop()
    }
}

$liveIds = @(Invoke-Docker @(
    'ps','-q',
    '--filter','label=com.docker.compose.project=local-secretary-runtime-db',
    '--filter','label=com.docker.compose.service=secretary-postgres'
))
if ($liveIds.Count -ne 1) { throw 'Expected one owned live Secretary PostgreSQL container.' }
$liveContainer = $liveIds[0]
$liveInfo = (Invoke-Docker @('inspect',$liveContainer) | Out-String | ConvertFrom-Json)[0]
if ($liveInfo.State.Health.Status -ne 'healthy') { throw 'Live Secretary PostgreSQL is not healthy.' }

$existingReplacement = @(
    Invoke-Docker @(
        'exec',$liveContainer,'psql','-X','-q','-A','-t',
        '-U','secretary_admin','-d','postgres','-v','ON_ERROR_STOP=1',
        '-c',"SELECT count(*) FROM pg_database WHERE datname='$ReplacementDatabase';"
    )
)
if (($existingReplacement | Out-String).Trim() -ne '0') {
    throw "Replacement database already exists: $ReplacementDatabase"
}

$project = 'local-secretary-test-rebuild-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$tempPort = Get-FreePort
$oldPort = $env:LSA_DB_PORT
$env:LSA_DB_PORT = "$tempPort"
$composeBase = @(
    'compose','--project-name',$project,'--project-directory',$composeDirectory,
    '--env-file',$envFile,'-f',$composeFile
)

function Invoke-TempCompose {
    param([string[]]$ComposeArgs)
    Invoke-Docker ($composeBase + $ComposeArgs)
}

$tempHostDump = Join-Path ([IO.Path]::GetTempPath()) (
    'lsa-rebuild-' + [guid]::NewGuid().ToString('N') + '.dump'
)
$tempRemoteSourceBackup = '/tmp/lsa-source-' + [guid]::NewGuid().ToString('N') + '.dump'
$tempRemoteRebuiltDump = '/tmp/lsa-rebuilt-' + [guid]::NewGuid().ToString('N') + '.dump'
$liveRemoteRebuiltDump = '/tmp/lsa-rebuilt-' + [guid]::NewGuid().ToString('N') + '.dump'
$replacementCreated = $false
$completed = $false

try {
    Invoke-TempCompose @('up','-d','--wait','--wait-timeout','120',$service)
    $tempIds = @(Invoke-TempCompose @('ps','-q',$service))
    if ($tempIds.Count -ne 1) { throw 'Expected one temporary rebuild PostgreSQL container.' }
    $tempContainer = $tempIds[0]

    Invoke-Docker @('cp',$BackupPath,"$($tempContainer):$tempRemoteSourceBackup")
    Invoke-TempCompose @('exec','-T',$service,'pg_restore','--list',$tempRemoteSourceBackup) | Out-Null

    Invoke-TempCompose @('exec','-T',$service,'sh','/opt/secretary/scripts/migrate.sh')

    $migrationState = @(
        Invoke-TempCompose @(
            'exec','-T',$service,'psql','-X','-q','-A','-t',
            '-U','secretary_admin','-d','secretary','-v','ON_ERROR_STOP=1',
            '-c',"SELECT count(*)::text || '|' || max(version) FROM secretary.schema_migrations;"
        )
    )
    if (($migrationState | Out-String).Trim() -ne '8|008_runtime_privileges.sql') {
        throw "Unexpected rebuilt schema migration state: $(($migrationState | Out-String).Trim())"
    }

    Invoke-TempCompose @(
        'exec','-T',$service,'pg_restore',
        '-U','secretary_admin','-d','secretary',
        '--data-only','--disable-triggers','--exit-on-error','--no-owner','--no-acl',
        '--exclude-table-data=secretary.schema_migrations',
        $tempRemoteSourceBackup
    )

    $transferArgs = @(
        $dataTransfer,
        '--source-port',"$livePort",
        '--target-port',"$tempPort",
        '--secret-file',$adminSecret,
        '--commit'
    )
    & $python @transferArgs
    if ($LASTEXITCODE -ne 0) { throw 'Selected daily data transfer into rebuilt database failed.' }

    Invoke-TempCompose @(
        'exec','-T',$service,'pg_dump',
        '-U','secretary_admin','-d','secretary',
        '--format=custom','--no-owner','--no-acl',
        '--file',$tempRemoteRebuiltDump
    )
    Invoke-TempCompose @('exec','-T',$service,'pg_restore','--list',$tempRemoteRebuiltDump) | Out-Null

    $runtimeArgs = @(
        $runtimeRunner,
        '--target-port',"$tempPort",
        '--live-port',"$livePort",
        '--admin-secret-file',$adminSecret,
        '--settings-source-port',"$livePort"
    )
    & $python @runtimeArgs
    if ($LASTEXITCODE -ne 0) {
        throw 'Improved runtime verification failed against the rebuilt temporary database.'
    }

    Invoke-Docker @('cp',"$($tempContainer):$tempRemoteRebuiltDump",$tempHostDump)

    Invoke-Docker @(
        'exec',$liveContainer,'createdb','-U','secretary_admin',
        '--template=template0',$ReplacementDatabase
    )
    $replacementCreated = $true

    Invoke-Docker @('cp',$tempHostDump,"$($liveContainer):$liveRemoteRebuiltDump")
    Invoke-Docker @(
        'exec',$liveContainer,'pg_restore',
        '-U','secretary_admin','-d',$ReplacementDatabase,
        '--single-transaction','--exit-on-error','--no-owner','--no-acl',
        $liveRemoteRebuiltDump
    )

    $provisionArgs = @(
        $provisionRuntime,
        '--port',"$livePort",
        '--database',$ReplacementDatabase,
        '--admin-secret-file',$adminSecret,
        '--runtime-secret-file',$runtimeSecret
    )
    & $python @provisionArgs
    if ($LASTEXITCODE -ne 0) { throw 'Daily runtime provisioning on replacement database failed.' }

    $replacementState = @(
        Invoke-Docker @(
            'exec',$liveContainer,'psql','-X','-q','-A','-t',
            '-U','secretary_admin','-d',$ReplacementDatabase,'-v','ON_ERROR_STOP=1',
            '-c',"SELECT count(*)::text || '|' || max(version) FROM secretary.schema_migrations;"
        )
    )
    if (($replacementState | Out-String).Trim() -ne '8|008_runtime_privileges.sql') {
        throw "Replacement database migration state is unexpected: $(($replacementState | Out-String).Trim())"
    }

    $completed = $true
    Write-Host ''
    Write-Host '=== Clean production rebuild ==='
    [pscustomobject]@{
        ReplacementDatabase = $ReplacementDatabase
        LivePort = $livePort
        ImprovedRuntimeVerifiedInDisposableDB = $true
        OldSecretaryPreserved = $true
        IsolatedSourcePreserved = $true
        ReplacementProvisioned = $true
    } | Format-List
    Write-Host 'PASS: clean replacement database created from the improved runtime schema and required daily data.'
    Write-Host 'The existing secretary database was not replaced or renamed.'
} finally {
    try {
        if (Test-Path -LiteralPath $tempHostDump) {
            Remove-Item -LiteralPath $tempHostDump -Force
        }
        try {
            Invoke-Docker @('exec',$liveContainer,'rm','-f',$liveRemoteRebuiltDump)
        } catch {
            if ($completed) { throw }
        }
        if ($replacementCreated -and -not $completed) {
            try {
                Invoke-Docker @(
                    'exec',$liveContainer,'dropdb','-U','secretary_admin',
                    '--if-exists','--force',$ReplacementDatabase
                )
            } catch {
                Write-Warning "Failed to remove incomplete replacement database $ReplacementDatabase."
            }
        }
    } finally {
        try {
            $env:LSA_DB_PORT = "$tempPort"
            Invoke-TempCompose @('down','--volumes')
        } finally {
            $env:LSA_DB_PORT = $oldPort
        }
    }
}
