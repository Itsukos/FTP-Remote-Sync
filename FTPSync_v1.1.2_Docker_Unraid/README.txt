FTP Sync v1.1.2 — Docker / Unraid Edition
==========================================
Vibe Coded by Itsuko  |  DM bugs: https://twitter.com/Itsukos

Monitors a remote FTP/FTPS server and automatically downloads new files
to your local machine. Controlled entirely through a web UI you access
from any browser on your network. Designed for always-on NAS setups,
Unraid, Synology, and any Linux server running Docker.


====================================================
 WHAT'S IN THIS FOLDER
====================================================

  ftp_core.py           The sync engine (FTP logic, history, worker)
  ftp_web.py            The web UI (runs inside the container)
  Dockerfile            Builds the Docker image
  docker-compose.yml    Start the container — edit this for your folders
  requirements.txt      Python dependencies (used by Dockerfile)
  unraid_template.xml   Unraid community app template
  unraid_setup.txt      Step-by-step Unraid guide


====================================================
 QUICK START — ANY MACHINE WITH DOCKER
====================================================

STEP 1 — Edit docker-compose.yml

  Under the "volumes:" section, add a line for every folder you want
  the app to be able to write files into.

  Format:
    - "/real/path/on/host:/path/inside/container"

  Examples:
    - "/mnt/user/Media/TV:/mnt/tv"
    - "/mnt/user/Media/Anime:/mnt/anime"
    - "/mnt/user/Downloads:/mnt/downloads"
    - "D:/Media/TV:/mnt/tv"            (Windows with Docker Desktop)

  The right-hand path (/mnt/tv etc.) is what you type into the app
  when setting a Local Path in Folder Pairs. Name them however you like.

STEP 2 — Start the container

    docker compose up -d

STEP 3 — Open the web UI

    http://localhost:8080
    or
    http://YOUR-SERVER-IP:8080

STEP 4 — Configure in the web UI

  - Dashboard: enter FTP credentials then Save
  - Folder Pairs: set Remote Path (on the FTP server) and Local Path
    (the right-hand path from your volumes, e.g. /mnt/tv)
  - Dashboard: click Start Sync

The app checks the FTP server on a schedule and downloads new files
automatically. That's it.


====================================================
 WEB UI TABS
====================================================

  Dashboard     FTP credentials, start/stop sync, live log
  Folder Pairs  Add/edit/remove remote to local sync pairs
  Pre-Scan      Preview what would sync before committing
  Ignore List   Paths, folders, or extensions to skip permanently
  History       Every downloaded file with search and CSV export
  FTP Errors    Files that keep failing (suppressed from main log)
  Browser       Full remote file browser (see below)
  Settings      Sync interval, parallel downloads, notifications,
                backup/restore, live updates, folder mount info


====================================================
 REMOTE FILE BROWSER (web UI)
====================================================

The Browser tab in the web UI is a full FTP/FTPS file manager.

  - Navigate the server directory tree in the left panel
  - Browse files with name, size, and date in the right panel
  - Sort by any column
  - Download files and whole folders to your local machine
  - Upload files from your local machine to the remote server
  - Create new folders on the server
  - Rename and delete files and folders
  - Transfer queue shows live per-file progress with cancel support
  - Drag and drop files over the file list in your browser to upload
  - Multiple server profiles with instant switching

The browser connects using its own FTP session separate from the sync
engine, so browsing never interrupts an active sync.


====================================================
 RUNNING AS NON-ROOT (PUID / PGID)
====================================================

The container runs as a non-root user so downloaded files are owned
by your chosen user instead of root — important for NAS setups.

Set PUID and PGID in docker-compose.yml to match the owner of your
download folders on the host:

  Most Linux desktops:     PUID=1000  PGID=1000
  Unraid (nobody:users):   PUID=99    PGID=100
  Synology (admin):        PUID=1026  PGID=101  (varies — check: id admin)
  Mac Docker Desktop:      PUID=501   PGID=20
  Windows Docker Desktop:  leave at 1000/1000  (WSL2 handles ownership)

To find your own UID/GID on Linux or Mac:   id

After changing PUID/PGID, just run:   docker compose up -d
No rebuild needed.


====================================================
 FTPS / TLS SUPPORT
====================================================

  - Auto-detects FTPS (explicit TLS on port 21)
  - Uses encrypted data channel (prot_p) required by servers like Whatbox
  - Falls back to plain FTP automatically if the server does not support TLS
  - Test Connection button shows landing directory and a file preview


====================================================
 JAPANESE / CJK FILENAME SUPPORT
====================================================

  - Sends OPTS UTF8 ON on connect
  - Uses binary-mode MLSD to read filenames without text-mode corruption
  - Falls back through UTF-8, Shift-JIS (cp932), EUC-JP, then Latin-1
  - Works with files uploaded from Windows, Linux, and Mac clients


====================================================
 ADDING OR CHANGING FOLDERS
====================================================

Edit docker-compose.yml to add, remove, or change volume mounts, then:

    docker compose up -d

Docker restarts the container with the new mounts. No rebuild required,
no settings or history lost.

