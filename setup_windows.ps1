param([ValidateSet('deflicker','all')][string]$Models='deflicker',[switch]$SkipPackages,[switch]$PlanOnly)
$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
$env:NIGHTFRAME_HOME=$taskRoot
foreach($name in @('work\tmp','work\pip-cache','work\hf-cache','work\torch-cache')){New-Item -ItemType Directory -Path (Join-Path $taskRoot $name) -Force | Out-Null}
$env:TEMP=Join-Path $taskRoot 'work\tmp';$env:TMP=$env:TEMP
$env:PIP_CACHE_DIR=Join-Path $taskRoot 'work\pip-cache'
$env:HF_HOME=Join-Path $taskRoot 'work\hf-cache'
$env:TORCH_HOME=Join-Path $taskRoot 'work\torch-cache'
$python=Join-Path $taskRoot 'work\research_env\Scripts\python.exe'
if(-not(Test-Path -LiteralPath $python)){
    if(Get-Command py -ErrorAction SilentlyContinue){& py -3.12 -m venv (Join-Path $taskRoot 'work\research_env')}
    elseif(Get-Command python -ErrorAction SilentlyContinue){& python -c "import sys;assert sys.version_info[:2]==(3,12),'Use Python 3.12 x64'";if($LASTEXITCODE -ne 0){throw 'Python 3.12 x64 required'};& python -m venv (Join-Path $taskRoot 'work\research_env')}
    else{throw 'Install Python 3.12 x64 first.'}
    if($LASTEXITCODE -ne 0){throw 'Could not create local Python environment'}
}
if($PlanOnly){& $python (Join-Path $taskRoot 'scripts\bootstrap_models.py') --models $Models --plan;exit $LASTEXITCODE}
foreach($tool in @('ffmpeg','ffprobe','curl.exe')){if(-not(Get-Command $tool -ErrorAction SilentlyContinue)){throw "Required command missing: $tool"}}
if(-not $SkipPackages){
    & $python -m pip install --upgrade pip
    if($LASTEXITCODE -ne 0){throw 'pip setup failed'}
    & $python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu128
    if($LASTEXITCODE -ne 0){throw 'CUDA PyTorch install failed'}
    & $python -m pip install -r (Join-Path $taskRoot 'requirements-runtime.txt')
    if($LASTEXITCODE -ne 0){throw 'Runtime package install failed'}
}
& $python (Join-Path $taskRoot 'scripts\bootstrap_models.py') --models $Models
if($LASTEXITCODE -ne 0){throw 'Model download/validation failed'}
& $python -c "import torch,cv2;assert torch.cuda.is_available(),'NVIDIA CUDA is unavailable';print(torch.__version__,torch.cuda.get_device_name(0),cv2.__version__)"
if($LASTEXITCODE -ne 0){throw 'GPU runtime check failed'}
Write-Output 'NightFrame runtime is ready. Run start.cmd.'
