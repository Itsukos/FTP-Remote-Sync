FTP Sync v1.1.2 — Windows Edition
===================================
Vibe Coded by Itsuko  |  DM bugs: https://twitter.com/Itsukos

Monitors a remote FTP/FTPS server and automatically downloads new files
to your local machine. Also includes a full remote file browser you can
use to upload, download, rename, delete, and organise files on the server.
Runs as a desktop app or a headless web UI you control from your browser.


====================================================
 WHAT'S IN THIS FOLDER
====================================================

  ftp_core.py           Shared engine (FTP logic, sync worker, history)
  ftp_gui.py            Desktop Tkinter app
  ftp_web.py            Headless Flask web UI
  ftpsync_bootstrap.py  PyInstaller runtime hook (enables live updates)
  ftp_build.spec        PyInstaller build config
  hook-tkinterdnd2.py   PyInstaller hook — bundles drag-and-drop support
  build_exe.bat         One-click EXE builder — double-click this
  requirements.txt      Python package dependencies


====================================================
 QUICK START — BUILD THE EXE (one time)
====================================================

STEP 1 — Install Python 3.10 or newer.
  Download: https://python.org
  IMPORTANT: Check "Add Python to PATH" during install.

STEP 2 — Put all files from this folder in the same directory.

STEP 3 — Double-click build_exe.bat
  This automatically:
    - Installs all dependencies (Flask, cryptography, tkinterdnd2, etc.)
    - Verifies drag-and-drop support is ready
    - Builds both EXEs using PyInstaller
    - Opens the dist\ folder when done

STEP 4 — Your EXEs are in the dist\ folder:
  FTPSync_GUI.exe    Desktop app — double-click to run
  FTPSync_Web.exe    Web UI — run it, then open http://localhost:8080

Both EXEs are fully self-contained. No Python needed to run them.


====================================================
 QUICK START — RUN FROM SOURCE (no build needed)
====================================================

  pip install flask cryptography pystray pillow tkinterdnd2
  python ftp_gui.py      # desktop window
  python ftp_web.py      # web UI at http://localhost:8080


====================================================
 TABS OVERVIEW
====================================================

  Dashboard     FTP credentials, start/stop sync, live log
  Folder Pairs  Add/edit/remove remote to local sync pairs
  Pre-Scan      Preview what would sync before committing
  Ignore List   Paths, folders, or extensions to skip permanently
  History       Every downloaded file with search and CSV export
  FTP Errors    Files that keep failing (suppressed from main log)
  Browser       Full remote file browser (see below)
  Settings      Interval, parallel downloads, themes, notifications,
                scheduled sync, backup/restore, live updates


====================================================
 SYNCING
====================================================

1. Launch FTPSync_GUI.exe
2. Dashboard tab: enter Host, Port, Username, Password, then Save
3. Folder Pairs tab: click Add
     Remote: folder on the FTP server    (e.g. /home/user/uploads)
     Local:  where files land on your PC  (e.g. D:\Downloads\FTPSync)
4. Dashboard: click Start Syncing
5. The app checks on a schedule (default 5 min, change in Settings)

The sync engine:
  - Downloads only new files using SHA256 fingerprint tracking
  - Never re-downloads the same file even if moved or renamed locally
  - Uses a separate FTP connection per folder pair (no shared-state bugs)
  - Saves partial downloads as .part files; renames on success,
    deletes on cancel or error
  - Supports multiple folder pairs running in parallel


====================================================
 REMOTE FILE BROWSER
====================================================

The Browser tab is a full FTP/FTPS file manager built into the app.

Connecting:
  - Select a server profile from the dropdown (or use Dashboard creds)
  - Click Connect — lands in your home directory automatically

Navigation:
  - Left panel: expandable directory tree (lazy-loaded)
  - Right panel: file list with name, size, and date columns
  - Click any column header to sort; click again to reverse
  - Double-click a folder to enter it; click the .. row to go up
  - Back, Forward, Up buttons and an editable address bar

File operations (toolbar buttons or right-click menu):
  - Download    save selected files/folders to the Destination folder
  - Upload      send local files to the current remote directory
  - New Folder  prompt for a name, create it on the server instantly
  - Rename      rename any file or folder in place
  - Delete      delete selected files/folders (asks for confirmation)
  - Select All  select everything in the current directory

Drag and drop (requires tkinterdnd2, installed automatically by build_exe.bat):
  - Drop files from Windows Explorer onto the file list to upload
  - Drop a folder from Explorer onto the file list to upload the whole tree
  - Drag files from the file list out to an Explorer window to download
  - Drop a local folder onto the Destination box to set the download target

Transfer queue:
  - All uploads and downloads show live per-file progress bars
  - Cancel individual transfers at any time
  - Downloads shown in green (down arrow), uploads in accent colour (up arrow)

Save Session:
  - Click "Save Session" to save a .bat (Windows) or .sh (Mac/Linux)
    launcher that reconnects to the current server and directory instantly
  - Double-click the launcher any time to open the app pre-connected


