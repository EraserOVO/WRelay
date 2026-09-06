@echo off
chcp 65001 >nul
rem ============================================================
rem  WRelay 一键安装脚本（Windows）
rem  1. 使用随安装包自带的自包含 Python 运行时 python\
rem     （无需本机安装 Python，也无需创建虚拟环境）
rem  2. 校验运行时依赖是否完整
rem  3. 缺少 .env.prod 时从 .env.example 复制生成
rem  完成后请编辑 .env.prod，再双击「启动面板.bat」
rem ============================================================
cd /d "%~dp0"

rem ---- 检查自包含运行时 ----
if not exist "python\python.exe" (
    echo [错误] 未找到自包含运行时 python\python.exe，
    echo        请重新运行 WRelay 安装包
    pause
    exit /b 1
)

echo [1/3] 校验 Python 运行时 ...
".\python\python.exe" --version
if errorlevel 1 (
    echo [错误] 运行时 python.exe 无法运行
    pause
    exit /b 1
)

echo [2/3] 校验依赖是否完整 ...
".\python\python.exe" -c "import nonebot, nonebot.adapters.qq, nonebot.adapters.discord, fastapi, uvicorn, httpx; print('依赖完整')"
if errorlevel 1 (
    echo [错误] 依赖不完整，请重新运行 WRelay 安装包
    pause
    exit /b 1
)

echo [3/3] 检查配置文件 .env.prod ...
if not exist ".env.prod" (
    copy /y ".env.example" ".env.prod" >nul
    echo     已从 .env.example 生成 .env.prod
) else (
    echo     .env.prod 已存在，跳过
)

echo.
echo ============================================================
echo  安装完成！接下来：
echo   1. 用记事本打开 .env.prod，填入你自己的机器人配置
echo      （每项含义见文件内注释，或 docs\SETUP.md）
echo   2. 双击「启动面板.bat」打开管理面板
echo   3. 在面板中勾选你的 Discord 频道与 QQ 群，点击「启动机器人」
echo ============================================================
pause
