# hook-tkinterdnd2.py
# PyInstaller hook for tkinterdnd2.
#
# tkinterdnd2 ships pre-compiled native Tcl/Tk extension binaries
# (tkdnd .dll on Windows, .so on Linux, .dylib on macOS).
# Without this hook those binaries are not included in the EXE and
# drag-and-drop silently does nothing at runtime.
#
# Usage: place this file next to your project, then build with:
#   pyinstaller ftp_build.spec --additional-hooks-dir=.

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all('tkinterdnd2')
