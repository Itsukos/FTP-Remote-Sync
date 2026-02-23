#!/bin/bash
# ============================================================
#  entrypoint.sh — FTP Sync v1.0.0
#
#  Runs as root briefly to:
#    1. Create a user/group matching PUID and PGID
#    2. chown /data and /mnt so the app can write to them
#    3. Drop privileges and exec the app as that user
#
#  This means files written by the app will be owned by
#  whatever user you set in PUID/PGID — matching your host.
# ============================================================

set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

echo "─────────────────────────────────────"
echo " FTP Sync v1.0.0"
echo " Running as UID=${PUID} GID=${PGID}"
echo "─────────────────────────────────────"

# Create group if it doesn't already exist with this GID
if ! getent group "${PGID}" > /dev/null 2>&1; then
    groupadd -g "${PGID}" ftpsync
fi

# Create user if it doesn't already exist with this UID
if ! getent passwd "${PUID}" > /dev/null 2>&1; then
    useradd -u "${PUID}" -g "${PGID}" -M -s /bin/false ftpsync
fi

# Fix ownership of app directories so the user can write to them
# /data is always ours. /mnt is the root for mounted folders —
# we only chown the mount root itself, not the contents, so we
# don't accidentally change permissions on your host files.
chown -R "${PUID}:${PGID}" /data /app

# For /mnt subdirectories that the app creates (not host bind mounts),
# we chown those too. Actual bind-mounted host folders keep host ownership.
chown "${PUID}:${PGID}" /mnt 2>/dev/null || true

# Drop root and exec the app as the target user
exec gosu "${PUID}:${PGID}" python /app/ftp_web.py
