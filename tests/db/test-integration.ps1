# Starts only a randomly named test project; data is synthetic. Requires Docker Desktop Linux engine.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$dbScript = Join-Path $root 'scripts/db/postgres.ps1'
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project .venv Python not found. Create the runtime virtual environment first.' }
$project = 'local-secretary-test-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$testEnvFile = Join-Path $root ('.env.postgres.' + $project + '.tmp')
$probe = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
try { $probe.Start(); $testPort = ([Net.IPEndPoint]$probe.LocalEndpoint).Port } finally { $probe.Stop() }
[IO.File]::WriteAllText($testEnvFile, "LSA_DB_PORT=$testPort`n", (New-Object Text.UTF8Encoding($false)))
$env:LSA_TEST_DB_ENV_FILE = $testEnvFile
$compose = @('compose','--project-name',$project,'--project-directory',(Join-Path $root 'docker'),'--env-file',
    $testEnvFile,'-f',(Join-Path $root 'docker/compose.postgres.yml'))
$backup = Join-Path $root "backups/$project.dump"
function Invoke-TestDocker([string[]]$a) {
    & docker @a
    if ($LASTEXITCODE -ne 0) { throw "Docker failed: $($a[0])" }
}
function Run-Sql([string]$file,[string]$database) {
    Invoke-TestDocker @('cp',$file,"${script:container}:/tmp/test.sql")
    Invoke-TestDocker ($compose + @('exec','-T','secretary-postgres','psql','-X','-U','secretary_admin','-d',$database,
        '-v','ON_ERROR_STOP=1','-f','/tmp/test.sql'))
}
function Snapshot-Others {
    $ids = @(Invoke-TestDocker @('ps','-aq'))
    $result = @()
    foreach ($id in $ids) {
        $d = (Invoke-TestDocker @('inspect',$id) | Out-String | ConvertFrom-Json)[0]
        if ($d.Config.Labels.'com.docker.compose.project' -ne $project) {
            $result += "$($d.Id)|$($d.State.Status)|$($d.State.StartedAt)|$($d.RestartCount)"
        }
    }
    return ($result | Sort-Object) -join "`n"
}
$before = Snapshot-Others
try {
    # Prove a bound port fails before creating a container.
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $testPort)
    $listener.Server.ExclusiveAddressUse = $true
    try {
        $listener.Start()
        $rejected = $false
        try { & $dbScript -Action Start -Project $project -EnvFile $testEnvFile } catch {
            if ($_.Exception.Message -notlike '*is unavailable*') { throw }
            $rejected = $true
        }
        if (-not $rejected) { throw 'Port conflict was not rejected.' }
        if (@(Invoke-TestDocker @('ps','-aq','--filter',"label=com.docker.compose.project=$project")).Count -ne 0) {
            throw 'Port-conflict preflight created containers.'
        }
    } finally { $listener.Stop() }
    & $dbScript -Action Start -Project $project -EnvFile $testEnvFile
    & $dbScript -Action Migrate -Project $project -EnvFile $testEnvFile
    & $dbScript -Action Migrate -Project $project -EnvFile $testEnvFile
    $script:container = (Invoke-TestDocker ($compose + @('ps','-q','secretary-postgres')) | Out-String).Trim()
    # Corrupt only this synthetic project's ledger; applied-file drift must fail closed.
    Run-Sql (Join-Path $PSScriptRoot 'corrupt-history.sql') 'secretary'
    $rejected = $false
    try { & $dbScript -Action Migrate -Project $project -EnvFile $testEnvFile } catch { $rejected = $true }
    if (-not $rejected) { throw 'Migration checksum drift was not rejected.' }
    Invoke-TestDocker @('cp',(Join-Path $PSScriptRoot 'repair-test-history.sql'),"${script:container}:/tmp/repair.sql")
    $checksum = (Get-FileHash (Join-Path $root 'db/migrations/001_memory.sql') -Algorithm SHA256).Hash.ToLowerInvariant()
    Invoke-TestDocker ($compose + @('exec','-T','secretary-postgres','psql','-X','-U','secretary_admin','-d','secretary',
        '-v','ON_ERROR_STOP=1','-v',"checksum=$checksum",'-f','/tmp/repair.sql'))
    Run-Sql (Join-Path $PSScriptRoot 'assertions.sql') 'secretary'
    # The same freshly migrated disposable DB also exercises the RITSUKO
    # external Action repository, not the in-memory test double.
    & $python (Join-Path $PSScriptRoot 'verify-external-action.py') $project $script:container
    if ($LASTEXITCODE -ne 0) { throw 'Isolated external Action PostgreSQL verification failed.' }
    # Policy migration 009 is verified against the same disposable DB.
    & $python (Join-Path $PSScriptRoot 'verify-runtime-policy.py') $project $script:container
    if ($LASTEXITCODE -ne 0) { throw 'Isolated runtime policy PostgreSQL verification failed.' }
    & $python (Join-Path $PSScriptRoot 'verify-durable-task-start.py') $project $script:container
    if ($LASTEXITCODE -ne 0) { throw 'Isolated durable Task PostgreSQL verification failed.' }
    & $python (Join-Path $PSScriptRoot 'verify-schema-diagram.py')
    if ($LASTEXITCODE -ne 0) { throw 'Schema diagram introspection verification failed.' }
    & $dbScript -Action Backup -Project $project -EnvFile $testEnvFile -BackupPath $backup
    Invoke-TestDocker ($compose + @('restart','secretary-postgres'))
    Invoke-TestDocker ($compose + @('up','-d','--wait','--wait-timeout','120','secretary-postgres'))
    Run-Sql (Join-Path $PSScriptRoot 'verify-persistence.sql') 'secretary'
    & $dbScript -Action Restore -Project $project -EnvFile $testEnvFile -BackupPath $backup -RestoreDatabase 'secretary_restore_test'
    Run-Sql (Join-Path $PSScriptRoot 'verify-persistence.sql') 'secretary_restore_test'
    & $dbScript -Action Doctor -Project $project -EnvFile $testEnvFile
    # The restore command must reject a pre-existing destination database.
    $rejected = $false
    try { & $dbScript -Action Restore -Project $project -EnvFile $testEnvFile -BackupPath $backup -RestoreDatabase 'secretary_restore_test' }
    catch { $rejected = $true }
    if (-not $rejected) { throw 'Existing restore destination was not rejected.' }
    & $python (Join-Path $PSScriptRoot 'verify-observation-sources.py')
    if ($LASTEXITCODE -ne 0) { throw 'Observation source persistence verification failed.' }
    Write-Host 'PASS: port conflict, migrations/reapply/drift rejection, constraints/roles, restart, binary backup/restore, overwrite refusal.'
} finally {
    # Exactly the random test project created by this run; never prune or target another project.
    Invoke-TestDocker ($compose + @('down','--volumes'))
    if ((Snapshot-Others) -ne $before) { throw 'Other container state changed during test; investigate.' }
    Remove-Item -LiteralPath $testEnvFile -ErrorAction SilentlyContinue
    Remove-Item Env:LSA_TEST_DB_ENV_FILE -ErrorAction SilentlyContinue
    Write-Host 'PASS: other container IDs, status, start times and restart counts unchanged.'
}
