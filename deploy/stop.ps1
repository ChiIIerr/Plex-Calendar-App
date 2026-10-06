#Requires -Version 5.1
[CmdletBinding()]
param([string]$StateDir = (Join-Path $env:ProgramData 'Calendarr'))
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator
$installed = Get-Installation $StateDir
Stop-CalendarrTask $installed.task_name
Write-Host 'Calendarr stopped. It will start again on the next boot; remove-startup.ps1 disables automatic startup.'
