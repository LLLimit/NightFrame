# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs
from pathlib import Path
ROOT = Path(SPECPATH).resolve().parent

datas = [(str(ROOT/'desktop/web'), 'web')]
binaries = []
datas += collect_data_files('webview')
binaries += collect_dynamic_libs('webview')


a = Analysis(
    [str(ROOT/'desktop/app.py')],
    pathex=[str(ROOT/'desktop')],
    binaries=binaries,
    datas=datas,
    hiddenimports=['webview.platforms.winforms'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NightFrame',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ROOT/'desktop/web/app.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='NightFrame',
)
