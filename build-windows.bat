@echo off
REM Build SceneMux Windows installer (maintainer / pre-release)
REM Output: scenemux-setup.exe in the repo root
REM See packaging\README.md — not a published GitHub Release until stable.

setlocal EnableExtensions
cd /d "%~dp0"

echo === SceneMux Windows packaging ===
echo.

where pyinstaller >nul 2>nul
if errorlevel 1 (
  echo PyInstaller not found. Install with:  pip install pyinstaller
  goto :error
)
where makensis >nul 2>nul
if errorlevel 1 (
  echo NSIS makensis not found. Install Nullsoft Scriptable Install System.
  goto :error
)

echo [1/3] Building studio client static assets...
pushd client
if not exist node_modules (
  call npm ci
  if errorlevel 1 goto :error_pop
)
call npm run build
if errorlevel 1 goto :error_pop
popd

echo [2/3] Building scenemux-server.exe with PyInstaller...
pushd server
pyinstaller scenemux-server.spec --clean --noconfirm
if errorlevel 1 goto :error_pop
if not exist dist\scenemux-server.exe (
  echo Expected server\dist\scenemux-server.exe was not produced.
  goto :error_pop
)
popd

echo [3/3] Packaging NSIS installer...
makensis packaging\windows\scenemux-installer.nsi
if errorlevel 1 goto :error

echo.
echo Build complete: scenemux-setup.exe
echo Version string is 0.1.0-dev — bump packaging\windows\scenemux-installer.nsi for releases.
echo Do not publish this exe as the official installer until a stable GitHub Release.
echo.
pause
exit /b 0

:error_pop
popd
:error
echo.
echo Build failed.
pause
exit /b 1
