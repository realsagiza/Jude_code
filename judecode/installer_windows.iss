;============================================================
;  Jude Code - Windows Installer (Inno Setup)
;  Creates a proper setup.exe that can optionally install
;  Python for a complete experience
;============================================================

#define MyAppName "Jude Code"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Jude"
#define MyAppURL "https://github.com/jude/judecode"
#define MyAppExeName "judecode.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=JudeCode_Setup_v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
DisableProgramGroupPage=yes

; ── Icon ──
; Uncomment if you have an icon file
; SetupIconFile=judecode.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "thai"; MessagesFile: "compiler:Languages\Thai.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "addtopath"; Description: "Add Jude Code to system PATH"; GroupDescription: "System Configuration"; Flags: checkedonce
Name: "installpython"; Description: "Install Python 3.12 (if not already installed)"; GroupDescription: "Dependencies"; Flags: unchecked

[Files]
; ── Main executable (built with PyInstaller) ──
Source: "dist\judecode.exe"; DestDir: "{app}"; Flags: ignoreversion

; ── Optional: Embedded Python (if you want to bundle it) ──
; Source: "redist\python-3.12.0-embed-amd64.zip"; DestDir: "{tmp}"; Tasks: installpython

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
; ── Run after install ──
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; ── Add to PATH ──
Filename: "setx"; Parameters: "PATH ""{app};%PATH%"""; StatusMsg: "Adding Jude Code to PATH..."; Tasks: addtopath; Flags: runhidden

[Registry]
Root: HKCU; Subkey: "Software\JudeCode"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey

[Code]
{ ── Custom wizard page for AI model configuration ── }
var
  ModelPage: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  { ── Model Configuration Page ── }
  ModelPage := CreateInputQueryPage(
    wpSelectTasks,
    'AI Model Configuration',
    'Configure which AI model Jude Code will use',
    'Leave defaults if you plan to use DeepSeek API.'
  );
  ModelPage.Add('API Base URL (default: https://api.deepseek.com):', False);
  ModelPage.Add('Model name (default: deepseek-chat):', False);
  ModelPage.Values[0] := 'https://api.deepseek.com';
  ModelPage.Values[1] := 'deepseek-chat';
end;

function GetModelUrl: string;
begin
  Result := ModelPage.Values[0];
end;

function GetModelName: string;
begin
  Result := ModelPage.Values[1];
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { ── Save config file ── }
    SaveStringToFile(
      ExpandConstant('{app}\config.ini'),
      '[JudeCode]' + #13#10 +
      'BaseURL=' + GetModelUrl() + #13#10 +
      'Model=' + GetModelName() + #13#10 +
      'InstallPath=' + ExpandConstant('{app}') + #13#10,
      False
    );
  end;
end;

[UninstallRun]
; Clean up config
Filename: "cmd.exe"; Parameters: "/c del ""{app}\config.ini"""; Flags: runhidden

[Messages]
; ── Custom messages ──
SetupAppTitle=Jude Code Installer
SetupWindowTitle=Jude Code - Windows Setup
