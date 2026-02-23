"""
ftp_core.py  -  Shared FTP sync engine
Used by ftp_gui.py (tkinter) and ftp_web.py (Flask).

SETTINGS FILE
  Saved as settings.json next to the EXE (or script).
  Created automatically on first run with all defaults.
  Human-readable and manually editable.
  Password is encrypted with a machine-derived key so it is not stored
  in plaintext - only this program on this machine can decrypt it.
  Credentials are NEVER written unless the user explicitly checks "Save credentials".

FINGERPRINTING
  SHA256(remote_path | size | modify_time)
  Written to SQLite on successful download.
  Checked before every download - match = permanent skip.

PARALLEL DOWNLOADS
  ThreadPoolExecutor - each worker opens its own FTP connection.
  Stop signal closes the FTP socket mid-transfer to actually interrupt it.
"""

import base64
import ftplib
import hashlib
import json
import os
import platform
import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

VERSION = "1.1.2"

# ---------------------------------------------------------------------------
# Paths - settings.json and history.db sit next to the EXE / script
# ---------------------------------------------------------------------------

def _app_dir() -> str:
    """Directory containing the running EXE or script."""
    import sys
    if getattr(sys, "frozen", False):          # PyInstaller EXE
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_main_script() -> str:
    """Return the path to ftp_gui.py (or the frozen EXE) for shortcut creation."""
    import sys
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.join(_app_dir(), "ftp_gui.py")

CONFIG_FILE = os.environ.get(
    "FTP_CONFIG_FILE",
    os.path.join(_app_dir(), "settings.json"),
)
DB_FILE = os.environ.get(
    "FTP_DB_FILE",
    os.path.join(_app_dir(), "history.db"),
)

# ---------------------------------------------------------------------------
# Password encryption
# ---------------------------------------------------------------------------
# Key derivation: PBKDF2(machine-id + app-salt, iterations=200_000)
# The key never leaves this machine. If someone copies settings.json to
# another machine the password field will fail to decrypt and be ignored.

_APP_SALT = b"ftpsync-v1-salt-2024"

def _derive_key() -> bytes:
    """Derive a Fernet key from a stable machine identifier."""
    try:
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        machine_id = _get_machine_id().encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=_APP_SALT,
            iterations=200_000,
        )
        raw = kdf.derive(machine_id)
        return base64.urlsafe_b64encode(raw)
    except Exception:
        # Fallback: SHA256-based pseudo-key (less strong but still not plaintext)
        raw = hashlib.sha256(_APP_SALT + _get_machine_id().encode()).digest()
        return base64.urlsafe_b64encode(raw)


def _get_machine_id() -> str:
    """Return a stable per-machine identifier."""
    sys_platform = platform.system()
    try:
        if sys_platform == "Windows":
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SOFTWARE\Microsoft\Cryptography")
            val, _ = winreg.QueryValueEx(key, "MachineGuid")
            return val
        elif sys_platform == "Linux":
            for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                if os.path.exists(path):
                    return open(path).read().strip()
        elif sys_platform == "Darwin":
            import subprocess
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]
            ).decode()
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[-2]
    except Exception:
        pass
    # Last resort: hostname + username (not truly unique but better than nothing)
    return socket.gethostname() + os.environ.get("USERNAME", os.environ.get("USER", "user"))


def encrypt_password(plaintext: str) -> str:
    """Return base64-encoded encrypted password, or '' on failure."""
    if not plaintext:
        return ""
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_key())
        return f.encrypt(plaintext.encode()).decode()
    except Exception:
        return ""


def decrypt_password(token: str) -> str:
    """Return decrypted password, or '' if it can't be decrypted."""
    if not token:
        return ""
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_key())
        return f.decrypt(token.encode()).decode()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Config  (settings.json)
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "_comment": "FTP Sync settings. Edit manually if needed. Do not share this file - it contains your encrypted password.",
    "host": "",
    "port": 21,
    "user": "",
    "password_enc": "",
    "save_credentials": False,
    "folder_pairs": [],
    "ignored_paths": [],
    "interval": 5,
    "parallel_downloads": 3,
    # Multiple server profiles — each entry mirrors the top-level host/port/user/password_enc
    # plus an optional "name" label.  The active server is always mirrored at the top level
    # for backward compatibility with the sync engine and all existing code.
    "servers": [],          # list of {name, host, port, user, password_enc, folder_pairs}
    "active_server": "",    # name of the currently-active server profile (empty = top-level)
}

def load_config() -> dict:
    """Load settings.json, creating it with defaults if it doesn't exist."""
    if not os.path.exists(CONFIG_FILE):
        _write_default_config()
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        # Migrate old plaintext 'password' key to encrypted 'password_enc'
        if "password" in cfg and "password_enc" not in cfg:
            cfg["password_enc"] = encrypt_password(cfg.pop("password"))
            _save_raw(cfg)
        elif "password" in cfg:
            cfg.pop("password")   # remove plaintext remnant
        return cfg
    except Exception:
        return dict(_DEFAULT_CONFIG)


def save_config(cfg: dict):
    """Save config, always ensuring password is encrypted and plaintext is removed."""
    # Never let plaintext password leak into file
    cfg.pop("password", None)
    _save_raw(cfg)


