#Requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Username, [string]$StateDir = (Join-Path $env:ProgramData 'Reelarr'))
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator
$installed = Get-Installation $StateDir
$task = Get-ReelarrTask $installed.task_name
$wasRunning = $task -and $task.State -eq 'Running'
Stop-ReelarrTask $installed.task_name
Push-Location $installed.install_dir
try {
    & (Join-Path $installed.install_dir '.venv\Scripts\python.exe') -m app.manage reset-admin $Username --config $installed.config_path
    if ($LASTEXITCODE -ne 0) { throw 'Administrator recovery failed.' }
} finally {
    Pop-Location
    if ($wasRunning) { Start-ScheduledTask -TaskName $installed.task_name -TaskPath '\' }
}
