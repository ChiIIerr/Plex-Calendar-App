#Requires -Version 5.1
# Test interpreter discovery without installing Python or changing system state.
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'deploy\common.ps1')

function Get-Command {
    param([string]$Name, $ErrorAction)
    if (($Name -eq 'py.exe' -and $script:hasLauncher) -or ($Name -eq 'python.exe' -and $script:hasPython)) { return $true }
}

function py.exe {
    $script:attempts += $args[0]
    $global:LASTEXITCODE = 1
    if ($args[0] -in $script:availableVersions) {
        $global:LASTEXITCODE = 0
        return 'C:\Python\python.exe'
    }
}

function python.exe {
    $script:attempts += 'PATH'
    $global:LASTEXITCODE = 0
    return 'C:\PathPython\python.exe'
}

function explicit-python {
    $script:attempts += 'explicit'
    $global:LASTEXITCODE = 0
    return 'C:\ExplicitPython\python.exe'
}

$cases = @(
    @{ available = @('-3.14', '-3.12'); launcher = $true; python = $true; explicit = ''; expected = 'C:\Python\python.exe'; attempts = '-3.14' },
    @{ available = @('-3.13', '-3.12'); launcher = $true; python = $true; explicit = ''; expected = 'C:\Python\python.exe'; attempts = '-3.14,-3.13' },
    @{ available = @('-3.12'); launcher = $true; python = $true; explicit = ''; expected = 'C:\Python\python.exe'; attempts = '-3.14,-3.13,-3.12' },
    @{ available = @(); launcher = $true; python = $true; explicit = ''; expected = 'C:\PathPython\python.exe'; attempts = '-3.14,-3.13,-3.12,PATH' },
    @{ available = @(); launcher = $false; python = $true; explicit = ''; expected = 'C:\PathPython\python.exe'; attempts = 'PATH' },
    @{ available = @('-3.14'); launcher = $true; python = $true; explicit = 'explicit-python'; expected = 'C:\ExplicitPython\python.exe'; attempts = 'explicit' }
)
foreach ($case in $cases) {
    $script:availableVersions = $case.available
    $script:hasLauncher = $case.launcher
    $script:hasPython = $case.python
    $script:attempts = @()
    $resolved = Resolve-PythonExecutable $case.explicit
    if ($resolved -ne $case.expected -or ($script:attempts -join ',') -ne $case.attempts) {
        throw "Incorrect Python selection: $resolved; attempts: $($script:attempts -join ',')"
    }
}
$script:hasLauncher = $false
$script:hasPython = $false
$missingRejected = $false
try { Resolve-PythonExecutable '' } catch { $missingRejected = $_.Exception.Message -like '*Python was not found*' }
if (-not $missingRejected) { throw 'Missing Python was not rejected.' }
Write-Host 'Python 3.14 preference, older-version fallback, PATH fallback, and explicit selection checks passed.'
