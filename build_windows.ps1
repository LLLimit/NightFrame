$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
$env:TEMP=Join-Path $taskRoot 'work\tmp';$env:TMP=$env:TEMP
$env:PIP_CACHE_DIR=Join-Path $taskRoot 'work\pip-cache'
$env:PYINSTALLER_CONFIG_DIR=Join-Path $taskRoot 'work\build-cache'
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$python=Join-Path $taskRoot 'work\desktop_env\Scripts\python.exe'
if(-not(Test-Path -LiteralPath $python)){& py -3.12 -m venv (Join-Path $taskRoot 'work\desktop_env');if($LASTEXITCODE -ne 0){throw 'Python 3.12 x64 is required'}}
& $python -m pip install pywebview==6.2.1 pyinstaller==6.22.3 Pillow
if($LASTEXITCODE -ne 0){throw 'Build dependencies failed'}
& $python -m PyInstaller --noconfirm (Join-Path $taskRoot 'desktop\NightFrame.spec') --workpath (Join-Path $taskRoot 'work\build') --distpath (Join-Path $taskRoot 'dist')
if($LASTEXITCODE -ne 0){throw 'App build failed'}
$bundle=Join-Path $taskRoot 'dist\NightFrame'
foreach($name in @('desktop','src_v03','src_v06','scripts','models.lock.json','requirements-runtime.txt','setup_windows.ps1','start.cmd')){
    Copy-Item -LiteralPath (Join-Path $taskRoot $name) -Destination $bundle -Recurse -Force
}
Write-Output 'App built in dist\NightFrame. Run setup_windows.ps1 in that directory before first use.'
