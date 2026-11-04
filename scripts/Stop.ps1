param([switch]$Force)
$ErrorActionPreference = 'Stop'
function Stop-VerifiedServiceProcess {
    param($Expected)
    try {
        Stop-Process -Id $Expected.ProcessId -ErrorAction Stop
    } catch {
        # The venv launcher can exit naturally after its child is stopped.
        # Accept only the disappearance of this already verified identity;
        # a still-running owned process must keep the original stop failure.
        $remaining = Get-CimInstance Win32_Process -Filter "ProcessId = $($Expected.ProcessId)"
        if ($remaining -and $remaining.CreationDate -eq $Expected.CreationDate -and $remaining.CommandLine -eq $Expected.CommandLine) { throw }
    }
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$recordPath = Join-Path $projectRoot '.local\server.json'
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Output '没有本项目的服务记录。'; exit 0 }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$ownedProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)"
if (-not $ownedProcess) { Write-Output '服务已停止。'; exit 0 }
$expectedPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if ($ownedProcess.ExecutablePath -ne $expectedPython -or $ownedProcess.CommandLine -notmatch '\s-m\s+formforge(?:\s|$)') {
    throw '记录对应的进程不属于本项目，未停止。'
}
if (-not $Force) {
    $token = (Get-Content -LiteralPath (Join-Path $projectRoot '.local\api-token.txt') -Raw).Trim()
    $projects = Invoke-RestMethod -Uri "http://127.0.0.1:$($record.port)/api/projects" -Headers @{Authorization="Bearer $token"}
    foreach ($project in $projects) {
        # All job states are retained in compact history; avoid downloading
        # every historical CAD input and report just to check for active work.
        $context = Invoke-RestMethod -Uri "http://127.0.0.1:$($record.port)/api/projects/$($project.id)?compact=true" -Headers @{Authorization="Bearer $token"}
        if ($context.jobs | Where-Object {$_.status -in @('queued','running')}) { throw '仍有任务运行，请在网页取消并等待任务结束后停止服务。' }
    }
}
# Close only FormForge's owned browser so its profile is saved and released.
try {
    $supplierToken = (Get-Content -LiteralPath (Join-Path $projectRoot '.local\api-token.txt') -Raw).Trim()
    $supplierSession = Invoke-RestMethod -Uri "http://127.0.0.1:$($record.port)/api/supplier/session" -Headers @{Authorization="Bearer $supplierToken"}
    try {
        $supplierExecutor = Invoke-RestMethod -Uri "http://127.0.0.1:$($record.port)/api/supplier/schema" -Headers @{Authorization="Bearer $supplierToken"}
        if ($supplierExecutor.activeOperations -gt 0) { throw '仍有下单浏览器步骤运行，请等待其返回后停止服务。' }
    } catch {
        if (-not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 404) { throw }
    }
    if ($supplierSession.browserRunning) {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($record.port)/api/supplier/session" -Headers @{Authorization="Bearer $supplierToken"} -ContentType 'application/json' -Body (@{action='close';requestId=('stop-browser-'+[guid]::NewGuid().ToString('N'))}|ConvertTo-Json) | Out-Null
    }
} catch {
    if (-not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 404) { throw }
}
# Windows 的 venv 启动器会另启 Python 子进程。先停止已核实的服务子进程，
# 否则仅退出启动器会留下旧监听，下一次 Start.ps1 会误用旧服务。
$serviceChildren = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($record.pid)" | Where-Object {
    $_.CommandLine -match '\s-m\s+formforge(?:\s|$)' -and
    $_.CommandLine -match "\s--port\s+$($record.port)(?:\s|$)"
})
foreach ($child in $serviceChildren) {
    $currentChild = Get-CimInstance Win32_Process -Filter "ProcessId = $($child.ProcessId)"
    if ($currentChild -and $currentChild.CreationDate -eq $child.CreationDate -and $currentChild.CommandLine -eq $child.CommandLine) {
        Stop-VerifiedServiceProcess $currentChild
    }
}
$currentParent = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)"
if ($currentParent -and $currentParent.CreationDate -eq $ownedProcess.CreationDate -and $currentParent.CommandLine -eq $ownedProcess.CommandLine) {
    Stop-VerifiedServiceProcess $currentParent
}
Write-Output 'FormForge 服务已停止。'
