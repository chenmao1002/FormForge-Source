param([int]$Port = 17840)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'Start.ps1') -Port $Port -NoBrowser
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'procurement-browser\node_modules\playwright\package.json'))) {
    throw '请先运行 scripts\Setup-ProcurementBrowser.ps1。'
}
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectRoot '.cache\playwright'
$taskDir = Join-Path $projectRoot '.local\playwright'
New-Item -ItemType Directory -Path $taskDir -Force | Out-Null
$descriptor = Join-Path $taskDir 'runtime.json'
if (Test-Path -LiteralPath $descriptor) {
    try {
        $existing = Get-Content -LiteralPath $descriptor -Raw | ConvertFrom-Json
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:$($existing.port)/status" -Headers @{Authorization="Bearer $($existing.token)"} -TimeoutSec 2
        if ($status.adapter -eq 'playwright-independent/1' -and $status.pid -eq $existing.pid) {
            Write-Output "独立询价执行器已在运行（进程 $($existing.pid)）。"
            exit 0
        }
    } catch { }
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$worker = Start-Process -FilePath (Get-Command node.exe).Source -ArgumentList @('procurement-browser/worker.cjs') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskDir "worker-$stamp.out.log") -RedirectStandardError (Join-Path $taskDir "worker-$stamp.err.log")
Write-Output "独立询价执行器已启动（进程 $($worker.Id)），付款及生产放行不可用。"
