# Shared helpers for Windows PowerShell 5.1 and PowerShell 7.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Open PowerShell as Administrator, then run this command again.'
    }
}

function Get-Installation([string]$StateDir) {
    $metadata = Join-Path $StateDir 'installation.json'
    if (-not (Test-Path -LiteralPath $metadata)) {
        throw "Reelarr is not installed at $StateDir. Run deploy\install.ps1 first."
    }
    return Get-Content -LiteralPath $metadata -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Get-ReelarrTask([string]$TaskName) {
    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($task -and $task.Description -ne 'Reelarr Plex Calendar managed startup.') {
        throw "The task '$TaskName' belongs to another application. Choose another TaskName."
    }
    return $task
}

function Stop-ReelarrTask([string]$TaskName) {
    $task = Get-ReelarrTask $TaskName
    if ($task -and $task.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $TaskName -TaskPath '\'
        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-ReelarrTask $TaskName).State -eq 'Running') {
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

function Wait-Reelarr([string]$ConfigPath, [string]$TaskName) {
    $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $hostAddress = $config.bind_host
    if ($hostAddress -eq '0.0.0.0') { $hostAddress = '127.0.0.1' }
    if ($hostAddress -eq '::') { $hostAddress = '::1' }
    if ($hostAddress.Contains(':')) { $hostAddress = "[$hostAddress]" }
    $address = "http://${hostAddress}:$($config.port)/healthz"
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $task = Get-ReelarrTask $TaskName
        if ($task -and $task.State -eq 'Running') {
            try {
                $response = Invoke-RestMethod -Uri $address -TimeoutSec 2
                if ($response.status -eq 'ok') { return }
            } catch { }
        }
        Start-Sleep -Seconds 1
    }
    throw "Reelarr did not become ready. Check $($config.log_dir)\server.log and Task Scheduler's Last Run Result. For startup, Python must be installed for all users."
}
