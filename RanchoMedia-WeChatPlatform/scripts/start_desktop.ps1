param([switch]$Isolated, [switch]$Build)
$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $workspacePath '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = Join-Path (Split-Path -Parent $workspacePath) '.venv\Scripts\pythonw.exe'
}
if (-not (Test-Path -LiteralPath $pythonPath) -and $env:VIRTUAL_ENV) {
    $pythonPath = Join-Path $env:VIRTUAL_ENV 'Scripts\pythonw.exe'
}
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create .venv and install .[desktop] first.' }
if ($Build -or -not (Test-Path -LiteralPath (Join-Path $workspacePath 'desktop\dist\index.html'))) {
    Push-Location (Join-Path $workspacePath 'desktop')
    try { & npm run build; if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' } }
    finally { Pop-Location }
}
$launchArgs = @('-m', 'rancho_wechat.desktop', '--workspace', ('"' + $workspacePath + '"'))
if ($Isolated) {
    $testState = Join-Path $workspacePath '.runtime\desktop-acceptance\ui-profile'
    $launchArgs += @('--state-dir', ('"' + $testState + '"'), '--no-network')
}
# The visible window is the requested desktop product, not a background helper.
Start-Process -FilePath $pythonPath -ArgumentList $launchArgs -WorkingDirectory $workspacePath
