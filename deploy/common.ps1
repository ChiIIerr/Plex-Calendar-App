# Shared helpers for Windows PowerShell 5.1 and PowerShell 7.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-MachinePythonExecutables([string]$Version) {
    foreach ($root in @('HKLM:\SOFTWARE\Python\PythonCore', 'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore')) {
        $install = Get-ItemProperty -LiteralPath (Join-Path $root "$Version\InstallPath") -ErrorAction SilentlyContinue
        if ($install) {
            $executable = $install.PSObject.Properties['ExecutablePath']
            $directory = $install.PSObject.Properties['(default)']
            if ($executable -and $executable.Value) { [string]$executable.Value }
            elseif ($directory -and $directory.Value) { Join-Path $directory.Value 'python.exe' }
        }
    }
}

function Test-ServicePythonExecutable([string]$PythonExe) {
    try {
        $details = & $PythonExe -c 'import json,struct,sys; print(json.dumps([sys.base_prefix, sys.version_info.major, sys.version_info.minor, struct.calcsize(''P'')*8]))' 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $details) { return $false }
        $info = ConvertFrom-Json -InputObject ([string]$details)
        if ($info.Count -ne 4 -or $info[1] -lt 3 -or ($info[1] -eq 3 -and $info[2] -lt 12) -or $info[3] -ne 64) { return $false }
        $basePrefix = [IO.Path]::GetFullPath([string]$info[0]).TrimEnd('\')
        # A different user's profile is also unavailable to the boot service.
        foreach ($profileRoot in @($env:USERPROFILE, (Split-Path $env:USERPROFILE -Parent))) {
            if ($basePrefix -eq $profileRoot -or $basePrefix.StartsWith($profileRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $false }
        }
        return $true
    } catch { return $false }
}

function Resolve-PythonExecutable([string]$PythonExe) {
    if ($PythonExe) {
        $resolved = & $PythonExe -c 'import sys; print(sys.executable)'
        if ($LASTEXITCODE -eq 0 -and $resolved) {
            if (Test-ServicePythonExecutable ([string]$resolved)) { return [string]$resolved }
            throw 'The selected Python cannot be used by Local Service. Pass -PythonExe with a 64-bit Python 3.12 or newer installation outside user profiles.'
        }
    } else {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        foreach ($version in @('3.14', '3.13', '3.12')) {
            # The launcher may hide a machine install behind a per-user install.
            foreach ($candidate in (Get-MachinePythonExecutables $version)) {
                try {
                    $resolved = & $candidate -c 'import sys; print(sys.executable)' 2>$null
                    if ($LASTEXITCODE -eq 0 -and $resolved -and (Test-ServicePythonExecutable ([string]$resolved))) { return [string]$resolved }
                } catch { }
            }
            if ($launcher) {
                try {
                    $resolved = & py.exe "-$version" -c 'import sys; print(sys.executable)' 2>$null
                    if ($LASTEXITCODE -eq 0 -and $resolved -and (Test-ServicePythonExecutable ([string]$resolved))) { return [string]$resolved }
                } catch { }
            }
        }
        if (Get-Command python.exe -ErrorAction SilentlyContinue) {
            $resolved = & python.exe -c 'import sys; print(sys.executable)'
            if ($LASTEXITCODE -eq 0 -and $resolved -and (Test-ServicePythonExecutable ([string]$resolved))) { return [string]$resolved }
        }
    }
    throw 'Python was not found for Local Service. Install 64-bit Python 3.14 for all users or pass -PythonExe with its full path. Per-user installations cannot be used for boot startup; Python 3.12 and 3.13 are also supported.'
}

function Initialize-PythonEnvironment([string]$PythonExe, [string]$InstallDir) {
    $installRoot = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    $environmentDir = [IO.Path]::GetFullPath((Join-Path $installRoot '.venv'))
    # --clear may delete only the dedicated environment beneath this installation.
    if ($environmentDir -ne ($installRoot + '\.venv')) { throw 'Invalid Python environment path.' }
    $serverPython = Join-Path $environmentDir 'Scripts\python.exe'
    $versionCode = 'import sys; print(sys.version_info[:2])'
    $selectedVersion = & $PythonExe -c $versionCode
    if ($LASTEXITCODE -ne 0 -or -not $selectedVersion) { throw 'Could not determine the selected Python version.' }
    $installedVersion = $null
    if (Test-Path -LiteralPath $serverPython) {
        try {
            $installedVersion = & $serverPython -c $versionCode 2>$null
            if ($LASTEXITCODE -ne 0) { $installedVersion = $null }
        } catch { $installedVersion = $null }
    }
    if ($installedVersion -ne $selectedVersion) {
        # Reinstall dependencies for the new ABI; retain configuration/data in StateDir.
        & $PythonExe -m venv --clear $environmentDir | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment. Your data is preserved; rerun the installer.' }
    }
    return $serverPython
}

function Get-PreviousProductName {
    $compatibility = Join-Path (Split-Path $PSScriptRoot -Parent) 'app\legacy.json'
    return (Get-Content -LiteralPath $compatibility -Raw -Encoding UTF8 | ConvertFrom-Json).previous_name
}

function Resolve-StateDirectory([string]$StateDir) {
    if ($StateDir -eq (Join-Path $env:ProgramData 'Calendarr') -and -not (Test-Path -LiteralPath (Join-Path $StateDir 'installation.json'))) {
        $previous = Join-Path $env:ProgramData (Get-PreviousProductName)
        if (Test-Path -LiteralPath (Join-Path $previous 'installation.json')) { return $previous }
    }
    return $StateDir
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Open PowerShell as Administrator, then run this command again.'
    }
}

