[CmdletBinding()]
param(
    [ValidateSet("release", "test")]
    [string]$Edition = "release",
    [string]$Owner = "",
    [switch]$CompileOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $projectRoot ".runtime\compile-env\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Prepare .runtime/compile-env and install requirements-lock.txt and requirements-build.txt first. See docs/SETUP.md."
}
$arguments = @((Join-Path $PSScriptRoot "build_compiled.py"), "--edition", $Edition)
if ($Owner) { $arguments += @("--owner", $Owner) }
if ($CompileOnly) { $arguments += "--compile-only" }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "WRelay release build failed ($LASTEXITCODE)." }
