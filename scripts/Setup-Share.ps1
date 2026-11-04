param([switch]$Mechanical, [switch]$Cam, [switch]$Flash, [switch]$Procurement, [switch]$Desktop)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $root
try {
    $env:PIP_CACHE_DIR = Join-Path $root '.cache\pip'
    $env:TEMP = Join-Path $root '.cache\tmp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        python -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ required"'
        if ($LASTEXITCODE -ne 0) { throw 'Please install Python 3.12 (64-bit) and add it to PATH.' }
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
    }
    & '.venv\Scripts\python.exe' -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    if ($Mechanical) { & "$PSScriptRoot\Setup-Mechanical.ps1" }
    if ($Cam) { & "$PSScriptRoot\Setup-Cam.ps1" -PythonExe (Join-Path $root '.venv\Scripts\python.exe') }
    if ($Flash) { & "$PSScriptRoot\Setup-Flash.ps1" }
    if ($Procurement) { & "$PSScriptRoot\Setup-ProcurementBrowser.ps1" }
    if ($Desktop) { & "$PSScriptRoot\Setup-Desktop.ps1" }
    Write-Output 'FormForge ready. Open the launch .cmd file.'
} finally { Pop-Location }
