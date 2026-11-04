$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$descriptor = Join-Path $projectRoot '.local\desktop\runtime.json'
if (-not (Test-Path -LiteralPath $descriptor)) { Write-Output 'FormForge 桌面验证版未运行。'; exit 0 }
$runtime = Get-Content -LiteralPath $descriptor -Raw | ConvertFrom-Json
if ($runtime.protocol -ne 1 -or $runtime.port -lt 1 -or $runtime.port -gt 65535 -or $runtime.token -notmatch '^[a-f0-9]{64}$' -or $runtime.pid -lt 1) { throw '桌面运行信息无效，未操作任何进程。' }
try {
    $desktopProcess = Get-Process -Id $runtime.pid -ErrorAction Stop
    $expected = (Join-Path $projectRoot 'desktop\node_modules\electron\dist\electron.exe')
    if ($desktopProcess.Path -ne $expected) { throw '进程与桌面运行时不一致。' }
    $headers = @{Authorization = ('Bearer ' + $runtime.token)}
    $reply = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($runtime.port)/shutdown" -Headers $headers -TimeoutSec 15
    if (-not $reply.closing) { throw '桌面未确认正常退出。' }
    if (-not $desktopProcess.WaitForExit(15000)) { throw '桌面正在保存会话，请稍后重试，未强制结束。' }
    Write-Output 'FormForge 桌面验证版已正常退出并保存会话；本地工程服务继续运行。'
} catch { throw '桌面未完成正常退出；未强制结束进程，请检查窗口后重试。' }
