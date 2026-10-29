$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$moduleRoot = Join-Path $repoRoot 'RanchoMedia-WeChatPlatform'
$sdkRoot = Join-Path $moduleRoot '.runtime\ecosystem\wechat-bili\.venv'
$sdkPython = Join-Path $sdkRoot 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $sdkPython)) {
    & python -m venv $sdkRoot
    if ($LASTEXITCODE -ne 0) { throw 'SDK venv creation failed.' }
}
# Existing credentials/configuration are never read, replaced or copied.
& $sdkPython -m pip install 'wechatpy @ git+https://github.com/wechatpy/wechatpy.git@a695b89891e0473113c946a3a44d948fcf217404'
if ($LASTEXITCODE -ne 0) { throw 'Pinned SDK installation failed.' }
& $sdkPython -c "import importlib.metadata,json; d=importlib.metadata.distribution('wechatpy'); assert json.loads(d.read_text('direct_url.json'))['vcs_info']['commit_id']=='a695b89891e0473113c946a3a44d948fcf217404'; print('SDK pin verified. No account or platform request was made.')"
if ($LASTEXITCODE -ne 0) { throw 'SDK pin verification failed.' }
