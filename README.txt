# FTP Sync

Automatic FTP/FTPS downloader with a built-in remote file browser.
Monitors a remote FTP server and downloads new files to your local machine
on a schedule — set it up once and forget it.

Available as a **Windows desktop app** and a **Docker/Unraid web UI**.

---

## What it does

- Watches one or more remote FTP folders and downloads anything new
- Tracks every file by SHA256 fingerprint — never downloads the same file twice
- Full remote **file browser**: navigate, upload, download, rename, delete, new folder
- Works with **FTPS/TLS** (explicit, auto-detected) — tested on Whatbox, seedboxes
- Handles **Japanese and CJK filenames** correctly (binary MLSD + encoding fallback)
- Multiple server profiles, drag & drop from Explorer, scheduled sync, system tray

---

## Download

| Version | Platform | Download |
|---------|----------|----------|
| v1.1.2 | Windows (desktop + web UI) | [FTPSync_v1.1.2_Windows.zip](../../releases/latest) |
| v1.1.2 | Docker / Unraid | [FTPSync_v1.1.2_Docker_Unraid.zip](../../releases/latest) |

---

## Windows — Quick Start

1. Install [Python 3.10+](https://python.org) — check **Add Python to PATH**
2. Unzip and double-click **`build_exe.bat`**
3. Find `FTPSync_GUI.exe` and `FTPSync_Web.exe` in the `dist\` folder

No Python needed to run the built EXEs — fully self-contained.

To run from source instead:
```bash
pip install flask cryptography pystray pillow tkinterdnd2
python ftp_gui.py   # desktop window
python ftp_web.py   # web UI at http://localhost:8080
```

---

## Docker — Quick Start
```yaml
# docker-compose.yml
services:
  ftp-sync:
    build: .
    ports:
      - "8080:8080"
    environment:
      - PUID=99    # nobody (Unraid default)
      - PGID=100   # users  (Unraid default)
    volumes:
      - ftp-sync-data:/data
      - /mnt/user/Media/TV:/mnt/tv        # add your folders here
      - /mnt/user/Downloads:/mnt/downloads
volumes:
  ftp-sync-data:
```
```bash
docker compose up -d
# then open http://YOUR-SERVER-IP:8080
```

See the included `unraid_setup.txt` for the full Unraid guide.

---

## Features

**Sync engine**
- Parallel downloads (1–10 connections)
- SHA256 fingerprint tracking — skips already-downloaded files
- Per folder-pair FTP connections (no shared-state bugs)
- Partial file cleanup (`.part` files deleted on cancel or error)
- FTPS explicit TLS with automatic detection and plain FTP fallback
- Binary-mode MLSD with UTF-8 → Shift-JIS → EUC-JP → Latin-1 fallback
- Pre-scan mode, ignore list, FTP error tracking

**Desktop GUI (Windows)**
- Remote file browser with upload, download, rename, delete, new folder
- Drag and drop from Windows Explorer
- Multiple server profiles with instant switching
- Save Session launchers (`.bat`/`.sh`) for one-click reconnect
- Six colour themes, desktop notifications, scheduled sync, system tray
- Single-instance enforcement
- Live update system — drop in `.py` files, no rebuild needed

**Web UI (Docker/Unraid)**
- Same remote file browser, accessible from any device on the network
- Drag and drop uploads in the browser
- Full transfer queue with per-file progress and cancel

---

## Screenshots

*Add screenshots here once you have them — they make a huge difference
for discoverability. Drag images into the GitHub editor to upload.*

---

## Tested With

- Whatbox (FTPS, proftpd, Japanese filenames)
- Generic vsftpd (plain FTP and FTPS)
- Unraid 6.x / 7.x
- Windows 10 / 11
- Docker Desktop (Windows and Mac)

---

## Built With

Python · Flask · Tkinter · tkinterdnd2 · cryptography · pystray · PyInstaller

---

## License

MIT
