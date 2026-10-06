#Requires -Version 5.1
[CmdletBinding()]
param([string]$StateDir = (Join-Path $env:ProgramData 'Reelarr'), [switch]$Restart, [switch]$Foreground)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator
$installed = Get-Installation $StateDir
$task = Get-ReelarrTask $installed.task_name
if ($Restart -or $Foreground) { Stop-ReelarrTask $installed.task_name }
if ($Foreground) {
    Push-Location $installed.install_dir
    try {
        & (Join-Path $installed.install_dir '.venv\Scripts\python.exe') -m app.server --config $installed.config_path
        if ($LASTEXITCODE -ne 0) { throw 'Reelarr exited with an error. Check server.log.' }
    } finally { Pop-Location }
} else {
    if (-not $task) { throw 'Startup is not registered. Run the installer again.' }
    Start-ScheduledTask -TaskName $installed.task_name -TaskPath '\'
    Wait-Reelarr $installed.config_path $installed.task_name
    Write-Host 'Reelarr is running.'
}
