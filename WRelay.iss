; Compiled WRelay release. Only the audited build directory is distributed.
#ifndef CompiledRoot
  #error CompiledRoot is required
#endif
#ifndef BuildVersion
  #error BuildVersion is required
#endif

[Setup]
AppId=WRelay
AppName=WRelay
AppVersion={#BuildVersion}
AppPublisher=WRelay
SignTool=wrelay-authenticode
SignedUninstaller=yes
DefaultDirName={code:GetDefaultReleaseDir}
DefaultGroupName=WRelay
OutputDir=dist
OutputBaseFilename=WRelay-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
AppMutex=Local\WRelayDesktopApp
UninstallDisplayIcon={app}\WRelay.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
SetupIconFile=panel\wrelay_panel.ico

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"

[Dirs]
Name: "{app}\data"

[Files]
Source: ".runtime\prerequisites\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy noencryption
Source: "{#CompiledRoot}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\WRelay"; Filename: "{app}\WRelay.exe"; WorkingDir: "{app}"; IconFilename: "{app}\WRelay.exe"
Name: "{group}\卸载 WRelay"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WRelay"; Filename: "{app}\WRelay.exe"; WorkingDir: "{app}"; IconFilename: "{app}\WRelay.exe"

[Run]
; Migrate an existing startup item to the branded current-user Run entry.
Filename: "{app}\WRelay.exe"; Parameters: "--migrate-autostart"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated
Filename: "{app}\WRelay.exe"; WorkingDir: "{app}"; Description: "启动 WRelay"; Flags: postinstall nowait skipifsilent

[Code]
function GetDefaultReleaseDir(Param: String): String;
var
  LocalAppData, UserProfile: String;
begin
  LocalAppData := GetEnv('LOCALAPPDATA');
  if LocalAppData = '' then begin
    UserProfile := GetEnv('USERPROFILE');
    if UserProfile = '' then
      RaiseException('无法确定当前用户的 Local AppData 目录。');
    LocalAppData := AddBackslash(UserProfile) + 'AppData\Local';
  end;
  Result := AddBackslash(LocalAppData) + 'Programs\WRelay';
end;

function WebView2Available: Boolean;
var
  Version: string;
  Key: string;
begin
  Key := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result :=
    (RegQueryStringValue(HKLM32, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  Attempt: Integer;
begin
  Result := '';
  if DirExists(ExpandConstant('{app}\.git')) then begin
    Result := '不能将安装包安装到 WRelay 源码目录。';
    exit;
  end;
  if WebView2Available then
    exit;
  try
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then begin
      Result := '无法启动 WebView2 Runtime 安装程序。';
      exit;
    end;
    if ExitCode <> 0 then begin
      Result := 'WebView2 Runtime 安装失败（退出码 ' + IntToStr(ExitCode) + '）。';
      exit;
    end;
    for Attempt := 1 to 20 do begin
      if WebView2Available then
        exit;
      Sleep(500);
    end;
    Result := 'WebView2 Runtime 尚未就绪，请重新运行安装程序。';
  except
    Result := '无法准备 WebView2 Runtime：' + GetExceptionMessage;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  EnvPath, ExamplePath: string;
begin
  if CurStep = ssPostInstall then begin
    EnvPath := ExpandConstant('{app}\.env.prod');
    ExamplePath := ExpandConstant('{app}\.env.example');
    if (not FileExists(EnvPath)) and FileExists(ExamplePath) then
      if not CopyFile(ExamplePath, EnvPath, False) then
        MsgBox('无法创建 .env.prod，请检查安装目录的写入权限。', mbError, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'WRelay');
end;

[UninstallDelete]
Type: files; Name: "{userstartup}\WRelay.lnk"
Type: files; Name: "{userstartup}\WRelayAutostart.vbs"
Type: filesandordirs; Name: "{app}\data\logs"
