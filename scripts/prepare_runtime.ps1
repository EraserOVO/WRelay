[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runtimeParent = Join-Path $projectRoot ".runtime"
$runtimeRoot = Join-Path $runtimeParent "python"
$runtimePython = Join-Path $runtimeRoot "python.exe"
$requirementsLock = Join-Path $projectRoot "requirements-lock.txt"

$pythonVersion = "3.12.14"
$releaseTag = "20260901"
$archiveName = "cpython-3.12.14+20260901-x86_64-pc-windows-msvc-install_only.tar.gz"
$expectedSha256 = "e90c1b6419da3bd812dd73bb3de40287a21abf153438147639ec5e20375ea93f"

function Get-RuntimeVersion([string]$pythonPath) {
    $output = & $pythonPath -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to run Python at $pythonPath"
    }

    $version = ($output | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($version)) {
        throw "Python at $pythonPath did not report its version"
    }
    return $version
}

function Install-LockedDependencies([string]$pythonPath) {
    Write-Host "Installing locked runtime dependencies ..."
    & $pythonPath -m pip install -r $requirementsLock
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to install dependencies from requirements-lock.txt"
    }
}

function Assert-RuntimeReady([string]$pythonPath) {
    & $pythonPath (Join-Path $PSScriptRoot "check_security_dependencies.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Bundled security dependencies do not match the validated versions"
    }
    $actualVersion = Get-RuntimeVersion $pythonPath
    if ($actualVersion -ne $pythonVersion) {
        throw "Runtime must be Python $pythonVersion; found $actualVersion"
    }

    & $pythonPath -c "import fastapi, httpx, nonebot, uvicorn; import nonebot.adapters.discord, nonebot.adapters.qq; import PIL, pystray, webview, clr; print('Bundled runtime dependencies complete')"
    if ($LASTEXITCODE -ne 0) {
        throw "Bundled runtime dependencies are incomplete"
    }
}

if (-not (Test-Path -LiteralPath $requirementsLock -PathType Leaf)) {
    throw "Missing dependency lock file: $requirementsLock"
}
New-Item -ItemType Directory -Path $runtimeParent -Force | Out-Null

if (Test-Path -LiteralPath $runtimeRoot) {
    if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
        throw "An incomplete .runtime\python directory already exists. Rename or remove it before preparing the runtime."
    }

    $existingVersion = Get-RuntimeVersion $runtimePython
    if ($existingVersion -ne $pythonVersion) {
        throw "Existing runtime is Python $existingVersion; rename or remove .runtime\python before preparing Python $pythonVersion."
    }

    Install-LockedDependencies $runtimePython
    Assert-RuntimeReady $runtimePython
    Write-Host "Bundled runtime is ready: $runtimePython"
    return
}

$archiveUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/$releaseTag/$archiveName"
$archivePath = Join-Path ([System.IO.Path]::GetTempPath()) ("wrelay-python-" + [Guid]::NewGuid().ToString("N") + ".tar.gz")
$stageRoot = Join-Path $runtimeParent ("prepare-" + [Guid]::NewGuid().ToString("N"))
$stageRootFull = [System.IO.Path]::GetFullPath($stageRoot)
$runtimeParentPrefix = [System.IO.Path]::GetFullPath($runtimeParent).TrimEnd('\') + '\'
$prepareError = $null
$cleanupErrors = [System.Collections.Generic.List[string]]::new()

try {
    Write-Host "Downloading Python $pythonVersion standalone runtime ..."
    Invoke-WebRequest -Uri $archiveUrl -OutFile $archivePath

    $actualSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $expectedSha256) {
        throw "Python runtime archive SHA-256 mismatch"
    }

    New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
    & tar.exe -xzf $archivePath -C $stageRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to extract the standalone Python runtime"
    }

    $stagedPythonRoot = Join-Path $stageRoot "python"
    $stagedPython = Join-Path $stagedPythonRoot "python.exe"
    if (-not (Test-Path -LiteralPath $stagedPython -PathType Leaf)) {
        throw "Standalone archive is missing python\python.exe"
    }

    Install-LockedDependencies $stagedPython
    Assert-RuntimeReady $stagedPython

    if (Test-Path -LiteralPath $runtimeRoot) {
        throw "Runtime destination already exists: $runtimeRoot"
    }
    Move-Item -LiteralPath $stagedPythonRoot -Destination $runtimeRoot
    Write-Host "Bundled runtime is ready: $runtimePython"
} catch {
    $prepareError = $_
} finally {
    try {
        if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
            Remove-Item -LiteralPath $archivePath -Force
        }
    } catch {
        $cleanupErrors.Add("Unable to remove temporary archive '$archivePath': $($_.Exception.Message)")
    }

    try {
        if (Test-Path -LiteralPath $stageRootFull) {
            if ($stageRootFull.StartsWith($runtimeParentPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                Remove-Item -LiteralPath $stageRootFull -Recurse -Force
            } else {
                $cleanupErrors.Add("Refusing to remove staging directory outside .runtime: $stageRootFull")
            }
        }
    } catch {
        $cleanupErrors.Add("Unable to remove staging directory '$stageRootFull': $($_.Exception.Message)")
    }
}

if ($prepareError) {
    foreach ($cleanupError in $cleanupErrors) {
        Write-Warning $cleanupError
    }
    throw $prepareError.Exception
}
if ($cleanupErrors.Count -gt 0) {
    throw ("Runtime preparation completed, but cleanup failed: " + [string]::Join("; ", $cleanupErrors))
}
