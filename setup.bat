@echo off
REM VidMuse first-run setup wizard (Windows)
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 scripts\setup_wizard.py %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python scripts\setup_wizard.py %*
  exit /b %ERRORLEVEL%
)

where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
  python3 scripts\setup_wizard.py %*
  exit /b %ERRORLEVEL%
)

echo Python 3 is required.
echo Install with:  winget install Python.Python.3.12
echo Or run:        powershell -ExecutionPolicy Bypass -File scripts\install_deps_windows.ps1
pause
exit /b 1
