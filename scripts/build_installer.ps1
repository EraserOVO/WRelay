[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$scriptPath = Join-Path $projectRoot "WRelay.iss"
$outputPath = Join-Path $projectRoot "WRelay-Setup.exe"

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
