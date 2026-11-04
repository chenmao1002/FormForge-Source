$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $env:PIP_CACHE_DIR=Join-Path $projectRoot '.cache\pip'
    $env:npm_config_cache=Join-Path $projectRoot '.cache\npm'
    $env:ELECTRON_CACHE=Join-Path $projectRoot '.cache\electron'
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) {throw 'Python 环境创建失败。'}
    }
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) {throw 'Python 依赖安装失败。'}
    npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) {throw '网页依赖安装失败。'}
    npm.cmd run check
    if ($LASTEXITCODE -ne 0) {throw '网页类型检查失败。'}
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) {throw '网页构建失败。'}
    Write-Output '环境已就绪。运行 启动FormForge.cmd。'
} finally {Pop-Location}
