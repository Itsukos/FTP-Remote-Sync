# ftp_build.spec
# PyInstaller spec file — builds TWO separate EXEs:
#   dist\FTPSync_GUI.exe   — desktop tkinter app
#   dist\FTPSync_Web.exe   — headless Flask web UI (no console window by default)
#
# Run from your project folder:
#   pyinstaller ftp_build.spec
#
# Requirements (run once first):
#   pip install pyinstaller flask

import sys
from PyInstaller.building.api import PYZ, EXE, COLLECT
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_all

# ── Collect tkinterdnd2 — bundles the native tkdnd .dll/.so into the EXE ──────
# Without this, drag-and-drop from Explorer silently does nothing at runtime.
_dnd_datas, _dnd_binaries, _dnd_hidden = collect_all('tkinterdnd2')

# ── Shared hidden imports needed by both builds ────────────────────────────────
HIDDEN = ['tkinterdnd2'] + _dnd_hidden + [
    "flask",
    "flask.templating",
    "jinja2",
    "jinja2.ext",
    "werkzeug",
    "werkzeug.serving",
    "werkzeug.routing",
    "sqlite3",
    "ftplib",
    "hashlib",
    "threading",
    "webbrowser",
    "socket",
    "cryptography",
    "cryptography.fernet",
    "cryptography.hazmat",
    "cryptography.hazmat.primitives",
    "cryptography.hazmat.primitives.hashes",
    "cryptography.hazmat.primitives.kdf.pbkdf2",
    "cryptography.hazmat.backends",
    "cryptography.hazmat.backends.openssl",
    "winreg",         # Windows machine-id (ignored on Linux/Mac)
    "pystray",
    "pystray._win32",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
]

# ══════════════════════════════════════════════════════════════════════════════
# BUILD 1 — Desktop GUI  (ftp_gui.py + ftp_core.py)
# ══════════════════════════════════════════════════════════════════════════════

gui_analysis = Analysis(
    ["ftp_gui.py"],
    pathex=["."],
    binaries=_dnd_binaries,
    datas=_dnd_datas,
    hiddenimports=HIDDEN + ["tkinter", "_tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox"],
    hookspath=["."],          # picks up hook-tkinterdnd2.py in the project folder
    hooksconfig={},
    runtime_hooks=["ftpsync_bootstrap.py"],
    excludes=["matplotlib", "numpy", "pandas"],
    noarchive=False,
)

gui_pyz = PYZ(gui_analysis.pure, gui_analysis.zipped_data)

gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    gui_analysis.binaries,
    gui_analysis.zipfiles,
    gui_analysis.datas,
    [],
    name="FTPSync_GUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,               # compress — requires UPX installed (optional)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # no black console window behind the GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,              # swap in an .ico path here if you have one
    onefile=True,           # single .exe file
)

# ══════════════════════════════════════════════════════════════════════════════
# BUILD 2 — Web UI  (ftp_web.py + ftp_core.py)
# ══════════════════════════════════════════════════════════════════════════════

web_analysis = Analysis(
    ["ftp_web.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["ftpsync_bootstrap.py"],
    excludes=["tkinter", "_tkinter", "matplotlib", "numpy", "pandas", "PIL"],
    noarchive=False,
)

web_pyz = PYZ(web_analysis.pure, web_analysis.zipped_data)

web_exe = EXE(
    web_pyz,
    web_analysis.scripts,
    web_analysis.binaries,
    web_analysis.zipfiles,
    web_analysis.datas,
    [],
    name="FTPSync_Web",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,           # keep console so you can see the URL printed at startup
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,
    onefile=True,
)
