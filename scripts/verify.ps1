param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $repoRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { $Python = (Get-Command python).Source }
function Invoke-Python([string[]]$Arguments) {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Python verification failed: ' + ($Arguments -join ' ')) }
}
Push-Location $repoRoot
try {
    Invoke-Python -Arguments @('scripts/check_public_tree.py')
    Invoke-Python -Arguments @('-m','pytest','tests','-q')
    Invoke-Python -Arguments @('-m','ruff','check','scripts','tests')
    foreach ($module in @('RanchoMediaOS','RanchoMedia-WeChatPlatform')) {
        Push-Location $module
        try {
            Invoke-Python -Arguments @('-m','pytest','-q')
            Invoke-Python -Arguments @('-m','ruff','check','.')
            if ($module -eq 'RanchoMediaOS') { Invoke-Python -Arguments @('-m','ruff','format','--check','.') }
            else { Invoke-Python -Arguments @('-m','ruff','format','--check','src','tests','scripts') }
            if ($module -eq 'RanchoMediaOS') { Invoke-Python -Arguments @('-m','mypy') }
            else { Invoke-Python -Arguments @('-m','mypy','--strict','src') }
        } finally { Pop-Location }
    }
    Push-Location 'RanchoMedia-WeChatPlatform/desktop'
    try {
        & npm test
        if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
    Invoke-Python -Arguments @('-m','pip','check')
} finally { Pop-Location }
