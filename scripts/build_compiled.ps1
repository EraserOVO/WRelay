[CmdletBinding()]
param([string]$Owner = "", [switch]$CompileOnly)
$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $projectRoot ".runtime\compile-env\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Prepare .runtime/compile-env and install requirements-lock.txt and requirements-build.txt first. See docs/BUILDING.md."
}
$arguments = @((Join-Path $PSScriptRoot "build_compiled.py"))
if ($Owner) { $arguments += @("--owner", $Owner) }
if ($CompileOnly) { $arguments += "--compile-only" }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "Compiled test package build failed ($LASTEXITCODE)." }
