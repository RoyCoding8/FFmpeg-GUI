; Inno Setup script for ffgui. Build the Nuitka onefile first (build\nuitka.cmd),
; then compile this script with ISCC. A portable .zip of build\dist is the same tree.
#define AppName "ffgui"
#define AppVersion "0.1.0"
#define AppExe "ffgui.exe"

[Setup]
AppId={{8C1B4A2E-77D1-4B6F-9A34-FFGUI0000001}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=dist
OutputBaseFilename=ffgui-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#AppExe}

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktop

[Tasks]
Name: "desktop"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional tasks:"

[Registry]
; .ffgui job exchange: {"version":1,"job":…,"meta":…}
Root: HKCU; Subkey: "Software\Classes\.ffgui"; ValueType: string; ValueName: ""; ValueData: "ffgui.job"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\ffgui.job"; ValueType: string; ValueName: ""; ValueData: "ffgui job file"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\ffgui.job\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKCU; Subkey: "Software\Classes\ffgui.job\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
