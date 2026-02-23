FTP Sync v1.1.2 — Windows Edition
===================================
Vibe Coded by Itsuko  |  DM bugs: https://twitter.com/Itsukos

A desktop app that monitors a remote FTP server and automatically downloads
new files to your local machine. Runs as either a windowed desktop app or a
background web UI you control from your browser.


====================================================
 WHAT'S IN THIS FOLDER
====================================================

  ftp_core.py           Shared engine (FTP logic, history, sync worker)
  ftp_gui.py            Desktop Tkinter window app
  ftp_web.py            Browser-based Flask web UI (headless)
  ftpsync_bootstrap.py  PyInstaller runtime hook (update system)
  ftp_build.spec        PyInstaller build config
  build_exe.bat         One-click EXE builder — double-click this
  requirements.txt      Python package dependencies


====================================================
 QUICK START — BUILD THE EXE (one time)
====================================================

STEP 1 — Install Python 3.10 or newer on your build machine.
  Download: https://python.org
  IMPORTANT: Check "Add Python to PATH" during install.

STEP 2 — Put all files from this folder in the same place.

STEP 3 — Double-click build_exe.bat
  This will automatically:
    • Install Flask, cryptography, and PyInstaller via pip
    • Build both EXEs using PyInstaller
    • Open the dist\ output folder when done

STEP 4 — Find your ready-to-run EXEs in the dist\ folder:
  FTPSync_GUI.exe    Desktop window app — double-click to run
  FTPSync_Web.exe    Web UI — run it, then open http://localhost:8080

Both EXEs are fully self-contained. No Python needed to run them.
Copy either EXE anywhere — it works standalone.


====================================================
 USING THE APP
====================================================

DESKTOP GUI (FTPSync_GUI.exe)
  1. Double-click FTPSync_GUI.exe
  2. Fill in your FTP server Host, Port, Username, Password
  3. Check "Save" so it remembers your credentials
  4. Go to Folder Pairs tab — click + Add
       Remote: the folder on your FTP server  (e.g. /home/user/uploads)
       Local:  where files land on your PC    (e.g. D:\Downloads\FTPSync)
  5. Back on Dashboard — click "Start Syncing"
  6. The app checks every 5 minutes (change in Settings)

WEB UI (FTPSync_Web.exe)
  1. Run FTPSync_Web.exe — a console window shows the URL
  2. Open your browser at http://localhost:8080
  3. Same steps as above via the web interface
  4. Access from other devices on your network via the LAN IP shown in Settings


====================================================
 TABS OVERVIEW
====================================================

  Dashboard     FTP credentials, start/stop sync, active transfers, log
  Folder Pairs  Add/edit/remove remote→local sync pairs
  Pre-Scan      Preview what would download before committing
  Ignore List   Paths/extensions to skip permanently
  History       Every downloaded file with search and export
  FTP Errors    Files that keep failing (suppressed from main log)
  Settings      Interval, parallel downloads, backup/restore, updates


====================================================
 UPDATING WITHOUT REBUILDING THE EXE
====================================================

When new .py files are available:

  METHOD A — In the app (recommended)
    Settings tab → Updates → Install Update (.py)
    Select the new files → Restart the EXE

  METHOD B — Manual
    Drop the new .py files into the  dist\updates\  folder
    (created automatically next to the EXE on first run)
    Restart the EXE

The Settings tab shows which version is running and which files
are active as overrides.

  ⚠  IMPORTANT: The update system requires the EXE to have been
     built with the v1.0.0 bootstrap (ftpsync_bootstrap.py).
     If you built an earlier EXE, rebuild once with build_exe.bat
     and updates will work correctly going forward.


====================================================
 DATA FILES (created automatically on first run)
====================================================

Both are stored next to the EXE in the dist\ folder:

  settings.json     Your FTP credentials and configuration
                    (password is AES-encrypted, machine-locked)
  history.db        SQLite database of every downloaded file
  updates\          Drop new .py files here to update the app

To move to a new machine:
  Settings → Backup & Migration → Export Settings + Export History CSV
  Import them on the new machine, re-enter your password.


====================================================
 FEATURES
====================================================

  ✓  Parallel downloads (1–10 simultaneous connections)
  ✓  SHA256 fingerprint tracking — never downloads the same file twice
  ✓  Encrypted credential storage (machine-locked AES key)
  ✓  Pre-scan mode — review what will download before it happens
  ✓  Ignore list — skip paths, folders, or file types
  ✓  FTP Errors tab — repeated failures suppressed from main log
  ✓  Export/import history CSV and settings JSON
  ✓  Update system — drop in new .py files, no rebuild needed
  ✓  FTPS (TLS) auto-detection with plain FTP fallback
  ✓  Test Connection button
  ✓  Stop button kills active transfers immediately (closes raw sockets)


====================================================
 TROUBLESHOOTING
====================================================

"Cannot connect" on Test Connection
  → Check host/port/credentials are correct
  → Some servers require passive mode — this is enabled by default
  → Try port 990 if port 21 times out (implicit FTPS)

Files downloading again after deletion
  → History tracks by fingerprint, not filename
  → If the remote file changed (new mtime/size) it will re-download
  → To force skip: add the path to the Ignore List

Stop button slow to respond
  → Fixed in v1.0.0 — stop now closes the raw data socket immediately

Updates not loading after installing .py files
  → Make sure you're using an EXE built with build_exe.bat from v1.0.0
  → The updates\ folder must be in the same directory as the EXE
  → Restart the EXE after installing updates


====================================================
 BUILDING FROM SOURCE (no EXE)
====================================================

  pip install flask cryptography
  python ftp_gui.py      # desktop window
  python ftp_web.py      # web UI at http://localhost:8080