The Settings tab in the web UI shows exactly which paths are currently
mounted and available inside the container.


====================================================
 MULTIPLE SERVER PROFILES
====================================================

Save as many FTP/FTPS servers as you like and switch between them
without re-entering credentials each time.

  - Browser tab: server dropdown then Add / Edit / Remove
  - Profiles are stored encrypted in settings.json on the data volume
  - Switching profiles updates both the browser and the sync engine


====================================================
 USEFUL DOCKER COMMANDS
====================================================

  docker compose up -d              Start (or restart) in background
  docker compose down               Stop
  docker compose logs -f            Watch live logs
  docker compose up -d --build      Rebuild after replacing .py files
  docker compose ps                 Check if it's running
  docker volume ls                  Confirm the data volume exists


====================================================
 UPDATING THE APP
====================================================

  1. Replace ftp_core.py and ftp_web.py with the new versions
  2. Run:   docker compose up -d --build
  3. Done — settings and history are untouched

Alternatively the web UI has a Settings: Updates section where you can
upload new .py files directly without touching the host filesystem.


====================================================
 DATA PERSISTENCE
====================================================

  ftp-sync-data (named Docker volume)
    Contains:  settings.json (config + encrypted credentials)
               history.db (SQLite, every downloaded file)
    Survives:  container restarts, rebuilds, docker compose down
    Backup:    Settings tab: Backup & Migration: Export Settings /
               Export History CSV

  Your mounted folders (bind mounts you configured)
    These are your real folders on disk. Docker only writes files into
    them — it never deletes or modifies anything already there.


====================================================
 UNRAID
====================================================

See unraid_setup.txt for the full guide. Short version:

  OPTION A — Build your own image (recommended for customisation)
    docker build -t YOURNAME/ftp-sync:latest .
    docker push YOURNAME/ftp-sync:latest
    Edit unraid_template.xml: change <Repository> to YOURNAME/ftp-sync:latest
    Unraid Docker tab: Add Container: paste the XML template

  OPTION B — Build directly on Unraid (no Docker Hub account needed)
    Copy ftp_core.py, ftp_web.py, Dockerfile, requirements.txt to
    /mnt/user/appdata/ftpsync-build/ then in the Unraid terminal:

      cd /mnt/user/appdata/ftpsync-build
      docker build -t ftp-sync:latest .
      docker run -d \
        --name ftp-sync \
        --restart=unless-stopped \
        -p 8080:8080 \
        -e PUID=99 -e PGID=100 \
        -v /mnt/user/appdata/ftpsync:/data \
        -v /mnt/user/Media/TV:/mnt/tv \
        -v /mnt/user/Media/Anime:/mnt/anime \
        ftp-sync:latest

    Add or remove -v lines for however many folders you need.
    In the app use /mnt/tv, /mnt/anime etc. as the Local Path.


====================================================
 FEATURES AT A GLANCE
====================================================

Sync engine
  + Parallel downloads (1-10 simultaneous connections)
  + SHA256 fingerprint tracking — never re-downloads the same file
  + Per folder-pair FTP connections — no shared-state interference
  + Partial file cleanup (.part files cleaned up on cancel or error)
  + Encrypted credential storage (volume-backed AES key)
  + Pre-scan mode — review before downloading
  + Ignore list — skip paths, folders, or file extensions
  + FTPS explicit TLS auto-detection with plain FTP fallback
  + Japanese / CJK filename support (binary MLSD + encoding fallback)
  + Test Connection with landing directory and file preview

Web UI extras
  + Full remote file browser with upload, download, rename, delete
  + Drag and drop uploads in browser
  + Multiple server profiles with instant switching
  + Live transfer queue with per-file progress and cancel
  + Export and import history CSV and settings JSON


====================================================
 TROUBLESHOOTING
====================================================

"Site can't be reached"
  - docker compose ps  (check the container is actually running)
  - Use http://YOUR-SERVER-IP:8080 rather than localhost if accessing
    from another device
  - Port conflict: change "8080:8080" to "9090:8080" in docker-compose.yml
    then docker compose up -d

Cannot connect (Test Connection fails)
  - Check host, port, and credentials on the Dashboard
  - Enable Verbose Logging in Settings for detailed FTP output
  - Check the FTP Errors tab for repeated failure messages

Files not appearing after sync
  - Test Connection to confirm the FTP credentials work
  - Check the FTP Errors tab for listing or download failures
  - Make sure the remote path exists and the account has read access

"No such file or directory" when downloading
  - The local path you entered in Folder Pairs is not mounted
  - Settings tab: Folder Mounts shows what paths are available
  - Add the correct -v line to docker-compose.yml and restart

Files downloaded with wrong owner (root-owned files)
  - Set PUID and PGID in docker-compose.yml to match the user that
    should own the files
  - Run "id" on the host to find the right values
  - docker compose up -d  (no rebuild needed)

Settings or history lost after restart
  - The ftp-sync-data named volume must exist
  - Run: docker volume ls  and confirm ftp-sync-data is listed
  - If it is missing, the data was on a container layer — add the
    named volume to docker-compose.yml as shown in the template
