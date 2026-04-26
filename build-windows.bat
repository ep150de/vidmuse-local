@echo off
REM Build Windows Installer for VidMuse Local
echo Building VidMuse Local Windows installer...
cd server
pyinstaller vidmuse-server.spec --clean
if errorlevel 1 goto :error
cd ..
makensis windows-installer\vidmuse-installer.nsi
if errorlevel 1 goto :error
echo.
echo Build complete: vidmuse-local-setup.exe
pause
exit /b 0
:error
echo Build failed!
pause
exit /b 1
