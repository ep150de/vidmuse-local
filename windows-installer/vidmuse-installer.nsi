; VidMuse Local Installer
; Built with NSIS

!include "MUI2.nsh"
!define APP_NAME "VidMuse Local"
!define VERSION "1.0.0"
!define INSTALL_DIR "$PROGRAMFILES64\VidMuse Local"

Name "${APP_NAME} ${VERSION}"
OutFile "vidmuse-local-setup.exe"
InstallDir "${INSTALL_DIR}"
RequestExecutionLevel admin

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

Section "VidMuse Server"
    SetOutPath "$INSTDIR\server"
    File "server\vidmuse-server.exe"
    File "docker-compose.yml"
    SetOutPath "$INSTDIR\ComfyUI\models\diffusion_models"
    
    FileOpen $0 "$INSTDIR\MODELS_INSTRUCTIONS.txt" w
    FileWrite $0 "=== VidMuse Model Downloads ===$
$
"
    FileWrite $0 "FLUX Dev fp8: https://huggingface.co/black-forest-labs/FLUX.1-dev$
"
    FileWrite $0 "Wan 2.1 i2v: https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged$
"
    FileWrite $0 "IP-Adapter: https://huggingface.co/h94/IP-Adapter$
$
"
    FileWrite $0 "Place downloaded models in this directory:$
"
    FileWrite $0 "$INSTDIR\ComfyUI\models\diffusion_models\$
"
    FileClose $0
SectionEnd

Section "Client"
    SetOutPath "$INSTDIR\client"
    File /r "client\*.*"
    FileOpen $0 "$INSTDIR\client\start-client.bat" w
    FileWrite $0 "@echo off$
"
    FileWrite $0 "cd /d %~dp0$
"
    FileWrite $0 "call npm install$
"
    FileWrite $0 "set VITE_SERVER_URL=http://localhost:8000$
"
    FileWrite $0 "npm run dev$
"
    FileClose $0
    CreateShortCut "$DESKTOP\VidMuse Client.lnk" "$INSTDIR\client\start-client.bat"
SectionEnd

Section -Post
    WriteRegStr HKLM "SOFTWARE\VidMuse Local" "InstallDir" "$INSTDIR"
    CreateDirectory "$SMPROGRAMS\VidMuse Local"
    CreateShortCut "$SMPROGRAMS\VidMuse Local\Server.lnk" "$INSTDIR\server\vidmuse-server.exe"
    CreateShortCut "$SMPROGRAMS\VidMuse Local\Client.lnk" "$INSTDIR\client\start-client.bat"
    WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
    Delete "$INSTDIR\server\vidmuse-server.exe"
    Delete "$INSTDIR\docker-compose.yml"
    RMDir /r "$INSTDIR\server"
    RMDir /r "$INSTDIR\client"
    RMDir /r "$INSTDIR\ComfyUI"
    Delete "$DESKTOP\VidMuse Client.lnk"
    RMDir "$SMPROGRAMS\VidMuse Local"
    RMDir "$INSTDIR"
    DeleteRegKey HKLM "SOFTWARE\VidMuse Local"
SectionEnd
