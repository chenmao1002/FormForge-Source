param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $env:PIP_CACHE_DIR = Join-Path $projectRoot '.cache\pip'
    $env:TEMP = Join-Path $projectRoot '.local\flash-temp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Force $env:TEMP | Out-Null
    if (-not (Test-Path '.venv-flash\Scripts\python.exe')) {
        & '.venv\Scripts\python.exe' -m venv .venv-flash
        if ($LASTEXITCODE -ne 0) { throw 'Flash environment creation failed.' }
    }
    & '.venv-flash\Scripts\python.exe' -m pip install --index-url https://pypi.org/simple -r requirements-flash.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Flash dependency installation failed.' }
    $packPath = Join-Path $projectRoot '.local\cmsis-packs\Keil.STM32G0xx_DFP.2.1.0.pack'
    New-Item -ItemType Directory -Force (Split-Path -Parent $packPath) | Out-Null
    if (-not (Test-Path -LiteralPath $packPath)) {
        & curl.exe --location --fail --retry 2 --max-time 120 'https://www.keil.com/pack/Keil.STM32G0xx_DFP.2.1.0.pack' --output $packPath
        if ($LASTEXITCODE -ne 0) { throw 'CMSIS-Pack download failed.' }
    }
    if ((Get-FileHash -LiteralPath $packPath -Algorithm SHA256).Hash -ne '3076c0f9a6a9a99f7a2325a2e519bed0ac3a3c1db3553748ceeb5b0676645889') { throw 'CMSIS-Pack SHA-256 mismatch.' }
    $additionalPacks = @(
        @{ File = 'Keil.STM32F1xx_DFP.2.4.1.pack'; Sha = '807ea15da5b172b916bbc47b2b87f1e621240ad208d38e82a417a2ef8191e9d1' },
        @{ File = 'Keil.STM32F4xx_DFP.3.1.1.pack'; Sha = '345231106fe697df24bbe9133aeeec1b0383d7891c4adf4f47bbb8b478e9f2f0' }
    )
    foreach ($item in $additionalPacks) {
        $additionalPackPath = Join-Path $projectRoot ('.local\cmsis-packs\' + $item.File)
        if (-not (Test-Path -LiteralPath $additionalPackPath)) {
            & curl.exe --location --fail --retry 2 --max-time 120 ('https://www.keil.com/pack/' + $item.File) --output $additionalPackPath
            if ($LASTEXITCODE -ne 0) { throw 'STM32 CMSIS-Pack download failed.' }
        }
        if ((Get-FileHash -LiteralPath $additionalPackPath -Algorithm SHA256).Hash -ne $item.Sha) { throw 'STM32 CMSIS-Pack SHA-256 mismatch.' }
    }
    $env:FORMFORGE_FLASH_INSTALL_INDEX = 'https://pypi.org/simple'
    & '.venv-flash\Scripts\python.exe' scripts/record-flash-environment.py
    if ($LASTEXITCODE -ne 0) { throw 'Flash environment verification failed.' }
} finally { Pop-Location }
