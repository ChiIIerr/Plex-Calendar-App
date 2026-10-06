#Requires -Version 5.1
[CmdletBinding()]
param([string]$StateDir = (Join-Path $env:ProgramData 'Calendarr'))
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator
$installed = Get-Installation $StateDir
Stop-CalendarrTask $installed.task_name
if (Get-CalendarrTask $installed.task_name) {
    Unregister-ScheduledTask -TaskName $installed.task_name -TaskPath '\' -Confirm:$false
}
Write-Host 'Automatic startup removed. Application files, configuration, and data are preserved. Rerun install.ps1 to enable startup again.'
