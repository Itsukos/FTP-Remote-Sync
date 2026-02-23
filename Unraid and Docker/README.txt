FTP Sync v1.0.0 — Docker / Unraid Edition
==========================================
Vibe Coded by Itsuko  |  DM bugs: https://twitter.com/Itsukos


====================================================
 WHAT'S IN THIS FOLDER
====================================================

  ftp_core.py           The sync engine
  ftp_web.py            The web UI (this runs inside Docker)
  Dockerfile            Builds the Docker image
  docker-compose.yml    Start the app — edit this to add your folders
  requirements.txt      Python dependencies (Dockerfile uses this)
  unraid_template.xml   Unraid Docker template
  unraid_setup.txt      Step-by-step Unraid guide


====================================================
 QUICK START — ANY MACHINE WITH DOCKER
====================================================

1. Open docker-compose.yml in a text editor.

2. Under the "volumes:" section, add a line for every folder
   you want the app to be able to download files into.

   The format is:
     - "/real/path/on/your/machine:/path/the/app/uses"

   Examples:
     - "/mnt/user/Media/TV:/mnt/tv"
     - "/mnt/user/Media/Anime:/mnt/anime"
     - "/mnt/user/Downloads:/mnt/downloads"
     - "D:/Media/TV:/mnt/tv"              (Windows)

   The right side (/mnt/tv etc.) is what you type into the app
   when setting a Local Path in Folder Pairs. You can name it
   whatever makes sense to you.

3. Save docker-compose.yml, then run:

     docker compose up -d

4. Open http://localhost:8080  (or http://YOUR-SERVER-IP:8080)

5. In the app:
   - Dashboard: enter FTP credentials → Save Credentials
   - Folder Pairs: Remote Path = FTP folder, Local Path = /mnt/tv
     (or whatever right-side path you chose in step 2)
   - Dashboard: Start Sync

That's it. The app checks the FTP server on a schedule and
downloads anything new automatically.


====================================================
 RUNNING AS NON-ROOT (PUID / PGID)
====================================================

The container runs as a non-root user for security.
It starts as root briefly, creates a user matching your PUID/PGID,
fixes directory ownership, then drops privileges before the app starts.

This means downloaded files are owned by your chosen user instead of
root — important for NAS and shared server setups.

Set PUID and PGID in docker-compose.yml to match the owner of your
download folders on the host:

  Most Linux desktops:   PUID=1000  PGID=1000
  Unraid (nobody:users): PUID=99    PGID=100
  Synology (admin):      PUID=1026  PGID=101  (varies — check: id admin)
  Mac Docker Desktop:    PUID=501   PGID=20   (typically)
  Windows Docker Desktop: leave at 1000/1000  (WSL2 handles it)

To find your own UID/GID on Linux/Mac:   id

After changing PUID/PGID:   docker compose up -d  (no rebuild needed)


====================================================
 ADDING OR CHANGING FOLDERS LATER
====================================================

Just edit docker-compose.yml again and run:

  docker compose up -d

Docker restarts the container with the new mounts — no rebuild,
no data loss, settings are preserved.

You can also do this through the web UI — see the Folder Mounts
section on the Settings tab, which shows you exactly what paths
are currently available inside the container.


====================================================
 USEFUL COMMANDS
====================================================

  docker compose up -d              Start (or restart) in background
  docker compose down               Stop
  docker compose logs -f            Watch live logs
  docker compose up -d --build      Rebuild after updating .py files
  docker compose ps                 Check if it's running


====================================================
 UPDATING THE APP
====================================================

  1. Replace ftp_core.py and ftp_web.py with the new versions
  2. Run:  docker compose up -d --build
  3. Done — settings and history are untouched


====================================================
 DATA PERSISTENCE
====================================================

  ftp-sync-data (named Docker volume)
    Contains:  settings, download history database
    Survives:  restarts, rebuilds, docker compose down

  Your mounted folders (bind mounts you configured)
    These are your real folders on disk — Docker doesn't touch them


====================================================
 UNRAID
====================================================

See unraid_setup.txt for the full guide. Short version:

  OPTION A — Build your own image and push to Docker Hub
    docker build -t YOURNAME/ftp-sync:latest .
    docker push YOURNAME/ftp-sync:latest
    Edit unraid_template.xml: change <Repository> to YOURNAME/ftp-sync:latest
    In Unraid Docker tab → Add Container → import the XML

  OPTION B — Build directly on Unraid (no Docker Hub needed)
    Copy ftp_core.py, ftp_web.py, Dockerfile, requirements.txt to
    /mnt/user/appdata/ftpsync-build/ then in the Unraid terminal:

      cd /mnt/user/appdata/ftpsync-build
      docker build -t ftp-sync:latest .
      docker run -d \
        --name ftp-sync \
        --restart=unless-stopped \
        -p 8080:8080 \
        -v /mnt/user/appdata/ftpsync:/data \
        -v /mnt/user/Media/TV:/mnt/tv \
        -v /mnt/user/Media/Anime:/mnt/anime \
        -e FTP_CONFIG_FILE=/data/config.json \
        -e FTP_DB_FILE=/data/history.db \
        ftp-sync:latest

    Add or remove -v lines for however many folders you need.
    In the app use /mnt/tv, /mnt/anime etc. as the Local Path.


====================================================
 TROUBLESHOOTING
====================================================

"Site can't be reached"
  → docker compose ps  (check it's actually running)
  → Try http://YOUR-SERVER-IP:8080 not localhost
  → Port 8080 already in use? Change "8080:8080" to "9090:8080"
    in docker-compose.yml then docker compose up -d

Files not downloading
  → Test Connection button on the Dashboard tab
  → Enable Verbose Logging in Settings for detailed errors
  → Check the FTP Errors tab

"No such file or directory" when downloading
  → The local path you entered isn't mounted
  → Check Settings tab → Folder Mounts to see what's available
  → Add the correct -v line to docker-compose.yml and restart

Files downloaded with wrong owner (root-owned files)
  → Set PUID and PGID in docker-compose.yml to match the user
    that should own the files. Run "id" to find your UID/GID.
  → Then: docker compose up -d  (no rebuild needed)

Settings/history lost after restart
  → The ftp-sync-data named volume must exist
  → Run: docker volume ls  and confirm ftp-sync-data is listed
