param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $env:PIP_CACHE_DIR = Join-Path $projectRoot '.cache\pip'
    $env:TEMP = Join-Path $projectRoot '.local\mechanical-temp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
    if (-not (Test-Path -LiteralPath '.venv-mechanical\Scripts\python.exe')) {
        & '.\.venv\Scripts\python.exe' -m venv .venv-mechanical
        if ($LASTEXITCODE -ne 0) { throw 'Mechanical Python environment creation failed.' }
    }
    $mechanicalRequirements = if (Test-Path -LiteralPath 'requirements-mechanical.lock.txt') { 'requirements-mechanical.lock.txt' } else { 'requirements-mechanical.txt' }
    & '.\.venv-mechanical\Scripts\python.exe' -m pip install --index-url https://pypi.org/simple -r $mechanicalRequirements
    if ($LASTEXITCODE -ne 0) { throw 'Mechanical dependency installation failed.' }
    & '.\.venv-mechanical\Scripts\python.exe' -m pip freeze | Set-Content -LiteralPath requirements-mechanical.lock.txt -Encoding ASCII
    if ($LASTEXITCODE -ne 0) { throw 'Mechanical dependency recording failed.' }
    Write-Output 'Mechanical runtime ready in FormForge.'
} finally { Pop-Location }
