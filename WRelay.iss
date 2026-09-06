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
DefaultDirName={autopf}\WRelay
DefaultGroupName={#MyAppName}
OutputBaseFilename=WRelay-Setup
OutputDir=.
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\panel\wrelay_panel.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
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
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "install.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "启动面板.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "AGENTS.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "ARCHITECTURE.md"; DestDir: "{app}"; Flags: ignoreversion

; ---- 面板 ----
; Excludes 排除开发环境运行产生的 __pycache__ 字节码缓存
Source: "panel\*"; DestDir: "{app}\panel"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__\*,*.pyc"

; ---- 插件源码（bot 与面板均按源码目录加载）----
Source: "plugins\*.py"; DestDir: "{app}\plugins"; Flags: recursesubdirs ignoreversion

; ---- 文档 / 脚本 ----
Source: "docs\*"; DestDir: "{app}\docs"; Flags: recursesubdirs ignoreversion
Source: "scripts\*.py"; DestDir: "{app}\scripts"; Flags: recursesubdirs ignoreversion

; ---- 运行时数据 ----
; 不打包开发环境的 data/*.json 与 bot.pid：其中含开发者真实的 QQ 群 /
; Discord 频道 / OpenID 配置与残留进程 PID，不应随安装包分发。
; 安装时仅创建空白 data 目录，settings 等默认结构由程序首次运行按需生成。

; ---- 自包含 Python 运行时（替换旧 .venv）----
; 排除调试符号、缓存、开发残留
Source: ".runtime\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs ignoreversion; Excludes: "*.pdb,__pycache__,.git"

[Icons]
Name: "{group}\启动 WRelay 管理面板"; Filename: "{app}\启动面板.bat"; IconFilename: "{app}\panel\wrelay_panel.ico"
Name: "{group}\卸载 WRelay"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WRelay管理面板"; Filename: "{app}\启动面板.bat"; IconFilename: "{app}\panel\wrelay_panel.ico"

[Run]
Filename: "{app}\启动面板.bat"; Description: "启动 WRelay 管理面板"; Flags: postinstall nowait shellexec skipifsilent

[UninstallDelete]
; 移除运行时生成的数据，保证卸载干净
Type: filesandordirs; Name: "{app}\data\logs"