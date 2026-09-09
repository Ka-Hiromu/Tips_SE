# -*- mode: python ; coding: utf-8 -*-
"""
Tips_SE PyInstaller スペック（onedir / windowed）。
PortAudio・libsndfile・soxr のネイティブバイナリを確実に同梱する。
ビルド: .venv\\Scripts\\pyinstaller Tips_SE.spec --noconfirm
出力  : dist\\Tips_SE\\Tips_SE.exe
"""
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas, binaries, hiddenimports = [], [], []

# 主要パッケージ（データ・バイナリ・サブモジュールを丸ごと収集）
for pkg in ("sounddevice", "soundfile", "soxr"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# PortAudio / libsndfile の DLL を明示的に同梱（取りこぼし防止）
for data_pkg in ("_sounddevice_data", "_soundfile_data"):
    datas += collect_data_files(data_pkg)

hiddenimports += ["soxr", "cffi", "_cffi_backend", "qrcode"]

# スマホ・リモートの配信ページを同梱
datas += [("remote_web", "remote_web")]

# 同梱フォント（Noto Sans JP）
datas += [("assets/fonts/NotoSansJP.ttf", "assets/fonts")]

# ロゴ（スプラッシュ）とアイコン
datas += [("assets/logo.png", "assets"), ("assets/icon.ico", "assets")]


a = Analysis(
    ["src/main_app.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PySide6.QtWebEngineCore",
              "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtQuick3D"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Tips_SE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # windowed（コンソール窓なし）
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Tips_SE",
)
