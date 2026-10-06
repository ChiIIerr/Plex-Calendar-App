#Requires -Version 5.1
[CmdletBinding()]
param([string]$StateDir = (Join-Path $env:ProgramData 'Calendarr'))
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator
$installed = Get-Installation $StateDir
$task = Get-CalendarrTask $installed.task_name
if ($task) {
    Get-ScheduledTaskInfo -TaskName $installed.task_name -TaskPath '\' | Select-Object TaskName, LastRunTime, LastTaskResult
    Write-Host "Task state: $($task.State)"
} else { Write-Host 'Automatic startup is not registered.' }
$config = Get-Content -LiteralPath $installed.config_path -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host "Configuration: $($installed.config_path)"
Write-Host "Data: $($config.data_dir)"
$logFile = Join-Path $config.log_dir 'server.log'
Write-Host "Log: $logFile"
if (Test-Path -LiteralPath $logFile) { Get-Content -LiteralPath $logFile -Encoding UTF8 -Tail 20 }
