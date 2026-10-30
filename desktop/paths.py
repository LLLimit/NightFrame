"""Resolve an installed bundle or a source checkout without machine paths."""
from pathlib import Path
import os, sys

def project_root():
    configured=os.environ.get('NIGHTFRAME_HOME')
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys,'frozen',False):
        folder=Path(sys.executable).resolve().parent
        if (folder/'desktop/worker.py').is_file():return folder
        if (folder.parent/'desktop/worker.py').is_file():return folder.parent
        return folder
    return Path(__file__).resolve().parent.parent

ROOT=project_root()
os.environ['NIGHTFRAME_HOME']=str(ROOT)

def gpu_python():
    configured=os.environ.get('NIGHTFRAME_PYTHON')
    return Path(configured).expanduser().resolve() if configured else ROOT/'work/research_env/Scripts/python.exe'
