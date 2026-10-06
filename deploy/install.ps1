#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'Calendarr'),
    [string]$StateDir = (Join-Path $env:ProgramData 'Calendarr'),
    [string]$PythonExe,
    [ValidatePattern('^[A-Za-z0-9_.-]{1,64}$')][string]$TaskName = 'Calendarr',
    [ValidateRange(1, 65535)][int]$Port = 8282,
    [string]$PublicUrl,
    [switch]$NoStart
)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator
$sourceDir = Split-Path $PSScriptRoot -Parent
if (-not $PSBoundParameters.ContainsKey('StateDir')) { $StateDir = Resolve-StateDirectory $StateDir }
$previousTaskName = $null
if (Test-Path -LiteralPath (Join-Path $StateDir 'installation.json')) {
    $existing = Get-Installation $StateDir
    if ($existing.PSObject.Properties.Name -contains 'previous_task_name') {
        $previousTaskName = $existing.previous_task_name
        if ($previousTaskName -ne (Get-PreviousProductName)) { throw 'Invalid previous startup task in installation metadata.' }
        $null = Get-CalendarrTask $previousTaskName
    }
    if (-not $PSBoundParameters.ContainsKey('InstallDir')) { $InstallDir = $existing.install_dir }
    if (-not $PSBoundParameters.ContainsKey('TaskName') -and $existing.task_name -ne (Get-PreviousProductName)) { $TaskName = $existing.task_name }
    if ($existing.task_name -ne $TaskName) {
        $previousTask = Get-CalendarrTask $existing.task_name
        if ($existing.task_name -ne (Get-PreviousProductName) -or ($previousTask -and $previousTask.Description -ne "$(Get-PreviousProductName) Plex Calendar managed startup.")) {
            throw 'Use the existing TaskName for an update.'
        }
        $previousTaskName = $existing.task_name
    }
}
$InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$StateDir = [IO.Path]::GetFullPath($StateDir).TrimEnd('\')
foreach ($target in @($InstallDir, $StateDir)) {
    if ($target -eq [IO.Path]::GetPathRoot($target).TrimEnd('\') -or $target -eq $sourceDir -or $sourceDir.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase) -or $target.StartsWith($sourceDir + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'InstallDir and StateDir must be dedicated directories outside the source checkout.'
    }
}
if ($InstallDir.StartsWith($StateDir + '\', [StringComparison]::OrdinalIgnoreCase) -or $StateDir.StartsWith($InstallDir + '\', [StringComparison]::OrdinalIgnoreCase) -or $StateDir -eq $InstallDir) {
    throw 'InstallDir and StateDir must be separate, non-nested directories.'
}
if (Test-Path -LiteralPath (Join-Path $StateDir 'installation.json')) {
    $existing = Get-Installation $StateDir
    if ($existing.install_dir -ne $InstallDir) {
        throw 'Use the existing InstallDir and TaskName for an update.'
    }
}
$null = Get-CalendarrTask $TaskName

# Never replace files or permissions in an unrelated existing directory.
$marker = Join-Path $InstallDir '.calendarr-installation'
if ((Test-Path -LiteralPath $InstallDir) -and (Get-ChildItem -LiteralPath $InstallDir -Force | Select-Object -First 1)) {
    $validMarker = $false
    foreach ($product in @('Calendarr', (Get-PreviousProductName))) {
        $candidateMarker = Join-Path $InstallDir ('.' + $product.ToLowerInvariant() + '-installation')
        if ((Test-Path -LiteralPath $candidateMarker) -and (Get-Content -LiteralPath $candidateMarker -Raw).Trim() -eq "$product native Windows installation") { $validMarker = $true }
    }
    if (-not $validMarker) {
        throw 'InstallDir is not empty and is not a Calendarr installation. Choose a new dedicated directory.'
    }
}
if ((Test-Path -LiteralPath $StateDir) -and (Get-ChildItem -LiteralPath $StateDir -Force | Select-Object -First 1) -and -not (Test-Path -LiteralPath (Join-Path $StateDir 'installation.json'))) {
    throw 'StateDir is not empty and has no Calendarr installation record. Choose a new dedicated directory.'
}

# Resolve a real executable; the startup task never depends on a PATH or launcher.
if ($PythonExe) {
    $resolved = & $PythonExe -c 'import sys; print(sys.executable)'
} elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $resolved = & py.exe -3.12 -c 'import sys; print(sys.executable)'
} elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    $resolved = & python.exe -c 'import sys; print(sys.executable)'
} else {
    throw 'Install 64-bit Python 3.12 for all users, including the Python launcher, then run this installer again.'
}
if ($LASTEXITCODE -ne 0 -or -not $resolved -or -not (Test-Path -LiteralPath ([string]$resolved))) {
    throw 'Python was not found. Install Python 3.12 for all users or pass -PythonExe with its full path.'
}
$PythonExe = [string]$resolved
& $PythonExe -c "import sys,struct; sys.exit(0 if sys.version_info >= (3, 12) and struct.calcsize('P') == 8 else 1)"
if ($LASTEXITCODE -ne 0) { throw '64-bit Python 3.12 or newer is required; Python 3.12 is recommended.' }
$basePrefix = & $PythonExe -c 'import sys; print(sys.base_prefix)'
if ($basePrefix.StartsWith($env:USERPROFILE + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'This is a per-user Python installation. Install Python for all users so Local Service can run it before sign-in.'
}

Stop-CalendarrTask $TaskName
if ($previousTaskName) { Stop-CalendarrTask $previousTaskName }
Protect-Directory $InstallDir 'ReadAndExecute'
Protect-Directory $StateDir 'Modify'
[IO.File]::WriteAllText($marker, 'Calendarr native Windows installation')
# Keep enough metadata to permit retrying a partially completed installation.
$configPath = Join-Path $StateDir 'server.json'
$metadata = [ordered]@{ install_dir = $InstallDir; task_name = $TaskName; config_path = $configPath }
if ($previousTaskName) { $metadata.previous_task_name = $previousTaskName }
Write-JsonFile (Join-Path $StateDir 'installation.json') $metadata
foreach ($folder in @('app', 'deploy')) {
    $destination = Join-Path $InstallDir $folder
    if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
    Copy-Item -LiteralPath (Join-Path $sourceDir $folder) -Destination $InstallDir -Recurse
}
foreach ($file in @('requirements.txt', 'requirements.lock', 'README.md')) {
    Copy-Item -LiteralPath (Join-Path $sourceDir $file) -Destination $InstallDir -Force
}
$serverPython = Join-Path $InstallDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $serverPython)) {
    & $PythonExe -m venv (Join-Path $InstallDir '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& $serverPython -m pip install --disable-pip-version-check -r (Join-Path $InstallDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check internet access and rerun the installer. Your data is preserved.' }

if (Test-Path -LiteralPath $configPath) {
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($PSBoundParameters.ContainsKey('Port')) { $config.port = $Port }
    if ($PSBoundParameters.ContainsKey('PublicUrl')) { $config.public_url = $PublicUrl }
} else {
    if (-not $PublicUrl) { $PublicUrl = "http://localhost:$Port" }
    $config = [ordered]@{
        bind_host = '127.0.0.1'; port = $Port; public_url = $PublicUrl
        trusted_proxies = '127.0.0.1,::1'
        data_dir = (Join-Path $StateDir 'data'); log_dir = (Join-Path $StateDir 'logs')
    }
}
$candidate = Join-Path $StateDir 'server.pending.json'
Write-JsonFile $candidate $config
Push-Location $InstallDir
try {
    & $serverPython -m app.server --config $candidate --check
    if ($LASTEXITCODE -ne 0) { throw 'Server configuration is invalid. Fix server.json and rerun the installer.' }
    $paths = & $serverPython -c "import json,sys; from app.server import load_config; c=load_config(sys.argv[1]); print(json.dumps([c['data_dir'],c['log_dir']]))" $candidate
    if ($LASTEXITCODE -ne 0) { throw 'Could not validate storage paths.' }
    foreach ($directory in ($paths | ConvertFrom-Json)) {
        if (-not $directory.StartsWith($StateDir + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Installed data_dir and log_dir must be inside StateDir so their permissions can be protected.'
        }
    }
    Move-Item -LiteralPath $candidate -Destination $configPath -Force
    Protect-Directory $InstallDir 'ReadAndExecute'
    Protect-Directory $StateDir 'Modify'
    # Detect a conflicting listener before registering or starting this task.
    & $serverPython -c "import socket,sys; from app.server import load_config; c=load_config(sys.argv[1]); s=socket.socket(socket.AF_INET6 if ':' in c['bind_host'] else socket.AF_INET); s.bind((c['bind_host'],c['port'])); s.close()" $configPath
    if ($LASTEXITCODE -ne 0) { throw 'The configured address/port is unavailable. Change port in server.json and rerun the installer.' }
} finally { Pop-Location }

$action = New-ScheduledTaskAction -Execute $serverPython -Argument "-m app.server --config `"$configPath`"" -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Calendarr Plex Calendar managed startup.' -Force | Out-Null
if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName -TaskPath '\'
    Wait-Calendarr $configPath $TaskName
}
if ($previousTaskName -and (Get-CalendarrTask $previousTaskName)) {
    Unregister-ScheduledTask -TaskName $previousTaskName -TaskPath '\' -Confirm:$false
}
Write-JsonFile (Join-Path $StateDir 'installation.json') ([ordered]@{ install_dir = $InstallDir; task_name = $TaskName; config_path = $configPath })
Write-Host "Installed Calendarr at $InstallDir"
Write-Host "Startup task: $TaskName (runs before sign-in as Local Service)"
Write-Host "Open $($config.public_url)"
Write-Host "Configuration: $configPath"
Write-Host "First-time setup token: $($config.data_dir)\setup-token (created when the server first starts)"
Write-Host "Logs: $($config.log_dir)\server.log"
