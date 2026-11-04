param([string]$DownloadProxy = '', [switch]$ChinaMirror)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:npm_config_cache = Join-Path $projectRoot '.cache\npm'
$env:ELECTRON_CACHE = Join-Path $projectRoot '.cache\electron'
$env:electron_config_cache = $env:ELECTRON_CACHE
if ($ChinaMirror) { $env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/' }
Push-Location $projectRoot
try {
    npm.cmd --prefix desktop ci
    if ($LASTEXITCODE -ne 0) { throw 'Electron 依赖准备失败，请检查项目 .cache\npm 日志。' }
    if ($DownloadProxy) {
        $proxyUri = [Uri]$DownloadProxy
        if ($proxyUri.Scheme -notin @('http', 'https')) { throw 'DownloadProxy 需要 HTTP 或 HTTPS 代理地址。' }
        $version = (Get-Content -LiteralPath (Join-Path $projectRoot 'desktop\node_modules\electron\package.json') -Raw | ConvertFrom-Json).version
        $filename = "electron-v$version-win32-x64.zip"
        $url = if ($ChinaMirror) { "https://npmmirror.com/mirrors/electron/v$version/$filename" } else { "https://github.com/electron/electron/releases/download/v$version/$filename" }
        New-Item -ItemType Directory -Path $env:ELECTRON_CACHE -Force | Out-Null
        $archive = Join-Path $env:ELECTRON_CACHE $filename
        $checksum = (Get-Content -LiteralPath (Join-Path $projectRoot 'desktop\node_modules\electron\checksums.json') -Raw | ConvertFrom-Json).$filename
        if (-not $checksum) { throw '当前 Electron 包未包含 Windows x64 运行时摘要。' }
        $cachedValid = (Test-Path -LiteralPath $archive) -and ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -eq $checksum)
        if (-not $cachedValid) {
            curl.exe --fail --location --retry 2 --proxy $DownloadProxy --output $archive $url
            if ($LASTEXITCODE -ne 0) { throw '官方 Electron 运行时下载失败；未关闭证书验证。' }
        }
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $checksum) { throw 'Electron 运行时 SHA-256 与官方 npm 包记录不一致。' }
        $dist = Join-Path $projectRoot 'desktop\node_modules\electron\dist'
        Expand-Archive -LiteralPath $archive -DestinationPath $dist -Force
        [IO.File]::WriteAllText((Join-Path $projectRoot 'desktop\node_modules\electron\path.txt'), 'electron.exe')
        @{version=$version;source=$url;sha256=$checksum;verifiedAt=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $env:ELECTRON_CACHE 'verified-runtime.json') -Encoding UTF8
    }
    node (Join-Path $projectRoot 'desktop\node_modules\electron\install.js')
    if ($LASTEXITCODE -ne 0) { throw 'Electron 运行时准备失败，请检查项目 .cache\electron 缓存与网络连接。' }
    Write-Output 'Electron 验证版已准备，可运行 启动FormForge桌面.cmd。'
} finally { Pop-Location }
