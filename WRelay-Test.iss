; Compiled test distribution: no live source-tree wildcards.
#ifndef CompiledRoot
  #error CompiledRoot is required
#endif
#ifndef BuildVersion
  #define BuildVersion "0.1.0"
#endif
#ifndef BuildId
  #error BuildId is required
#endif

[Setup]
AppId=WRelay.CompiledTest
AppName=WRelay 测试版
AppVersion={#BuildVersion}
AppPublisher=WRelay
DefaultDirName={code:GetDefaultTestDir}
DefaultGroupName=WRelay 测试版
OutputDir=dist
OutputBaseFilename=WRelay-Test-{#BuildId}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
UninstallDisplayIcon={app}\panel\wrelay_panel.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
SetupIconFile=panel\wrelay_panel.ico
InfoBeforeFile=docs\TEST_PACKAGE.md

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"

[Dirs]
Name: "{app}\data"

[Files]
Source: ".runtime\prerequisites\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy noencryption
; CompiledRoot has passed scripts/build_compiled.py's source/private-file audit.
Source: "{#CompiledRoot}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\WRelay 测试版"; Filename: "{app}\WRelay.exe"; WorkingDir: "{app}"; IconFilename: "{app}\panel\wrelay_panel.ico"
Name: "{group}\卸载 WRelay 测试版"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WRelay 测试版"; Filename: "{app}\WRelay.exe"; WorkingDir: "{app}"; IconFilename: "{app}\panel\wrelay_panel.ico"

[Run]
Filename: "{app}\WRelay.exe"; WorkingDir: "{app}"; Description: "启动 WRelay 测试版（请先退出现有版本）"; Flags: postinstall nowait skipifsilent unchecked

[Code]
function GetDefaultTestDir(Param: String): String;
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
  Result := AddBackslash(LocalAppData) + 'Programs\WRelay-Test';
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
  if FileExists(ExpandConstant('{app}\bot.py')) or FileExists(ExpandConstant('{app}\panel\desktop_app.pyw')) then begin
    Result := '请选择独立的测试版目录，不能覆盖源码版 WRelay。';
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
        MsgBox('无法创建初始账号配置，请检查安装目录写入权限。', mbError, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'WRelay Test');
end;

[UninstallDelete]
Type: files; Name: "{userstartup}\WRelay Test.lnk"
Type: files; Name: "{userstartup}\WRelayTestAutostart.vbs"
; Keep user configuration and runtime data, including diagnostic logs.
