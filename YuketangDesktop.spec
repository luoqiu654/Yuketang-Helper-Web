# -*- mode: python ; coding: utf-8 -*-
"""Standalone Windows desktop build; existing web build remains unchanged."""
import os
from pathlib import Path

import certifi
from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH)
FRONTEND = Path(os.environ.get('YUKETANG_DESKTOP_FRONTEND', ROOT / 'frontend' / 'dist')).resolve()
if not (FRONTEND / 'index.html').is_file():
    raise SystemExit('Build the frontend before packaging the desktop app.')

webview_data, webview_binaries, webview_hidden = collect_all('webview')
clr_data, clr_binaries, clr_hidden = collect_all('clr_loader')
net_data, net_binaries, net_hidden = collect_all('pythonnet')

a = Analysis(
    [str(ROOT / 'desktop_entry.py')],
    pathex=[str(ROOT / 'backend')],
    datas=[
        (str(FRONTEND), 'static'),
        (str(ROOT / 'frontend' / 'src' / 'locales'), 'locales'),
        (certifi.where(), 'certifi'),
        *collect_data_files('tzdata'),
        *webview_data, *clr_data, *net_data,
    ],
    binaries=[*webview_binaries, *clr_binaries, *net_binaries],
    hiddenimports=[
        *webview_hidden, *clr_hidden, *net_hidden,
        'certifi', 'tzdata', 'platformdirs', 'pystray._win32',
        'uvicorn.logging', 'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
    ],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='YuketangEvan', icon=str(ROOT / 'desktop_assets' / 'app.ico'),
    debug=False, strip=False, upx=False, console=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name='YuketangEvan',
)
