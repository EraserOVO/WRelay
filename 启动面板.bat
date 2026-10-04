@echo off
chcp 65001 >nul
rem 启动 WRelay 桌面程序（无控制台窗口）
rem 使用相对路径，可放在项目任意位置双击运行
cd /d "%~dp0"

set "PYTHONW_EXE="
if exist "python\pythonw.exe" set "PYTHONW_EXE=%CD%\python\pythonw.exe"
if not defined PYTHONW_EXE if exist ".venv\Scripts\pythonw.exe" set "PYTHONW_EXE=%CD%\.venv\Scripts\pythonw.exe"
if not defined PYTHONW_EXE (
    echo [错误] 未找到 Python 运行时，请先运行 install.bat
    pause
    exit /b 1
)

if not exist "panel\desktop_app.pyw" (
    echo [错误] 未找到 panel\desktop_app.pyw
    pause
    exit /b 1
)

start "" "%PYTHONW_EXE%" "panel\desktop_app.pyw"
exit /b 0
