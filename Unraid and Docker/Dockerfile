# ============================================================
#  Dockerfile — FTP Sync v1.0.0
#  Vibe Coded by Itsuko | https://twitter.com/Itsukos
#
#  Runs as a non-root user for security.
#  The user ID and group ID can be set at runtime via PUID/PGID
#  environment variables so file ownership matches your host.
#
#  Default: PUID=1000, PGID=1000
#  Unraid:  PUID=99,   PGID=100  (nobody:users)
#
#  Set these in docker-compose.yml under environment:
#    - PUID=99
#    - PGID=100
# ============================================================

FROM python:3.12-slim

LABEL maintainer="Itsuko"
LABEL description="FTP Remote-to-Local Sync v1.0.0"
LABEL version="1.0.0"

# curl for health check, gosu for clean privilege drop at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY ftp_core.py         .
COPY ftp_web.py          .
COPY docker-compose.yml  .

# Create the directories the app needs.
# entrypoint.sh will chown these to PUID:PGID at startup.
RUN mkdir -p /data /mnt /downloads

# Default env — override in docker-compose.yml
ENV FTP_CONFIG_FILE=/data/config.json
ENV FTP_DB_FILE=/data/history.db
ENV FTP_NO_BROWSER=true
ENV FTP_VERBOSE=false
ENV PUID=1000
ENV PGID=1000

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Entrypoint handles the PUID/PGID privilege drop, then starts the app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
