@echo off
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo The runtime is missing. Run setup_dev.ps1 first.
  exit /b 1
)
cd /d "%~dp0"
"%PY%" tools\check.py %*
exit /b %ERRORLEVEL%
