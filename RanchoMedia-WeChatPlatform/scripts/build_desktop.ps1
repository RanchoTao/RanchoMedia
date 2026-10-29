$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $workspacePath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = Join-Path (Split-Path -Parent $workspacePath) '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $pythonPath) -and $env:VIRTUAL_ENV) {
    $pythonPath = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create and install a Python development environment first.' }
Push-Location $workspacePath
try {
    & $pythonPath scripts/build_desktop_icon.py
    if ($LASTEXITCODE -ne 0) { throw 'Icon build failed.' }
    Push-Location desktop
    try { & npm ci; if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }; & npm run build; if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' } }
    finally { Pop-Location }
    & $pythonPath -m PyInstaller --noconfirm --clean --windowed --onedir --name RanchoMedia --icon desktop/public/rancho.ico --paths src --add-data 'desktop/dist;desktop' --collect-all webview --collect-all pythonnet --collect-all clr_loader --hidden-import webview.platforms.winforms --hidden-import webview.platforms.edgechromium --hidden-import rancho_wechat.wechat_diagnostics scripts/desktop_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Windows package build failed.' }
    Write-Output (Join-Path $workspacePath 'dist\RanchoMedia\RanchoMedia.exe')
}
finally { Pop-Location }
