param([string]$PythonExe = $env:FORMFORGE_CAM_BASE_PYTHON)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:TEMP = Join-Path $root '.cache\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $root '.cache\pip-cam'
New-Item -ItemType Directory -Force -Path $env:TEMP, $env:PIP_CACHE_DIR | Out-Null
if (-not $PythonExe) {
    throw 'Supply -PythonExe with an installed Python 3.12+ executable, or set FORMFORGE_CAM_BASE_PYTHON.'
}
& $PythonExe -c 'import sys; assert sys.version_info >= (3,12), "CAM requires Python >=3.12"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }
$environment = Join-Path $root '.venv-cam'
if (-not (Test-Path -LiteralPath (Join-Path $environment 'Scripts\python.exe'))) {
    & $PythonExe -m venv $environment
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated CAM environment.' }
}
$camPython = Join-Path $environment 'Scripts\python.exe'
$requirements = Join-Path $root 'requirements-cam.lock'
if (-not (Test-Path -LiteralPath $requirements)) { $requirements = Join-Path $root 'requirements-cam.txt' }
& $camPython -m pip install --index-url https://pypi.org/simple --requirement $requirements
if ($LASTEXITCODE -ne 0) { throw 'CAM dependency installation failed.' }
& $camPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'CAM dependencies are inconsistent.' }
Write-Output 'The independent CAM runtime is ready in FormForge.'
