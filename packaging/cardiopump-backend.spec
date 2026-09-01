from PyInstaller.utils.hooks import collect_data_files


a = Analysis(
    [os.path.join(SPECPATH, "backend_entry.py")],
    pathex=[os.path.join(SPECPATH, "..", "src")],
    binaries=[],
    datas=collect_data_files("cardiopump_backend"),
    hiddenimports=[],
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
    a.binaries,
    a.datas,
    [],
    name="cardiopump-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
