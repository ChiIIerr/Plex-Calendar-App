#Requires -Version 5.1
# Test interpreter discovery without installing Python or changing system state.
param([string]$PythonExe, [string]$PreviousPythonExe, [string]$PerUserPythonExe)
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'deploy\common.ps1')
$realEligibility = (Get-Item Function:\Test-ServicePythonExecutable).ScriptBlock

function Get-MachinePythonExecutables([string]$Version) {
    if ($Version -in $script:machineVersions) { return 'machine-python' }
}

function Test-ServicePythonExecutable([string]$Executable) {
    return $Executable -notlike 'C:\Users\*' -and $Executable -notin $script:ineligiblePaths
}

function machine-python {
    $script:attempts += 'machine'
    $global:LASTEXITCODE = 0
    return 'C:\MachinePython\python.exe'
}

function user-python {
    $script:attempts += 'user'
    $global:LASTEXITCODE = 0
    return 'C:\Users\Test\Python\python.exe'
}

function Get-Command {
    param([string]$Name, $ErrorAction)
    if (($Name -eq 'py.exe' -and $script:hasLauncher) -or ($Name -eq 'python.exe' -and $script:hasPython)) { return $true }
}

function py.exe {
    $script:attempts += $args[0]
    $global:LASTEXITCODE = 1
    if ($args[0] -in $script:availableVersions) {
        $global:LASTEXITCODE = 0
        if ($args[0] -in $script:perUserVersions) { return 'C:\Users\Test\Python\python.exe' }
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
    @{ available = @('-3.14'); launcher = $true; python = $true; explicit = 'explicit-python'; expected = 'C:\ExplicitPython\python.exe'; attempts = 'explicit' },
    @{ available = @('-3.14'); machine = @('3.14'); perUser = @('-3.14'); launcher = $true; python = $true; explicit = ''; expected = 'C:\MachinePython\python.exe'; attempts = 'machine' },
    @{ available = @('-3.14'); machine = @('3.13'); perUser = @('-3.14'); launcher = $true; python = $true; explicit = ''; expected = 'C:\MachinePython\python.exe'; attempts = '-3.14,machine' },
    @{ available = @('-3.14'); machine = @('3.14'); invalid = @('C:\MachinePython\python.exe'); launcher = $true; python = $true; explicit = ''; expected = 'C:\Python\python.exe'; attempts = 'machine,-3.14' },
    @{ available = @('-3.14'); perUser = @('-3.14'); launcher = $true; python = $true; explicit = ''; expected = 'C:\PathPython\python.exe'; attempts = '-3.14,-3.13,-3.12,PATH' }
)
foreach ($case in $cases) {
    $script:availableVersions = $case.available
    $script:machineVersions = @()
    $script:perUserVersions = @()
    $script:ineligiblePaths = @()
    if ($case.ContainsKey('machine')) { $script:machineVersions = $case.machine }
    if ($case.ContainsKey('perUser')) { $script:perUserVersions = $case.perUser }
    if ($case.ContainsKey('invalid')) { $script:ineligiblePaths = $case.invalid }
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
$script:machineVersions = @()
$missingRejected = $false
try { Resolve-PythonExecutable '' } catch { $missingRejected = $_.Exception.Message -like '*Python was not found*' }
if (-not $missingRejected) { throw 'Missing Python was not rejected.' }
Write-Host 'Python 3.14 preference, older-version fallback, PATH fallback, and explicit selection checks passed.'
$userRejected = $false
try { Resolve-PythonExecutable 'user-python' } catch { $userRejected = $_.Exception.Message -like '*cannot be used by Local Service*' }
if (-not $userRejected) { throw 'An explicitly selected per-user installation was not rejected.' }
Write-Host 'Machine registration preference, per-user/invalid-runtime fallback, and explicit per-user rejection checks passed.'

if ($PythonExe) {
    if (-not (& $realEligibility $PythonExe)) { throw 'The real system Python was not accepted for Local Service.' }
    if ($PerUserPythonExe -and (& $realEligibility $PerUserPythonExe)) { throw 'The real per-user Python was not rejected for Local Service.' }
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