function Get-Installation([string]$StateDir) {
    $StateDir = Resolve-StateDirectory $StateDir
    $metadata = Join-Path $StateDir 'installation.json'
    if (-not (Test-Path -LiteralPath $metadata)) {
        throw "Calendarr is not installed at $StateDir. Run deploy\install.ps1 first."
    }
    return Get-Content -LiteralPath $metadata -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Get-CalendarrTask([string]$TaskName) {
    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
    $descriptions = @('Calendarr Plex Calendar managed startup.', "$(Get-PreviousProductName) Plex Calendar managed startup.")
    if ($task -and $task.Description -notin $descriptions) {
        throw "The task '$TaskName' belongs to another application. Choose another TaskName."
    }
    return $task
}

function Stop-CalendarrTask([string]$TaskName) {
    $task = Get-CalendarrTask $TaskName
    if ($task -and $task.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $TaskName -TaskPath '\'
        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-CalendarrTask $TaskName).State -eq 'Running') {
            if ((Get-Date) -gt $deadline) { throw "Timed out stopping task '$TaskName'." }
            Start-Sleep -Milliseconds 200
        }
    }
}

function Write-JsonFile([string]$Path, $Value) {
    $text = $Value | ConvertTo-Json -Depth 10
    [IO.File]::WriteAllText($Path, $text, (New-Object Text.UTF8Encoding($false)))
}

function Protect-Directory([string]$Path, [string]$ServiceRights) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($entry in @(@('S-1-5-18', 'FullControl'), @('S-1-5-32-544', 'FullControl'), @('S-1-5-19', $ServiceRights))) {
        $sid = New-Object Security.Principal.SecurityIdentifier($entry[0])
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, $entry[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
    # Remove old explicit grants on descendants; inherit this protected root.
    if (Get-ChildItem -LiteralPath $Path -Force | Select-Object -First 1) {
        & icacls.exe (Join-Path $Path '*') /reset /T /Q | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Unable to protect all files in $Path." }
    }
}

function Wait-Calendarr([string]$ConfigPath, [string]$TaskName) {
    $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $hostAddress = $config.bind_host
    if ($hostAddress -eq '0.0.0.0') { $hostAddress = '127.0.0.1' }
    if ($hostAddress -eq '::') { $hostAddress = '::1' }
    if ($hostAddress.Contains(':')) { $hostAddress = "[$hostAddress]" }
    $address = "http://${hostAddress}:$($config.port)/healthz"
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $task = Get-CalendarrTask $TaskName
        if ($task -and $task.State -eq 'Running') {
            try {
                $response = Invoke-RestMethod -Uri $address -TimeoutSec 2
                if ($response.status -eq 'ok') { return }
            } catch { }
        }
        Start-Sleep -Seconds 1
    }
    throw "Calendarr did not become ready. Check $($config.log_dir)\server.log and Task Scheduler's Last Run Result. For startup, Python must be installed for all users."
}
