param([string]$Registry = 'https://registry.npmjs.org')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:npm_config_cache = Join-Path $projectRoot '.cache\npm'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectRoot '.cache\playwright'
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = '1'
Push-Location $projectRoot
try {
    npm.cmd --prefix procurement-browser ci --registry=$Registry --fetch-timeout=20000 --fetch-retries=0
    if ($LASTEXITCODE -ne 0) { throw 'Playwright 依赖准备失败，请检查项目内 .cache\npm 日志。' }
    Write-Output 'Playwright 已准备；验证使用系统 Edge，专用会话留在本项目。'
} finally { Pop-Location }