====================================================
 MULTIPLE SERVER PROFILES
====================================================

Save as many FTP/FTPS servers as you like and switch between them
without re-entering credentials each time.

  - Browser tab: server dropdown then Add / Edit / Remove
  - Profiles are stored encrypted in settings.json
  - Switching profiles updates both the browser and the sync engine


====================================================
 FTPS / TLS SUPPORT
====================================================

  - Auto-detects FTPS (explicit TLS on port 21) — no manual toggle
  - Uses encrypted data channel (prot_p) required by servers like Whatbox
  - Falls back to plain FTP automatically if TLS is not supported
  - Test Connection shows landing directory and a file preview


====================================================
 JAPANESE / CJK FILENAME SUPPORT
====================================================

The sync engine and browser handle servers with mixed-encoding filenames:

  - Sends OPTS UTF8 ON on connect
  - Uses binary-mode MLSD to read filenames without text-mode corruption
  - Falls back through UTF-8, Shift-JIS (cp932), EUC-JP, then Latin-1
  - Works with files uploaded from Windows, Linux, and Mac clients


====================================================
 SINGLE INSTANCE
====================================================

Only one copy of the app runs at a time. If you double-click the EXE
while it's already open, the existing window is raised to the front
instead of opening a second copy.

Uses SO_EXCLUSIVEADDRUSE on Windows so this works reliably even after
a crash without a previous instance cleanly releasing the port.


====================================================
 THEMES
====================================================

Six built-in themes available in Settings:
  Dark (default), Mocha, Sakura, Light, Midnight Blue, Solarized

Theme changes apply immediately without a restart.


====================================================
 NOTIFICATIONS AND SCHEDULED SYNC
====================================================

  - Desktop notifications on sync complete or error (toggle in Settings)
  - Scheduled sync: set a time of day to start syncing automatically
  - System tray icon: minimize to tray, restore, start/stop from menu


====================================================
 UPDATING WITHOUT REBUILDING
====================================================

When updated .py files are available:

  METHOD A — Inside the app
    Settings tab: Updates section, Install Update (.py)
    Select the new file(s), then restart the EXE

  METHOD B — Manual
    Drop the new .py files into dist\updates\
    (folder is created automatically next to the EXE on first run)
    Restart the EXE

Settings shows the running version and which files are active overrides.


====================================================
 DATA FILES
====================================================

Created automatically next to the EXE in dist\:

  settings.json   All configuration and FTP credentials
                  (password is AES-encrypted, machine-locked)
  history.db      SQLite database of every downloaded file
  updates\        Drop updated .py files here to patch the app

To migrate to a new machine:
  Settings: Backup & Migration: Export Settings + Export History CSV
  Import on the new machine, then re-enter your password once.


====================================================
 FEATURES AT A GLANCE
====================================================

Sync engine
  + Parallel downloads (1-10 simultaneous connections)
  + SHA256 fingerprint tracking — never re-downloads the same file
  + Per folder-pair FTP connections — no shared-state interference
  + Partial file cleanup (.part files cleaned up on cancel or error)
  + Encrypted credential storage (machine-locked AES key)
  + Pre-scan mode — review before downloading
  + Ignore list — skip paths, folders, or file extensions
  + FTPS explicit TLS auto-detection with plain FTP fallback
  + Japanese / CJK filename support (binary MLSD + encoding fallback)
  + Test Connection with landing directory and file preview

Desktop GUI extras
  + Full remote file browser with upload, download, rename, delete
  + Drag and drop from Windows Explorer (requires tkinterdnd2)
  + Multiple server profiles with instant switching
  + Save Session launchers (.bat / .sh) for one-click reconnect
  + Six colour themes
  + Desktop notifications on sync events
  + Scheduled sync at a set time of day
  + System tray with minimize, restore, and start/stop controls
  + Single-instance enforcement (second launch focuses existing window)
  + Live update system — drop in .py files, no EXE rebuild needed
  + Export and import history CSV and settings JSON


====================================================
 TROUBLESHOOTING
====================================================

Cannot connect (Test Connection fails)
  - Check host, port, and credentials
  - Passive mode is on by default and required by most servers
  - Try port 990 for implicit FTPS, port 21 for explicit FTPS

Files re-downloading after deletion
  - History tracks by SHA256 fingerprint, not by filename
  - If the remote file changed (different size or timestamp), it re-downloads
  - To force-skip a file: add its path to the Ignore List

Drag and drop not working
  - Must be built with build_exe.bat from v1.1.2 (includes hook-tkinterdnd2.py)
  - Running from source: pip install tkinterdnd2

New Folder does nothing
  - Make sure you are connected in the Browser tab first
  - v1.1.2 fixes a missing import that caused this silently

App opens multiple windows
  - Fixed in v1.1.2 — single instance now uses SO_EXCLUSIVEADDRUSE on Windows
  - If it still happens, kill all FTPSync processes in Task Manager and relaunch
