[CmdletBinding()]
param(
    [string]$InstallerPath = "",
    [switch]$KeepInstallDir
)

$ErrorActionPreference = "Stop"

$scriptFile = $MyInvocation.MyCommand.Path
$scriptDir = Split-Path -Parent $scriptFile
$projectRoot = Split-Path -Parent $scriptDir
if ([string]::IsNullOrWhiteSpace($projectRoot)) {
    throw "Unable to resolve the project root from the smoke test script path"
}
if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
    $InstallerPath = Join-Path $projectRoot "WRelay-Setup.exe"
}
$installer = [System.IO.Path]::GetFullPath($InstallerPath)
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "Installer not found: $installer"
}

# 每次只使用当前项目目录下唯一生成的空目录，避免覆盖真实安装或运行数据。
# 从脚本位置推导项目根目录，避免受调用目录或 PowerShell 宿主影响。
$smokeRoot = $projectRoot
$smokePrefix = "WRelay-installer-smoke-"
$installDir = $smokeRoot + "\" + ($smokePrefix + [Guid]::NewGuid().ToString("N"))
$installerLog = $installDir + ".setup.log"

function Test-ExpectedFile([string]$relativePath) {
    $path = Join-Path $installDir $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Installed file missing: $relativePath"
    }
}

$smokeError = $null
$cleanupError = $null

try {
    $arguments = @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/SP-",
        "/NOICONS",
        ('/DIR="{0}"' -f $installDir),
        ('/LOG="{0}"' -f $installerLog)
    )
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        $logTail = ((Get-Content -LiteralPath $installerLog -Tail 25 -ErrorAction SilentlyContinue) | Out-String).Trim()
        throw "Installer failed with exit code $($process.ExitCode). Log: $logTail"
    }

    foreach ($file in @(
        "python\python.exe",
        "python\pythonw.exe",
        "bot.py",
        "plugins\sender.py",
        "panel\desktop_app.pyw",
        "panel\管理面板.pyw",
        "panel\wrelay_panel.ico",
        "panel\web\index.html",
        "panel\web\app.js",
        "panel\web\desktop.js",
        "panel\web\local-api.js",
        "panel\web\style.css",
        ".env.prod"
    )) {
        Test-ExpectedFile $file
    }

    [string]$runtimePython = $installDir + "\python\python.exe"

    # Pillow 不仅要能导入，还要能创建 sender 所需的 RGBA 图像对象。
    $mediaProcess = Start-Process -FilePath $runtimePython -ArgumentList @("-c", "m=__import__('PIL.Image',fromlist=['Image']);m.new('RGBA',(1,1))") -WorkingDirectory $installDir -Wait -PassThru -NoNewWindow
    if ($mediaProcess.ExitCode -ne 0) {
        throw "Installed runtime media smoke test failed with exit code $($mediaProcess.ExitCode)"
    }

    $desktopProcess = Start-Process -FilePath $runtimePython -ArgumentList @("-c", "__import__('webview');__import__('pystray');__import__('clr')") -WorkingDirectory $installDir -Wait -PassThru -NoNewWindow
    if ($desktopProcess.ExitCode -ne 0) {
        throw "Installed runtime desktop dependency check failed with exit code $($desktopProcess.ExitCode)"
    }

    # 全新安装会生成示例 .env.prod。bot.py 应在凭据检查处退出，
    # 不建立 Discord / QQ 外部连接；这同时验证安装版入口与核心依赖加载。
    $startupStdout = $installDir + "\startup.stdout.txt"
    $startupStderr = $installDir + "\startup.stderr.txt"
    $startupProcess = Start-Process -FilePath $runtimePython -ArgumentList "bot.py" -WorkingDirectory $installDir -Wait -PassThru -NoNewWindow -RedirectStandardOutput $startupStdout -RedirectStandardError $startupStderr
    if ($startupProcess.ExitCode -ne 2) {
        $text = ((Get-Content -LiteralPath $startupStdout, $startupStderr -ErrorAction SilentlyContinue) | Out-String).Trim()
        throw "Fresh-install startup guard expected exit code 2, got $($startupProcess.ExitCode). Output: $text"
    }

} catch {
    $smokeError = $_
} finally {
    if (-not $KeepInstallDir) {
        try {
            if (Test-Path -LiteralPath $installDir) {
                $allowedPrefix = $smokeRoot + "\" + $smokePrefix
                if (-not $installDir.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                    throw "Refusing to clean an install directory outside the smoke-test path: $installDir"
                }

                $uninstaller = Get-ChildItem -LiteralPath $installDir -Filter "unins*.exe" -File |
                    Select-Object -First 1
                if (-not $uninstaller) {
                    throw "Smoke-test uninstaller not found"
                }

                $uninstallProcess = Start-Process -FilePath $uninstaller.FullName -ArgumentList @(
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART"
                ) -Wait -PassThru -NoNewWindow
                if ($uninstallProcess.ExitCode -ne 0) {
                    throw "Smoke-test uninstall failed with exit code $($uninstallProcess.ExitCode)"
                }

                Remove-Item -LiteralPath $installDir -Recurse -Force
            }
        } catch {
            $cleanupError = "Smoke-test cleanup failed; temporary install may remain at '$installDir'. $($_.Exception.Message)"
        }
    }
}

if ($smokeError -and $cleanupError) {
    Write-Warning $cleanupError
    throw $smokeError.Exception
}
if ($smokeError) {
    throw $smokeError.Exception
}
if ($cleanupError) {
    throw $cleanupError
}

Remove-Item -LiteralPath $installerLog -Force -ErrorAction SilentlyContinue

Write-Host "Installer smoke test passed: install, media, and first-start guard."
