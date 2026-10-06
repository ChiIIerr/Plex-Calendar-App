#Requires -Version 5.1
[CmdletBinding()]
param([string]$StateDir = (Join-Path $env:ProgramData 'Reelarr'))
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Administrator
$installed = Get-Installation $StateDir
Stop-ReelarrTask $installed.task_name
Write-Host 'Reelarr stopped. It will start again on the next boot; remove-startup.ps1 disables automatic startup.'
