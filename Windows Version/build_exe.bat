@echo off
setlocal enabledelayedexpansion
title FTP Sync - EXE Builder
chcp 65001 >nul

echo.
echo ====================================================
echo  FTP Sync EXE Builder
echo ====================================================
echo.

REM ── Check Python ─────────────────────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found on PATH.
    echo Install Python 3.10+ from https://python.org
    echo Make sure to check "Add Python to PATH" during install.
    echo.
    pause & exit /b 1
)
echo Python found:
python --version
echo.

REM ── Check required source files ──────────────────────────────────────────
echo Checking required files...
set MISSING=0
for %%F in (ftp_core.py ftp_gui.py ftp_web.py ftp_build.spec ftpsync_bootstrap.py hook-tkinterdnd2.py) do (
    if not exist "%%F" (
        echo   MISSING: %%F
        set MISSING=1
    )
)
if %MISSING%==1 (
    echo.
    echo ERROR: One or more required files are missing.
    echo Make sure all .py, .spec, and hook files are in this folder.
    echo.
    pause & exit /b 1
)
echo   All files present.
echo.

REM ── Step 1: Install / upgrade all dependencies ───────────────────────────
echo [Step 1 of 4] Installing dependencies...
echo   flask, cryptography, pystray, pillow, pyinstaller, tkinterdnd2
echo.
pip install ^
    flask>=3.0.0 ^
    cryptography>=42.0.0 ^
    pystray>=0.19.0 ^
    pillow>=10.0.0 ^
    tkinterdnd2>=0.4.0 ^
    pyinstaller>=6.0.0 ^
    --upgrade --quiet
if %errorlevel% neq 0 (
    echo.
    echo ERROR: pip install failed.  Check your internet connection.
    echo.
    pause & exit /b 1
)
echo   Done.
echo.

REM ── Step 2: Verify tkinterdnd2 installed (drag-and-drop requirement) ─────
echo [Step 2 of 4] Verifying tkinterdnd2...
python -c "import tkinterdnd2; print('  tkinterdnd2 version:', tkinterdnd2.__version__ if hasattr(tkinterdnd2,'__version__') else 'OK')"
if %errorlevel% neq 0 (
    echo.
    echo ERROR: tkinterdnd2 failed to import after install.
    echo Drag-and-drop from Explorer will NOT work in the built EXE.
    echo Try:  pip install tkinterdnd2 --force-reinstall
    echo.
    pause & exit /b 1
)
echo.

REM ── Step 3: Build EXEs with PyInstaller ──────────────────────────────────
echo [Step 3 of 4] Building EXEs with PyInstaller...
echo   This can take 2-5 minutes.  Please wait...
echo.
REM hook-tkinterdnd2.py is picked up via hookspath=["."] inside ftp_build.spec,
REM which bundles the native tkdnd .dll into the EXE for Explorer drag-and-drop.
pyinstaller ftp_build.spec --noconfirm --clean
if %errorlevel% neq 0 (
    echo.
    echo ERROR: PyInstaller build failed.
    echo Read the output above for details.
    echo.
    pause & exit /b 1
)

REM ── Step 4: Done ─────────────────────────────────────────────────────────
echo.
echo [Step 4 of 4] Build complete!
echo.
echo ====================================================
echo  Output files are in the dist\ folder:
echo.
echo   dist\FTPSync_GUI.exe   - Desktop GUI  (double-click to open)
echo   dist\FTPSync_Web.exe   - Web UI (run, then open browser)
echo.
echo  Both EXEs are self-contained — no Python needed to run them.
echo.
echo  settings.json and history.db save next to each EXE in dist\
echo  (created automatically on first run)
echo.
echo  ── Drag-and-drop from Explorer ─────────────────────────────
echo  The tkdnd native library is bundled in the EXE.
echo  Drop files onto the browser file list to upload,
echo  drag remote files out to download.
echo.
echo  ── To update without rebuilding ────────────────────────────
echo  1. Drop updated .py files into dist\updates\
echo  2. Restart the EXE
echo  3. Use Settings ^> Updates to install from inside the app
echo ====================================================
echo.

set /p OPEN=Open the dist folder now? (y/n): 
if /i "%OPEN%"=="y" explorer dist

echo.
pause
