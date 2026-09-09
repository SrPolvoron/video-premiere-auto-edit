[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$Audio,
    [switch]$Dev
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    & $Python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
    if ($LASTEXITCODE -ne 0) { throw "Python version check failed" }
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
    }
    $Extras = @()
    if ($Audio) { $Extras += "audio" }
    if ($Dev) { $Extras += "dev" }
    $Spec = "."
    if ($Extras.Count -gt 0) { $Spec = ".[" + ($Extras -join ",") + "]" }
    & ".\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -e $Spec
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
    & ".\.venv\Scripts\autoeditor.exe" doctor
    if ($LASTEXITCODE -ne 0) { throw "Environment check failed" }
    Write-Host "Next: .\.venv\Scripts\autoeditor.exe demo .\work\smoke"
    Write-Host "FFmpeg and model runtime/weights are installed separately; nothing was downloaded for them."
} finally {
    Pop-Location
}
