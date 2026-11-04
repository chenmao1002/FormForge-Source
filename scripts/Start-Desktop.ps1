param([int]$Port = 17840)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'Start.ps1') -Port $Port -NoBrowser
$electronExe = Join-Path $projectRoot 'desktop\node_modules\electron\dist\electron.exe'
if (-not (Test-Path -LiteralPath $electronExe)) { throw '缺少 Electron，请先运行 scripts\Setup-Desktop.ps1。' }
$env:FORMFORGE_PORT = "$Port"
$env:ELECTRON_CACHE = Join-Path $projectRoot '.cache\electron'
$localDir = Join-Path $projectRoot '.local\desktop'
New-Item -ItemType Directory -Path $localDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$process = Start-Process -FilePath $electronExe -ArgumentList @('desktop/main.cjs') -WorkingDirectory $projectRoot -WindowStyle Normal -PassThru -RedirectStandardOutput (Join-Path $localDir "desktop-$stamp.out.log") -RedirectStandardError (Join-Path $localDir "desktop-$stamp.err.log")
Write-Output "FormForge 桌面验证版已启动（进程 $($process.Id)），仅报价。"
