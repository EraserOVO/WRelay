; WRelay 安装脚本
; 打包自包含 Python 运行时（python-build-standalone 3.12.14）+ 项目源码
; 目标机器无需预装 Python

#define MyAppName "WRelay"
#define MyAppVersion "0.1.0"
#define MyAppExeName "bot.py"
#define SourceRoot "."

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=WRelay
; 使用当前用户目录，保证普通用户可以写入 data/ 与 .env.prod。
DefaultDirName={localappdata}\Programs\WRelay
DefaultGroupName={#MyAppName}
OutputBaseFilename=WRelay-Setup
OutputDir=.
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

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"

[Dirs]
; 空白 data 目录：全新安装不携带任何开发环境运行数据
Name: "{app}\data"

[Files]
; ---- 项目源码 ----
Source: "bot.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements-lock.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "install.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "启动面板.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

; ---- 面板 ----
; Excludes 排除开发环境运行产生的 __pycache__ 字节码缓存
Source: "panel\*"; DestDir: "{app}\panel"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__\*,*.pyc"

; ---- 插件源码（bot 与面板均按源码目录加载）----
Source: "plugins\*.py"; DestDir: "{app}\plugins"; Flags: recursesubdirs ignoreversion

; ---- 用户文档 ----
Source: "docs\*"; DestDir: "{app}\docs"; Flags: recursesubdirs ignoreversion

; ---- 运行时数据 ----
; 不打包开发环境的 data/*.json 与 bot.pid：其中含开发者真实的 QQ 群 /
; Discord 频道 / OpenID 配置与残留进程 PID，不应随安装包分发。
; 安装时仅创建空白 data 目录，settings 等默认结构由程序首次运行按需生成。

; ---- 自包含 Python 运行时（替换旧 .venv）----
; 排除调试符号、缓存、开发残留
Source: ".runtime\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs ignoreversion; Excludes: "*.pdb,__pycache__,.git"

[Icons]
Name: "{group}\启动 WRelay 管理面板"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\panel\管理面板.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\panel\wrelay_panel.ico"
Name: "{group}\卸载 WRelay"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WRelay管理面板"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\panel\管理面板.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\panel\wrelay_panel.ico"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\panel\管理面板.pyw"""; WorkingDir: "{app}"; Description: "启动 WRelay 管理面板"; Flags: postinstall nowait skipifsilent

[Code]
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

[UninstallDelete]
; 仅清理日志；保留 data/ 下的用户配置、凭据关联信息、去重游标和重试队列。
; 卸载默认不删除用户数据，避免误删后无法恢复。
Type: filesandordirs; Name: "{app}\data\logs"
