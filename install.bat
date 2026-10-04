@echo off
chcp 65001 >nul
rem ============================================================
rem  WRelay 安装脚本
rem  - 安装包：使用随包提供的 python\python.exe
rem  - 源码目录：若没有内置 Python，则创建 .venv 并安装依赖
rem ============================================================
cd /d "%~dp0"

set "PYTHON_EXE="
if exist "python\python.exe" set "PYTHON_EXE=%CD%\python\python.exe"

if not defined PYTHON_EXE if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"

if not defined PYTHON_EXE (
    echo 未找到内置 Python，准备创建 .venv ...
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        where python >nul 2>&1
        if errorlevel 1 (
            echo [错误] 未找到 Python 3.10+，请安装 Python 或使用 WRelay 安装包。
            pause
            exit /b 1
        )
        python -m venv .venv
    )
    if errorlevel 1 (
        echo [错误] 创建 .venv 失败
        pause
        exit /b 1
    )
    set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
)

echo [1/3] 使用 Python：%PYTHON_EXE%
"%PYTHON_EXE%" --version
if errorlevel 1 (
    echo [错误] Python 运行时无法运行
    pause
    exit /b 1
)
"%PYTHON_EXE%" -c "import sys; print('Python ' + sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
    echo [错误] Python 版本低于 3.10，请安装 Python 3.10+ 或使用 WRelay 安装包。
    pause
    exit /b 1
)

if exist "python\python.exe" (
    echo [2/3] 校验安装包依赖 ...
    "%PYTHON_EXE%" -c "import nonebot, nonebot.adapters.qq, nonebot.adapters.discord, fastapi, uvicorn, httpx; print('依赖完整')"
) else (
    echo [2/3] 安装项目依赖 ...
    if not exist "requirements-lock.txt" (
        echo [错误] 找不到 requirements-lock.txt，请确认项目文件完整。
        pause
        exit /b 1
    )
    "%PYTHON_EXE%" -m pip --version >nul 2>&1
    if errorlevel 1 (
        echo [错误] 当前 Python 没有可用的 pip，请重新创建 .venv 或安装完整 Python。
        pause
        exit /b 1
    )
    "%PYTHON_EXE%" -m pip install -r requirements-lock.txt -e .
)
if errorlevel 1 (
    echo [错误] 依赖安装或校验失败。
    echo         请检查网络、代理和 requirements-lock.txt；也可以查看上方 pip 错误信息。
    pause
    exit /b 1
)

echo [3/3] 检查配置文件 .env.prod ...
if not exist ".env.prod" (
    if not exist ".env.example" (
        echo [错误] 找不到 .env.example，无法生成 .env.prod。
        pause
        exit /b 1
    )
    copy /y ".env.example" ".env.prod" >nul
    if errorlevel 1 (
        echo [错误] 无法创建 .env.prod，请检查目录写入权限。
        pause
        exit /b 1
    )
    echo     已从 .env.example 生成 .env.prod
) else (
    echo     .env.prod 已存在，跳过
)

echo.
echo 安装完成！请双击“启动面板.bat”，按配置向导完成账号与转发设置。
pause
