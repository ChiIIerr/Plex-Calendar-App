#Requires -Version 5.1
# Runs only on disposable GitHub-hosted Windows runners.
$ErrorActionPreference = 'Stop'
if ($env:CI -ne 'true') { throw 'This check requires disposable CI state.' }
$root = Split-Path $PSScriptRoot -Parent
$installDir = Join-Path $env:ProgramFiles ('Calendarr CI App ' + [char]0xE9)
$stateDir = Join-Path $env:ProgramData ('Calendarr CI State ' + [char]0xFC)
$taskName = 'Calendarr-CI'
$pythonExe = (Get-Command python.exe).Source

# Parse every script with the actual Windows PowerShell 5.1 parser.
Get-ChildItem (Join-Path $root 'deploy') -Filter '*.ps1' | ForEach-Object {
    $tokens = $null
    $errors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) { throw ($errors | Out-String) }
}
try {
    if ($env:CALENDARR_BASELINE_PYTHON) {
        # Seed a 3.12 environment so the 3.14 job exercises an actual runtime upgrade.
        if (Test-Path -LiteralPath $installDir) { throw 'Runtime upgrade test directory is already in use.' }
        New-Item -ItemType Directory -Path $installDir | Out-Null
        [IO.File]::WriteAllText((Join-Path $installDir '.calendarr-installation'), 'Calendarr native Windows installation')
        & $env:CALENDARR_BASELINE_PYTHON -m venv (Join-Path $installDir '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python 3.12 upgrade fixture.' }
        & (Join-Path $installDir '.venv\Scripts\python.exe') -m pip install -r (Join-Path $root 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Could not install the Python 3.12 upgrade fixture dependencies.' }
    }
    & (Join-Path $root 'deploy\install.ps1') -InstallDir $installDir -StateDir $stateDir -TaskName $taskName -PythonExe $pythonExe -Port 18282
    $versionCode = 'import sys; print(sys.version_info[:2])'
    $expectedVersion = & $pythonExe -c $versionCode
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the selected Python version.' }
    $installedVersion = & (Join-Path $installDir '.venv\Scripts\python.exe') -c $versionCode
    if ($LASTEXITCODE -ne 0 -or $installedVersion -ne $expectedVersion) { throw 'Installer retained the wrong Python runtime.' }
    $task = Get-ScheduledTask -TaskName $taskName
    $account = $task.Principal.UserId
    if ($account.StartsWith('S-1-')) {
        $accountSid = $account
    } else {
        $accountSid = (New-Object Security.Principal.NTAccount($account)).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    if ($accountSid -ne 'S-1-5-19') { throw "Wrong task account: $account ($accountSid)." }
    if ($task.Settings.ExecutionTimeLimit -ne 'PT0S') { throw 'Startup task has a runtime limit.' }
    if ($task.Triggers.CimClass.CimClassName -notcontains 'MSFT_TaskBootTrigger') { throw 'Missing boot trigger.' }
    if ($task.Settings.RestartCount -ne 5) { throw 'Missing failure recovery.' }
    if ($task.Actions.Execute -ne (Join-Path $installDir '.venv\Scripts\python.exe')) { throw 'Wrong task executable.' }
    foreach ($path in @($installDir, $stateDir, (Join-Path $stateDir 'data\encryption.key'))) {
        $acl = Get-Acl -LiteralPath $path
        $sids = @($acl.Access | ForEach-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value })
        if ($sids -contains 'S-1-5-32-545' -or $sids -contains 'S-1-1-0' -or $sids -contains 'S-1-5-11') { throw "Unprotected path: $path" }
        if ($sids -notcontains 'S-1-5-19') { throw "Local Service has no access to $path" }
    }
    & $pythonExe (Join-Path $root 'tests\smoke_native.py') --windows-state $stateDir
    if ($LASTEXITCODE -ne 0) { throw 'Installed application smoke check failed.' }
    & (Join-Path $root 'deploy\remove-startup.ps1') -StateDir $stateDir
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Startup removal failed.' }
    if (-not (Test-Path (Join-Path $stateDir 'data\calendarr.sqlite3'))) { throw 'Startup removal deleted data.' }
    Write-Host 'Windows installation, task configuration, ACL, update, and startup removal checks passed.'
} finally {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    if (Test-Path $stateDir) {
        Get-ChildItem $stateDir -Filter '*.log' -Recurse | ForEach-Object { Get-Content $_.FullName -Encoding UTF8 -Tail 40 }
    }
}