def _save_raw(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------------------
# Multi-server helpers
# ---------------------------------------------------------------------------

def list_servers(cfg: dict) -> list:
    """Return the servers list, always including a synthetic entry for the top-level creds."""
    servers = list(cfg.get("servers", []))
    # Ensure the built-in top-level entry appears as "Default" if it has a host
    has_default = any(s.get("name") == "Default" for s in servers)
    if not has_default and cfg.get("host"):
        servers.insert(0, {
            "name": "Default",
            "host": cfg["host"],
            "port": cfg.get("port", 21),
            "user": cfg.get("user", ""),
            "password_enc": cfg.get("password_enc", ""),
            "folder_pairs": cfg.get("folder_pairs", []),
        })
    return servers


def get_server(cfg: dict, name: str) -> dict:
    """Return a server profile dict by name, or the top-level config if not found."""
    for s in cfg.get("servers", []):
        if s.get("name") == name:
            return s
    return cfg


def save_server(cfg: dict, profile: dict) -> dict:
    """Add or update a server profile. Returns updated cfg."""
    servers = cfg.setdefault("servers", [])
    for i, s in enumerate(servers):
        if s.get("name") == profile["name"]:
            servers[i] = profile
            return cfg
    servers.append(profile)
    return cfg


def delete_server(cfg: dict, name: str) -> dict:
    """Remove a server profile by name."""
    cfg["servers"] = [s for s in cfg.get("servers", []) if s.get("name") != name]
    return cfg


def activate_server(cfg: dict, name: str) -> dict:
    """
    Copy a named server profile into the top-level config keys so the sync
    engine and all existing code sees the correct active server.
    """
    profile = get_server(cfg, name)
    cfg["host"]         = profile.get("host", "")
    cfg["port"]         = profile.get("port", 21)
    cfg["user"]         = profile.get("user", "")
    cfg["password_enc"] = profile.get("password_enc", "")
    if profile.get("folder_pairs"):
        cfg["folder_pairs"] = profile["folder_pairs"]
    cfg["active_server"] = name
    return cfg


def _write_default_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(_DEFAULT_CONFIG, f, indent=2)


def get_password(cfg: dict) -> str:
    """Extract and decrypt the password from a config dict."""
    return decrypt_password(cfg.get("password_enc", ""))


def set_password(cfg: dict, plaintext: str):
    """Encrypt and store password in cfg dict (does not save to disk)."""
    cfg["password_enc"] = encrypt_password(plaintext)
    cfg.pop("password", None)


def validate_config(cfg: dict) -> list:
    """
    Check config for obvious problems.
    Returns list of warning strings (empty = all good).
    """
    warnings = []
    if not cfg.get("host"):
        warnings.append("No FTP host configured.")
    port = cfg.get("port", 21)
    if not isinstance(port, int) or not (1 <= port <= 65535):
        warnings.append(f"Port '{port}' is invalid (must be 1-65535).")
    if not cfg.get("folder_pairs"):
        warnings.append("No folder pairs configured.")
    for i, pair in enumerate(cfg.get("folder_pairs", [])):
        if not pair.get("remote"):
            warnings.append(f"Folder pair {i+1} has no remote path.")
        if not pair.get("local"):
            warnings.append(f"Folder pair {i+1} has no local path.")
    interval = cfg.get("interval", 5)
    if not isinstance(interval, (int, float)) or interval < 1:
        warnings.append("Interval must be at least 1 minute.")
    parallel = cfg.get("parallel_downloads", 3)
    if not isinstance(parallel, int) or not (1 <= parallel <= 10):
        warnings.append("Parallel downloads must be 1-10.")
    return warnings


def is_ignored(remote_path: str, ignored_paths: list) -> bool:
    norm = remote_path.rstrip("/")
    for pattern in ignored_paths:
        p = pattern.rstrip("/")
        if norm == p or norm.startswith(p + "/"):
            return True
    return False


# ---------------------------------------------------------------------------
# Download History  (SQLite)
# ---------------------------------------------------------------------------

class DownloadHistory:

    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._lock   = threading.Lock()
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        with self._lock, self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint   TEXT    UNIQUE NOT NULL,
                    remote_path   TEXT    NOT NULL,
                    local_path    TEXT    NOT NULL,
                    file_size     INTEGER NOT NULL DEFAULT 0,
                    modify_time   TEXT    NOT NULL DEFAULT '',
                    downloaded_at TEXT    NOT NULL,
                    pair_remote   TEXT    NOT NULL DEFAULT '',
                    pair_local    TEXT    NOT NULL DEFAULT '',
                    source        TEXT    NOT NULL DEFAULT 'download'
                )
            """)
            for col, defn in [
                ("pair_remote", "TEXT NOT NULL DEFAULT ''"),
                ("pair_local",  "TEXT NOT NULL DEFAULT ''"),
                ("source",      "TEXT NOT NULL DEFAULT 'download'"),
            ]:
                try:
                    c.execute(f"ALTER TABLE downloads ADD COLUMN {col} {defn}")
                except Exception:
                    pass

    @staticmethod
    def make_fingerprint(remote_path: str, size: int, modify: str) -> str:
        raw = f"{remote_path}|{size}|{modify}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def already_downloaded(self, fingerprint: str) -> bool:
        with self._lock, self._conn() as c:
            return c.execute(
                "SELECT 1 FROM downloads WHERE fingerprint=?", (fingerprint,)
            ).fetchone() is not None

    def record(self, fingerprint: str, remote_path: str, local_path: str,
               size: int, modify: str,
               pair_remote: str = "", pair_local: str = "",
               source: str = "download"):
        with self._lock, self._conn() as c:
            c.execute("""
                INSERT OR IGNORE INTO downloads
                    (fingerprint, remote_path, local_path, file_size,
                     modify_time, downloaded_at, pair_remote, pair_local, source)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                fingerprint, remote_path, local_path, size, modify,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                pair_remote, pair_local, source,
            ))

    def get_all(self, limit: int = 1000) -> list:
        with self._lock, self._conn() as c:
            rows = c.execute("""
                SELECT id, remote_path, local_path, file_size,
                       modify_time, downloaded_at, pair_remote, source
                FROM downloads ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
        return [
            {"id": r[0], "remote_path": r[1], "local_path": r[2],
             "file_size": r[3], "modify_time": r[4], "downloaded_at": r[5],
             "pair_remote": r[6], "source": r[7]}
            for r in rows
        ]

    def get_stats(self) -> dict:
        with self._lock, self._conn() as c:
            total    = c.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
            total_b  = c.execute("SELECT SUM(file_size) FROM downloads").fetchone()[0] or 0
            today    = c.execute(
                "SELECT COUNT(*) FROM downloads WHERE date(downloaded_at)=date('now')"
            ).fetchone()[0]
            prescans = c.execute(
                "SELECT COUNT(*) FROM downloads WHERE source IN ('prescan','prescan_manual')"
            ).fetchone()[0]
        return {"total_files": total, "total_bytes": total_b,
                "today": today, "prescans": prescans}

    def delete_record(self, record_id: int):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM downloads WHERE id=?", (record_id,))

    def clear_all(self):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM downloads")


# ---------------------------------------------------------------------------
# FTP connection
# ---------------------------------------------------------------------------

def _ftp_negotiate_encoding(ftp, log_fn=None):
    """
    Negotiate the best encoding with the server.
    Most modern FTP servers (including Japanese seedboxes) support UTF-8.
    We try OPTS UTF8 ON first; if the server rejects it we probe by trying
    to list a known path and checking if MLSD gives valid text.
    Falls back to latin-1 only when UTF-8 is explicitly rejected.
    """
    def dbg(m):
        if log_fn: log_fn(f"  [ENC] {m}")
    # Try to enable UTF-8 mode (RFC 2640)
    try:
        resp = ftp.sendcmd("OPTS UTF8 ON")
        dbg(f"OPTS UTF8 ON -> {resp}")
        ftp.encoding = "utf-8"
        return "utf-8"
    except Exception as e:
        dbg(f"OPTS UTF8 ON rejected: {e}")
    # Some servers advertise UTF8 in FEAT — check
    try:
        feat = ftp.sendcmd("FEAT")
        if "UTF8" in feat.upper():
            ftp.encoding = "utf-8"
            dbg("UTF8 in FEAT, using utf-8")
            return "utf-8"
    except Exception:
        pass
    # Default to utf-8 anyway — Python's ftplib sends commands as the
    # socket encoding.  Most modern servers accept UTF-8 paths even
    # without OPTS UTF8 ON.  We only fall back to latin-1 if the server
    # explicitly advertised no-UTF8 support.
    ftp.encoding = "utf-8"
    dbg("Defaulting to utf-8")
    return "utf-8"


def ftp_safe_name(raw_bytes_or_str, server_encoding="utf-8") -> str:
    """
    Decode a filename received from the server, handling encoding mismatches.

    For str input: return as-is. ftplib already decoded using ftp.encoding
    (which we set to utf-8 after OPTS UTF8 ON), so the string is correct.
    The old latin-1→utf-8 re-encode trick was too aggressive and mangled
    valid ASCII/UTF-8 filenames from servers that do support UTF-8 properly.

    For bytes input (rare — only from raw socket reads): try utf-8, then
    server_encoding, then common CJK encodings, then latin-1 fallback.
    """
    if isinstance(raw_bytes_or_str, str):
        return raw_bytes_or_str   # already a correct Python string
    # bytes — try encodings in order
    for enc in ("utf-8", server_encoding, "shift_jis", "euc-jp", "latin-1"):
        try:
            return raw_bytes_or_str.decode(enc)
        except Exception:
            continue
    return raw_bytes_or_str.decode("latin-1", errors="replace")


def ftp_connect(host, port, user, password, log_fn=None):
    def dbg(m):
        if log_fn: log_fn(f"  [DBG] {m}")
    try:
        dbg("Trying FTPS...")
        ftp = ftplib.FTP_TLS()
        ftp.connect(host, int(port), timeout=30)
        ftp.login(user, password)
        ftp.prot_p()          # encrypted data channel (required by most FTPS servers)
        ftp.set_pasv(True)
        dbg("Connected via FTPS")
        _ftp_negotiate_encoding(ftp, log_fn)
        return ftp
    except ftplib.error_perm as e:
        raise Exception(f"Login failed: {e}")
    except Exception as e:
        dbg(f"FTPS failed ({e}), trying plain FTP...")
    ftp = ftplib.FTP()
    ftp.connect(host, int(port), timeout=30)
    ftp.login(user, password)
    ftp.set_pasv(True)
    _ftp_negotiate_encoding(ftp, log_fn)
    dbg("Connected via plain FTP")
    return ftp


# ---------------------------------------------------------------------------
# Update / Sideload system
# ---------------------------------------------------------------------------

import py_compile
import shutil

UPDATABLE_FILES = ["ftp_core.py", "ftp_gui.py", "ftp_web.py"]

def get_updates_dir() -> str:
    """Return the updates/ folder next to the EXE (or script)."""
    return os.path.join(_app_dir(), "updates")

def get_active_overrides() -> list:
    """
    Return list of filenames currently active as overrides.
    Empty list if running from source or no overrides installed.
    """
    if not getattr(__import__("sys"), "frozen", False):
        return []
    marker = os.path.join(_app_dir(), ".active_overrides")
    try:
        with open(marker) as f:
            return [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        return []

def install_update(src_path: str) -> tuple:
    """
    Validate and install a single .py file as an override.
    Returns (True, filename) on success or (False, error_message) on failure.

    Validation steps:
      1. Filename must be one of UPDATABLE_FILES
      2. File must compile without syntax errors
      3. File is backed up then copied to updates/

    The override takes effect on next restart.
    """
    filename = os.path.basename(src_path)
    if filename not in UPDATABLE_FILES:
        return False, (
            f"'{filename}' is not an updatable file.\n"
            f"Accepted: {', '.join(UPDATABLE_FILES)}"
        )

    # Syntax check before installing
    try:
        py_compile.compile(src_path, doraise=True)
    except py_compile.PyCompileError as e:
        return False, f"Syntax error in {filename}:\n{e}"

    updates_dir = get_updates_dir()
    os.makedirs(updates_dir, exist_ok=True)

    dest = os.path.join(updates_dir, filename)

    # Back up existing override if present
    if os.path.exists(dest):
        backup = dest + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(dest, backup)

    shutil.copy2(src_path, dest)
    return True, filename


def remove_override(filename: str) -> tuple:
    """
    Remove an installed override file.
    Returns (True, msg) or (False, error).
    """
    if filename not in UPDATABLE_FILES:
        return False, f"'{filename}' is not a recognised override file."
    dest = os.path.join(get_updates_dir(), filename)
    if not os.path.exists(dest):
        return False, f"No override installed for {filename}."
    os.remove(dest)
    return True, f"Override removed: {filename}. Restart to take effect."


def get_override_status() -> dict:
    """
    Return info about each updatable file:
      {filename: {"installed": bool, "path": str|None, "mtime": str|None}}
    """
    updates_dir = get_updates_dir()
    status = {}
    for fname in UPDATABLE_FILES:
        path = os.path.join(updates_dir, fname)
        if os.path.exists(path):
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
            status[fname] = {"installed": True, "path": path, "mtime": mtime}
        else:
            status[fname] = {"installed": False, "path": None, "mtime": None}
    return status


# ---------------------------------------------------------------------------
# Export / Import  (history CSV + settings JSON)
# ---------------------------------------------------------------------------

import csv
import shutil

def export_history_csv(db_history: "DownloadHistory", dest_path: str):
    """Write all history records to a CSV file."""
    rows = db_history.get_all(limit=999_999)
    with open(dest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id", "downloaded_at", "source", "remote_path", "local_path",
            "file_size", "modify_time", "pair_remote",
        ])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def import_history_csv(db_history: "DownloadHistory", src_path: str) -> tuple:
    """
    Import records from a CSV file exported by export_history_csv.
    Skips rows whose fingerprint is already in the DB (safe to run repeatedly).
    Returns (imported_count, skipped_count).
    """
    imported = skipped = 0
    with open(src_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                fp = DownloadHistory.make_fingerprint(
                    row["remote_path"],
                    int(row.get("file_size", 0)),
                    row.get("modify_time", ""),
                )
                if db_history.already_downloaded(fp):
                    skipped += 1
                    continue
                db_history.record(
                    fingerprint=fp,
                    remote_path=row["remote_path"],
                    local_path=row["local_path"],
                    size=int(row.get("file_size", 0)),
                    modify=row.get("modify_time", ""),
                    pair_remote=row.get("pair_remote", ""),
                    pair_local=row.get("pair_local", ""),
                    source=row.get("source", "download"),
                )
                imported += 1
            except Exception:
                skipped += 1
    return imported, skipped


def export_settings(dest_path: str, include_password: bool = False):
    """
    Copy settings.json to dest_path, stripping the encrypted password
    unless include_password=True.
    """
    cfg = load_config()
    if not include_password:
        cfg.pop("password_enc", None)
        cfg["save_credentials"] = False
        cfg["_comment"] = (
            "Exported settings (password stripped for safety). "
            "Re-enter your password after importing."
        )
    with open(dest_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def import_settings(src_path: str) -> list:
    """
    Load settings from src_path and merge into current settings.json.
    Credentials in current file are preserved if the import has none.
    Returns list of warning strings from validate_config.
    """
    with open(src_path, encoding="utf-8") as f:
        incoming = json.load(f)
    current = load_config()
    # Merge: incoming wins except for credentials which we keep if import has none
    if not incoming.get("host") and current.get("host"):
        incoming["host"]         = current["host"]
        incoming["port"]         = current.get("port", 21)
        incoming["user"]         = current.get("user", "")
        incoming["password_enc"] = current.get("password_enc", "")
        incoming["save_credentials"] = current.get("save_credentials", False)
    incoming.pop("_comment", None)
    save_config(incoming)
    return validate_config(incoming)


# ---------------------------------------------------------------------------
# Test connection  (used by the Test Connection button in GUI and web UI)
# ---------------------------------------------------------------------------

def test_connection(host: str, port: int, user: str, password: str) -> tuple:
    """
    Try connecting to the FTP server.
    Returns (True, info_string) on success or (False, error_string) on failure.
    info_string includes server banner, working directory, and whether TLS was used.
    """
    if not host:
        return False, "No host specified."
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False, f"Invalid port: {port!r}"

    tls = False
    try:
        ftp = ftplib.FTP_TLS()
        ftp.connect(host, port, timeout=15)
        ftp.login(user, password)
        ftp.prot_p()          # encrypted data channel
        ftp.set_pasv(True)
        tls = True
    except ftplib.error_perm as e:
        return False, f"Login failed: {e}"
    except Exception:
        # Fall back to plain FTP
        try:
            ftp = ftplib.FTP()
            ftp.connect(host, port, timeout=15)
            ftp.login(user, password)
            ftp.set_pasv(True)
        except ftplib.error_perm as e:
            return False, f"Login failed: {e}"
        except Exception as e:
            return False, f"Cannot connect: {e}"

    try:
        _ftp_negotiate_encoding(ftp)
        welcome = ftp.getwelcome().strip()
        cwd     = ftp.pwd()
        # List the landing directory to verify read access and show contents
        entries = ftp_list_dir_full(ftp, cwd)
        dirs    = [e["name"] for e in entries if e["is_dir"]]
        files   = [e["name"] for e in entries if not e["is_dir"]]
        dir_preview  = ", ".join(dirs[:5])  + ("…" if len(dirs)  > 5 else "")
        file_preview = ", ".join(files[:3]) + ("…" if len(files) > 3 else "")
        ftp.quit()
    except Exception as e:
        try: ftp.close()
        except: pass
        return False, f"Connected but error during inspection: {e}"

    proto = "FTPS (TLS)" if tls else "Plain FTP"
    contents = ""
    if dirs:
        contents += f"\nFolders: {dir_preview}"
    if files:
        contents += f"\nFiles: {file_preview}"
    if not dirs and not files:
        contents = "\nContents: (empty directory)"
    info = (
        f"Connected via {proto}\n"
        f"Server: {welcome[:120]}\n"
        f"Landing dir: {cwd}"
        f"{contents}"
    )
    return True, info


# ---------------------------------------------------------------------------
# Directory listing
# ---------------------------------------------------------------------------

def ftp_list_dirs(ftp, path) -> list:
    """List only subdirectories of path. Uses ftp_list_dir_full internally."""
    entries = ftp_list_dir_full(ftp, path)
    return sorted(e["name"] for e in entries if e["is_dir"])


def _mlsd_binary(ftp, path, log_fn=None):
    """
    Read an MLSD listing in TYPE I (binary) mode, bypassing ftplib's
    retrlines() which always sends TYPE A and can mangle non-ASCII bytes.

    Returns a list of (name_str, facts_dict) tuples, or None on failure.
    The name bytes are decoded as latin-1 so all 0x00-0xFF values survive;
    callers recover the real encoding from those bytes.
    """
    def dbg(m):
        if log_fn: log_fn(f"  [MLSD-bin] {m}")

    clean = path.rstrip("/") or "/"
    try:
        ftp.sendcmd("TYPE I")          # binary — no CR/LF mangling
    except Exception as e:
        dbg(f"TYPE I failed: {e} — will use retrlines fallback")
        return None

    try:
        # OPTS MLST sets the facts we want
        try:
            ftp.sendcmd("OPTS MLST type;size;modify;")
        except Exception:
            pass

        cmd = f"MLSD {clean}" if clean != "/" else "MLSD /"
        with ftp.transfercmd(cmd) as conn:
            raw_bytes = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                raw_bytes += chunk
        ftp.voidresp()   # consume the 226 Transfer complete
    except Exception as e:
        dbg(f"MLSD binary transfer failed: {e}")
        return None

    dbg(f"MLSD binary got {len(raw_bytes)} bytes raw")

    results = []
    for raw_line in raw_bytes.split(b"\r\n"):
        if not raw_line:
            continue
        # Split: "facts; name"  — the separator is "; " (semicolon space)
        try:
            facts_part, _, name_bytes = raw_line.partition(b" ")
        except Exception:
            continue
        # Decode name as latin-1 (preserves all byte values 0x00-0xFF)
        name_raw = name_bytes.decode("latin-1", errors="replace").strip()
        if name_raw in (".", "..") or not name_raw:
            continue
        # Parse facts
        facts = {}
        for fact in facts_part.decode("ascii", errors="replace").rstrip(";").split(";"):
            if "=" in fact:
                k, _, v = fact.partition("=")
                facts[k.lower().strip()] = v.strip()
        results.append((name_raw, facts))

    dbg(f"MLSD binary parsed {len(results)} entries")
    return results


def ftp_list_dir_full(ftp, path, log_fn=None) -> list:
    """
    List a single remote directory.  Returns a list of dicts:
      {"name": str, "is_dir": bool, "size": int, "modify": str}

    Uses binary-mode MLSD to avoid TYPE A mangling of non-ASCII filenames
    (proftpd/Whatbox sends UTF-8 or cp932 bytes that get corrupted in text mode).
    Falls back to LIST then NLST if MLSD is unavailable.
    """
    def dbg(m):
        if log_fn: log_fn(f"  [LIST] {m}")

    def _sort(lst):
        return sorted(lst, key=lambda e: (not e["is_dir"], e["name"].lower()))

    def _decode_name(name_latin1):
        """
        Recover the correct filename from a latin-1 decoded byte string.
        Tries UTF-8 first (most modern servers), then cp932/Shift-JIS
        (Japanese Windows filenames), then euc-jp, then keeps latin-1 as-is.
        """
        raw = name_latin1.encode("latin-1", errors="surrogateescape")
        for enc in ("utf-8", "cp932", "euc_jp"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
        return name_latin1   # genuine latin-1 filename

    def _parse_list_line(line_latin1):
        """Parse one Unix ls-style LIST line decoded as latin-1."""
        parts = line_latin1.split(None, 8)
        if len(parts) < 9:
            return None
        name = parts[8].strip()
        if name in (".", "..") or not name:
            return None
        if " -> " in name:
            name = name.split(" -> ")[0].strip()
        is_dir = parts[0].startswith("d")
        try:    sz = int(parts[4])
        except: sz = 0
        return {"name": _decode_name(name), "is_dir": is_dir,
                "size": sz, "modify": " ".join(parts[5:8])}

    entries  = []
    last_err = None
    clean    = path.rstrip("/") or "/"

    # ── MLSD in binary mode ───────────────────────────────────────────────────
    try:
        raw_entries = _mlsd_binary(ftp, path, log_fn=log_fn)
        if raw_entries is not None:
            for name_latin1, facts in raw_entries:
                name   = _decode_name(name_latin1)
                etype  = facts.get("type", "").lower()
                is_dir = etype in ("dir", "cdir", "pdir")
                try:    sz = int(facts.get("size", 0))
                except: sz = 0
                entries.append({"name": name, "is_dir": is_dir,
                                 "size": sz, "modify": facts.get("modify", "")})
            if entries or raw_entries:
                dbg(f"MLSD (binary) returned {len(entries)} entries")
                # Restore TYPE I after MLSD so subsequent file transfers work
                try: ftp.sendcmd("TYPE I")
                except: pass
                return _sort(entries)
            dbg("MLSD (binary) returned 0 entries — falling through to LIST")
    except Exception as e:
        last_err = e
        dbg(f"MLSD binary block failed: {e!r}")

    # Ensure binary mode is set for subsequent operations
    try: ftp.sendcmd("TYPE I")
    except: pass

    # ── LIST ─────────────────────────────────────────────────────────────────
    raw_lines = []
    def _collect(line):
        # ftplib gives a decoded str from TYPE A; re-encode to bytes then
        # decode as latin-1 so we can apply our encoding recovery.
        raw_lines.append(line.encode("latin-1", errors="surrogateescape"))

    try:
        ftp.dir(clean, _collect)
        dbg(f"LIST '{clean}' → {len(raw_lines)} lines")
        for raw_line in raw_lines:
            line = raw_line.decode("latin-1", errors="replace")
            entry = _parse_list_line(line)
            if entry:
                entries.append(entry)
        if entries or raw_lines:
            dbg(f"LIST returned {len(entries)} entries")
            return _sort(entries)
        dbg("LIST returned 0 entries — trying NLST")
    except Exception as e:
        last_err = e
        dbg(f"LIST failed: {e!r} — trying NLST")

    # ── NLST (last resort) ────────────────────────────────────────────────────
    try:
        names = ftp.nlst(clean)
        dbg(f"NLST '{clean}' → {len(names)} names")
        for name in names:
            if name in (".", ".."):
                continue
            basename = _decode_name(name.split("/")[-1])
            full     = clean.rstrip("/") + "/" + name.split("/")[-1]
            is_dir   = False
            try: ftp.cwd(full); ftp.cwd(clean); is_dir = True
            except: pass
            try:    sz = 0 if is_dir else (ftp.size(full) or 0)
            except: sz = 0
            entries.append({"name": basename, "is_dir": is_dir,
                             "size": sz, "modify": ""})
        dbg(f"NLST returned {len(entries)} entries")
    except Exception as e:
        last_err = e
        dbg(f"NLST failed: {e!r}")

    if not entries and last_err:
        dbg(f"All listing methods exhausted. Last error: {last_err!r}")
    return _sort(entries)


def ftp_list_recursive(ftp, remote_path, log_fn=None) -> dict:
    results = {}

    def dbg(m):
        if log_fn: log_fn(f"  [DBG] {m}")

    def _decode(name_latin1):
        """Recover true encoding from a latin-1 decoded filename."""
        raw = name_latin1.encode("latin-1", errors="surrogateescape")
        for enc in ("utf-8", "cp932", "euc_jp"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
        return name_latin1

    def _walk_mlsd(path, prefix=""):
        # Use binary-mode MLSD to avoid TYPE A mangling of non-ASCII filenames
        raw_entries = _mlsd_binary(ftp, path, log_fn=log_fn)
        if raw_entries is None:
            return None
        for name_latin1, facts in raw_entries:
            if name_latin1 in (".", ".."): continue
            name  = _decode(name_latin1)
            etype = facts.get("type", "").lower()
            rel   = f"{prefix}/{name}" if prefix else name
            full  = f"{path}/{name}"
            if etype in ("dir", "cdir", "pdir"):
                _walk_mlsd(full, rel)
            elif etype in ("file", ""):
                try: size = int(facts.get("size", 0))
                except ValueError: size = 0
                results[rel] = (size, facts.get("modify", ""))
        return True

    def _walk_list(path, prefix=""):
        raw_lines = []
        def _cb(line):
            raw_lines.append(line.encode("latin-1", errors="surrogateescape"))
        try: ftp.dir(path, _cb)
        except Exception as e:
            dbg(f"LIST '{path}' failed: {e}"); return
        for raw_line in raw_lines:
            line = raw_line.decode("latin-1", errors="replace")
            parts = line.split(None, 8)
            if len(parts) < 9: continue
            name = parts[8].strip()
            if name in (".", ".."): continue
            if " -> " in name: name = name.split(" -> ")[0].strip()
            name = _decode(name)
            rel  = f"{prefix}/{name}" if prefix else name
            full = f"{path}/{name}"
            if parts[0].startswith("d"):
                _walk_list(full, rel)
            else:
                try: size = int(parts[4])
                except ValueError: size = 0
                results[rel] = (size, " ".join(parts[5:8]))

    def _walk_nlst(path, prefix=""):
        enc = getattr(ftp, "encoding", "utf-8") or "utf-8"
        try: names = ftp.nlst(path)
        except Exception as e:
            dbg(f"NLST '{path}' failed: {e}"); return
        for name in names:
            if name in (".", ".."): continue
            basename = ftp_safe_name(name.split("/")[-1], enc)
            full = path.rstrip("/") + "/" + name.split("/")[-1]
            rel  = f"{prefix}/{basename}" if prefix else basename
            try:
                ftp.cwd(full); ftp.cwd(path)
                _walk_nlst(full, rel)
            except Exception:
                try: size = ftp.size(full) or 0
                except: size = 0
                results[rel] = (size, "")

    if _walk_mlsd(remote_path) is None:
        _walk_list(remote_path)
        if not results:
            _walk_nlst(remote_path)

    return results


# ---------------------------------------------------------------------------
# Upload / delete / rename helpers
# ---------------------------------------------------------------------------

def ftp_upload_file(ftp, local_path: str, remote_path: str,
                    progress_cb=None, stop_event=None) -> None:
    """
    Upload local_path to remote_path on the FTP server.
    progress_cb(done_bytes, total_bytes) called periodically.
    stop_event: threading.Event; raises if set mid-transfer.
    """
    size = os.path.getsize(local_path)
    done = [0]

    # Ensure remote parent directory exists
    remote_dir = remote_path.rsplit("/", 1)[0] or "/"
    _ftp_mkdirs(ftp, remote_dir)

    with open(local_path, "rb") as fin:
        def _cb(chunk):
            if stop_event and stop_event.is_set():
                raise Exception("Cancelled")
            done[0] += len(chunk)
            if progress_cb:
                progress_cb(done[0], size)
        ftp.storbinary(f"STOR {remote_path}", fin, blocksize=65536,
                       callback=_cb)


def _ftp_mkdirs(ftp, path: str) -> None:
    """Recursively create remote directories (MKD), ignoring existing ones."""
    if path in ("/", ""):
        return
    parts = path.strip("/").split("/")
    current = ""
    for part in parts:
        current = current + "/" + part
        try:
            ftp.mkd(current)
        except ftplib.error_perm as e:
            if "550" not in str(e) and "File exists" not in str(e):
                pass  # already exists or other harmless error


def ftp_delete_remote(ftp, remote_path: str, is_dir: bool = False) -> None:
    """Delete a remote file (DELE) or empty directory (RMD)."""
    if is_dir:
        ftp.rmd(remote_path)
    else:
        ftp.delete(remote_path)


def ftp_rename_remote(ftp, old_path: str, new_path: str) -> None:
    """Rename/move a remote path using RNFR/RNTO."""
    ftp.rename(old_path, new_path)


# ---------------------------------------------------------------------------
# Pre-Scanner
# ---------------------------------------------------------------------------

class PreScanResult:
    def __init__(self):
        self.auto_matched  = []  # (remote_path, local_path, size, modify)
        self.needs_review  = []  # {remote_path, size, modify, fingerprint, reason, candidates}
        self.remote_only   = []  # (remote_path, size)
        self.already_known = 0


class PreScanner:
    def __init__(self, config: dict, history: "DownloadHistory",
                 remote_path: str, local_path: str,
                 on_log=None, on_progress=None, on_done=None):
        self.config      = config
        self.history     = history
        self.remote_path = remote_path
        self.local_path  = local_path
        self.on_log      = on_log      or (lambda m: None)
        self.on_progress = on_progress or (lambda s, m, t: None)
        self.on_done     = on_done     or (lambda r: None)
        self._stop       = threading.Event()

    def stop(self):
        self._stop.set()

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        return t

    def _run(self):
        log    = self.on_log
        result = PreScanResult()
        log(f"[PreScan] Remote: {self.remote_path}  Local: {self.local_path}")

        password = get_password(self.config)
        try:
            ftp = ftp_connect(
                self.config.get("host", ""),
                self.config.get("port", 21),
                self.config.get("user", ""),
                password,
            )
        except Exception as e:
            log(f"[PreScan] ERROR connecting: {e}")
            self.on_done(result)
            return

        log("[PreScan] Listing remote files...")
        try:
            remote_files = ftp_list_recursive(ftp, self.remote_path)
        except Exception as e:
            log(f"[PreScan] ERROR listing remote: {e}")
            try: ftp.quit()
            except: pass
            self.on_done(result)
            return
        try: ftp.quit()
        except: pass

        log(f"[PreScan] Remote: {len(remote_files)} files found")
        log("[PreScan] Scanning local folder...")
        local_index = {}
        for dirpath, _, filenames in os.walk(self.local_path):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                try: sz = os.path.getsize(full)
                except OSError: continue
                local_index.setdefault(fname.lower(), []).append((full, sz))

        local_total = sum(len(v) for v in local_index.values())
        log(f"[PreScan] Local: {local_total} files found")

        ignored_paths = self.config.get("ignored_paths", [])
        total = len(remote_files)

        for i, (rel, (rem_size, rem_modify)) in enumerate(remote_files.items()):
            if self._stop.is_set():
                break

            remote_full = f"{self.remote_path}/{rel}"

            if ignored_paths and is_ignored(remote_full, ignored_paths):
                self.on_progress(i + 1, len(result.auto_matched), total)
                continue

            fname_lower = rel.split("/")[-1].lower()
            fingerprint = DownloadHistory.make_fingerprint(remote_full, rem_size, rem_modify)

            if self.history.already_downloaded(fingerprint):
                result.already_known += 1
                self.on_progress(i + 1, len(result.auto_matched), total)
                continue

            candidates   = local_index.get(fname_lower, [])
            size_matches = [c for c in candidates if c[1] == rem_size]

            if len(size_matches) == 1:
                local_full = size_matches[0][0]
                self.history.record(
                    fingerprint=fingerprint, remote_path=remote_full,
                    local_path=local_full, size=rem_size, modify=rem_modify,
                    pair_remote=self.remote_path, pair_local=self.local_path,
                    source="prescan",
                )
                result.auto_matched.append((remote_full, local_full, rem_size, rem_modify))
                log(f"[PreScan] Matched: {rel.split('/')[-1]}")
            elif len(size_matches) > 1:
                result.needs_review.append({
                    "remote_path": remote_full, "size": rem_size,
                    "modify": rem_modify, "fingerprint": fingerprint,
                    "reason": f"Multiple local copies ({len(size_matches)})",
                    "candidates": [c[0] for c in size_matches],
                })
            elif candidates:
                result.needs_review.append({
                    "remote_path": remote_full, "size": rem_size,
                    "modify": rem_modify, "fingerprint": fingerprint,
                    "reason": f"Name match but size differs (local={candidates[0][1]}, remote={rem_size})",
                    "candidates": [c[0] for c in candidates],
                })
            else:
                result.remote_only.append((remote_full, rem_size))

            self.on_progress(i + 1, len(result.auto_matched), total)

        log(f"[PreScan] Done. Matched:{len(result.auto_matched)} "
            f"Review:{len(result.needs_review)} "
            f"Remote-only:{len(result.remote_only)} "
            f"Already known:{result.already_known}")
        self.on_done(result)


# ---------------------------------------------------------------------------
# Parallel Sync Worker
# ---------------------------------------------------------------------------

class SyncWorker(threading.Thread):
    """
    Downloads files in parallel using a thread pool.
    Each worker opens its own FTP connection.
    Stop signal is propagated by closing active FTP sockets - this actually
    interrupts retrbinary() mid-transfer instead of waiting for it to finish.
    """

    def __init__(self, config: dict, history: DownloadHistory,
                 on_log=None, on_transfer_start=None,
                 on_transfer_progress=None, on_transfer_done=None,
                 on_transfer_error=None, on_cycle_done=None,
                 debug: bool = False):
        super().__init__(daemon=True)
        self.config   = config
        self.history  = history
        self.debug    = debug
        self._stop    = threading.Event()
        self._tid     = 0
        self._tid_lock = threading.Lock()
        # Track active FTP connections so stop() can close them immediately
        self._active_ftp      = []
        self._active_ftp_lock = threading.Lock()
        # Track per-file error counts so repeated failures are suppressed from
        # the main log and routed to the FTP Errors tab instead
        self._error_counts      = {}   # remote_path -> count
        self._error_counts_lock = threading.Lock()
        self.ERROR_THRESHOLD    = 2    # suppress after this many failures

        self.on_log               = on_log               or (lambda m: None)
        self.on_transfer_start    = on_transfer_start    or (lambda t, f: None)
        self.on_transfer_progress = on_transfer_progress or (lambda t, p, d, tot: None)
        self.on_transfer_done     = on_transfer_done     or (lambda t, s: None)
        # on_transfer_error(remote_path, error_msg, count) — called on every failure
        self.on_transfer_error    = on_transfer_error    or (lambda rp, e, c: None)
        self.on_cycle_done        = on_cycle_done        or (lambda: None)

    def stop(self):
        self._stop.set()
        # To immediately unblock retrbinary() we must close the raw socket.
        # ftp.abort() only sends an ABOR command over the control channel
        # but retrbinary() is blocked on the *data* socket waiting for bytes.
        # Closing ftp.sock (the control socket) AND the data socket raises
        # immediately inside the blocking read, which exits the thread.
        with self._active_ftp_lock:
            for ftp in self._active_ftp:
                # Close data socket first (this is what retrbinary blocks on)
                try:
                    if hasattr(ftp, "fp") and ftp.fp:
                        ftp.fp.close()
                except: pass
                # Close control socket
                try:
                    if ftp.sock:
                        ftp.sock.close()
                except: pass
                try: ftp.close()
                except: pass

    def _next_tid(self):
        with self._tid_lock:
            self._tid += 1
            return self._tid

    def _register_ftp(self, ftp):
        with self._active_ftp_lock:
            self._active_ftp.append(ftp)

    def _unregister_ftp(self, ftp):
        with self._active_ftp_lock:
            try: self._active_ftp.remove(ftp)
            except ValueError: pass

    def run(self):
        while not self._stop.is_set():
            self._sync_all()
            try:
                self.on_cycle_done()
            except Exception:
                pass
            interval = self.config.get("interval", 5) * 60
            elapsed  = 0
            while elapsed < interval and not self._stop.is_set():
                time.sleep(2)
                elapsed += 2

    def _sync_all(self):
        # Credentials, pairs, and ignored paths come from self.config (set by caller).
        # interval and parallel_downloads are re-read from disk so live edits take effect.
        disk_cfg = load_config()
        cfg      = dict(self.config)
        cfg["interval"]           = disk_cfg.get("interval", cfg.get("interval", 5))
        cfg["parallel_downloads"] = disk_cfg.get("parallel_downloads", cfg.get("parallel_downloads", 3))
        cfg["ignored_paths"]      = disk_cfg.get("ignored_paths", cfg.get("ignored_paths", []))

        pairs    = cfg.get("folder_pairs", [])
        host     = cfg.get("host", "")
        port     = cfg.get("port", 21)
        user     = cfg.get("user", "")
        password = get_password(cfg)
        workers  = max(1, min(10, cfg.get("parallel_downloads", 3)))

        if not host or not pairs:
            return

        log_fn = self.on_log if self.debug else None

        try:
            ftp_main = ftp_connect(host, port, user, password, log_fn=log_fn)
        except Exception as e:
            self.on_log(f"[ERROR] Cannot connect: {e}")
            return

        # Use a fresh FTP connection per folder pair.
        # _mlsd_binary (binary-mode MLSD) leaves the connection in TYPE I mode
        # and uses transfercmd(), which can leave the session in an inconsistent
        # state. Reusing one connection across pairs causes empty listings on
        # subsequent pairs. A fresh connection per pair costs one extra login but
        # is completely reliable.
        download_queue = []
        try: ftp_main.quit()
        except: pass

        for pair in pairs:
            if self._stop.is_set():
                break
            try:
                ftp_pair = ftp_connect(host, port, user, password, log_fn=log_fn)
            except Exception as e:
                self.on_log(f"[ERROR] Cannot connect for {pair['remote']}: {e}")
                continue
            try:
                items = self._plan_pair(ftp_pair, pair["remote"], pair["local"],
                                        log_fn, cfg.get("ignored_paths", []))
                download_queue.extend(items)
            finally:
                try: ftp_pair.quit()
                except: pass

        if not download_queue or self._stop.is_set():
            if not download_queue:
                self.on_log("  No new files to download")
            return

        self.on_log(f"  Downloading {len(download_queue)} file(s) "
                    f"with {workers} parallel worker(s)...")

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self._download_file_worker,
                    host, port, user, password, *item
                ): item
                for item in download_queue
                if not self._stop.is_set()
            }
            for future in as_completed(futures):
                if self._stop.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    future.result()
                except Exception as e:
                    self.on_log(f"[ERROR] Worker: {e}")

    def _plan_pair(self, ftp, remote_path, local_path, log_fn, ignored_paths):
        self.on_log(f"Checking {remote_path} -> {local_path}")
        try:
            current = ftp_list_recursive(ftp, remote_path, log_fn=log_fn)
        except Exception as e:
            self.on_log(f"[ERROR] Listing {remote_path}: {e}")
            return []

        self.on_log(f"  Found {len(current)} file(s) on remote")
        to_download = []

        for rel, (size, modify) in current.items():
            if self._stop.is_set(): break
            remote_file = f"{remote_path}/{rel}"

            if ignored_paths and is_ignored(remote_file, ignored_paths):
                if self.debug: self.on_log(f"  [IGNORED] {rel}")
                continue

            fingerprint = DownloadHistory.make_fingerprint(remote_file, size, modify)
            if self.history.already_downloaded(fingerprint):
                if self.debug: self.on_log(f"  [SKIP] {rel}")
                continue

            local_file = os.path.join(local_path, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(local_file), exist_ok=True)
            to_download.append((remote_file, local_file, size, modify,
                                fingerprint, remote_path, local_path))

        return to_download

    def _download_file_worker(self, host, port, user, password,
                               remote_file, local_file, total_size,
                               modify, fingerprint, pair_remote, pair_local):
        tid     = self._next_tid()
        display = os.path.basename(remote_file)
        self.on_transfer_start(tid, display)
        downloaded = [0]

        try:
            ftp = ftp_connect(host, port, user, password)
        except Exception as e:
            self.on_transfer_done(tid, f"Error: {e}")
            self.on_log(f"  [ERROR] {display}: connect failed: {e}")
            return

        self._register_ftp(ftp)

        # Write to .part temp file; rename to final only on complete success.
        # This guarantees a cancel, stop, or crash never leaves silent partial data.
        part_file = local_file + ".part"

        def callback(data):
            if self._stop.is_set():
                raise Exception("Stopped by user")
            with open(part_file, "ab") as f:
                f.write(data)
            downloaded[0] += len(data)
            pct = int(downloaded[0] / total_size * 100) if total_size else 0
            self.on_transfer_progress(tid, pct, downloaded[0], total_size)

        try:
            if os.path.exists(part_file): os.remove(part_file)
            if os.path.exists(local_file): os.remove(local_file)
            ftp.retrbinary(f"RETR {remote_file}", callback, blocksize=65536)
            # Atomic rename — only appears as the real file after full transfer
            os.rename(part_file, local_file)
            self.history.record(
                fingerprint=fingerprint, remote_path=remote_file,
                local_path=local_file, size=total_size, modify=modify,
                pair_remote=pair_remote, pair_local=pair_local, source="download",
            )
            self.on_transfer_done(tid, "Done")
            self.on_log(f"  Downloaded: {display}")
        except Exception as e:
            if self._stop.is_set():
                self.on_transfer_done(tid, "Stopped")
            else:
                err_msg = str(e)
                with self._error_counts_lock:
                    self._error_counts[remote_file] = self._error_counts.get(remote_file, 0) + 1
                    count = self._error_counts[remote_file]
                self.on_transfer_done(tid, f"Error: {err_msg}")
                self.on_transfer_error(remote_file, err_msg, count)
                if count <= self.ERROR_THRESHOLD:
                    self.on_log(f"  [ERROR] {display}: {err_msg}")
                elif count == self.ERROR_THRESHOLD + 1:
                    self.on_log(
                        f"  [ERROR] {display} has failed {count} times — "
                        f"further errors suppressed. See FTP Errors tab."
                    )
            # Always clean up .part — never leave partial data on disk
            try:
                if os.path.exists(part_file): os.remove(part_file)
            except: pass
        finally:
            self._unregister_ftp(ftp)
            try: ftp.quit()
            except: pass
