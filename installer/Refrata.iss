; Refrata installer — LucsLabs Studios
; Built by tools/build.py:  ISCC /DAppVersion=x.y.z /DRoot=<repo> installer\Refrata.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Root
  #define Root ".."
#endif
#define AppName "Refrata"
#define AppExe "Refrata.exe"
#define Studio "LucsLabs Studios"
#define ProgId "Refrata.Media"
#define CapKey "Software\LucsLabs Studios\Refrata\Capabilities"

[Setup]
AppId={{6B0E3F2A-9C4D-4E1B-8A57-1C0FBE5A7D21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Studio}
AppPublisherURL=mailto:lucslabs.studios@gmail.com
AppSupportURL=mailto:lucslabs.studios@gmail.com
AppCopyright=© {#Studio}
VersionInfoCompany={#Studio}
VersionInfoDescription={#AppName} — instalador
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Root}\dist
OutputBaseFilename=Refrata-Setup-{#AppVersion}
SetupIconFile={#Root}\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
WizardImageFile={#Root}\assets\installer\wizard.bmp,{#Root}\assets\installer\wizard@2.5x.bmp
WizardSmallImageFile={#Root}\assets\installer\small.bmp,{#Root}\assets\installer\small@2.5x.bmp
Compression=lzma2/max
SolidCompression=yes
ChangesAssociations=yes
CloseApplications=yes
; Only ask Restart Manager about executables: by default it registers every .exe/.dll that will be
; replaced (536 files with libVLC's plugins), and RmGetList took ~3.5 s on "Preparando para
; instalar". A running Refrata.exe is what holds those DLLs, so it is still found and closed.
CloseApplicationsFilter=*.exe
#ifdef Sign
; defined by tools/build.py (ISCC /DSign /Sbvsign=...): signs the setup and the uninstaller
SignTool=bvsign
SignedUninstaller=yes
#endif
RestartApplications=no
LicenseFile={#Root}\installer\LICENSE-pt.txt

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[CustomMessages]
ptbr.AssocTask=Mostrar o Refrata em "Abrir com" para vídeos, músicas e playlists
ptbr.LaunchNow=Abrir o Refrata agora

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "assoc"; Description: "{cm:AssocTask}"; GroupDescription: "Integração com o Windows:"

[Files]
Source: "{#Root}\dist\Refrata\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Player de mídia da {#Studio}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; ProgID used for every media type
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueData: "Mídia do Refrata"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueData: """{app}\{#AppExe}"",0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\{#ProgId}\shell\open"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assoc
; "Abrir com" entry for the executable
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assoc
; Default Apps (Settings › Apps › Apps padrão)
Root: HKA; Subkey: "Software\LucsLabs Studios"; Flags: uninsdeletekeyifempty; Tasks: assoc
Root: HKA; Subkey: "Software\LucsLabs Studios\Refrata"; Flags: uninsdeletekeyifempty; Tasks: assoc
Root: HKA; Subkey: "{#CapKey}"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "{#CapKey}"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Player moderno sobre o motor do VLC, da {#Studio}"; Tasks: assoc
Root: HKA; Subkey: "{#CapKey}"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: """{app}\{#AppExe}"",0"; Tasks: assoc
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#AppName}"; ValueData: "{#CapKey}"; Flags: uninsdeletevalue; Tasks: assoc
; "Iniciar com o Windows" is written by the app itself; never leave it behind after uninstalling
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Refrata"; Flags: uninsdeletevalue
; per-extension entries (generated from refrata/media.py)
#include AddBackslash(Root) + "build\assoc.iss"

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchNow}"; Flags: nowait postinstall skipifsilent
; in-app updates run the setup with /VERYSILENT /relaunch=1: start the new version afterwards
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: RelaunchAfterUpdate

[Code]
function RelaunchAfterUpdate: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:relaunch|0}') = '1');
end;

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
