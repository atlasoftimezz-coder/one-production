# Creates the development runtime in .venv from requirements.lock.
# (P7 replaces this with a bundled embeddable Python for friends' installs.)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is not installed. Install it with: winget install astral-sh.uv"
    exit 1
}
uv venv .venv --python 3.12
uv pip sync requirements.lock --python .venv\Scripts\python.exe
Write-Host "Ready. Run start.bat to launch, check.bat P0 to test."
