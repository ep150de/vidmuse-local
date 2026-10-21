; SceneMux Windows installer (NSIS)
; Maintained packaging source — see packaging/README.md
; Build via build-windows.bat from the repo root (not a published Release yet).

!include "MUI2.nsh"

!define APP_NAME "SceneMux"
!define VERSION "0.1.0-dev"
!define INSTALL_DIR "$PROGRAMFILES64\SceneMux"
!define REG_KEY "SOFTWARE\SceneMux"

Name "${APP_NAME} ${VERSION}"
OutFile "..\..\scenemux-setup.exe"
InstallDir "${INSTALL_DIR}"
RequestExecutionLevel admin

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

Section "SceneMux Server"
    SetOutPath "$INSTDIR\server"
    File "..\..\server\dist\scenemux-server.exe"
    File "..\..\docker-compose.yml"
    File "..\..\docker-compose.cloud.yml"
    File "..\..\.env.example"

    FileOpen $0 "$INSTDIR\MODELS_INSTRUCTIONS.txt" w
    FileWrite $0 "=== SceneMux — Comfy / Playground models ===$\r$\n$\r$\n"
    FileWrite $0 "SceneMux does not bundle multi-GB weights. Install models in your$\r$\n"
    FileWrite $0 "ComfyUI (or AI Playground) models tree, or use Comfy Cloud / FAL.$\r$\n$\r$\n"
    FileWrite $0 "Engines (see docs/ENGINES.md in the git repo):$\r$\n"
    FileWrite $0 "  - MiniMax H3 Ref2VA  (lip-sync PERF / finals)$\r$\n"
    FileWrite $0 "  - FastH3             (8-step draft — not Ref2VA)$\r$\n"
    FileWrite $0 "  - LTX-2.3 IA2V       (draft — not Ref2VA)$\r$\n$\r$\n"
    FileWrite $0 "Comfy-Org MiniMax-H3:$\r$\n"
    FileWrite $0 "  https://huggingface.co/Comfy-Org/MiniMax-H3$\r$\n"
    FileWrite $0 "LTX-2.3:$\r$\n"
    FileWrite $0 "  https://huggingface.co/Lightricks/LTX-2.3$\r$\n$\r$\n"
    FileWrite $0 "Optional draft cloud: set FAL_KEY in .env (https://fal.ai/dashboard/keys)$\r$\n"
    FileClose $0
SectionEnd

Section "Studio client (static build)"
    ; Expects client\dist from: cd client && npm ci && npm run build
    SetOutPath "$INSTDIR\client"
    File /r "..\..\client\dist\*.*"

    FileOpen $0 "$INSTDIR\client\serve-hint.txt" w
    FileWrite $0 "Static studio UI. Serve this folder with any static host, or open via$\r$\n"
    FileWrite $0 "the SceneMux API if you configure static mounting. For development,$\r$\n"
    FileWrite $0 "prefer: cd client && npm run dev with VITE_SERVER_URL=http://localhost:8000$\r$\n"
    FileClose $0

    CreateShortCut "$DESKTOP\SceneMux Server.lnk" "$INSTDIR\server\scenemux-server.exe"
SectionEnd

Section -Post
    WriteRegStr HKLM "${REG_KEY}" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "${REG_KEY}" "Version" "${VERSION}"
    CreateDirectory "$SMPROGRAMS\SceneMux"
    CreateShortCut "$SMPROGRAMS\SceneMux\Server.lnk" "$INSTDIR\server\scenemux-server.exe"
    CreateShortCut "$SMPROGRAMS\SceneMux\Uninstall.lnk" "$INSTDIR\uninstall.exe"
    WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
    Delete "$INSTDIR\server\scenemux-server.exe"
    Delete "$INSTDIR\docker-compose.yml"
    Delete "$INSTDIR\docker-compose.cloud.yml"
    Delete "$INSTDIR\.env.example"
    Delete "$INSTDIR\MODELS_INSTRUCTIONS.txt"
    Delete "$INSTDIR\uninstall.exe"
    RMDir /r "$INSTDIR\server"
    RMDir /r "$INSTDIR\client"
    Delete "$DESKTOP\SceneMux Server.lnk"
    RMDir /r "$SMPROGRAMS\SceneMux"
    RMDir "$INSTDIR"
    DeleteRegKey HKLM "${REG_KEY}"
SectionEnd
