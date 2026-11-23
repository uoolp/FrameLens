# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：pyinstaller FrameLens.spec --noconfirm"""

a = Analysis(
    ['app/main.py'],
    pathex=['app'],
    binaries=[],
    datas=[('bin/PresentMon.exe', 'bin'), ('bin/icon.ico', 'bin')],
    hiddenimports=['psutil', 'psutil._pswindows', 'history_window',
                   'session_index', 'chart_hover', 'controller', 'config',
                   'target_picker'],
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
    name='FrameLens',
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
    icon='bin/icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FrameLens',
)
