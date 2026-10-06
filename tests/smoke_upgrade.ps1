#Requires -Version 5.1
# Build an earlier default installation in disposable Windows CI state.
$ErrorActionPreference = 'Stop'
if ($env:CI -ne 'true') { throw 'This check requires disposable CI state.' }
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $root 'deploy\common.ps1')
$previousName = Get-PreviousProductName
$stateDir = Join-Path $env:ProgramData $previousName
$installDir = Join-Path $env:ProgramFiles $previousName
$pythonExe = (Get-Command python.exe).Source
foreach ($path in @($stateDir, $installDir, (Join-Path $env:ProgramData 'Calendarr'))) {
    if (Test-Path $path) { throw 'Upgrade test directories are already in use.' }
}
try {
    Protect-Directory $stateDir 'Modify'
    Protect-Directory $installDir 'ReadAndExecute'
    [IO.File]::WriteAllText((Join-Path $installDir ('.' + $previousName.ToLowerInvariant() + '-installation')), "$previousName native Windows installation")
    $configPath = Join-Path $stateDir 'server.json'
    Write-JsonFile $configPath ([ordered]@{ bind_host = '127.0.0.1'; port = 18283; public_url = 'http://localhost:18283'; trusted_proxies = '127.0.0.1,::1'; data_dir = (Join-Path $stateDir 'data'); log_dir = (Join-Path $stateDir 'logs') })
    Write-JsonFile (Join-Path $stateDir 'installation.json') ([ordered]@{ install_dir = $installDir; task_name = $previousName; config_path = $configPath })
    & $pythonExe (Join-Path $root 'tests\seed_upgrade.py') seed $stateDir
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the upgrade fixture.' }
    $keyBefore = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $stateDir 'data\encryption.key')))
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument '/c exit 0' -WorkingDirectory $installDir
    $principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount
    Register-ScheduledTask -TaskName $previousName -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $principal -Description "$previousName Plex Calendar managed startup." | Out-Null
    # No path/task parameters: detect defaults and reuse the original storage.
    & (Join-Path $root 'deploy\install.ps1') -PythonExe $pythonExe
    $installed = Get-Installation (Join-Path $env:ProgramData 'Calendarr')
    if ($installed.task_name -ne 'Calendarr' -or $installed.install_dir -ne $installDir) { throw 'Existing installation was not recognized.' }
    if (Get-ScheduledTask -TaskName $previousName -ErrorAction SilentlyContinue) { throw 'Previous startup task was not removed.' }
    if (-not (Get-ScheduledTask -TaskName 'Calendarr' -ErrorAction SilentlyContinue)) { throw 'New startup task was not registered.' }
    & $pythonExe (Join-Path $root 'tests\seed_upgrade.py') verify $stateDir
    if ($LASTEXITCODE -ne 0) { throw 'Upgrade verification failed.' }
    $keyAfter = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $stateDir 'data\encryption.key')))
    if ($keyAfter -ne $keyBefore) { throw 'Upgrade replaced the encryption key.' }
    # Repeating the update and resolving state through the default must remain safe.
    & (Join-Path $root 'deploy\install.ps1') -PythonExe $pythonExe
    & (Join-Path $root 'deploy\start.ps1') -Restart
    & $pythonExe (Join-Path $root 'tests\seed_upgrade.py') verify $stateDir
    if ($LASTEXITCODE -ne 0) { throw 'Repeated update lost persistent data.' }
    Write-Host 'Previous installation detection, task rename, restart, and repeated upgrade checks passed.'
} finally {
    foreach ($name in @('Calendarr', $previousName)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
        }
    }
    if (Test-Path $stateDir) {
        Get-ChildItem $stateDir -Filter '*.log' -Recurse | ForEach-Object { Get-Content $_.FullName -Encoding UTF8 -Tail 20 }
    }
}
