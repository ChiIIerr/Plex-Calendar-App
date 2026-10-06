#Requires -Version 5.1
# Test interpreter discovery without installing Python or changing system state.
param([string]$PythonExe, [string]$PreviousPythonExe)
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

if ($PythonExe) {
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $testRoot = [IO.Path]::GetFullPath((Join-Path $temporaryRoot ('Calendarr Python Check ' + [char]0xE9 + ' ' + [Guid]::NewGuid().ToString('N'))))
    if (-not $testRoot.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid test directory.' }
    try {
        if ($PreviousPythonExe) {
            & $PreviousPythonExe -m venv (Join-Path $testRoot '.venv')
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the previous Python environment.' }
            [IO.File]::WriteAllText((Join-Path $testRoot '.venv\old-runtime.txt'), 'old')
        }
        $serverPython = Initialize-PythonEnvironment $PythonExe $testRoot
        $expected = & $PythonExe -c 'import sys; print(sys.version_info[:2])'
        if ($LASTEXITCODE -ne 0) { throw 'Could not read the selected Python version.' }
        $actual = & $serverPython -c 'import sys; print(sys.version_info[:2])'
        if ($LASTEXITCODE -ne 0 -or $actual -ne $expected) { throw 'Environment uses the wrong Python version.' }
        if ($PreviousPythonExe -and (Test-Path -LiteralPath (Join-Path $testRoot '.venv\old-runtime.txt'))) { throw 'Environment retained the old runtime files.' }
        [IO.File]::WriteAllText((Join-Path $testRoot '.venv\same-runtime.txt'), 'retained')
        [IO.File]::WriteAllText((Join-Path $testRoot 'retained-state.txt'), 'retained')
        $null = Initialize-PythonEnvironment $PythonExe $testRoot
        foreach ($file in @('.venv\same-runtime.txt', 'retained-state.txt')) {
            if (-not (Test-Path -LiteralPath (Join-Path $testRoot $file))) { throw 'Same-version rerun removed environment or state files.' }
        }
        Write-Host 'Real Python environment creation, runtime selection, and same-version reuse checks passed.'
    } finally {
        if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
    }
}
