$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$descriptor = Join-Path $projectRoot '.local\playwright\runtime.json'
if (-not (Test-Path -LiteralPath $descriptor)) { Write-Output '独立询价执行器尚未运行。'; exit 0 }
$runtime = Get-Content -LiteralPath $descriptor -Raw | ConvertFrom-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($runtime.port)/command" -Headers @{Authorization="Bearer $($runtime.token)"} -ContentType 'application/json' -Body '{"action":"shutdown"}' | Out-Null
if (Get-Process -Id $runtime.pid -ErrorAction SilentlyContinue) { Wait-Process -Id $runtime.pid -Timeout 10 }
Write-Output '独立询价执行器已正常关闭并保存会话。'
