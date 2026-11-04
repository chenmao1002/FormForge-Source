param([int]$Port = 17840, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw '缺少本地环境，请先运行 scripts\Setup.ps1。' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'web\assets\app.js'))) { throw '网页尚未构建，请先运行 scripts\Setup.ps1。' }
$url = "http://127.0.0.1:$Port"
$running = $false
try { $health = Invoke-RestMethod -Uri "$url/health" -TimeoutSec 2; $running = $health.app -eq 'FormForge' } catch { }
if (-not $running) {
    $env:PYTHONUTF8 = '1'
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    $env:FORMFORGE_ROOT = $projectRoot
    $runtime = Join-Path $projectRoot '.local'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $process = Start-Process -FilePath $pythonExe -ArgumentList @('-m','formforge','--port',"$Port") -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime "server-$stamp.out.log") -RedirectStandardError (Join-Path $runtime "server-$stamp.err.log")
    @{pid=$process.Id;port=$Port;startedAt=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtime 'server.json') -Encoding UTF8
    for ($attempt=0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        if ($process.HasExited) { throw 'FormForge 服务启动失败，请查看 .local 中的日志。' }
        try { $health=Invoke-RestMethod -Uri "$url/health" -TimeoutSec 1; if ($health.app -eq 'FormForge') {$running=$true;break} } catch { }
    }
    if (-not $running) { throw 'FormForge 服务尚未就绪，请查看 .local 中的日志。' }
}
Write-Output "FormForge: $url"
if (-not $NoBrowser) { Start-Process $url }
