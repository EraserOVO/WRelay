[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$scriptPath = Join-Path $projectRoot "WRelay.iss"
$outputPath = Join-Path $projectRoot "WRelay-Setup.exe"
$runtimePython = Join-Path $projectRoot ".runtime\python\python.exe"
$webViewBootstrapper = Join-Path $projectRoot ".runtime\prerequisites\MicrosoftEdgeWebview2Setup.exe"

$requiredFiles = @(
    "WRelay.iss",
    ".runtime\python\python.exe",
    "pyproject.toml",
    "requirements-lock.txt",
    "panel\wrelay_panel.ico"
)

foreach ($relativePath in $requiredFiles) {
    $path = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing build input: $relativePath"
    }
}

# 只在目标机器缺少 WebView2 时运行。构建时固定打包一份微软签名的
# Evergreen Bootstrapper，避免安装器现场下载并执行未经校验的文件。
if (-not (Test-Path -LiteralPath $webViewBootstrapper -PathType Leaf)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $webViewBootstrapper) -Force | Out-Null
    Write-Host "Downloading Microsoft WebView2 Bootstrapper ..."
    Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -OutFile $webViewBootstrapper
}
$webViewSignature = Get-AuthenticodeSignature -LiteralPath $webViewBootstrapper
if (($webViewSignature.Status -ne "Valid") -or (-not $webViewSignature.SignerCertificate) -or
    ($webViewSignature.SignerCertificate.Subject -notmatch "Microsoft Corporation")) {
    throw "WebView2 Bootstrapper must have a valid Microsoft Corporation signature: $webViewBootstrapper"
}

# 安装包会把 .runtime\python 原样复制为 python\。仅检查解释器存在不足以
# 保证可用：例如 Pillow 缺失时，Discord Emoji 合成图片会在用户机器上失败。
# 这里在编译前验证运行时依赖，避免产生表面构建成功、实际功能缺包的安装包。
Write-Host "Checking bundled runtime dependencies ..."
& $runtimePython -c "import fastapi, httpx, nonebot, uvicorn; import nonebot.adapters.discord, nonebot.adapters.qq; import PIL, pystray, webview, clr; print('Bundled runtime dependencies complete')"
if ($LASTEXITCODE -ne 0) {
    throw "Bundled runtime dependencies are incomplete. Rebuild or repair .runtime before creating the installer."
}

$compilerCandidates = @()
if ($env:INNO_SETUP_ISCC) {
    $compilerCandidates += $env:INNO_SETUP_ISCC
}
$command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($command) {
    $compilerCandidates += $command.Source
}
$compilerCandidates += @(
    (Join-Path ${env:ProgramFiles} "Inno Setup 7\ISCC.exe"),
    (Join-Path ${env:ProgramFiles} "Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:LOCALAPPDATA} "Programs\Inno Setup 7\ISCC.exe"),
    (Join-Path ${env:LOCALAPPDATA} "Programs\Inno Setup 6\ISCC.exe")
)

$iscc = $compilerCandidates |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
    Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup compiler not found. Install Inno Setup or set INNO_SETUP_ISCC to ISCC.exe."
}

Write-Host "Project: $projectRoot"
Write-Host "Compiler: $iscc"
Write-Host "WebView2 Bootstrapper: $webViewBootstrapper"
Push-Location $projectRoot
try {
    & $iscc $scriptPath
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compilation failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
    throw "Compiler finished without creating WRelay-Setup.exe"
}

$artifact = Get-Item -LiteralPath $outputPath
$hash = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash
Write-Host "Output: $($artifact.FullName)"
Write-Host "Size: $($artifact.Length) bytes"
Write-Host "SHA256: $hash"
