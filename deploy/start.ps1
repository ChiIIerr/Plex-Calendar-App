# Run from the repository directory. Python 3.12+ must be on PATH.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path ".venv/Scripts/python.exe")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment." }
    & .venv/Scripts/python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Could not install dependencies." }
}
if (-not $env:DATA_DIR) { $env:DATA_DIR = Join-Path (Get-Location) "data" }
& .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8282 --workers 1 --no-access-log
