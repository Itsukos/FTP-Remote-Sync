"""
ftp_gui.py  -  Tkinter desktop GUI for FTP sync.
Run:  python ftp_gui.py
"""

# ---------------------------------------------------------------------------
# Self-update: runs FIRST before any imports, fixes PyInstaller's frozen
# importer so files in updates/ take priority over baked-in versions.
# ---------------------------------------------------------------------------
def _apply_updates():
    import os, sys, importlib
    if not getattr(sys, "frozen", False):
        return  # plain .py - nothing to do

    exe_dir     = os.path.dirname(sys.executable)
    updates_dir = os.path.join(exe_dir, "updates")
    if not os.path.isdir(updates_dir):
        return

    has_updates = any(
        f in os.listdir(updates_dir)
        for f in ("ftp_core.py", "ftp_gui.py", "ftp_web.py")
    )
    if not has_updates:
        return

    # Step 1 - put updates/ on sys.path
    if updates_dir not in sys.path:
        sys.path.insert(0, updates_dir)

    # Step 2 - move PathFinder BEFORE FrozenImporter in sys.meta_path
    # (FrozenImporter intercepts baked-in modules before sys.path is checked)
    try:
        frozen_idx = path_idx = None
        for i, finder in enumerate(sys.meta_path):
            n = type(finder).__name__
            if n == "FrozenImporter": frozen_idx = i
            elif n == "PathFinder":   path_idx   = i
        if frozen_idx is not None and path_idx is not None and path_idx > frozen_idx:
            sys.meta_path.insert(frozen_idx, sys.meta_path.pop(path_idx))
    except Exception:
        pass

    # Step 3 - clear import caches and any already-frozen module entries
    importlib.invalidate_caches()
    for mod in ("ftp_core", "ftp_gui", "ftp_web"):
        sys.modules.pop(mod, None)

    # Step 4 - if there is an updated ftp_gui.py, exec it and stop running
    # the frozen version. This replaces the current main script entirely.
    # Guard prevents infinite re-exec if the override also calls _apply_updates.
    if os.environ.get("FTPSYNC_OVERRIDE_ACTIVE") == "1":
        return  # already running from override, don't re-exec
    gui_override = os.path.join(updates_dir, "ftp_gui.py")
    if os.path.exists(gui_override):
        os.environ["FTPSYNC_OVERRIDE_ACTIVE"] = "1"
        with open(gui_override, "r", encoding="utf-8") as _f:
            _code = _f.read()
        exec(compile(_code, gui_override, "exec"), {"__file__": gui_override, "__name__": "__main__"})
        raise SystemExit(0)   # frozen script stops here; override took over

_apply_updates()
# ---------------------------------------------------------------------------

import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk

import ftp_core as core

# Tray icon support (pystray + Pillow) — graceful fallback if not installed
try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    _TRAY_AVAILABLE = True
except ImportError:
    _TRAY_AVAILABLE = False

# Drag-and-drop support via tkinterdnd2 (optional).
# Import attempted here; auto-install happens later in __main__ AFTER the
# single-instance check, so pip never runs in a second instance.
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False

# ---------------------------------------------------------------------------
# Theme definitions
# Each theme is a dict of semantic colour roles used throughout the app.
# ---------------------------------------------------------------------------
THEMES = {
    "Dark (default)": {
        "bg":         "#1e1e2e",   # window / tab background
        "bg2":        "#181825",   # slightly darker panels
        "bg3":        "#313244",   # input fields, cards
        "bg4":        "#45475a",   # buttons, borders
        "fg":         "#cdd6f4",   # primary text
        "fg2":        "#a6adc8",   # secondary / muted text
        "fg3":        "#585b70",   # very muted / disabled
        "accent":     "#89b4fa",   # blue accent (headings, tabs)
        "accent2":    "#b4befe",   # lighter accent
        "green":      "#40a02b",   # success / start
        "green_fg":   "#a6e3a1",   # success text
        "red":        "#d20f39",   # danger / stop
        "red_fg":     "#f38ba8",   # error text
        "yellow":     "#f9e2af",   # warning text
        "link":       "#89b4fa",   # clickable links
        "pill_run":   "#40a02b",
        "pill_idle":  "#45475a",
        "tray_idle":  (30, 102, 245, 255),
        "tray_sync":  (166, 227, 161, 255),
    },
    "Purple": {
        "bg":         "#1a1025",
        "bg2":        "#120c1e",
        "bg3":        "#2d1f42",
        "bg4":        "#3d2d55",
        "fg":         "#e0d0f8",
        "fg2":        "#b89ed4",
        "fg3":        "#6b5585",
        "accent":     "#c084fc",
        "accent2":    "#d8b4fe",
        "green":      "#7c3aed",
        "green_fg":   "#c4b5fd",
        "red":        "#be185d",
        "red_fg":     "#f9a8d4",
        "yellow":     "#fde68a",
        "link":       "#c084fc",
        "pill_run":   "#7c3aed",
        "pill_idle":  "#3d2d55",
        "tray_idle":  (192, 132, 252, 255),
        "tray_sync":  (196, 181, 253, 255),
    },
    "Sakura": {
        "bg":         "#1f1520",
        "bg2":        "#170e18",
        "bg3":        "#2e1a2e",
        "bg4":        "#3d2540",
        "fg":         "#f5d0e8",
        "fg2":        "#c9a0bc",
        "fg3":        "#7a5570",
        "accent":     "#f4a8c7",
        "accent2":    "#fbc8dd",
        "green":      "#c2527a",
        "green_fg":   "#fbc8dd",
        "red":        "#8b1a4a",
        "red_fg":     "#f4a8c7",
        "yellow":     "#fde8c0",
        "link":       "#f4a8c7",
        "pill_run":   "#c2527a",
        "pill_idle":  "#3d2540",
        "tray_idle":  (244, 168, 199, 255),
        "tray_sync":  (251, 200, 221, 255),
    },
    "Light": {
        "bg":         "#f5f5f7",
        "bg2":        "#e8e8ec",
        "bg3":        "#ffffff",
        "bg4":        "#d1d1d8",
        "fg":         "#1a1a2e",
        "fg2":        "#4a4a6a",
        "fg3":        "#9a9ab0",
        "accent":     "#2563eb",
        "accent2":    "#3b82f6",
        "green":      "#16a34a",
        "green_fg":   "#15803d",
        "red":        "#dc2626",
        "red_fg":     "#b91c1c",
        "yellow":     "#92400e",
        "link":       "#2563eb",
        "pill_run":   "#16a34a",
        "pill_idle":  "#d1d1d8",
        "tray_idle":  (37, 99, 235, 255),
        "tray_sync":  (22, 163, 74, 255),
    },
    "Midnight Blue": {
        "bg":         "#0d1117",
        "bg2":        "#090e14",
        "bg3":        "#161b22",
        "bg4":        "#21262d",
        "fg":         "#c9d1d9",
        "fg2":        "#8b949e",
        "fg3":        "#484f58",
        "accent":     "#58a6ff",
        "accent2":    "#79c0ff",
        "green":      "#238636",
        "green_fg":   "#56d364",
        "red":        "#da3633",
        "red_fg":     "#ff7b72",
        "yellow":     "#e3b341",
        "link":       "#58a6ff",
        "pill_run":   "#238636",
        "pill_idle":  "#21262d",
        "tray_idle":  (88, 166, 255, 255),
        "tray_sync":  (86, 211, 100, 255),
    },
    "Mocha": {
        "bg":         "#2c1f14",
        "bg2":        "#1e1409",
        "bg3":        "#3d2b1a",
        "bg4":        "#5a3f28",
        "fg":         "#f0e0c8",
        "fg2":        "#c8a882",
        "fg3":        "#7a5c3a",
        "accent":     "#e8a050",
        "accent2":    "#f0c080",
        "green":      "#7a9a3a",
        "green_fg":   "#b8d870",
        "red":        "#c03020",
        "red_fg":     "#f09080",
        "yellow":     "#f0d060",
        "link":       "#e8a050",
        "pill_run":   "#7a9a3a",
        "pill_idle":  "#5a3f28",
        "tray_idle":  (232, 160, 80, 255),
        "tray_sync":  (184, 216, 112, 255),
    },
}

_current_theme: dict = THEMES["Dark (default)"]

def T(key: str) -> str:
    """Get a colour value from the active theme."""
    return _current_theme.get(key, "#ff00ff")  # magenta = missing key, easy to spot

# CJK-capable font: try fonts that cover Japanese/Chinese/Korean on Windows/Mac/Linux
def _cjk_font(size: int = 10, bold: bool = False) -> tuple:
    """Return a font tuple that can render CJK characters."""
    weight = "bold" if bold else "normal"
    # Preferred order: broad Unicode coverage first
    for name in ("Yu Gothic UI", "Meiryo UI", "MS Gothic", "Noto Sans CJK JP",
                 "Noto Sans JP", "IPAGothic", "TakaoPGothic",
                 "WenQuanYi Micro Hei", "Segoe UI", "Arial Unicode MS"):
        try:
            import tkinter as _tk
            import tkinter.font as _tkf
            f = _tkf.Font(family=name, size=size, weight=weight)
            # If the font family was actually found, actual() returns it
            if f.actual("family").lower().startswith(name.lower().split()[0]):
                return (name, size, weight) if bold else (name, size)
        except Exception:
            continue
    return ("Segoe UI", size, weight) if bold else ("Segoe UI", size)


# ---------------------------------------------------------------------------
# Remote Folder Browser
# ---------------------------------------------------------------------------

class RemoteBrowserDialog(tk.Toplevel):
    def __init__(self, parent, credentials, initial_path="/"):
        super().__init__(parent)
        self.title("Browse Remote FTP Server")
        self.configure(bg=T("bg"))
        self.geometry("540x500")
        self.resizable(True, True)
        self.grab_set()
        self.credentials   = credentials
        self.ftp           = None
        self.result        = None
        self._current_path = "/"
        self._loading      = False
        self._build_ui()
        threading.Thread(target=self._connect_and_load,
                         args=(initial_path,), daemon=True).start()
        self.wait_window()

    def _build_ui(self):
        tk.Label(self, text="Select Remote Folder", bg=T("bg"), fg=T("accent"),
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
        pf = tk.Frame(self, bg=T("bg2"), pady=6)
        pf.pack(fill="x", padx=10)
        tk.Label(pf, text="Path:", bg=T("bg2"), fg=T("fg2"),
                 font=("Segoe UI", 9)).pack(side="left", padx=(8, 4))
        self.path_var = tk.StringVar(value="/")
        e = tk.Entry(pf, textvariable=self.path_var, width=36,
                     bg=T("bg3"), fg=T("fg"), insertbackground=T("fg"),
                     relief="flat", font=("Consolas", 10))
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _: self._go_to_path())
        tk.Button(pf, text="Go", command=self._go_to_path,
                  bg=T("bg4"), fg=T("fg"), relief="flat", width=4).pack(side="left", padx=2)
        tk.Button(pf, text="Up", command=self._go_up,
                  bg=T("bg4"), fg=T("fg"), relief="flat", width=4).pack(side="left", padx=2)
        self.status_var = tk.StringVar(value="Connecting...")
        tk.Label(self, textvariable=self.status_var, bg=T("bg"), fg=T("yellow"),
                 font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=4)
        tf = tk.Frame(self, bg=T("bg"))
        tf.pack(fill="both", expand=True, padx=10, pady=4)
        style = ttk.Style()
        style.configure("Br.Treeview", background=T("bg2"), foreground=T("fg"),
                        fieldbackground=T("bg2"), rowheight=26, font=("Segoe UI", 10))
        self.tree = ttk.Treeview(tf, show="tree", selectmode="browse", style="Br.Treeview")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _: self._on_double_click())
        self.tree.bind("<<TreeviewSelect>>", lambda _: self._on_select())
        # Buttons - pack BEFORE tree so they are always visible
        bf = tk.Frame(self, bg=T("bg"))
        bf.pack(side="bottom", fill="x", padx=10, pady=(4, 12))
        tk.Button(bf, text="Select This Folder", command=self._select,
                  bg=T("green"), fg="white", relief="flat",
                  font=("Segoe UI", 10), width=20).pack(side="left", padx=4)
        tk.Button(bf, text="Cancel", command=self.destroy,
                  bg=T("red"), fg="white", relief="flat",
                  font=("Segoe UI", 10), width=10).pack(side="left", padx=4)

    def _connect_and_load(self, initial_path):
        try:
            c = self.credentials
            if "password_enc" in c:
                pwd = core.get_password(c)
            else:
                pwd = c.get("_plaintext_pass", c.get("password", ""))
            self.ftp = core.ftp_connect(c["host"], c["port"], c["user"], pwd)
            # Always start at the server's actual landing directory.
            # "/" may be empty/inaccessible on servers like Whatbox that
            # place users directly in their home dir.
            login_dir = self.ftp.pwd()
            start = login_dir if initial_path in ("/", "", None) else initial_path
            self.after(0, lambda: self._load_path(start))
        except Exception as e:
            self.after(0, lambda: self.status_var.set(f"Connection failed: {e}"))

    def _load_path(self, path):
        if self._loading: return
        self._loading = True
        self.status_var.set(f"Loading {path}...")
        errors = []
        def _log(m): errors.append(m)
        def worker():
            try:
                entries = core.ftp_list_dir_full(self.ftp, path, log_fn=_log)
                dirs = sorted(e["name"] for e in entries if e["is_dir"])
                if not dirs and errors:
                    # Surface the actual listing error to the status bar
                    detail = " | ".join(errors[-3:])
                    self.after(0, lambda: self.status_var.set(
                        f"Listed {path} (0 folders) — debug: {detail}"))
                self.after(0, lambda: self._populate(path, dirs))
            except Exception as e:
                err = str(e) + (" | " + " | ".join(errors) if errors else "")
                self.after(0, lambda: self.status_var.set(f"Error: {err}"))
                self._loading = False
        threading.Thread(target=worker, daemon=True).start()

    def _populate(self, path, dirs):
        self._current_path = path
        self.path_var.set(path)
        for item in self.tree.get_children(): self.tree.delete(item)
        for d in dirs:
            self.tree.insert("", "end", iid=path.rstrip("/")+"/"+d, text=f"📁 {d}")
        self.status_var.set(
            f"{path}  —  {len(dirs)} subfolder(s)" if dirs
            else f"{path}  —  no subfolders (check Log tab for details)")
        self._loading = False

    def _on_double_click(self):
        sel = self.tree.selection()
        if sel: self._load_path(sel[0])

    def _on_select(self):
        sel = self.tree.selection()
        if sel: self.path_var.set(sel[0])

    def _go_up(self):
        path = self._current_path.rstrip("/")
        self._load_path("/".join(path.split("/")[:-1]) or "/")

    def _go_to_path(self):
        self._load_path(self.path_var.get().strip() or "/")

    def _select(self):
        self.result = self.path_var.get().strip() or "/"
        self.destroy()

    def destroy(self):
        if self.ftp:
            try: self.ftp.quit()
            except: pass
            self.ftp = None
        super().destroy()


# ---------------------------------------------------------------------------
# Pair Dialog
# ---------------------------------------------------------------------------

class PairDialog(tk.Toplevel):
    def __init__(self, parent, credentials=None, existing=None):
        super().__init__(parent)
        self.title("Add / Edit Folder Pair")
        self.configure(bg=T("bg"))
        self.resizable(False, False)
        self.result      = None
        self.credentials = credentials or {}
        self.grab_set()

        tk.Label(self, text="Remote Path (on FTP server):", bg=T("bg"),
                 fg=T("fg"), font=("Segoe UI", 10)).grid(
            row=0, column=0, columnspan=2, padx=14, pady=(14, 4), sticky="w")
        rr = tk.Frame(self, bg=T("bg"))
        rr.grid(row=1, column=0, columnspan=2, padx=14, pady=(0, 4), sticky="ew")
        self.remote_var = tk.StringVar(value=existing["remote"] if existing else "/")
        tk.Entry(rr, textvariable=self.remote_var, width=36,
                 bg=T("bg3"), fg=T("fg"), insertbackground=T("fg"),
                 relief="flat", font=("Consolas", 10)).pack(side="left")
        has_creds = bool(self.credentials.get("host"))
        tk.Button(rr, text="Browse Server", command=self._browse_remote,
                  bg=T("bg4") if has_creds else T("bg2"),
                  fg=T("fg") if has_creds else T("fg3"),
                  relief="flat", font=("Segoe UI", 9),
                  state="normal" if has_creds else "disabled").pack(side="left", padx=8)

        tk.Label(self, text="Local Path (on this machine):", bg=T("bg"),
                 fg=T("fg"), font=("Segoe UI", 10)).grid(
            row=2, column=0, columnspan=2, padx=14, pady=(10, 4), sticky="w")
        lr = tk.Frame(self, bg=T("bg"))
        lr.grid(row=3, column=0, columnspan=2, padx=14, pady=(0, 16), sticky="ew")
        self.local_var = tk.StringVar(value=existing["local"] if existing else "")
        tk.Entry(lr, textvariable=self.local_var, width=36,
                 bg=T("bg3"), fg=T("fg"), insertbackground=T("fg"),
                 relief="flat").pack(side="left")
        tk.Button(lr, text="Browse", command=self._browse_local,
                  bg=T("bg4"), fg=T("fg"), relief="flat",
                  font=("Segoe UI", 9)).pack(side="left", padx=8)

        br = tk.Frame(self, bg=T("bg"))
        br.grid(row=4, column=0, columnspan=2, pady=(0, 14))
        tk.Button(br, text="OK", command=self._ok, width=10,
                  bg=T("green"), fg="white", relief="flat",
                  font=("Segoe UI", 10)).pack(side="left", padx=8)
        tk.Button(br, text="Cancel", command=self.destroy, width=10,
                  bg=T("red"), fg="white", relief="flat",
                  font=("Segoe UI", 10)).pack(side="left", padx=8)
        self.wait_window()

    def _browse_remote(self):
        dlg = RemoteBrowserDialog(self, self.credentials,
                                  initial_path=self.remote_var.get() or "/")
        if dlg.result: self.remote_var.set(dlg.result)

    def _browse_local(self):
        d = filedialog.askdirectory(parent=self)
        if d: self.local_var.set(d)

    def _ok(self):
        r = self.remote_var.get().strip()
        l = self.local_var.get().strip()
        if not r or not l:
            messagebox.showerror("Error", "Both paths are required.", parent=self)
            return
        self.result = {"remote": r, "local": l}
        self.destroy()


# ---------------------------------------------------------------------------
# Pre-Scan Review Dialog
# ---------------------------------------------------------------------------

class ReviewDialog(tk.Toplevel):
    def __init__(self, parent, items: list, history: core.DownloadHistory):
        super().__init__(parent)
        self.title("Pre-Scan Review - Ambiguous Files")
        self.configure(bg=T("bg"))
        self.resizable(True, True)
        self.grab_set()
        self.history    = history
        self.items      = items
        self._decisions = {}

        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = min(960, sw - 80), min(620, sh - 80)
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.minsize(700, 400)

        hdr = tk.Frame(self, bg=T("bg"))
        hdr.pack(fill="x", padx=14, pady=(14, 4))
        tk.Label(hdr, text="Pre-Scan Review - Ambiguous Files",
                 bg=T("bg"), fg=T("accent"),
                 font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(hdr,
                 text="Double-click a row to toggle.  "
                      "Mark as Known = fingerprinted, never downloaded.  "
                      "Skip = normal sync handles it.",
                 bg=T("bg"), fg=T("fg2"),
                 font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(4, 0))

        # Button bar packed BEFORE table so it is always visible
        bf = tk.Frame(self, bg=T("bg2"), pady=10)
        bf.pack(side="bottom", fill="x")
        tk.Button(bf, text="Mark All as Known", command=self._mark_all,
                  bg=T("bg4"), fg=T("fg"), relief="flat",
                  font=("Segoe UI", 10), padx=12, pady=4).pack(side="left", padx=14)
        tk.Button(bf, text="Skip All", command=self._skip_all,
                  bg=T("bg4"), fg=T("fg"), relief="flat",
                  font=("Segoe UI", 10), padx=12, pady=4).pack(side="left", padx=4)
        tk.Button(bf, text="Save Changes", command=self._apply,
                  bg=T("green"), fg="white", relief="flat",
                  font=("Segoe UI", 11, "bold"), padx=18, pady=6).pack(side="right", padx=14)
        tk.Button(bf, text="Cancel", command=self.destroy,
                  bg=T("red"), fg="white", relief="flat",
                  font=("Segoe UI", 10), padx=12, pady=4).pack(side="right", padx=4)

        tf = tk.Frame(self, bg=T("bg"))
        tf.pack(fill="both", expand=True, padx=12, pady=(4, 0))

        cols = ("action", "filename", "reason", "candidates")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings")
        self.tree.heading("action",     text="Action")
        self.tree.heading("filename",   text="Remote File")
        self.tree.heading("reason",     text="Reason")
        self.tree.heading("candidates", text="Local Candidates")
        self.tree.column("action",     width=120, anchor="center", stretch=False)
        self.tree.column("filename",   width=220, anchor="w")
        self.tree.column("reason",     width=260, anchor="w")
        self.tree.column("candidates", width=300, anchor="w")

        vsb = ttk.Scrollbar(tf, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(tf, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        vsb.pack(side="right",  fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.tag_configure("known", foreground=T("green_fg"))
        self.tree.tag_configure("skip",  foreground=T("fg"))

        for item in items:
            name  = item["remote_path"].split("/")[-1]
            cands = ", ".join(item.get("candidates", []))
            iid   = self.tree.insert("", "end",
                                     values=("Skip", name, item["reason"], cands),
                                     tags=("skip",))
            self._decisions[iid] = "skip"

        self.tree.bind("<Double-1>", self._toggle)
        self.wait_window()

    def _toggle(self, event):
        iid = self.tree.focus()
        if not iid: return
        vals = list(self.tree.item(iid)["values"])
        if self._decisions[iid] == "skip":
            self._decisions[iid] = "known"
            vals[0] = "Mark as Known"
            self.tree.item(iid, values=vals, tags=("known",))
        else:
            self._decisions[iid] = "skip"
            vals[0] = "Skip"
            self.tree.item(iid, values=vals, tags=("skip",))

    def _mark_all(self):
        for iid in self._decisions:
            self._decisions[iid] = "known"
            vals = list(self.tree.item(iid)["values"])
            vals[0] = "Mark as Known"
            self.tree.item(iid, values=vals, tags=("known",))

    def _skip_all(self):
        for iid in self._decisions:
            self._decisions[iid] = "skip"
            vals = list(self.tree.item(iid)["values"])
            vals[0] = "Skip"
            self.tree.item(iid, values=vals, tags=("skip",))

    def _apply(self):
        for idx, (iid, decision) in enumerate(self._decisions.items()):
            if decision == "known":
                item  = self.items[idx]
                local = item["candidates"][0] if item.get("candidates") else ""
                self.history.record(
                    fingerprint=item["fingerprint"],
                    remote_path=item["remote_path"],
                    local_path=local,
                    size=item["size"],
                    modify=item["modify"],
                    source="prescan_manual",
                )
        self.destroy()


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Formatting helpers (used by browser tab)
# ---------------------------------------------------------------------------

class _ServerDialog(tk.Toplevel):
    """
    Modal dialog for adding or editing a server profile.
    result = {"name", "host", "port", "user", "password_enc"} or None if cancelled.
    """
    def __init__(self, parent, title="Server", profile=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(bg=T("bg"))
        self.result = None

        profile = profile or {}

        pad = dict(padx=10, pady=4)
        fields_frame = tk.Frame(self, bg=T("bg"))
        fields_frame.pack(fill="x", padx=16, pady=(16, 4))

        def row(label, default="", secret=False, width=32):
            r = fields_frame.grid_size()[1]
            tk.Label(fields_frame, text=label, bg=T("bg"), fg=T("fg2"),
                     font=("Segoe UI", 9), anchor="e", width=12).grid(
                         row=r, column=0, sticky="e", **pad)
            var = tk.StringVar(value=default)
            e = tk.Entry(fields_frame, textvariable=var, width=width,
                         bg=T("bg3"), fg=T("fg"), insertbackground=T("fg"),
                         relief="flat", font=("Segoe UI", 10))
            if secret:
                e.config(show="*")
            e.grid(row=r, column=1, sticky="w", **pad)
            return var

        self._name_var = row("Name:",     profile.get("name", ""),  width=28)
        self._host_var = row("Host:",     profile.get("host", ""),  width=28)
        self._port_var = row("Port:",     str(profile.get("port", 21)), width=8)
        self._user_var = row("Username:", profile.get("user", ""),  width=28)

        # Password — show blank (never pre-fill from enc)
        tk.Label(fields_frame, text="Password:", bg=T("bg"), fg=T("fg2"),
                 font=("Segoe UI", 9), anchor="e", width=12).grid(
                     row=4, column=0, sticky="e", **pad)
        self._pass_var = tk.StringVar()
        self._pass_entry = tk.Entry(fields_frame, textvariable=self._pass_var,
                                    width=28, show="*",
                                    bg=T("bg3"), fg=T("fg"),
                                    insertbackground=T("fg"), relief="flat",
                                    font=("Segoe UI", 10))
        self._pass_entry.grid(row=4, column=1, sticky="w", **pad)

        self._show_pass = tk.BooleanVar(value=False)
        tk.Checkbutton(fields_frame, text="Show", variable=self._show_pass,
                       bg=T("bg"), fg=T("fg2"), selectcolor=T("bg3"),
                       activebackground=T("bg"), font=("Segoe UI", 8),
                       command=self._toggle_pass).grid(row=4, column=2, padx=4)

        # Keep existing encrypted password if user leaves password blank
        self._existing_enc = profile.get("password_enc", "")

        # Test button
        tk.Button(fields_frame, text="Test Connection",
                  bg=T("bg4"), fg=T("fg"), relief="flat", font=("Segoe UI", 9),
                  command=self._test).grid(row=5, column=1, sticky="w",
                                           padx=10, pady=(4, 0))

        # Buttons
        btn_row = tk.Frame(self, bg=T("bg"))
        btn_row.pack(fill="x", padx=16, pady=12)
        tk.Button(btn_row, text="Save", bg=T("green"), fg="white",
                  relief="flat", font=("Segoe UI", 10, "bold"), width=10,
                  command=self._save).pack(side="left", padx=4)
        tk.Button(btn_row, text="Cancel", bg=T("bg4"), fg=T("fg"),
                  relief="flat", font=("Segoe UI", 10), width=8,
                  command=self.destroy).pack(side="left", padx=4)

        self._center()
        self.wait_window()

    def _toggle_pass(self):
        self._pass_entry.config(show="" if self._show_pass.get() else "*")

    def _test(self):
        host = self._host_var.get().strip()
        user = self._user_var.get().strip()
        raw  = self._pass_var.get()
        if raw:
            import core as _core
            enc = _core.encrypt_password(raw)
            pwd = raw
        else:
            enc = self._existing_enc
            pwd = core.decrypt_password(enc)
        if not host:
            messagebox.showerror("Missing", "Enter a host.", parent=self)
            return
        try:
            port = int(self._port_var.get().strip() or 21)
        except ValueError:
            port = 21
        try:
            ftp = core.ftp_connect(host, port, user, pwd)
            ftp.quit()
            messagebox.showinfo("Success", f"Connected to {host}!", parent=self)
        except Exception as e:
            messagebox.showerror("Failed", str(e), parent=self)

    def _save(self):
        name = self._name_var.get().strip()
        host = self._host_var.get().strip()
        if not name:
            messagebox.showerror("Required", "Enter a profile name.", parent=self)
            return
        if not host:
            messagebox.showerror("Required", "Enter a host.", parent=self)
            return
        try:
            port = int(self._port_var.get().strip() or 21)
        except ValueError:
            port = 21

        raw = self._pass_var.get()
        if raw:
            enc = core.encrypt_password(raw)
        else:
            enc = self._existing_enc   # keep existing if unchanged

        self.result = {
            "name":         name,
            "host":         host,
            "port":         port,
            "user":         self._user_var.get().strip(),
            "password_enc": enc,
            "folder_pairs": [],
        }
        self.destroy()

    def _center(self):
        self.update_idletasks()
        pw = self.master.winfo_rootx() + self.master.winfo_width() // 2
        ph = self.master.winfo_rooty() + self.master.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw - w//2}+{ph - h//2}")


def _fmt_size(n) -> str:
    try: n = int(n)
    except: return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"

def _fmt_modify(raw: str) -> str:
    if not raw: return ""
    try:
        from datetime import datetime
        if len(raw) >= 14 and raw[:14].isdigit():
            return datetime.strptime(raw[:14], "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
        return raw[:16]
    except Exception:
        return raw[:16]

class FTPSyncApp:
    def __init__(self, root):
        self.root    = root
        self.root.title("FTP Remote Sync")
        self.root.geometry("960x740")
        self.root.resizable(True, True)
        self.root.configure(bg=T("bg"))

        self.history      = core.DownloadHistory()
        self.worker       = None
        self.interval_var = tk.IntVar(value=5)
        self.parallel_var = tk.IntVar(value=3)
        self.debug_var    = tk.BooleanVar(value=False)
        self.log_queue    = queue.Queue()
        self._tray_icon        = None
        self._quitting         = False   # True only when user explicitly picks Quit

        # Notification settings (loaded from config below)
        self._theme_var        = tk.StringVar(value="Dark (default)")
        self._notif_mode       = tk.StringVar(value="every")   # every | batch | cycle | off
        self._notif_batch_var  = tk.IntVar(value=5)            # N for batch mode
        self._notif_count      = 0   # files downloaded this sync cycle
        self._notif_batch_acc  = 0   # accumulator for batch mode

        # Load config - this also creates settings.json on first run
        cfg = core.load_config()
        self.folder_pairs = cfg.get("folder_pairs", [])

        # Load theme + notification preferences
        saved_theme = cfg.get("theme", "Dark (default)")
        if saved_theme in THEMES:
            self._theme_var.set(saved_theme)
            global _current_theme
            _current_theme = THEMES[saved_theme]
        self._notif_mode.set(cfg.get("notif_mode", "every"))
        self._notif_batch_var.set(cfg.get("notif_batch", 5))
        # Maps tid -> filename so transfer table never reads back from treeview
        self._transfer_names = {}
        # FTP error records: {remote_path: {"error": str, "count": int, "last_seen": str}}
        self._ftp_errors = {}
        self.interval_var.set(cfg.get("interval", 5))
        self.parallel_var.set(cfg.get("parallel_downloads", 3))

        # Credentials: only populate UI fields if save_credentials was set
        saved = cfg.get("save_credentials", False)
        self._saved_host = cfg.get("host", "") if saved else ""
        self._saved_port = str(cfg.get("port", 21)) if saved else "21"
        self._saved_user = cfg.get("user", "") if saved else ""
        self._saved_pass = core.get_password(cfg) if saved else ""
        self._save_creds_default = saved

        self._build_ui()
        self._poll_log()

        # Wire close button: minimise to tray if available, else destroy
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Start tray icon in background thread
        if _TRAY_AVAILABLE:
            threading.Thread(target=self._run_tray, daemon=True).start()
        self._check_config_on_start(cfg)

    def _check_config_on_start(self, cfg):
        """Warn about config issues on startup without blocking."""
        warns = core.validate_config(cfg)
        # Only show if there are real actionable problems and host is set
        # (skip warnings for brand-new empty config)
        if warns and cfg.get("host"):
            self.log("[Config] Warnings:\n  " + "\n  ".join(warns))

    # -----------------------------------------------------------------------
    # Style
    # -----------------------------------------------------------------------

    def _apply_ttk_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TFrame",    background=T("bg"))
        s.configure("TLabel",    background=T("bg"), foreground=T("fg"), font=("Segoe UI", 10))
        s.configure("TButton",   background=T("bg3"), foreground=T("fg"), font=("Segoe UI", 10), relief="flat", borderwidth=0)
        s.map("TButton",         background=[("active", T("bg4")), ("pressed", T("bg4"))],
                                 foreground=[("active", T("fg"))])
        s.configure("G.TButton", background=T("green"), foreground="#fff", font=("Segoe UI", 10))
        s.map("G.TButton",       background=[("active", T("green")), ("pressed", T("green"))])
        s.configure("R.TButton", background=T("red"), foreground="#fff", font=("Segoe UI", 10))
        s.map("R.TButton",       background=[("active", T("red")), ("pressed", T("red"))])
        s.configure("Treeview",  background=T("bg2"), foreground=T("fg"),
                    fieldbackground=T("bg2"), rowheight=24, font=("Segoe UI", 9))
        s.configure("Treeview.Heading", background=T("bg3"), foreground=T("accent"),
                    font=("Segoe UI", 9, "bold"), relief="flat")
        s.map("Treeview",        background=[("selected", T("bg4"))],
                                 foreground=[("selected", T("fg"))])
        s.configure("TNotebook", background=T("bg"), borderwidth=0)
        s.configure("TNotebook.Tab", background=T("bg3"), foreground=T("fg"),
                    padding=[12, 4], font=("Segoe UI", 10))
        s.map("TNotebook.Tab",   background=[("selected", T("accent")), ("active", T("bg4"))],
                                 foreground=[("selected", "#fff"), ("active", T("fg"))])
        s.configure("TSeparator",  background=T("bg4"))
        s.configure("TSpinbox",    background=T("bg3"), foreground=T("fg"),
                    fieldbackground=T("bg3"), insertcolor=T("fg"),
                    arrowcolor=T("fg2"), font=("Segoe UI", 10))
        s.configure("TCombobox",   background=T("bg3"), foreground=T("fg"),
                    fieldbackground=T("bg3"), selectbackground=T("bg4"),
                    selectforeground=T("fg"), arrowcolor=T("fg2"))
        s.map("TCombobox",         fieldbackground=[("readonly", T("bg3"))],
                                   selectbackground=[("readonly", T("bg3"))],
                                   foreground=[("readonly", T("fg"))])
        s.configure("TScrollbar",  background=T("bg4"), troughcolor=T("bg2"),
                    arrowcolor=T("fg2"), borderwidth=0)
        s.configure("TCheckbutton", background=T("bg"), foreground=T("fg"),
                    font=("Segoe UI", 10))
        s.map("TCheckbutton",      background=[("active", T("bg"))])
        s.configure("Br.Treeview", background=T("bg2"), foreground=T("fg"),
                    fieldbackground=T("bg2"), rowheight=26, font=("Segoe UI", 10))

    # Keep old name as alias so any existing calls don't break
    def _apply_style(self):
        self._apply_ttk_styles()

    # -----------------------------------------------------------------------
    # UI Build
    # -----------------------------------------------------------------------

    def _build_ui(self):
        self._apply_ttk_styles()
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_dash     = ttk.Frame(nb)
        self.tab_folders  = ttk.Frame(nb)
        self.tab_prescan  = ttk.Frame(nb)
        self.tab_ignore   = ttk.Frame(nb)
        self.tab_history  = ttk.Frame(nb)
        self.tab_errors   = ttk.Frame(nb)
        self.tab_browser  = ttk.Frame(nb)
        self.tab_settings = ttk.Frame(nb)
        nb.add(self.tab_dash,     text="  Dashboard  ")
        nb.add(self.tab_folders,  text="  Folder Pairs  ")
        nb.add(self.tab_prescan,  text="  Pre-Scan  ")
        nb.add(self.tab_ignore,   text="  Ignore List  ")
        nb.add(self.tab_history,  text="  History  ")
        nb.add(self.tab_errors,   text="  FTP Errors  ")
        nb.add(self.tab_browser,  text="  Browser  ")
        nb.add(self.tab_settings, text="  Settings  ")
        self.nb            = nb   # keep reference so we can flash the errors tab
        self.notebook      = nb   # alias used by --session auto-connect
        self._br_tab_index = 6    # Browser tab is index 6 (0-based)
        nb.bind("<<NotebookTabChanged>>", self._on_tab_change)
        self._build_dashboard()
        self._build_folders()
        self._build_prescan()
        self._build_ignore()
        self._build_history()
        self._build_errors()
        self._build_browser()
        self._build_settings()

    # -----------------------------------------------------------------------
    # Dashboard
    # -----------------------------------------------------------------------

    def _build_dashboard(self):
        t = self.tab_dash

        top = ttk.Frame(t)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="FTP Remote Sync", font=("Segoe UI", 14, "bold"),
                  foreground=T("accent")).pack(side="left")
        self.start_btn = ttk.Button(top, text="Start Sync", style="G.TButton",
                                    command=self.start_sync, width=12)
        self.start_btn.pack(side="right", padx=4)
        self.stop_btn = ttk.Button(top, text="Stop", style="R.TButton",
                                   command=self.stop_sync, width=8, state="disabled")
        self.stop_btn.pack(side="right", padx=4)
        ttk.Button(top, text="Sync Now", command=self.sync_now, width=10).pack(side="right", padx=4)

        # Credentials
        cf = tk.Frame(t, bg=T("bg2"), pady=6)
        cf.pack(fill="x", padx=10, pady=(0, 4))
        fields = [
            ("Host:",     "host_var", 22, self._saved_host, False),
            ("Port:",     "port_var",  6, self._saved_port, False),
            ("User:",     "user_var", 14, self._saved_user, False),
            ("Password:", "pass_var", 14, self._saved_pass, True),
        ]
        col = 0
        for label, attr, width, val, secret in fields:
            tk.Label(cf, text=label, bg=T("bg2"), fg=T("fg2"),
                     font=("Segoe UI", 9)).grid(row=0, column=col, padx=(8, 2))
            col += 1
            var = tk.StringVar(value=val)
            setattr(self, attr, var)
            e = tk.Entry(cf, textvariable=var, width=width,
                         bg=T("bg3"), fg=T("fg"), insertbackground=T("fg"), relief="flat")
            if secret: e.config(show="*")
            e.grid(row=0, column=col, padx=2)
            col += 1
        self.save_creds_var = tk.BooleanVar(value=self._save_creds_default)
        tk.Checkbutton(cf, text="Save", variable=self.save_creds_var,
                       bg=T("bg2"), fg=T("fg2"), selectcolor=T("bg3"),
                       activebackground=T("bg2"), font=("Segoe UI", 9),
                       command=self._on_save_creds_toggle).grid(row=0, column=col, padx=(8, 4))
        col += 1
        self.test_btn_ref = tk.Button(cf, text="Test Connection", command=self._test_connection,
                  bg=T("accent"), fg="white", relief="flat",
                  font=("Segoe UI", 9), padx=10)
        self.test_btn_ref.grid(row=0, column=col, padx=(4, 8))

        sr = tk.Frame(t, bg=T("bg"))
        sr.pack(fill="x", padx=12, pady=2)
        self.status_var = tk.StringVar(value="Idle")
        tk.Label(sr, textvariable=self.status_var, bg=T("bg"), fg=T("green_fg"),
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Checkbutton(sr, text="Verbose log", variable=self.debug_var,
                       bg=T("bg"), fg=T("fg2"), selectcolor=T("bg3"),
                       activebackground=T("bg"), font=("Segoe UI", 9)).pack(side="right", padx=4)
        ttk.Button(sr, text="Open settings.json", command=self._open_settings_file,
                   width=18).pack(side="right", padx=8)

        pt_hdr = tk.Frame(t, bg=T("bg"))
        pt_hdr.pack(fill="x", padx=12, pady=(6, 2))
        ttk.Label(pt_hdr, text="Active Transfers", font=("Segoe UI", 10, "bold"),
                  foreground=T("accent")).pack(side="left")
        ttk.Button(pt_hdr, text="Clear Completed",
                   command=self._clear_completed_transfers).pack(side="right")
        pf = ttk.Frame(t)
        pf.pack(fill="both", expand=True, padx=10)
        cols = ("file", "progress", "transferred", "status")
        self.pt = ttk.Treeview(pf, columns=cols, show="headings", height=6)
        self.pt.heading("file",        text="File")
        self.pt.heading("progress",    text="Progress")
        self.pt.heading("transferred", text="Transferred")
        self.pt.heading("status",      text="Status")
        self.pt.column("file",        width=320, anchor="w")
        self.pt.column("progress",    width=160, anchor="center")
        self.pt.column("transferred", width=160, anchor="center")
        self.pt.column("status",      width=100, anchor="center")
        sb = ttk.Scrollbar(pf, orient="vertical", command=self.pt.yview)
        self.pt.configure(yscrollcommand=sb.set)
        self.pt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        lh = tk.Frame(t, bg=T("bg"))
        lh.pack(fill="x", padx=12, pady=(8, 2))
        ttk.Label(lh, text="Log", font=("Segoe UI", 10, "bold"),
                  foreground=T("accent")).pack(side="left")
        ttk.Button(lh, text="Clear", command=self._clear_log, width=7).pack(side="right")
        lf = ttk.Frame(t)
        lf.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.log_text = tk.Text(lf, height=9, bg=T("bg2"), fg=T("fg2"),
                                font=("Consolas", 9), relief="flat",
                                state="disabled", wrap="word")
        lsb = ttk.Scrollbar(lf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

    def _on_save_creds_toggle(self):
        """When user unchecks Save, immediately wipe credentials from disk."""
        if not self.save_creds_var.get():
            cfg = core.load_config()
            cfg["save_credentials"] = False
            cfg["host"]         = ""
            cfg["port"]         = 21
            cfg["user"]         = ""
            cfg["password_enc"] = ""
            core.save_config(cfg)
            self.log("[Config] Credentials removed from settings.json")

    def _test_connection(self):
        creds = self._collect_credentials()
        if not creds.get("host"):
            messagebox.showwarning("Missing", "Enter a host first.", parent=self.root)
            return
        # Run in background so UI doesn't freeze
        self.test_btn_ref.config(state="disabled", text="Testing...")
        def run():
            ok, msg = core.test_connection(
                creds["host"], creds["port"],
                creds["user"], creds["_plaintext_pass"]
            )
            self.root.after(0, lambda: self._show_test_result(ok, msg))
        import threading
        threading.Thread(target=run, daemon=True).start()

    def _show_test_result(self, ok: bool, msg: str):
        # Re-enable the button (find it by searching cf children)
        try:
            self.test_btn_ref.config(state="normal", text="Test Connection")
        except Exception:
            pass
        if ok:
            messagebox.showinfo("Connection Successful", msg, parent=self.root)
            self.log(f"[Test] OK - {msg.splitlines()[0]}")
        else:
            messagebox.showerror("Connection Failed", msg, parent=self.root)
            self.log(f"[Test] FAILED - {msg}")

    def _open_settings_file(self):
        import subprocess, sys
        path = core.CONFIG_FILE
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # -----------------------------------------------------------------------
    # Folder Pairs tab
    # -----------------------------------------------------------------------

    def _build_folders(self):
        t = self.tab_folders
        ttk.Label(t, text="Folder Pairs  (Remote -> Local)",
                  font=("Segoe UI", 12, "bold"), foreground=T("accent")).pack(anchor="w", padx=12, pady=8)
        ttk.Label(t, text="Changes are saved to settings.json automatically.",
                  foreground=T("fg2")).pack(anchor="w", padx=12)
        br = ttk.Frame(t)
        br.pack(anchor="w", padx=12, pady=6)
        ttk.Button(br, text="+ Add",  command=self.add_pair).pack(side="left", padx=4)
        ttk.Button(br, text="Edit",   command=self.edit_pair).pack(side="left", padx=4)
        ttk.Button(br, text="Remove", command=self.remove_pair).pack(side="left", padx=4)
        cols = ("remote", "local")
        self.pair_tree = ttk.Treeview(t, columns=cols, show="headings")
        self.pair_tree.heading("remote", text="Remote Path")
        self.pair_tree.heading("local",  text="Local Path")
        self.pair_tree.column("remote", width=400, anchor="w")
        self.pair_tree.column("local",  width=400, anchor="w")
        sb = ttk.Scrollbar(t, orient="vertical", command=self.pair_tree.yview)
        self.pair_tree.configure(yscrollcommand=sb.set)
        self.pair_tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=8)
        sb.pack(side="right", fill="y", pady=8)
        self._refresh_pair_tree()

    # -----------------------------------------------------------------------
    # Pre-Scan tab
    # -----------------------------------------------------------------------

    def _build_prescan(self):
        t = self.tab_prescan
        ttk.Label(t, text="Pre-Scan Existing Folders",
                  font=("Segoe UI", 12, "bold"), foreground=T("accent")).pack(anchor="w", padx=12, pady=(12, 2))
        ttk.Label(t, text=(
            "Point this at folders you already have locally. The scanner connects to FTP,\n"
            "matches files by name and size, and fingerprints them so they are never downloaded again."
        ), foreground=T("fg2"), font=("Segoe UI", 9)).pack(anchor="w", padx=12)

        inp = tk.Frame(t, bg=T("bg2"), pady=10)
        inp.pack(fill="x", padx=12, pady=8)

        # ── Quick-pick from saved folder pairs ───────────────────────────
        tk.Label(inp, text="Use saved pair:", bg=T("bg2"), fg=T("fg2"),
                 font=("Segoe UI", 9)).grid(row=0, column=0, padx=(10, 4), pady=4, sticky="w")
        self.ps_pair_var = tk.StringVar(value="— pick a pair or type manually below —")
        self.ps_pair_combo = ttk.Combobox(inp, textvariable=self.ps_pair_var,
                                          state="readonly", width=44)
        self.ps_pair_combo.grid(row=0, column=1, padx=4, pady=4, sticky="w")
        self.ps_pair_combo.bind("<<ComboboxSelected>>", self._ps_pair_selected)
        self._ps_refresh_pair_combo()

        tk.Label(inp, text="Remote Path:", bg=T("bg2"), fg=T("fg2"),
                 font=("Segoe UI", 9)).grid(row=1, column=0, padx=(10, 4), pady=4, sticky="w")
        self.ps_remote_var = tk.StringVar()
        tk.Entry(inp, textvariable=self.ps_remote_var, width=42,
                 bg=T("bg3"), fg=T("fg"), insertbackground=T("fg"),
                 relief="flat", font=("Consolas", 10)).grid(row=1, column=1, padx=4, pady=4)
        tk.Button(inp, text="Browse FTP", command=self._ps_browse_remote,
                  bg=T("bg4"), fg=T("fg"), relief="flat",
                  font=("Segoe UI", 9)).grid(row=1, column=2, padx=8)

        tk.Label(inp, text="Local Folder:", bg=T("bg2"), fg=T("fg2"),
                 font=("Segoe UI", 9)).grid(row=2, column=0, padx=(10, 4), pady=4, sticky="w")
        self.ps_local_var = tk.StringVar()
        tk.Entry(inp, textvariable=self.ps_local_var, width=42,
                 bg=T("bg3"), fg=T("fg"), insertbackground=T("fg"),
                 relief="flat").grid(row=2, column=1, padx=4, pady=4)
        tk.Button(inp, text="Browse Local", command=self._ps_browse_local,
                  bg=T("bg4"), fg=T("fg"), relief="flat",
                  font=("Segoe UI", 9)).grid(row=2, column=2, padx=8)

        btn_row = tk.Frame(t, bg=T("bg"))
        btn_row.pack(anchor="w", padx=12, pady=4)
        self.ps_start_btn = tk.Button(btn_row, text="Start Pre-Scan",
                                      command=self._ps_start,
                                      bg=T("green"), fg="white", relief="flat",
                                      font=("Segoe UI", 10), width=16)
        self.ps_start_btn.pack(side="left", padx=4)
        self.ps_stop_btn = tk.Button(btn_row, text="Cancel",
                                     command=self._ps_stop,
                                     bg=T("red"), fg="white", relief="flat",
                                     font=("Segoe UI", 10), width=10, state="disabled")
        self.ps_stop_btn.pack(side="left", padx=4)

        self.ps_status_var = tk.StringVar(value="Ready. Enter paths above and click Start Pre-Scan.")
        tk.Label(t, textvariable=self.ps_status_var, bg=T("bg"), fg=T("yellow"),
                 font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=2)

        pbar_frame = tk.Frame(t, bg=T("bg3"), height=14)
        pbar_frame.pack(fill="x", padx=12, pady=(0, 4))
        self.ps_pbar = tk.Frame(pbar_frame, bg=T("accent"), height=14, width=0)
        self.ps_pbar.place(x=0, y=0, height=14)

        ttk.Label(t, text="Results", font=("Segoe UI", 10, "bold"),
                  foreground=T("accent")).pack(anchor="w", padx=12, pady=(8, 2))
        rf = ttk.Frame(t)
        rf.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        rcols = ("status", "name", "detail")
        self.ps_tree = ttk.Treeview(rf, columns=rcols, show="headings", height=10)
        self.ps_tree.heading("status", text="Result")
        self.ps_tree.heading("name",   text="File")
        self.ps_tree.heading("detail", text="Detail")
        self.ps_tree.column("status", width=110, anchor="center")
        self.ps_tree.column("name",   width=280, anchor="w")
        self.ps_tree.column("detail", width=400, anchor="w")
        sb = ttk.Scrollbar(rf, orient="vertical", command=self.ps_tree.yview)
        self.ps_tree.configure(yscrollcommand=sb.set)
        self.ps_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.ps_scanner = None

    def _ps_refresh_pair_combo(self):
        """Populate the prescan pair combobox from current folder_pairs."""
        choices = ["— pick a pair or type manually below —"]
        for p in self.folder_pairs:
            choices.append(f"{p['remote']}  →  {p['local']}")
        self.ps_pair_combo["values"] = choices
        self.ps_pair_var.set(choices[0])

    def _ps_pair_selected(self, event=None):
        """Fill remote/local fields when user picks a saved pair."""
        idx = self.ps_pair_combo.current()
        if idx <= 0:
            return
        pair = self.folder_pairs[idx - 1]   # offset by 1 for the placeholder
        self.ps_remote_var.set(pair["remote"])
        self.ps_local_var.set(pair["local"])

    def _ps_browse_remote(self):
        creds = self._collect_credentials()
        if not creds.get("host"):
            messagebox.showwarning("No credentials", "Enter credentials on Dashboard first.")
            return
        dlg = RemoteBrowserDialog(self.root, creds,
                                  initial_path=self.ps_remote_var.get() or "/")
        if dlg.result:
            self.ps_remote_var.set(dlg.result)

    def _ps_browse_local(self):
        d = filedialog.askdirectory(parent=self.root)
        if d:
            self.ps_local_var.set(d)

    def _ps_start(self):
        creds  = self._collect_credentials()
        remote = self.ps_remote_var.get().strip()
        local  = self.ps_local_var.get().strip()
        if not remote or not local:
            messagebox.showerror("Missing", "Both remote and local paths are required.")
            return
        if not creds.get("host"):
            messagebox.showerror("Missing", "Enter FTP credentials on Dashboard first.")
            return

        for item in self.ps_tree.get_children():
            self.ps_tree.delete(item)
        self.ps_status_var.set("Scanning...")
        self.ps_start_btn.config(state="disabled")
        self.ps_stop_btn.config(state="normal")

        cfg = core.load_config()
        core.set_password(cfg, creds.get("_plaintext_pass", ""))
        cfg.update({k: creds[k] for k in ("host", "port", "user") if k in creds})

        self.ps_scanner = core.PreScanner(
            config=cfg, history=self.history,
            remote_path=remote, local_path=local,
            on_log=self.log,
            on_progress=self._ps_on_progress,
            on_done=self._ps_on_done,
        )
        self.ps_scanner.start()

    def _ps_stop(self):
        if self.ps_scanner:
            self.ps_scanner.stop()
        self.ps_start_btn.config(state="normal")
        self.ps_stop_btn.config(state="disabled")
        self.ps_status_var.set("Cancelled.")

    def _ps_on_progress(self, scanned, matched, total):
        pct = int(scanned / total * 100) if total else 0
        self.root.after(0, lambda: self._ps_update_progress(scanned, matched, total, pct))

    def _ps_update_progress(self, scanned, matched, total, pct):
        self.ps_status_var.set(f"Scanning... {scanned}/{total} checked, {matched} matched so far")
        bar_w = int(self.ps_pbar.master.winfo_width() * pct / 100)
        self.ps_pbar.place(x=0, y=0, width=max(bar_w, 0), height=14)

    def _ps_on_done(self, result):
        self.root.after(0, lambda: self._ps_show_results(result))

    def _ps_show_results(self, result):
        self.ps_start_btn.config(state="normal")
        self.ps_stop_btn.config(state="disabled")
        self.ps_status_var.set(
            f"Done.  Matched: {len(result.auto_matched)}  |  "
            f"Needs review: {len(result.needs_review)}  |  "
            f"Will download: {len(result.remote_only)}  |  "
            f"Already known: {result.already_known}"
        )
        for (rpath, lpath, size, _) in result.auto_matched:
            self.ps_tree.insert("", "end", values=(
                "Matched", rpath.split("/")[-1], f"-> {lpath}"))
        for item in result.needs_review:
            self.ps_tree.insert("", "end", values=(
                "Needs Review", item["remote_path"].split("/")[-1], item["reason"]))
        for (rpath, size) in result.remote_only:
            self.ps_tree.insert("", "end", values=(
                "Will Download", rpath.split("/")[-1], f"Not found locally ({self._fmt_size(size)})"))

        if result.needs_review:
            if messagebox.askyesno(
                "Review Needed",
                f"{len(result.needs_review)} file(s) could not be auto-matched.\n\n"
                "Open the review dialog to decide what to do with them?"
            ):
                ReviewDialog(self.root, result.needs_review, self.history)

    # -----------------------------------------------------------------------
    # Ignore List tab
    # -----------------------------------------------------------------------

    def _build_ignore(self):
        t = self.tab_ignore
        ttk.Label(t, text="Server Ignore List",
                  font=("Segoe UI", 12, "bold"), foreground=T("accent")).pack(
            anchor="w", padx=12, pady=(12, 2))
        ttk.Label(t, text=(
            "Remote folders listed here are completely skipped during every sync and pre-scan.\n"
            "Use exact remote paths or prefixes - a prefix also skips everything inside it."
        ), foreground=T("fg2"), font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0, 8))

        add_frame = tk.Frame(t, bg=T("bg2"), pady=8)
        add_frame.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(add_frame, text="Remote path to ignore:", bg=T("bg2"),
                 fg=T("fg2"), font=("Segoe UI", 9)).pack(side="left", padx=(10, 6))
        self.ignore_entry_var = tk.StringVar()
        tk.Entry(add_frame, textvariable=self.ignore_entry_var, width=46,
                 bg=T("bg3"), fg=T("fg"), insertbackground=T("fg"),
                 relief="flat", font=("Consolas", 10)).pack(side="left", padx=4)
        tk.Button(add_frame, text="Browse FTP", command=self._ignore_browse,
                  bg=T("bg4"), fg=T("fg"), relief="flat",
                  font=("Segoe UI", 9)).pack(side="left", padx=6)
        tk.Button(add_frame, text="+ Add", command=self._ignore_add,
                  bg=T("green"), fg="white", relief="flat",
                  font=("Segoe UI", 10), padx=10).pack(side="left", padx=4)

        lf = ttk.Frame(t)
        lf.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        cols = ("path",)
        self.ignore_tree = ttk.Treeview(lf, columns=cols, show="headings")
        self.ignore_tree.heading("path", text="Ignored Remote Path")
        self.ignore_tree.column("path", width=700, anchor="w")
        isb = ttk.Scrollbar(lf, orient="vertical", command=self.ignore_tree.yview)
        self.ignore_tree.configure(yscrollcommand=isb.set)
        self.ignore_tree.pack(side="left", fill="both", expand=True)
        isb.pack(side="right", fill="y")

        bf = ttk.Frame(t)
        bf.pack(anchor="w", padx=12, pady=(4, 10))
        ttk.Button(bf, text="Remove Selected", command=self._ignore_remove).pack(side="left", padx=4)
        ttk.Button(bf, text="Clear All",       command=self._ignore_clear).pack(side="left", padx=4)
        self._refresh_ignore_tree()

    def _ignore_browse(self):
        creds = self._collect_credentials()
        if not creds.get("host"):
            messagebox.showwarning("No credentials", "Enter credentials on Dashboard first.")
            return
        dlg = RemoteBrowserDialog(self.root, creds,
                                  initial_path=self.ignore_entry_var.get() or "/")
        if dlg.result:
            self.ignore_entry_var.set(dlg.result)

    def _ignore_add(self):
        path = self.ignore_entry_var.get().strip()
        if not path:
            messagebox.showwarning("Empty", "Enter a remote path to ignore.")
            return
        cfg     = core.load_config()
        ignored = cfg.get("ignored_paths", [])
        if path in ignored:
            messagebox.showinfo("Already exists", f"{path} is already in the list.")
            return
        ignored.append(path)
        cfg["ignored_paths"] = ignored
        core.save_config(cfg)
        self.ignore_entry_var.set("")
        self._refresh_ignore_tree()
        self.log(f"[Ignore] Added: {path}")

    def _ignore_remove(self):
        sel = self.ignore_tree.selection()
        if not sel: return
        cfg     = core.load_config()
        ignored = cfg.get("ignored_paths", [])
        for iid in sel:
            path = self.ignore_tree.item(iid)["values"][0]
            if path in ignored:
                ignored.remove(path)
                self.log(f"[Ignore] Removed: {path}")
        cfg["ignored_paths"] = ignored
        core.save_config(cfg)
        self._refresh_ignore_tree()

    def _ignore_clear(self):
        if messagebox.askyesno("Clear All", "Remove all ignored paths?"):
            cfg = core.load_config()
            cfg["ignored_paths"] = []
            core.save_config(cfg)
            self._refresh_ignore_tree()

    def _refresh_ignore_tree(self):
        for item in self.ignore_tree.get_children():
            self.ignore_tree.delete(item)
        cfg     = core.load_config()
        ignored = cfg.get("ignored_paths", [])
        for path in ignored:
            self.ignore_tree.insert("", "end", values=(path,))

    # -----------------------------------------------------------------------
    # History tab
    # -----------------------------------------------------------------------

    def _build_errors(self):
        t = self.tab_errors
        hdr = tk.Frame(t, bg=T("bg"))
        hdr.pack(fill="x", padx=12, pady=8)
        ttk.Label(hdr, text="FTP Errors",
                  font=("Segoe UI", 12, "bold"), foreground=T("red_fg")).pack(side="left")
        ttk.Button(hdr, text="Clear All",  command=self._clear_errors).pack(side="right", padx=4)
        ttk.Button(hdr, text="Ignore All", command=self._err_ignore_all).pack(side="right", padx=4)
        ttk.Label(hdr,
                  text="Files that have failed 2+ times are listed here instead of spamming the log.",
                  foreground=T("fg2"), font=("Segoe UI", 9)).pack(side="left", padx=16)

        cols = ("remote_path", "error", "count", "last_seen")
        self.err_tree = ttk.Treeview(t, columns=cols, show="headings")
        self.err_tree.heading("remote_path", text="Remote Path")
        self.err_tree.heading("error",       text="Error")
        self.err_tree.heading("count",       text="Fails")
        self.err_tree.heading("last_seen",   text="Last Seen")
        self.err_tree.column("remote_path", width=420, anchor="w")
        self.err_tree.column("error",       width=280, anchor="w")
        self.err_tree.column("count",       width=50,  anchor="center")
        self.err_tree.column("last_seen",   width=130, anchor="center")
        sb = ttk.Scrollbar(t, orient="vertical", command=self.err_tree.yview)
        self.err_tree.configure(yscrollcommand=sb.set)
        self.err_tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=8)
        sb.pack(side="right", fill="y", pady=8)
        self.err_tree.tag_configure("new", foreground=T("red_fg"))
        self.err_tree.tag_configure("old", foreground=T("fg3"))

        # Right-click context menu
        menu = tk.Menu(t, tearoff=0, bg=T("bg3"), fg=T("fg"),
                       activebackground=T("bg4"), activeforeground=T("fg"))
        menu.add_command(label="Add to Ignore List",  command=self._err_add_to_ignore)
        menu.add_command(label="Remove this entry",   command=self._err_remove_selected)
        self.err_tree.bind("<Button-3>", lambda e: menu.post(e.x_root, e.y_root))

    def _clear_errors(self):
        self._ftp_errors.clear()
        for item in self.err_tree.get_children():
            self.err_tree.delete(item)
        # Reset error counts in the active worker too
        if self.worker:
            with self.worker._error_counts_lock:
                self.worker._error_counts.clear()
        self.log("[Errors] Error list cleared.")
        # Remove the badge from the tab label
        self.nb.tab(self.tab_errors, text="  FTP Errors  ")

    def _err_add_to_ignore(self):
        sel = self.err_tree.selection()
        if not sel:
            return
        remote_path = self.err_tree.item(sel[0])["values"][0]
        cfg = core.load_config()
        ignored = cfg.get("ignored_paths", [])
        if remote_path not in ignored:
            ignored.append(remote_path)
            cfg["ignored_paths"] = ignored
            core.save_config(cfg)
            self._refresh_ignore_tree()
            self.log(f"[Ignore] Added from error list: {remote_path}")
        messagebox.showinfo("Added to Ignore List",
                            f"Added:\n{remote_path}\n\nThis path will be skipped on the next sync.",
                            parent=self.root)

    def _err_ignore_all(self):
        """Add every path currently in the error list to the Ignore List."""
        if not self._ftp_errors:
            messagebox.showinfo("Nothing to Ignore", "The error list is empty.", parent=self.root)
            return
        paths = list(self._ftp_errors.keys())
        cfg     = core.load_config()
        ignored = cfg.get("ignored_paths", [])
        added   = [p for p in paths if p not in ignored]
        if not added:
            messagebox.showinfo("Already Ignored",
                                "All paths in the error list are already on the Ignore List.",
                                parent=self.root)
            return
        ignored.extend(added)
        cfg["ignored_paths"] = ignored
        core.save_config(cfg)
        self._refresh_ignore_tree()
        self.log(f"[Ignore] Added {len(added)} path(s) from error list.")
        messagebox.showinfo("Ignored All",
                            f"Added {len(added)} path(s) to the Ignore List.\n"
                            "They will be skipped on the next sync.",
                            parent=self.root)

    def _err_remove_selected(self):
        sel = self.err_tree.selection()
        if not sel:
            return
        for iid in sel:
            vals = self.err_tree.item(iid)["values"]
            remote_path = vals[0]
            self._ftp_errors.pop(remote_path, None)
            if self.worker:
                with self.worker._error_counts_lock:
                    self.worker._error_counts.pop(remote_path, None)
            self.err_tree.delete(iid)

    def _on_transfer_error(self, remote_path, error_msg, count):
        """Called from worker thread on every download failure."""
        def _update():
            ts = datetime.now().strftime("%H:%M:%S")
            self._ftp_errors[remote_path] = {
                "error": error_msg, "count": count, "last_seen": ts
            }
            # Update or insert row in errors treeview
            iid = remote_path
            tag = "new" if count <= 3 else "old"
            vals = (remote_path, error_msg, count, ts)
            if self.err_tree.exists(iid):
                self.err_tree.item(iid, values=vals, tags=(tag,))
            else:
                self.err_tree.insert("", 0, iid=iid, values=vals, tags=(tag,))
            # Flash the tab label with count badge
            total = len(self._ftp_errors)
            self.nb.tab(self.tab_errors, text=f"  FTP Errors ({total})  ")
        self.root.after(0, _update)

    def _build_history(self):
        t = self.tab_history
        hdr = tk.Frame(t, bg=T("bg"))
        hdr.pack(fill="x", padx=12, pady=8)
        ttk.Label(hdr, text="Download History", font=("Segoe UI", 12, "bold"),
                  foreground=T("accent")).pack(side="left")
        ttk.Button(hdr, text="Clear All",      command=self._clear_history).pack(side="right", padx=4)
        ttk.Button(hdr, text="Refresh",         command=self._refresh_history).pack(side="right", padx=4)
        ttk.Button(hdr, text="Import CSV",      command=self._import_history).pack(side="right", padx=4)
        ttk.Button(hdr, text="Export CSV",      command=self._export_history).pack(side="right", padx=4)

        self.hist_stats_var = tk.StringVar()
        tk.Label(t, textvariable=self.hist_stats_var, bg=T("bg"), fg=T("green_fg"),
                 font=("Segoe UI", 9)).pack(anchor="w", padx=12)

        sf = tk.Frame(t, bg=T("bg"))
        sf.pack(fill="x", padx=12, pady=(4, 2))
        tk.Label(sf, text="Search:", bg=T("bg"), fg=T("fg2"),
                 font=("Segoe UI", 9)).pack(side="left")
        self.hist_search_var = tk.StringVar()
        self.hist_search_var.trace_add("write", lambda *_: self._filter_history())
        tk.Entry(sf, textvariable=self.hist_search_var, width=40,
                 bg=T("bg3"), fg=T("fg"), insertbackground=T("fg"),
                 relief="flat").pack(side="left", padx=8)

        cols = ("downloaded_at", "source", "remote_path", "local_path", "file_size")
        self.hist_tree = ttk.Treeview(t, columns=cols, show="headings")
        self.hist_tree.heading("downloaded_at", text="Date")
        self.hist_tree.heading("source",        text="Source")
        self.hist_tree.heading("remote_path",   text="Remote Path")
        self.hist_tree.heading("local_path",    text="Local Path")
        self.hist_tree.heading("file_size",     text="Size")
        self.hist_tree.column("downloaded_at", width=130, anchor="center")
        self.hist_tree.column("source",        width=90,  anchor="center")
        self.hist_tree.column("remote_path",   width=310, anchor="w")
        self.hist_tree.column("local_path",    width=240, anchor="w")
        self.hist_tree.column("file_size",     width=80,  anchor="center")
        sb = ttk.Scrollbar(t, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb.set)
        self.hist_tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=8)
        sb.pack(side="right", fill="y", pady=8)
        self._all_history = []

    def _refresh_history(self):
        self._all_history = self.history.get_all()
        stats = self.history.get_stats()
        mb = stats["total_bytes"] / 1048576
        self.hist_stats_var.set(
            f"Total: {stats['total_files']} files  |  {mb:.1f} MB  |  "
            f"Today: {stats['today']}  |  Pre-scanned: {stats['prescans']}"
        )
        self._filter_history()

    def _filter_history(self):
        q = self.hist_search_var.get().lower()
        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)
        for row in self._all_history:
            if q and q not in row["remote_path"].lower() and q not in row["local_path"].lower():
                continue
            self.hist_tree.insert("", "end", values=(
                row["downloaded_at"], row.get("source", "download"),
                row["remote_path"], row["local_path"],
                self._fmt_size(row["file_size"]),
            ))

    def _clear_history(self):
        if messagebox.askyesno("Clear History",
                               "Delete ALL history?\nFiles will be re-downloaded on next sync."):
            self.history.clear_all()
            self._refresh_history()

    def _export_history(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Export History",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"ftpsync_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if not path:
            return
        try:
            count = core.export_history_csv(self.history, path)
            messagebox.showinfo("Export Complete",
                                f"Exported {count} record(s) to:\n{path}", parent=self.root)
            self.log(f"[History] Exported {count} records to {path}")
        except Exception as e:
            messagebox.showerror("Export Failed", str(e), parent=self.root)

    def _import_history(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Import History CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            imported, skipped = core.import_history_csv(self.history, path)
            messagebox.showinfo("Import Complete",
                                f"Imported: {imported} new record(s)\n"
                                f"Skipped:  {skipped} (already known or invalid)",
                                parent=self.root)
            self.log(f"[History] Imported {imported}, skipped {skipped} from {path}")
            self._refresh_history()
        except Exception as e:
            messagebox.showerror("Import Failed", str(e), parent=self.root)

    def _on_tab_change(self, event):
        nb = event.widget
        if nb.tab(nb.select(), "text").strip() == "History":
            self._refresh_history()

    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # FTP Browser tab
    # -----------------------------------------------------------------------

    def _build_browser(self):
        t = self.tab_browser

        # State
        self._br_ftp        = None
        self._br_cwd        = "/"
        self._br_history    = ["/"]
        self._br_fwd        = []
        self._br_busy       = False
        self._br_dl_active  = {}
        self._br_dl_stop    = {}
        self._br_local_dir  = tk.StringVar(value="")
        self._br_status     = tk.StringVar(value="Not connected. Click Connect to browse the FTP server.")
        self._br_entries    = []          # list of entry dicts for current dir
        self._br_iid_map    = {}          # treeview iid -> entry dict (avoids name parsing)
        self._br_cfg        = core.load_config()   # cached active-server config for FTP ops
        self._br_sort_col   = "name"
        self._br_sort_rev   = False
        self._br_clipboard  = []          # cut entries for move: [{"remote_path","entry"}, ...]
        self._br_clipboard_op = None      # "cut" or "copy"

        # ── Server switcher bar ──────────────────────────────────────────────
        sv_bar = tk.Frame(t, bg=T("bg3"), pady=4)
        sv_bar.pack(fill="x")

        tk.Label(sv_bar, text="Server:", bg=T("bg3"), fg=T("fg2"),
                 font=("Segoe UI", 9)).pack(side="left", padx=(10, 4))

        self._br_server_var = tk.StringVar(value="— select server —")
        self._br_server_combo = ttk.Combobox(sv_bar, textvariable=self._br_server_var,
                                              state="readonly", width=24)
        self._br_server_combo.pack(side="left", padx=4)
        self._br_server_combo.bind("<<ComboboxSelected>>", self._br_server_selected)

        tk.Button(sv_bar, text="+ Add Server", bg=T("bg4"), fg=T("fg"),
                  relief="flat", font=("Segoe UI", 9),
                  command=self._br_add_server).pack(side="left", padx=4)

        tk.Button(sv_bar, text="Edit", bg=T("bg4"), fg=T("fg"),
                  relief="flat", font=("Segoe UI", 9),
                  command=self._br_edit_server).pack(side="left", padx=2)

        tk.Button(sv_bar, text="Remove", bg=T("bg4"), fg=T("fg"),
                  relief="flat", font=("Segoe UI", 9),
                  command=self._br_remove_server).pack(side="left", padx=2)

        tk.Button(sv_bar, text="💾 Save Session", bg=T("accent"), fg="white",
                  relief="flat", font=("Segoe UI", 9, "bold"),
                  command=self._br_save_session).pack(side="right", padx=(4, 10))

        tk.Label(sv_bar, text="Active:", bg=T("bg3"), fg=T("fg3"),
                 font=("Segoe UI", 8)).pack(side="right", padx=(12, 2))
        self._br_active_label = tk.Label(sv_bar, text="none", bg=T("bg3"),
                 fg=T("accent"), font=("Segoe UI", 8, "bold"))
        self._br_active_label.pack(side="right")

        # Populate server dropdown
        self._br_refresh_server_list()

        # ── Top toolbar ──────────────────────────────────────────────────────
        top = tk.Frame(t, bg=T("bg2"), pady=6)
        top.pack(fill="x")

        self._br_back_btn = tk.Button(top, text="◀", width=3,
            bg=T("bg4"), fg=T("fg"), relief="flat", font=("Segoe UI", 11),
            command=self._br_go_back)
        self._br_back_btn.pack(side="left", padx=(8, 2), pady=2)

        self._br_fwd_btn = tk.Button(top, text="▶", width=3,
            bg=T("bg4"), fg=T("fg"), relief="flat", font=("Segoe UI", 11),
            command=self._br_go_fwd)
        self._br_fwd_btn.pack(side="left", padx=2, pady=2)

        tk.Button(top, text="Up", width=4,
            bg=T("bg4"), fg=T("fg"), relief="flat", font=("Segoe UI", 9),
            command=self._br_go_up).pack(side="left", padx=2, pady=2)

        tk.Button(top, text="Refresh", width=8,
            bg=T("bg4"), fg=T("fg"), relief="flat", font=("Segoe UI", 9),
            command=lambda: self._br_navigate(self._br_cwd, push=False)).pack(side="left", padx=2, pady=2)

        self._br_path_var = tk.StringVar(value="/")
        path_entry = tk.Entry(top, textvariable=self._br_path_var,
            bg=T("bg3"), fg=T("fg"), insertbackground=T("fg"),
            relief="flat", font=("Consolas", 10))
        path_entry.pack(side="left", fill="x", expand=True, padx=8, ipady=4)
        path_entry.bind("<Return>", lambda e: self._br_navigate(self._br_path_var.get().strip()))

        tk.Button(top, text="Connect", width=9,
            bg=T("green"), fg="white", relief="flat", font=("Segoe UI", 9, "bold"),
            command=self._br_connect).pack(side="left", padx=(0, 4), pady=2)
        tk.Button(top, text="Disconnect", width=10,
            bg=T("bg4"), fg=T("fg"), relief="flat", font=("Segoe UI", 9),
            command=self._br_disconnect).pack(side="left", padx=(0, 4), pady=2)
        tk.Button(top, text="🔍 Diagnose", width=12,
            bg="#f9e2af", fg="#1e1e2e", relief="flat", font=("Segoe UI", 9, "bold"),
            command=self._br_diagnose).pack(side="left", padx=(0, 8), pady=2)

        # ── Main pane: left tree + right content ────────────────────────────
        paned = tk.PanedWindow(t, orient="horizontal", bg=T("bg4"),
                               sashwidth=4, sashrelief="flat")
        paned.pack(fill="both", expand=True)

        # Left: dir tree
        left = tk.Frame(paned, bg=T("bg"))
        paned.add(left, minsize=160, width=220)

        tk.Label(left, text="Folders", bg=T("bg"), fg=T("accent"),
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=8, pady=(6, 2))

        dir_fr = tk.Frame(left, bg=T("bg"))
        dir_fr.pack(fill="both", expand=True)
        self._br_dir_tree = ttk.Treeview(dir_fr, show="tree", selectmode="browse")
        self._br_dir_tree.tag_configure("dir",     foreground=T("accent"))
        self._br_dir_tree.tag_configure("loading", foreground=T("fg3"))
        dir_vsb = ttk.Scrollbar(dir_fr, orient="vertical", command=self._br_dir_tree.yview)
        self._br_dir_tree.configure(yscrollcommand=dir_vsb.set)
        dir_vsb.pack(side="right", fill="y")
        self._br_dir_tree.pack(side="left", fill="both", expand=True)
        self._br_dir_tree.bind("<<TreeviewOpen>>",   self._br_tree_expand)
        self._br_dir_tree.bind("<<TreeviewSelect>>", self._br_tree_select)

        # Right: file list + dest + queue
        right = tk.Frame(paned, bg=T("bg"))
        paned.add(right, minsize=400)

        # File list header
        fl_hdr = tk.Frame(right, bg=T("bg2"), pady=4)
        fl_hdr.pack(fill="x")
        tk.Label(fl_hdr, text="Remote Files", bg=T("bg2"), fg=T("accent"),
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=10)
        tk.Label(fl_hdr,
                 text="Dbl-click=open/queue  |  Right-click=menu  |  Drag to Destination",
                 bg=T("bg2"), fg=T("fg3"), font=("Segoe UI", 8)).pack(side="left")
        tk.Button(fl_hdr, text="Select All", bg=T("bg4"), fg=T("fg"),
                  relief="flat", font=("Segoe UI", 8),
                  command=self._br_select_all).pack(side="right", padx=6)
        tk.Button(fl_hdr, text="New Folder", bg=T("bg4"), fg=T("fg"),
                  relief="flat", font=("Segoe UI", 8),
                  command=self._br_new_folder).pack(side="right", padx=2)

        # File treeview
        fl_fr = tk.Frame(right, bg=T("bg"))
        fl_fr.pack(fill="both", expand=True)

        fl_cols = ("name", "size", "modified")
        self._br_file_tree = ttk.Treeview(fl_fr, columns=fl_cols,
                                           show="headings", selectmode="extended")
        self._br_file_tree.heading("name",     text="Name",
            command=lambda: self._br_sort("name"))
        self._br_file_tree.heading("size",     text="Size",
            command=lambda: self._br_sort("size"))
        self._br_file_tree.heading("modified", text="Modified",
            command=lambda: self._br_sort("modified"))
        self._br_file_tree.column("name",     width=340, anchor="w", stretch=True)
        self._br_file_tree.column("size",     width=90,  anchor="e", stretch=False)
        self._br_file_tree.column("modified", width=150, anchor="w", stretch=False)
        self._br_file_tree.tag_configure("dir",  foreground=T("accent"))
        self._br_file_tree.tag_configure("file", foreground=T("fg"))

        fl_vsb = ttk.Scrollbar(fl_fr, orient="vertical",   command=self._br_file_tree.yview)
        fl_hsb = ttk.Scrollbar(fl_fr, orient="horizontal", command=self._br_file_tree.xview)
        self._br_file_tree.configure(yscrollcommand=fl_vsb.set, xscrollcommand=fl_hsb.set)
        fl_hsb.pack(side="bottom", fill="x")
        fl_vsb.pack(side="right",  fill="y")
        self._br_file_tree.pack(side="left", fill="both", expand=True)
        self._br_file_tree.bind("<Double-1>",    self._br_file_dbl)
        self._br_file_tree.bind("<Return>",      self._br_file_dbl)
        self._br_file_tree.bind("<BackSpace>",   lambda e: self._br_go_up())
        self._br_file_tree.bind("<Button-3>",    self._br_right_click)   # Windows/Linux
        self._br_file_tree.bind("<Button-2>",    self._br_right_click)   # macOS

        # CJK-capable font so Japanese filenames render correctly
        try:
            cjk = _cjk_font(10)
            import tkinter.font as _tkf
            style = ttk.Style()
            style.configure("CJK.Treeview", font=cjk,
                            background=T("bg2"), foreground=T("fg"),
                            fieldbackground=T("bg2"), rowheight=22)
            self._br_file_tree.configure(style="CJK.Treeview")
        except Exception:
            pass

        # ── Transfer Controls ────────────────────────────────────────────────
        xfer_frame = tk.Frame(right, bg=T("bg3"), pady=6)
        xfer_frame.pack(fill="x")

        tk.Label(xfer_frame, text="Local folder:", bg=T("bg3"), fg=T("fg2"),
                 font=("Segoe UI", 9)).pack(side="left", padx=(10, 4))

        self._dest_entry = tk.Entry(xfer_frame, textvariable=self._br_local_dir,
            bg=T("bg4"), fg=T("fg"), insertbackground=T("fg"),
            relief="flat", font=("Consolas", 10), width=30)
        self._dest_entry.pack(side="left", padx=4, ipady=3)

        tk.Button(xfer_frame, text="Browse…", bg=T("bg4"), fg=T("fg"),
                  relief="flat", font=("Segoe UI", 9),
                  command=self._br_browse_local).pack(side="left", padx=2)

        # ↓ Download  ↑ Upload  ↔ Move (remote rename)
        tk.Button(xfer_frame, text="↓ Download",
            bg=T("green"), fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), padx=8,
            command=self._br_download_selected).pack(side="left", padx=(8, 2))

        tk.Button(xfer_frame, text="↑ Upload",
            bg=T("accent"), fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), padx=8,
            command=self._br_upload_files).pack(side="left", padx=2)

        tk.Button(xfer_frame, text="Move (rename)",
            bg=T("bg4"), fg=T("fg"), relief="flat",
            font=("Segoe UI", 9), padx=8,
            command=self._br_move_selected).pack(side="left", padx=2)

        tk.Button(xfer_frame, text="Delete",
            bg=T("red"), fg="white", relief="flat",
            font=("Segoe UI", 9), padx=6,
            command=self._br_delete_selected).pack(side="right", padx=(2, 10))

        # DnD setup (after widgets exist)
        self._br_setup_dnd()

        # ── Download queue ───────────────────────────────────────────────────
        q_hdr = tk.Frame(right, bg=T("bg2"), pady=3)
        q_hdr.pack(fill="x")
        tk.Label(q_hdr, text="Queue", bg=T("bg2"), fg=T("accent"),
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=10)
        tk.Button(q_hdr, text="Clear Done", bg=T("bg4"), fg=T("fg"),
                  relief="flat", font=("Segoe UI", 8),
                  command=self._br_clear_done).pack(side="right", padx=6)

        q_outer = tk.Frame(right, bg=T("bg"), height=150)
        q_outer.pack(fill="x")
        q_outer.pack_propagate(False)

        self._br_q_canvas = tk.Canvas(q_outer, bg=T("bg"), highlightthickness=0)
        q_vsb2 = ttk.Scrollbar(q_outer, orient="vertical", command=self._br_q_canvas.yview)
        self._br_q_canvas.configure(yscrollcommand=q_vsb2.set)
        q_vsb2.pack(side="right", fill="y")
        self._br_q_canvas.pack(side="left", fill="both", expand=True)

        self._br_q_inner = tk.Frame(self._br_q_canvas, bg=T("bg"))
        self._br_q_win   = self._br_q_canvas.create_window(
            (0, 0), window=self._br_q_inner, anchor="nw")
        self._br_q_inner.bind("<Configure>",
            lambda e: self._br_q_canvas.configure(
                scrollregion=self._br_q_canvas.bbox("all")))
        self._br_q_canvas.bind("<Configure>",
            lambda e: self._br_q_canvas.itemconfig(self._br_q_win, width=e.width))

        # Status bar
        tk.Label(right, textvariable=self._br_status,
                 bg=T("bg2"), fg=T("fg2"), font=("Segoe UI", 8), anchor="w",
                 ).pack(fill="x", side="bottom", ipady=2)

    # ── Browser: server management ────────────────────────────────────────

    def _br_refresh_server_list(self):
        """Reload the server combo from config."""
        cfg     = core.load_config()
        servers = core.list_servers(cfg)
        names   = [s["name"] for s in servers]
        self._br_server_combo["values"] = names
        active = cfg.get("active_server", "")
        if active and active in names:
            self._br_server_var.set(active)
            self._br_active_label.configure(text=active)
        elif names:
            self._br_server_var.set(names[0])
        else:
            self._br_server_var.set("— no servers saved —")

    def _br_server_selected(self, event=None):
        """Switch to the selected server profile."""
        name = self._br_server_var.get()
        if not name or name.startswith("—"):
            return
        cfg = core.load_config()
        cfg = core.activate_server(cfg, name)
        core.save_config(cfg)
        self._br_cfg = cfg             # update cached config immediately
        self._br_active_label.configure(text=name)
        if self._br_ftp:
            self._br_disconnect()
        self._br_status.set(f"Switched to {name} — click Connect.")

    def _br_add_server(self):
        """Open a dialog to add a new server profile."""
        dlg = _ServerDialog(self.root, title="Add Server")
        if dlg.result:
            cfg = core.load_config()
            cfg = core.save_server(cfg, dlg.result)
            core.save_config(cfg)
            self._br_refresh_server_list()
            self._br_server_var.set(dlg.result["name"])

    def _br_edit_server(self):
        """Edit the currently selected server profile."""
        name = self._br_server_var.get()
        if not name or name.startswith("—"):
            return
        cfg     = core.load_config()
        profile = core.get_server(cfg, name)
        dlg     = _ServerDialog(self.root, title="Edit Server", profile=profile)
        if dlg.result:
            # Remove old entry and add updated one
            cfg = core.delete_server(cfg, name)
            cfg = core.save_server(cfg, dlg.result)
            core.save_config(cfg)
            self._br_refresh_server_list()

    def _br_remove_server(self):
        """Remove the selected server profile."""
        name = self._br_server_var.get()
        if not name or name.startswith("—"):
            return
        if name == "Default":
            messagebox.showinfo("Can't remove",
                "The Default server is built from Dashboard credentials.",
                parent=self.root)
            return
        if not messagebox.askyesno("Remove server",
                f"Remove '{name}' from saved servers?", parent=self.root):
            return
        cfg = core.load_config()
        cfg = core.delete_server(cfg, name)
        core.save_config(cfg)
        self._br_refresh_server_list()

    # ── Browser: save session / desktop shortcut ───────────────────────────

    def _br_save_session(self):
        """
        Save a single self-contained launcher file:
          Windows → .bat  that embeds the session JSON in a here-doc and launches the app.
          Mac/Linux → .sh  that does the same.
        One file, double-click to reconnect — no separate .session.json needed.
        """
        name = self._br_server_var.get()
        if not name or name.startswith("—"):
            messagebox.showinfo("No server selected",
                "Select a server profile first.", parent=self.root)
            return

        cfg     = core.load_config()
        profile = core.get_server(cfg, name)
        host    = profile.get("host", "")
        if not host:
            messagebox.showerror("No host",
                "The selected profile has no host configured.", parent=self.root)
            return

        import sys as _sys, json as _json
        safe_name = "".join(c for c in name
                            if c.isalnum() or c in (" ", "-", "_")).strip() or "FTPSession"

        session_data = {
            "session_name": name,
            "host":         host,
            "port":         profile.get("port", 21),
            "user":         profile.get("user", ""),
            "password_enc": profile.get("password_enc", ""),
            "start_path":   self._br_cwd,
            "local_dir":    self._br_local_dir.get(),
        }
        session_json = _json.dumps(session_data)  # single line, no newlines

        exe    = _sys.executable.replace("\\", "\\\\")
        script = core.get_main_script().replace("\\", "\\\\")

        is_win = _sys.platform == "win32"
        ext    = ".bat" if is_win else ".sh"

        from tkinter import filedialog as _fd
        out_path = _fd.asksaveasfilename(
            parent           = self.root,
            title            = "Save Session Launcher",
            initialdir       = self._get_desktop_path(),
            initialfile      = safe_name + ext,
            defaultextension = ext,
            filetypes        = (
                ([("Batch files", "*.bat"), ("All files", "*.*")] if is_win
                 else [("Shell scripts", "*.sh"), ("All files", "*.*")])))
        if not out_path:
            return

        if is_win:
            # Write a .bat that saves the embedded JSON to a temp file then launches
            bat = (
                "@echo off\n"
                "setlocal\n"
                f"set SESSION_JSON={session_json}\n"
                "set TMPJSON=%TEMP%\\ftpsync_session_%RANDOM%.json\n"
                "echo %SESSION_JSON% > %TMPJSON%\n"
                f"\"{exe}\" \"{script}\" --session \"%TMPJSON%\"\n"
                "del %TMPJSON% 2>nul\n"
                "endlocal\n"
            )
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(bat)
        else:
            # Write a .sh that writes JSON to a temp file and launches
            json_escaped = session_json.replace("'", "'\''")
            sh = (
                "#!/bin/bash\n"
                "TMPJSON=$(mktemp /tmp/ftpsync_session_XXXXXX.json)\n"
                f"printf '%s' '{json_escaped}' > \"$TMPJSON\"\n"
                f"\"{exe}\" \"{script}\" --session \"$TMPJSON\"\n"
                "rm -f \"$TMPJSON\"\n"
            )
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(sh)
            os.chmod(out_path, 0o755)

        messagebox.showinfo(
            "Session saved",
            f"Launcher saved to:\n{out_path}\n\n"
            "Double-click it to open this server directly.",
            parent=self.root)

    @staticmethod
    def _get_desktop_path() -> str:
        import sys as _sys
        if _sys.platform == "win32":
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
                desktop, _ = winreg.QueryValueEx(key, "Desktop")
                return desktop
            except Exception:
                pass
        return os.path.join(os.path.expanduser("~"), "Desktop")

    # ── Browser: diagnostics ──────────────────────────────────────────────

    def _br_diagnose(self):
        """
        Raw FTP diagnostic: makes a fresh connection and tries every listing
        method step-by-step, capturing every server response and error.
        Opens a scrollable popup so the user can see exactly what's happening.
        """
        cfg = getattr(self, "_br_cfg", None) or core.load_config()
        if not cfg.get("host"):
            messagebox.showerror("No host", "Configure server credentials first.", parent=self.root)
            return

        lines = []
        def log(m): lines.append(m)

        def run():
            import ftplib, ssl, traceback
            host = cfg["host"]
            port = int(cfg.get("port", 21))
            user = cfg.get("user", "")
            pwd  = core.get_password(cfg)

            log(f"=== FTP Diagnostics ===")
            log(f"Host: {host}:{port}  User: {user}")
            log("")

            ftp = None

            # ── Step 1: FTPS connect ──────────────────────────────────────
            log("--- Step 1: FTPS (TLS) connect ---")
            try:
                ftp = ftplib.FTP_TLS()
                ftp.set_debuglevel(0)
                ftp.connect(host, port, timeout=20)
                log(f"  connect() OK")
                ftp.login(user, pwd)
                log(f"  login() OK")
                ftp.prot_p()
                log(f"  prot_p() OK")
                ftp.set_pasv(True)
                log(f"  set_pasv(True) OK")
                welcome = ftp.getwelcome()
                log(f"  Welcome: {welcome[:120]}")
            except Exception as e:
                log(f"  FTPS FAILED: {e}")
                log("  Trying plain FTP...")
                try:
                    ftp = ftplib.FTP()
                    ftp.connect(host, port, timeout=20)
                    ftp.login(user, pwd)
                    ftp.set_pasv(True)
                    log(f"  Plain FTP OK. Welcome: {ftp.getwelcome()[:80]}")
                except Exception as e2:
                    log(f"  Plain FTP also FAILED: {e2}")
                    self.root.after(0, lambda: self._show_diag(lines))
                    return

            # ── Step 2: encoding negotiation ─────────────────────────────
            log("")
            log("--- Step 2: Encoding ---")
            try:
                resp = ftp.sendcmd("OPTS UTF8 ON")
                log(f"  OPTS UTF8 ON -> {resp}")
                ftp.encoding = "utf-8"
            except Exception as e:
                log(f"  OPTS UTF8 ON failed: {e}")
                ftp.encoding = "utf-8"
                log("  Defaulting to utf-8 anyway")

            # ── Step 3: PWD ───────────────────────────────────────────────
            log("")
            log("--- Step 3: PWD ---")
            try:
                cwd = ftp.pwd()
                log(f"  PWD = '{cwd}'")
            except Exception as e:
                log(f"  PWD failed: {e}")
                cwd = "/"

            # ── Step 4: MLSD <path> ───────────────────────────────────────
            log("")
            log(f"--- Step 4: MLSD '{cwd}' ---")
            try:
                raw = list(ftp.mlsd(cwd, facts=["type", "size", "modify"]))
                log(f"  Got {len(raw)} entries")
                for name, facts in raw[:10]:
                    log(f"    {facts.get('type','?'):6}  {name}")
                if len(raw) > 10: log(f"    ... and {len(raw)-10} more")
            except Exception as e:
                log(f"  MLSD failed: {e}")
                log(f"  {traceback.format_exc().splitlines()[-1]}")

            # ── Step 5: MLSD no-arg (after CWD) ──────────────────────────
            log("")
            log(f"--- Step 5: CWD '{cwd}' then MLSD (no arg) ---")
            try:
                ftp.cwd(cwd)
                log(f"  CWD OK")
                raw2 = list(ftp.mlsd(facts=["type", "size", "modify"]))
                log(f"  MLSD (no arg) got {len(raw2)} entries")
                for name, facts in raw2[:10]:
                    log(f"    {facts.get('type','?'):6}  {name}")
                if len(raw2) > 10: log(f"    ... and {len(raw2)-10} more")
            except Exception as e:
                log(f"  FAILED: {e}")

            # ── Step 6: LIST ──────────────────────────────────────────────
            log("")
            log(f"--- Step 6: LIST '{cwd}' ---")
            list_lines = []
            try:
                ftp.dir(cwd, list_lines.append)
                log(f"  Got {len(list_lines)} lines")
                for l in list_lines[:10]: log(f"    {l}")
                if len(list_lines) > 10: log(f"    ... and {len(list_lines)-10} more")
            except Exception as e:
                log(f"  LIST failed: {e}")

            # ── Step 7: NLST ──────────────────────────────────────────────
            log("")
            log(f"--- Step 7: NLST '{cwd}' ---")
            try:
                names = ftp.nlst(cwd)
                log(f"  Got {len(names)} names")
                for n in names[:10]: log(f"    {n}")
            except Exception as e:
                log(f"  NLST failed: {e}")

            # ── Step 8: FEAT ──────────────────────────────────────────────
            log("")
            log("--- Step 8: FEAT ---")
            try:
                feat = ftp.sendcmd("FEAT")
                for l in feat.splitlines(): log(f"  {l}")
            except Exception as e:
                log(f"  FEAT failed: {e}")

            try: ftp.quit()
            except: pass

            log("")
            log("=== Diagnostics complete ===")
            self.root.after(0, lambda: self._show_diag(lines))

        import threading as _t
        self._br_status.set("Running diagnostics…")
        _t.Thread(target=run, daemon=True).start()

    def _show_diag(self, lines):
        """Show diagnostic results in a scrollable popup."""
        self._br_status.set("Diagnostics done — see popup")
        win = tk.Toplevel(self.root)
        win.title("FTP Diagnostics")
        win.configure(bg=T("bg"))
        win.geometry("720x540")

        # Copy button
        top = tk.Frame(win, bg=T("bg"))
        top.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(top, text="FTP Diagnostic Report", bg=T("bg"), fg=T("accent"),
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        def _copy():
            win.clipboard_clear()
            win.clipboard_append("\n".join(lines))
            messagebox.showinfo("Copied", "Report copied to clipboard.", parent=win)
        tk.Button(top, text="📋 Copy to Clipboard", bg=T("bg4"), fg=T("fg"),
                  relief="flat", command=_copy,
                  font=("Segoe UI", 9)).pack(side="right")

        frame = tk.Frame(win, bg=T("bg"))
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        txt = tk.Text(frame, bg="#0d0d17", fg="#a6e3a1",
                      font=("Consolas", 9), relief="flat",
                      wrap="none", state="normal")
        sb_y = ttk.Scrollbar(frame, orient="vertical",   command=txt.yview)
        sb_x = ttk.Scrollbar(frame, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side="right",  fill="y")
        sb_x.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True)
        txt.insert("end", "\n".join(lines))
        txt.configure(state="disabled")

        # Highlight errors in red
        txt.configure(state="normal")
        content = "\n".join(lines)
        for keyword in ("FAILED", "failed", "Error", "ERROR"):
            start = "1.0"
            while True:
                pos = txt.search(keyword, start, stopindex="end")
                if not pos: break
                end = f"{pos}+{len(keyword)}c"
                txt.tag_add("err", pos, end)
                start = end
        txt.tag_configure("err", foreground="#f38ba8")
        txt.configure(state="disabled")

    # ── Browser: drag and drop ─────────────────────────────────────────────

    def _br_setup_dnd(self):
        """
        Wire up drag-and-drop for the browser.

        With tkinterdnd2:
          • Drop local files/folders onto the file list  → upload to current remote dir
          • Drop local folder onto Destination entry     → set as local download target
          • Drag remote files out of file list           → download then hand to Explorer

        Without tkinterdnd2 (fallback):
          • Click-drag within the file list, release over Destination → queue download
        """
        if _DND_AVAILABLE:
            try:
                # ── Destination entry: drop local folder to set download target ──
                self._dest_entry.drop_target_register(DND_FILES)
                self._dest_entry.dnd_bind("<<Drop>>", self._br_dnd_drop_dest)

                # ── File list: drop local files to UPLOAD them ──────────────────
                self._br_file_tree.drop_target_register(DND_FILES)
                self._br_file_tree.dnd_bind("<<Drop>>", self._br_dnd_drop_upload)

                # ── File list: drag remote files OUT to Explorer ────────────────
                self._br_file_tree.drag_source_register(1, DND_FILES)
                self._br_file_tree.dnd_bind("<<DragInitCmd>>", self._br_dnd_drag_init)

                self._br_status.set(
                    "Not connected — click Connect. "
                    "Drop local files onto the list to upload; "
                    "drag remote files out to download.")
                return
            except Exception:
                pass
        # Fallback: mouse-drag within app, release over Destination → download
        self._br_drag_data = {"dragging": False, "tip": None, "x0": 0, "y0": 0}
        self._br_file_tree.bind("<ButtonPress-1>",   self._br_drag_start)
        self._br_file_tree.bind("<B1-Motion>",       self._br_drag_motion)
        self._br_file_tree.bind("<ButtonRelease-1>", self._br_drag_release)

    def _br_dnd_drop_dest(self, event):
        """Drop local folder onto Destination entry → set as download target."""
        raw = event.data.strip()
        # tkinterdnd2 wraps paths with spaces in {braces}
        if raw.startswith("{"):
            path = raw.strip("{}").split("} {")[0]
        else:
            path = raw.split()[0]
        self._br_local_dir.set(path)
        self._br_status.set(f"Destination set: {path}")

    def _br_dnd_drop_upload(self, event):
        """Drop local files/folders onto the file list → upload to current remote dir."""
        if not self._br_ftp:
            messagebox.showerror("Not connected", "Connect first.", parent=self.root)
            return
        raw = event.data.strip()
        # Parse the tkinterdnd2 path list: paths with spaces are in {braces}
        local_paths = []
        remaining = raw
        while remaining:
            remaining = remaining.strip()
            if not remaining:
                break
            if remaining.startswith("{"):
                end = remaining.find("}")
                if end == -1:
                    local_paths.append(remaining.strip("{}"))
                    break
                local_paths.append(remaining[1:end])
                remaining = remaining[end+1:]
            else:
                parts = remaining.split(None, 1)
                local_paths.append(parts[0])
                remaining = parts[1] if len(parts) > 1 else ""

        if not local_paths:
            return

        for local_path in local_paths:
            local_path = local_path.strip()
            if not local_path or not os.path.exists(local_path):
                continue
            if os.path.isdir(local_path):
                # Upload entire directory recursively
                self._br_upload_dir(local_path)
            else:
                # Upload single file
                name        = os.path.basename(local_path)
                remote_path = self._br_cwd.rstrip("/") + "/" + name
                size        = os.path.getsize(local_path)
                self._br_start_transfer(
                    getattr(self, "_br_cfg", None) or core.load_config(),
                    "upload", remote_path, local_path, size)

    def _br_upload_dir(self, local_dir):
        """Recursively upload a local directory to the current remote path."""
        if not self._br_ftp:
            return
        cfg         = getattr(self, "_br_cfg", None) or core.load_config()
        dir_name    = os.path.basename(local_dir.rstrip(os.sep))
        remote_base = self._br_cwd.rstrip("/") + "/" + dir_name

        def _queue_tree(local_root, remote_root):
            for entry in os.scandir(local_root):
                remote_path = remote_root + "/" + entry.name
                if entry.is_dir(follow_symlinks=False):
                    _queue_tree(entry.path, remote_path)
                else:
                    self._br_start_transfer(
                        cfg, "upload", remote_path, entry.path,
                        entry.stat().st_size)

        _queue_tree(local_dir, remote_base)
        self._br_status.set(
            f"Queued upload of folder: {dir_name} → {remote_base}")

    def _br_dnd_drag_init(self, event):
        """Provide file list to DnD engine when drag starts."""
        files = self._br_selected_file_paths()
        if not files:
            return "break"
        return ("copy", "DND_Files", " ".join(f"{{{p}}}" for p in files))

    def _br_drag_start(self, event):
        self._br_drag_data.update(dragging=False, x0=event.x, y0=event.y)

    def _br_drag_motion(self, event):
        dx = abs(event.x - self._br_drag_data["x0"])
        dy = abs(event.y - self._br_drag_data["y0"])
        if dx > 8 or dy > 8:
            self._br_drag_data["dragging"] = True
            n = len(self._br_selected_file_paths())
            if n and not self._br_drag_data.get("tip"):
                tip = tk.Toplevel(self.root)
                tip.overrideredirect(True)
                tip.attributes("-topmost", True)
                tip.configure(bg=T("bg3"))
                tk.Label(tip, text=f"Release over Destination to queue {n} file(s)",
                         bg=T("bg3"), fg=T("fg"), font=("Segoe UI", 9),
                         padx=10, pady=5).pack()
                self._br_drag_data["tip"] = tip
            if self._br_drag_data.get("tip"):
                self._br_drag_data["tip"].geometry(
                    f"+{event.x_root+14}+{event.y_root+8}")

    def _br_drag_release(self, event):
        if self._br_drag_data.get("tip"):
            self._br_drag_data["tip"].destroy()
            self._br_drag_data["tip"] = None
        if self._br_drag_data["dragging"]:
            # Check if released over dest entry
            try:
                ex = self._dest_entry.winfo_rootx()
                ey = self._dest_entry.winfo_rooty()
                ew = self._dest_entry.winfo_width()
                eh = self._dest_entry.winfo_height()
                if ex <= event.x_root <= ex+ew and ey <= event.y_root <= ey+eh:
                    self._br_download_selected()
            except Exception:
                pass
        self._br_drag_data["dragging"] = False

    def _br_selected_file_paths(self):
        """Return list of full remote paths for selected non-dir entries."""
        paths = []
        for e in self._br_get_selected_entries():
            if not e["is_dir"]:
                paths.append(self._br_cwd.rstrip("/") + "/" + e["name"])
        return paths

    # ── Browser: connection ────────────────────────────────────────────────

    def _br_connect(self):
        if self._br_ftp:
            self._br_disconnect()
        cfg = core.load_config()
        if not cfg.get("host"):
            messagebox.showerror("No credentials",
                "Select a server profile above, or enter FTP details on the Dashboard tab.",
                parent=self.root)
            return
        self._br_status.set("Connecting…")
        self._br_busy = False   # always clear so navigate works after connect
        self.root.update_idletasks()

        # Cache the active config so _br_make_ftp() always has the right creds
        self._br_cfg = cfg

        def _do():
            try:
                ftp = core.ftp_connect(
                    cfg["host"], cfg.get("port", 21),
                    cfg.get("user", ""), core.get_password(cfg))
                # Get the actual landing directory — servers like Whatbox
                # may put you in /home/username, not /
                try:
                    start_path = ftp.pwd()
                except Exception:
                    start_path = "/"
                return ftp, start_path, None
            except Exception as e:
                return None, "/", str(e)

        def _finish(ftp, start_path, err):
            if err:
                self._br_status.set(f"Connection failed: {err}")
                messagebox.showerror("Connect failed", err, parent=self.root)
                return
            self._br_ftp = ftp
            label = cfg.get("active_server") or cfg.get("host", "server")
            self._br_status.set(f"Connected to {label}")
            # Populate dir tree seeded at start_path, then navigate there
            self._br_populate_dir_tree(start_path)
            self._br_navigate(start_path, push=False)

        threading.Thread(
            target=lambda: self.root.after(0, _finish, *_do()),
            daemon=True).start()

    def _br_connect_to_path(self, start_path="/"):
        """
        Connect to the browser and navigate directly to start_path.
        Used by the --session launcher to restore the saved working directory.
        """
        if self._br_ftp:
            self._br_disconnect()
        cfg = getattr(self, "_br_cfg", None) or core.load_config()
        if not cfg.get("host"):
            return
        self._br_busy = False
        self._br_status.set("Connecting (session)…")

        def _do():
            try:
                ftp = core.ftp_connect(
                    cfg["host"], cfg.get("port", 21),
                    cfg.get("user", ""), core.get_password(cfg))
                try:
                    # Verify start_path is accessible; fall back to pwd()
                    ftp.cwd(start_path)
                    actual_path = ftp.pwd()
                except Exception:
                    actual_path = ftp.pwd()
                return ftp, actual_path, None
            except Exception as e:
                return None, "/", str(e)

        def _finish(ftp, actual_path, err):
            if err:
                self._br_status.set(f"Session connect failed: {err}")
                return
            self._br_ftp = ftp
            self._br_cfg = cfg
            self._br_status.set(
                f"Session connected — {cfg.get('host','')} — {actual_path}")
            self._br_populate_dir_tree(actual_path)
            self._br_navigate(actual_path, push=False)

        import threading as _t
        _t.Thread(target=lambda: self.root.after(0, _finish, *_do()),
                  daemon=True).start()

    def _br_make_ftp(self):
        """Open a short-lived FTP connection using the cached browser config."""
        cfg = getattr(self, "_br_cfg", None) or core.load_config()
        return core.ftp_connect(
            cfg["host"], cfg.get("port", 21),
            cfg.get("user", ""), core.get_password(cfg))

    def _br_disconnect(self):
        if self._br_ftp:
            try: self._br_ftp.quit()
            except: pass
            self._br_ftp = None
        self._br_dir_tree.delete(*self._br_dir_tree.get_children())
        self._br_file_tree.delete(*self._br_file_tree.get_children())
        self._br_status.set("Disconnected.")

    # ── Browser: left tree ─────────────────────────────────────────────────

    def _br_populate_dir_tree(self, root_path):
        """Seed the left dir tree. Children loaded lazily via <<TreeviewOpen>>."""
        t = self._br_dir_tree
        t.delete(*t.get_children())
        iid = t.insert("", "end", text="/", open=False,
                       tags=("dir",), values=("/",))
        # Add placeholder so the expand arrow appears
        t.insert(iid, "end", text="...", tags=("loading",))
        # Pre-load root in background so user sees dirs immediately
        threading.Thread(target=self._br_load_tree_node,
                         args=(iid, "/"), daemon=True).start()

    def _br_tree_expand(self, event):
        t = self._br_dir_tree
        # <<TreeviewOpen>> iid comes from the event widget item, not focus
        node = t.focus()
        if not node:
            return
        kids = t.get_children(node)
        if len(kids) == 1 and t.item(kids[0], "text") == "...":
            t.delete(kids[0])
            path = (t.item(node, "values") or ("",))[0] or "/"
            threading.Thread(target=self._br_load_tree_node,
                             args=(node, path), daemon=True).start()

    def _br_load_tree_node(self, node, path):
        """Load subdirs for a tree node — uses its own FTP connection to avoid races."""
        def _log(m):
            self.root.after(0, lambda msg=m: self._log(msg))
        try:
            ftp     = self._br_make_ftp()
            entries = core.ftp_list_dir_full(ftp, path, log_fn=_log)
            try: ftp.quit()
            except: pass
        except Exception as e:
            _log(f"[Browser tree] {path}: {e}")
            return
        def _ui():
            # Remove any stale "..." placeholder that may still be there
            for kid in self._br_dir_tree.get_children(node):
                if self._br_dir_tree.item(kid, "text") == "...":
                    self._br_dir_tree.delete(kid)
            for e in entries:
                if not e["is_dir"]: continue
                full  = path.rstrip("/") + "/" + e["name"]
                child = self._br_dir_tree.insert(
                    node, "end", text=e["name"], tags=("dir",), values=(full,))
                self._br_dir_tree.insert(child, "end", text="...", tags=("loading",))
        self.root.after(0, _ui)

    def _br_tree_select(self, event):
        node = self._br_dir_tree.focus()
        path = (self._br_dir_tree.item(node, "values") or ("",))[0]
        if path:
            self._br_navigate(path)

    # ── Browser: navigation ────────────────────────────────────────────────

    def _br_navigate(self, path, push=True):
        path = (path or "/").strip()
        if not path.startswith("/"): path = "/" + path
        if self._br_busy or not self._br_ftp:
            if not self._br_ftp:
                self._br_status.set("Not connected — click Connect.")
            return
        if push and path != self._br_cwd:
            self._br_history.append(self._br_cwd)
            self._br_fwd.clear()
        self._br_cwd = path
        self._br_path_var.set(path)
        self._br_busy = True
        self._br_status.set(f"Loading {path}...")
        self._br_file_tree.delete(*self._br_file_tree.get_children())
        threading.Thread(target=self._br_load_dir, args=(path,), daemon=True).start()

    def _br_load_dir(self, path):
        """
        List remote directory using the main _br_ftp connection.
        Always clears _br_busy when done, no matter what happens.
        """
        log_msgs = []
        def _log(m):
            log_msgs.append(m)
            self.root.after(0, lambda msg=m: self._log(msg))

        def _attempt(ftp_conn):
            return core.ftp_list_dir_full(ftp_conn, path, log_fn=_log)

        entries = None
        err     = None
        try:
            entries = _attempt(self._br_ftp)
            # If result is empty and MLSD was attempted, the connection may be
            # poisoned. Reconnect and retry once on a clean connection.
            if not entries and any("[LIST]  MLSD" in m for m in log_msgs):
                _log("[Browser] Empty result after MLSD — reconnecting for clean LIST")
                self._br_ftp = self._br_make_ftp()
                entries = _attempt(self._br_ftp)
        except Exception as first_err:
            _log(f"[Browser] Listing failed ({first_err!r}), reconnecting…")
            try:
                self._br_ftp = self._br_make_ftp()
                entries = _attempt(self._br_ftp)
            except Exception as e:
                err = str(e)
                _log(f"[Browser] Retry also failed: {err}")

        # Always dispatch back to the main thread; _br_busy is always cleared here.
        if entries is not None:
            self.root.after(0, self._br_populate_files, entries)
        else:
            final_err = err or "Unknown listing error"
            self.root.after(0, lambda: (
                self._br_status.set(f"Error listing {path}: {final_err}"),
                setattr(self, "_br_busy", False)))

    def _br_populate_files(self, entries):
        self._br_entries = entries
        self._br_iid_map = {}
        self._br_busy    = False
        ft = self._br_file_tree
        ft.delete(*ft.get_children())

        if self._br_cwd != "/":
            ft.insert("", "end", iid="__up__",
                      values=("  ↑ ..", "", ""), tags=("dir",))

        for idx, e in enumerate(entries):
            prefix = "  📁 " if e["is_dir"] else "  📄 "
            tag    = "dir"   if e["is_dir"] else "file"
            sz     = ""      if e["is_dir"] else _fmt_size(e["size"])
            mod    = _fmt_modify(e.get("modify", ""))
            iid    = f"__entry_{idx}__"
            ft.insert("", "end", iid=iid,
                      values=(prefix + e["name"], sz, mod), tags=(tag,))
            self._br_iid_map[iid] = e

        n_dirs  = sum(1 for e in entries if e["is_dir"])
        n_files = sum(1 for e in entries if not e["is_dir"])
        self._br_status.set(
            f"{self._br_cwd}   |   {n_dirs} folder(s),  {n_files} file(s)")

    def _br_go_back(self):
        if self._br_history:
            self._br_fwd.append(self._br_cwd)
            self._br_navigate(self._br_history.pop(), push=False)

    def _br_go_fwd(self):
        if self._br_fwd:
            self._br_history.append(self._br_cwd)
            self._br_navigate(self._br_fwd.pop(), push=False)

    def _br_go_up(self):
        if self._br_cwd == "/": return
        parent = "/".join(self._br_cwd.rstrip("/").split("/")[:-1]) or "/"
        self._br_navigate(parent)

    def _br_file_dbl(self, event=None):
        sel = self._br_file_tree.selection()
        if not sel: return
        iid = sel[0]
        if iid == "__up__":
            self._br_go_up(); return
        entry = self._br_iid_map.get(iid)
        if not entry: return
        if entry["is_dir"]:
            self._br_navigate(self._br_cwd.rstrip("/") + "/" + entry["name"])
        else:
            self._br_queue_files([entry])

    def _br_get_selected_entries(self):
        """Return list of entry dicts for currently selected rows (excluding __up__)."""
        result = []
        for iid in self._br_file_tree.selection():
            if iid == "__up__": continue
            e = self._br_iid_map.get(iid)
            if e:
                result.append(e)
        return result

    def _br_select_all(self):
        for iid in self._br_file_tree.get_children():
            if iid != "__up__":
                self._br_file_tree.selection_add(iid)

    def _br_sort(self, col):
        self._br_sort_rev = (not self._br_sort_rev
                             if self._br_sort_col == col else False)
        self._br_sort_col = col
        key = {"name":     lambda e: e["name"].lower(),
               "size":     lambda e: e["size"],
               "modified": lambda e: e.get("modify", "")}.get(col, lambda e: "")
        self._br_entries.sort(key=key, reverse=self._br_sort_rev)
        self._br_populate_files(self._br_entries)

    # ── Browser: local folder ──────────────────────────────────────────────

    def _br_browse_local(self):
        path = filedialog.askdirectory(parent=self.root, title="Choose download folder")
        if path:
            self._br_local_dir.set(path)

    # ── Browser: download ──────────────────────────────────────────────────

    def _br_download_selected(self):
        entries = self._br_get_selected_entries()
        if not entries:
            messagebox.showinfo("Nothing selected",
                "Select files or folders to download.", parent=self.root)
            return
        local_dir = self._br_local_dir.get().strip()
        if not local_dir:
            messagebox.showerror("No destination",
                "Set a local folder.", parent=self.root)
            return
        if not os.path.isdir(local_dir):
            try: os.makedirs(local_dir, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Bad destination", str(e), parent=self.root)
                return

        files   = [e for e in entries if not e["is_dir"]]
        folders = [e for e in entries if e["is_dir"]]

        # Queue individual files directly
        if files:
            self._br_queue_files(files, local_dir)

        # Expand folders recursively then queue each file inside
        if folders:
            self._br_download_folders(folders, local_dir)

    def _br_download_folders(self, folder_entries, local_dir):
        """Recursively list each folder and queue all files inside it."""
        if not self._br_ftp:
            messagebox.showerror("Not connected", "Connect first.", parent=self.root)
            return
        cfg = core.load_config()

        # Show a status update while we walk
        self._br_status.set("Scanning folder structure…")

        def _expand_and_queue():
            all_files = []   # list of (remote_path, local_path, size)
            errors    = []

            # We need a fresh FTP connection for the recursive walk so we don't
            # block the UI connection
            try:
                walk_ftp = core.ftp_connect(
                    cfg["host"], cfg.get("port", 21),
                    cfg.get("user", ""), core.get_password(cfg))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "Connect failed", str(e), parent=self.root))
                return

            for folder_entry in folder_entries:
                remote_folder = self._br_cwd.rstrip("/") + "/" + folder_entry["name"]
                local_folder  = os.path.join(local_dir, folder_entry["name"])
                self.root.after(0, lambda n=folder_entry["name"]:
                    self._br_status.set(f"Scanning {n}…"))
                try:
                    file_map = core.ftp_list_recursive(walk_ftp, remote_folder)
                    # file_map: {relative_path: (size, modify)}
                    for rel_path, (size, _modify) in file_map.items():
                        remote_file = remote_folder.rstrip("/") + "/" + rel_path
                        local_file  = os.path.join(
                            local_folder,
                            rel_path.replace("/", os.sep))
                        all_files.append((remote_file, local_file, size))
                except Exception as e:
                    errors.append(f"{folder_entry['name']}: {e}")

            try: walk_ftp.quit()
            except: pass

            def _queue_all():
                if errors:
                    messagebox.showwarning(
                        "Scan errors",
                        "Some folders could not be scanned:\n" + "\n".join(errors),
                        parent=self.root)
                if not all_files:
                    self._br_status.set("No files found in selected folders.")
                    return
                self._br_status.set(
                    f"Queuing {len(all_files)} file(s) from folder(s)…")
                for remote_path, local_path, size in all_files:
                    self._br_start_transfer(cfg, "download",
                                            remote_path, local_path, size)
                self._br_status.set(
                    f"{self._br_cwd}  —  {len(all_files)} file(s) queued")

            self.root.after(0, _queue_all)

        threading.Thread(target=_expand_and_queue, daemon=True).start()

    def _br_queue_files(self, entries, local_dir=None, remote_base=None):
        """Queue entries for download. remote_base defaults to self._br_cwd."""
        if local_dir is None:
            local_dir = self._br_local_dir.get().strip()
        if not local_dir:
            messagebox.showerror("No destination",
                "Set a local folder first.", parent=self.root)
            return
        if remote_base is None:
            remote_base = self._br_cwd
        cfg = core.load_config()
        for e in entries:
            remote = remote_base.rstrip("/") + "/" + e["name"]
            local  = os.path.join(local_dir, e["name"])
            self._br_start_transfer(cfg, "download", remote, local, e["size"])

    def _br_start_transfer(self, cfg, direction, remote_path, local_path, size=0,
                            on_done=None):
        """
        direction: "download" (remote→local) or "upload" (local→remote).
        Spawns a daemon thread, shows a queue row with progress bar.
        on_done(ok): optional callback fired on the main thread when complete.
        """
        import threading as _th
        stop_ev = _th.Event()
        key     = id(stop_ev)

        # ── Queue row ───────────────────────────────────────────────────────
        row = tk.Frame(self._br_q_inner, bg=T("bg2"), pady=3)
        row.pack(fill="x", padx=4, pady=2)

        dir_indicator = "↓" if direction == "download" else "↑"
        name = os.path.basename(local_path if direction == "upload" else remote_path)
        cjk  = _cjk_font(9)

        tk.Label(row, text=dir_indicator, bg=T("bg2"),
                 fg=T("green_fg") if direction == "download" else T("accent"),
                 font=("Segoe UI", 9, "bold"), width=2).pack(side="left", padx=(6, 0))
        tk.Label(row, text=name, bg=T("bg2"), fg=T("fg"),
                 font=cjk, anchor="w", width=28).pack(side="left", padx=(2, 4))

        pbar_bg = tk.Frame(row, bg=T("bg4"), height=8, width=160)
        pbar_bg.pack(side="left", padx=4)
        pbar_bg.pack_propagate(False)
        pbar = tk.Frame(pbar_bg,
                        bg=T("green") if direction == "download" else T("accent"),
                        height=8, width=0)
        pbar.place(x=0, y=0, height=8)

        pct_lbl = tk.Label(row, text="0%",  bg=T("bg2"), fg=T("fg2"),
                           font=("Segoe UI", 8), width=5)
        pct_lbl.pack(side="left", padx=2)

        st_lbl = tk.Label(row, text="Queued", bg=T("bg2"), fg=T("fg3"),
                          font=("Segoe UI", 8), width=10, anchor="w")
        st_lbl.pack(side="left", padx=4)

        tk.Button(row, text="✕", bg=T("bg4"), fg=T("red_fg"),
                  relief="flat", font=("Segoe UI", 8), width=2,
                  command=stop_ev.set).pack(side="right", padx=6)

        self._br_dl_active[key] = {
            "row": row, "pbar": pbar, "pbar_bg": pbar_bg,
            "pct": pct_lbl, "st": st_lbl, "done": False,
        }
        self._br_q_canvas.after(80, lambda: self._br_q_canvas.yview_moveto(1.0))

        def _upd(pct, sz_str, status, done, ok):
            try:
                info = self._br_dl_active[key]
                bw   = info["pbar_bg"].winfo_width() or 160
                info["pbar"].configure(width=max(1, int(bw * pct / 100)))
                info["pct"].configure(text=f"{pct}%")
                info["st"].configure(
                    text=status,
                    fg=T("green_fg") if (done and ok) else
                       T("red_fg")   if (done and not ok) else T("fg2"))
                if done:
                    info["done"] = True
            except Exception:
                pass

        def _run():
            def _st(t): self.root.after(0, lambda: st_lbl.configure(text=t))
            _st("Connecting…")
            try:
                ftp = core.ftp_connect(cfg["host"], cfg.get("port", 21),
                                       cfg.get("user", ""), core.get_password(cfg))
            except Exception as e:
                self.root.after(0, lambda: _upd(0, "", "ERR:connect", True, False))
                return

            if direction == "download":
                _st("Downloading…")
                done_b = [0]
                nonlocal_size = [size]
                # Write to .part file; rename to final name only on success.
                # This means a cancel or crash NEVER leaves a silent partial file.
                part_path = local_path + ".part"
                try:
                    if os.path.exists(part_path):  os.remove(part_path)
                    if os.path.exists(local_path): os.remove(local_path)
                    os.makedirs(os.path.dirname(os.path.abspath(local_path)),
                                exist_ok=True)
                    with open(part_path, "wb") as fout:
                        def _dl_cb(chunk):
                            if stop_ev.is_set(): raise Exception("Cancelled")
                            fout.write(chunk)
                            done_b[0] += len(chunk)
                            if nonlocal_size[0] == 0:
                                nonlocal_size[0] = done_b[0]
                            pct = int(done_b[0] / nonlocal_size[0] * 100) if nonlocal_size[0] else 0
                            sz  = _fmt_size(done_b[0])
                            self.root.after(0,
                                lambda p=pct, s=sz: _upd(p, s, s, False, True))
                        ftp.retrbinary(f"RETR {remote_path}", _dl_cb, blocksize=65536)
                    # Only rename to final name after a complete, successful transfer
                    os.rename(part_path, local_path)
                    try: ftp.quit()
                    except: pass
                    self.root.after(0, lambda: _upd(100, "", "Done ✓", True, True))
                    self._on_file_downloaded(name)
                    if on_done: self.root.after(0, lambda: on_done(True))
                except Exception as e:
                    try: ftp.quit()
                    except: pass
                    # Always clean up the .part file — never leave partial data behind
                    try:
                        if os.path.exists(part_path): os.remove(part_path)
                    except: pass
                    err = ("Cancelled" if stop_ev.is_set() else str(e)[:18])
                    self.root.after(0, lambda: _upd(0, "", err, True, False))
                    if on_done: self.root.after(0, lambda: on_done(False))

            else:  # upload
                _st("Uploading…")
                try:
                    up_size = os.path.getsize(local_path)
                except Exception:
                    up_size = 0
                done_b = [0]
                upload_ok = [False]
                try:
                    def _prog(d, t):
                        if stop_ev.is_set(): raise Exception("Cancelled")
                        pct = int(d / t * 100) if t else 0
                        sz  = _fmt_size(d)
                        self.root.after(0,
                            lambda p=pct, s=sz: _upd(p, s, s, False, True))
                    core.ftp_upload_file(ftp, local_path, remote_path,
                                         progress_cb=_prog,
                                         stop_event=stop_ev)
                    upload_ok[0] = True
                    try: ftp.quit()
                    except: pass
                    self.root.after(0, lambda: _upd(100, "", "Done ✓", True, True))
                    self.root.after(200, lambda: self._br_navigate(self._br_cwd, push=False))
                    if on_done: self.root.after(0, lambda: on_done(True))
                except Exception as e:
                    # Delete the incomplete remote file — mirrors sync engine behaviour
                    if not upload_ok[0]:
                        try: core.ftp_delete_remote(ftp, remote_path, is_dir=False)
                        except: pass
                    try: ftp.quit()
                    except: pass
                    err = ("Cancelled" if stop_ev.is_set() else str(e)[:18])
                    self.root.after(0, lambda: _upd(0, "", err, True, False))
                    if on_done: self.root.after(0, lambda: on_done(False))

        _th.Thread(target=_run, daemon=True).start()

    # Compatibility alias
    def _br_start_single_dl(self, cfg, remote_path, local_path, size=0):
        self._br_start_transfer(cfg, "download", remote_path, local_path, size)

    # ── Browser: upload ───────────────────────────────────────────────────

    def _br_upload_files(self):
        """Upload local files to the current remote directory."""
        if not self._br_ftp:
            messagebox.showerror("Not connected", "Connect first.", parent=self.root)
            return
        paths = filedialog.askopenfilenames(parent=self.root,
                                            title="Choose files to upload")
        if not paths:
            return
        cfg = core.load_config()
        for local_path in paths:
            name        = os.path.basename(local_path)
            remote_path = self._br_cwd.rstrip("/") + "/" + name
            size        = os.path.getsize(local_path)
            self._br_start_transfer(cfg, "upload", remote_path, local_path, size)

    # ── Browser: move / rename ─────────────────────────────────────────────

    def _br_move_selected(self):
        """Rename/move selected remote file or folder on the server."""
        entries = self._br_get_selected_entries()
        if not entries:
            messagebox.showinfo("Nothing selected",
                "Select a file or folder to rename.", parent=self.root)
            return
        if len(entries) > 1:
            messagebox.showinfo("One at a time",
                "Select a single item to rename/move.", parent=self.root)
            return
        if not self._br_ftp:
            messagebox.showerror("Not connected", "Connect first.", parent=self.root)
            return
        e        = entries[0]
        old_name = e["name"]
        old_path = self._br_cwd.rstrip("/") + "/" + old_name

        new_name = simpledialog.askstring(
            "Rename / Move",
            f"New name or full remote path for:\n{old_name}",
            initialvalue=old_name,
            parent=self.root,
        )
        if not new_name or new_name == old_name:
            return

        # If user typed just a name, keep in current dir; if full path use it
        if "/" not in new_name:
            new_path = self._br_cwd.rstrip("/") + "/" + new_name
        else:
            new_path = new_name if new_name.startswith("/") else "/" + new_name

        def _do():
            try:
                core.ftp_rename_remote(self._br_ftp, old_path, new_path)
                self.root.after(0, lambda: (
                    self._br_status.set(f"Renamed: {old_name} → {new_name}"),
                    self._br_navigate(self._br_cwd, push=False),
                ))
            except Exception as ex:
                self.root.after(0, lambda: messagebox.showerror(
                    "Rename failed", str(ex), parent=self.root))

        threading.Thread(target=_do, daemon=True).start()

    # ── Browser: delete ────────────────────────────────────────────────────

    def _br_delete_selected(self):
        """Delete selected remote files/folders after confirmation."""
        entries = self._br_get_selected_entries()
        if not entries:
            messagebox.showinfo("Nothing selected",
                "Select items to delete.", parent=self.root)
            return
        if not self._br_ftp:
            messagebox.showerror("Not connected", "Connect first.", parent=self.root)
            return
        names = "\n".join(e["name"] for e in entries[:8])
        if len(entries) > 8:
            names += f"\n… and {len(entries)-8} more"
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Permanently delete from server:\n{names}\n\nThis cannot be undone.",
            parent=self.root,
        ):
            return

        def _do():
            errors = []
            for e in entries:
                path = self._br_cwd.rstrip("/") + "/" + e["name"]
                try:
                    core.ftp_delete_remote(self._br_ftp, path, e["is_dir"])
                except Exception as ex:
                    errors.append(f"{e['name']}: {ex}")
            def _ui():
                if errors:
                    messagebox.showerror("Delete errors",
                        "\n".join(errors), parent=self.root)
                self._br_navigate(self._br_cwd, push=False)
            self.root.after(0, _ui)

        threading.Thread(target=_do, daemon=True).start()

    # ── Browser: new folder ────────────────────────────────────────────────

    def _br_new_folder(self):
        if not self._br_ftp:
            messagebox.showerror("Not connected", "Connect first.", parent=self.root)
            return
        name = simpledialog.askstring("New Folder", "Folder name:",
                                      parent=self.root)
        if not name or not name.strip():
            return

        folder_name = name.strip()
        new_path    = self._br_cwd.rstrip("/") + "/" + folder_name
        cwd_at_mkd  = self._br_cwd

        def _do():
            # Create the folder using its own connection — never touches
            # self._br_ftp so there's zero race with any in-flight listing.
            err = None
            try:
                ftp = self._br_make_ftp()
                try:
                    ftp.mkd(new_path)
                finally:
                    try: ftp.quit()
                    except: pass
            except Exception as ex:
                err = str(ex)

            if err:
                self.root.after(0, lambda e=err: messagebox.showerror(
                    "New folder failed", e, parent=self.root))
                return

            # mkd succeeded — update the UI on the main thread.
            # Strategy: merge the new folder into whatever _br_entries currently
            # holds (it may have been refreshed by a concurrent listing), then
            # re-render and highlight the new row.
            # We do NOT skip if the entry is "already there" — a concurrent
            # listing could have raced us and already added it, but we still
            # need to highlight it so the user sees something happened.
            def _inject():
                if self._br_cwd != cwd_at_mkd:
                    return  # user navigated away — nothing to do

                new_entry = {"name": folder_name, "is_dir": True,
                             "size": 0, "modify": ""}

                # Build a deduplicated entry list that always contains the new folder
                seen  = set()
                merged = []
                for e in self._br_entries + [new_entry]:
                    key = (e["name"], e["is_dir"])
                    if key not in seen:
                        seen.add(key)
                        merged.append(e)

                merged.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
                self._br_populate_files(merged)

                # Highlight the new folder so the user can see it
                for iid, e in self._br_iid_map.items():
                    if e.get("name") == folder_name and e.get("is_dir"):
                        self._br_file_tree.selection_set(iid)
                        self._br_file_tree.see(iid)
                        break

            self.root.after(0, _inject)

        threading.Thread(target=_do, daemon=True).start()

    # ── Browser: right-click context menu ─────────────────────────────────

    def _br_right_click(self, event):
        # Select the row under cursor first
        iid = self._br_file_tree.identify_row(event.y)
        if iid and iid not in self._br_file_tree.selection():
            self._br_file_tree.selection_set(iid)

        entries  = self._br_get_selected_entries()
        n        = len(entries)
        files    = [e for e in entries if not e["is_dir"]]
        n_files  = len(files)

        menu = tk.Menu(self.root, tearoff=0,
                       bg=T("bg3"), fg=T("fg"),
                       activebackground=T("accent"), activeforeground="white",
                       relief="flat", bd=1)

        local_dir = self._br_local_dir.get().strip()

        # Download
        n_dirs_sel = sum(1 for e in entries if e["is_dir"])
        dl_parts   = []
        if n_files:     dl_parts.append(f"{n_files} file{'s' if n_files!=1 else ''}")
        if n_dirs_sel:  dl_parts.append(f"{n_dirs_sel} folder{'s' if n_dirs_sel!=1 else ''}")
        dl_label = ("↓ Download  (" + ", ".join(dl_parts) + ")") if dl_parts else "↓ Download"
        menu.add_command(label=dl_label,
                         state="normal" if (entries and local_dir) else "disabled",
                         command=self._br_download_selected)

        # Download & move to local (download then delete remote)
        menu.add_command(label="↓ Download & Remove from server",
                         state="normal" if (entries and local_dir) else "disabled",
                         command=lambda: self._br_download_and_delete(entries))

        menu.add_separator()

        # Upload
        menu.add_command(label="↑ Upload files here…",
                         command=self._br_upload_files)

        menu.add_separator()

        # Rename / Move
        menu.add_command(label="✎ Rename / Move on server",
                         state="normal" if n == 1 else "disabled",
                         command=self._br_move_selected)

        menu.add_separator()

        # Delete
        menu.add_command(label=f"✕ Delete from server ({n} item{'s' if n!=1 else ''})",
                         state="normal" if n else "disabled",
                         foreground=T("red_fg"),
                         command=self._br_delete_selected)

        if n == 1 and not entries[0]["is_dir"]:
            menu.add_separator()
            menu.add_command(label="📋 Copy remote path",
                             command=lambda: (
                                 self.root.clipboard_clear(),
                                 self.root.clipboard_append(
                                     self._br_cwd.rstrip("/") + "/" + entries[0]["name"])
                             ))

        menu.tk_popup(event.x_root, event.y_root)

    def _br_download_and_delete(self, entries):
        """Download files/folders then delete them from the server on success."""
        local_dir = self._br_local_dir.get().strip()
        if not local_dir:
            messagebox.showerror("No local folder",
                "Set a local folder first.", parent=self.root)
            return
        cfg         = core.load_config()
        remote_base = self._br_cwd

        files   = [e for e in entries if not e["is_dir"]]
        folders = [e for e in entries if e["is_dir"]]

        def _delete_remote(remote_path, is_dir):
            def _do():
                try:
                    ftp2 = core.ftp_connect(
                        cfg["host"], cfg.get("port", 21),
                        cfg.get("user", ""), core.get_password(cfg))
                    core.ftp_delete_remote(ftp2, remote_path, is_dir)
                    ftp2.quit()
                    self.root.after(0, lambda:
                        self._br_navigate(self._br_cwd, push=False))
                except Exception as ex:
                    self.root.after(0, lambda: messagebox.showerror(
                        "Delete failed", str(ex), parent=self.root))
            threading.Thread(target=_do, daemon=True).start()

        # Queue individual files with on_done delete callback
        for e in files:
            remote = remote_base.rstrip("/") + "/" + e["name"]
            local  = os.path.join(local_dir, e["name"])
            def _after(ok, _r=remote, _e=e):
                if ok:
                    _delete_remote(_r, False)
            self._br_start_transfer(cfg, "download", remote, local,
                                    e["size"], on_done=_after)

        # For folders: expand recursively, queue all files, delete folder after all done
        if folders:
            def _expand_and_dl():
                try:
                    walk_ftp = core.ftp_connect(
                        cfg["host"], cfg.get("port", 21),
                        cfg.get("user", ""), core.get_password(cfg))
                except Exception as e:
                    self.root.after(0, lambda: messagebox.showerror(
                        "Connect failed", str(e), parent=self.root))
                    return

                for folder_entry in folders:
                    remote_folder = remote_base.rstrip("/") + "/" + folder_entry["name"]
                    local_folder  = os.path.join(local_dir, folder_entry["name"])
                    try:
                        file_map = core.ftp_list_recursive(walk_ftp, remote_folder)
                    except Exception:
                        file_map = {}

                    all_items = list(file_map.items())
                    total     = len(all_items)
                    done_ok   = [0]

                    def _folder_done(ok, _total=total, _rf=remote_folder,
                                     _fe=folder_entry, _done=done_ok):
                        if ok:
                            _done[0] += 1
                            if _done[0] >= _total:
                                _delete_remote(_rf, True)

                    for rel_path, (size, _) in all_items:
                        remote_file = remote_folder.rstrip("/") + "/" + rel_path
                        local_file  = os.path.join(
                            local_folder, rel_path.replace("/", os.sep))
                        self.root.after(0,
                            lambda r=remote_file, l=local_file, s=size:
                                self._br_start_transfer(cfg, "download", r, l, s,
                                                        on_done=_folder_done))

                try: walk_ftp.quit()
                except: pass

            threading.Thread(target=_expand_and_dl, daemon=True).start()

    def _br_clear_done(self):
        for key in [k for k, v in self._br_dl_active.items() if v.get("done")]:
            try: self._br_dl_active[key]["row"].destroy()
            except: pass
            self._br_dl_active.pop(key, None)

    # Settings tab
    # -----------------------------------------------------------------------

    def _build_settings(self):
        outer = self.tab_settings

        # ── Scrollable canvas wrapper ──────────────────────────────────────
        canvas = tk.Canvas(outer, bg=T("bg"), highlightthickness=0)
        vsb    = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        t = tk.Frame(canvas, bg=T("bg"))
        win_id = canvas.create_window((0, 0), window=t, anchor="nw")

        def _on_frame_resize(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_resize(event):
            canvas.itemconfig(win_id, width=event.width)
        t.bind("<Configure>", _on_frame_resize)
        canvas.bind("<Configure>", _on_canvas_resize)

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # ── Theme picker ───────────────────────────────────────────────────
        ttk.Label(t, text="Settings", font=("Segoe UI", 12, "bold"),
                  foreground=T("accent")).pack(anchor="w", padx=12, pady=10)

        th_frame = ttk.Frame(t); th_frame.pack(anchor="w", padx=12, pady=(0, 6))
        ttk.Label(th_frame, text="Theme:").pack(side="left")
        theme_combo = ttk.Combobox(th_frame, textvariable=self._theme_var,
                                   values=list(THEMES.keys()),
                                   state="readonly", width=20)
        theme_combo.pack(side="left", padx=8)
        ttk.Label(th_frame, text="(restart to fully apply)",
                  foreground=T("fg3"), font=("Segoe UI", 8)).pack(side="left")
        theme_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_theme_live())

        r1 = ttk.Frame(t); r1.pack(anchor="w", padx=12, pady=4)
        ttk.Label(r1, text="Check interval (minutes):").pack(side="left")
        ttk.Spinbox(r1, from_=1, to=1440, textvariable=self.interval_var,
                    width=8).pack(side="left", padx=8)

        r2 = ttk.Frame(t); r2.pack(anchor="w", padx=12, pady=4)
        ttk.Label(r2, text="Parallel downloads (1-10):").pack(side="left")
        ttk.Spinbox(r2, from_=1, to=10, textvariable=self.parallel_var,
                    width=8).pack(side="left", padx=8)
        ttk.Label(r2, text="  (each download uses its own FTP connection)",
                  foreground=T("fg2"), font=("Segoe UI", 9)).pack(side="left")

        # ── Notification settings ─────────────────────────────────────────────
        ttk.Separator(t, orient="horizontal").pack(fill="x", padx=12, pady=(10, 6))
        ttk.Label(t, text="Tray Notifications",
                  font=("Segoe UI", 10, "bold"), foreground=T("accent")).pack(anchor="w", padx=12, pady=(0, 6))

        nf = ttk.Frame(t)
        nf.pack(anchor="w", padx=12, pady=2)

        modes = [
            ("every",  "Every file    — notify when each download completes"),
            ("batch",  "Every N files — notify once after every N downloads"),
            ("cycle",  "End of cycle  — one summary when a sync run finishes"),
            ("off",    "Off           — no notifications"),
        ]
        for val, label in modes:
            rb = tk.Radiobutton(nf, text=label, variable=self._notif_mode,
                                value=val, command=self._notif_mode_changed,
                                bg=T("bg"), fg=T("fg"), selectcolor=T("bg3"),
                                activebackground=T("bg"), activeforeground=T("accent"),
                                font=("Segoe UI", 9), relief="flat", bd=0)
            rb.pack(anchor="w", pady=1)

        # Batch size row — only visible when "batch" is selected
        self._notif_batch_frame = ttk.Frame(t)
        self._notif_batch_frame.pack(anchor="w", padx=32, pady=(2, 4))
        ttk.Label(self._notif_batch_frame, text="Notify every").pack(side="left")
        ttk.Spinbox(self._notif_batch_frame, from_=2, to=100,
                    textvariable=self._notif_batch_var, width=6).pack(side="left", padx=6)
        ttk.Label(self._notif_batch_frame, text="files",
                  foreground=T("fg2")).pack(side="left")
        self._notif_mode_changed()   # set initial visibility

        ttk.Button(t, text="Save Settings", command=self.save_settings).pack(anchor="w", padx=12, pady=8)

        ttk.Separator(t, orient="horizontal").pack(fill="x", padx=12, pady=10)
        ttk.Label(t, text="Backup & Migration",
                  font=("Segoe UI", 10, "bold"), foreground=T("accent")).pack(anchor="w", padx=12, pady=(0, 6))
        ttk.Label(t, text=(
            "Export your settings and history to move to a new machine or back up your configuration.\n"
            "Passwords are stripped from exported settings - re-enter them after importing."
        ), foreground=T("fg2"), font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0, 8))

        bf = ttk.Frame(t)
        bf.pack(anchor="w", padx=12, pady=2)
        ttk.Button(bf, text="Export Settings",      command=self._export_settings).pack(side="left", padx=4)
        ttk.Button(bf, text="Import Settings",      command=self._import_settings).pack(side="left", padx=4)

        bf2 = ttk.Frame(t)
        bf2.pack(anchor="w", padx=12, pady=(6, 2))
        ttk.Button(bf2, text="Export History CSV",  command=self._export_history).pack(side="left", padx=4)
        ttk.Button(bf2, text="Import History CSV",  command=self._import_history).pack(side="left", padx=4)
        ttk.Label(bf2, text="  (same as buttons on the History tab)",
                  foreground=T("fg3"), font=("Segoe UI", 8)).pack(side="left")

        ttk.Separator(t, orient="horizontal").pack(fill="x", padx=12, pady=10)

        # ── Updates section ───────────────────────────────────────────────────
        ttk.Label(t, text="Updates",
                  font=("Segoe UI", 10, "bold"), foreground=T("accent")).pack(anchor="w", padx=12, pady=(0, 4))
        ttk.Label(t, text=(
            "Drop in new .py files to update the program without rebuilding the EXE.\n"
            "Files are validated for syntax errors before installing. Restart to apply."
        ), foreground=T("fg2"), font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0, 8))

        uf = ttk.Frame(t)
        uf.pack(anchor="w", padx=12, pady=(0, 6))
        ttk.Button(uf, text="Install Update (.py)", command=self._install_update).pack(side="left", padx=4)
        ttk.Button(uf, text="View Installed Updates", command=self._show_update_status).pack(side="left", padx=4)

        # Active overrides notice
        overrides = core.get_active_overrides()
        if overrides:
            notice = f"Active overrides: {', '.join(overrides)}"
            color  = T("yellow")
        else:
            notice = "No overrides active - running baked-in version."
            color  = T("fg3")
        ttk.Label(t, text=notice, foreground=color,
                  font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 4))

        ttk.Separator(t, orient="horizontal").pack(fill="x", padx=12, pady=10)
        ttk.Label(t, text=f"Settings file: {core.CONFIG_FILE}",
                  foreground=T("fg3"), font=("Segoe UI", 8)).pack(anchor="w", padx=12, pady=(16, 2))
        ttk.Label(t, text=f"History file:  {core.DB_FILE}",
                  foreground=T("fg3"), font=("Segoe UI", 8)).pack(anchor="w", padx=12)

        ttk.Separator(t, orient="horizontal").pack(fill="x", padx=12, pady=16)
        tk.Label(t, text="Vibe Coded by Itsuko",
                 bg=T("bg"), fg=T("accent"),
                 font=("Segoe UI", 11, "bold italic")).pack(anchor="center", pady=(0, 2))
        tk.Label(t, text="Built with Python, Tkinter & Flask",
                 bg=T("bg"), fg=T("fg3"),
                 font=("Segoe UI", 8)).pack(anchor="center", pady=(0, 4))
        link = tk.Label(t, text="DM bugs @ twitter.com/Itsukos",
                        bg=T("bg"), fg=T("accent2"),
                        font=("Segoe UI", 8, "underline"), cursor="hand2")
        link.pack(anchor="center", pady=(0, 2))
        link.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://twitter.com/Itsukos"))
        tk.Label(t, text=f"v{core.VERSION}",
                 bg=T("bg"), fg=T("bg4"),
                 font=("Segoe UI", 8)).pack(anchor="center", pady=(2, 0))

    # -----------------------------------------------------------------------
    # Update / sideload handlers
    # -----------------------------------------------------------------------

    def _install_update(self):
        paths = filedialog.askopenfilenames(
            parent=self.root,
            title="Select .py update file(s) to install",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
        )
        if not paths:
            return

        results = []
        for path in paths:
            ok, msg = core.install_update(path)
            results.append((ok, os.path.basename(path), msg))

        # Build summary message
        succeeded = [r for r in results if r[0]]
        failed    = [r for r in results if not r[0]]

        lines = []
        if succeeded:
            lines.append(f"Installed ({len(succeeded)}):")
            for _, fname, _ in succeeded:
                lines.append(f"  \u2713  {fname}")
        if failed:
            lines.append(f"\nFailed ({len(failed)}):")
            for _, fname, err in failed:
                lines.append(f"  \u2717  {fname}: {err}")

        if succeeded:
            lines.append(
                "\nRestart the application for the update(s) to take effect.\n"
                f"Files are in: {core.get_updates_dir()}"
            )
            self.log(f"[Update] Installed: {', '.join(r[1] for r in succeeded)}")
            messagebox.showinfo("Update Installed", "\n".join(lines), parent=self.root)
        else:
            messagebox.showerror("Update Failed", "\n".join(lines), parent=self.root)

    def _show_update_status(self):
        """Open a dialog showing installed overrides with option to remove them."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Installed Updates")
        dlg.configure(bg=T("bg"))
        dlg.resizable(True, False)
        dlg.grab_set()

        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        w, h = 620, 320
        dlg.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        tk.Label(dlg, text="Installed Update Files",
                 bg=T("bg"), fg=T("accent"),
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        tk.Label(dlg,
                 text="These .py files override the baked-in versions. Restart to apply changes.\n"
                      "Remove an override to revert to the original built-in version.",
                 bg=T("bg"), fg=T("fg2"),
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(0, 10))

        status = core.get_override_status()
        overrides = core.get_active_overrides()

        for fname, info in status.items():
            row = tk.Frame(dlg, bg=T("bg2"), pady=8)
            row.pack(fill="x", padx=14, pady=3)

            if info["installed"]:
                icon  = "\u2713"
                color = T("green_fg")
                detail = f"Installed  {info['mtime']}"
                active_note = "  (ACTIVE)" if fname in overrides else "  (restart to activate)"
            else:
                icon  = "\u2013"
                color = T("fg3")
                detail = "Using built-in version"
                active_note = ""

            tk.Label(row, text=f"{icon}  {fname}",
                     bg=T("bg2"), fg=color,
                     font=("Segoe UI", 10, "bold"), width=18, anchor="w").pack(side="left", padx=(10, 4))
            tk.Label(row, text=detail + active_note,
                     bg=T("bg2"), fg=T("fg2"),
                     font=("Segoe UI", 9)).pack(side="left", padx=4)

            if info["installed"]:
                def make_remove(fn):
                    def remove():
                        ok, msg = core.remove_override(fn)
                        if ok:
                            self.log(f"[Update] Removed override: {fn}")
                        dlg.destroy()
                        self._show_update_status()  # refresh
                    return remove
                tk.Button(row, text="Remove", command=make_remove(fname),
                          bg=T("red"), fg="white", relief="flat",
                          font=("Segoe UI", 9)).pack(side="right", padx=10)

        upd_dir = core.get_updates_dir()
        tk.Label(dlg, text=f"Updates folder: {upd_dir}",
                 bg=T("bg"), fg=T("fg3"),
                 font=("Segoe UI", 8)).pack(anchor="w", padx=14, pady=(8, 2))

        bf = tk.Frame(dlg, bg=T("bg"))
        bf.pack(fill="x", padx=14, pady=(4, 14))
        tk.Button(bf, text="Install Another Update",
                  command=lambda: [dlg.destroy(), self._install_update()],
                  bg=T("accent"), fg="white", relief="flat",
                  font=("Segoe UI", 10), padx=12).pack(side="left", padx=4)
        tk.Button(bf, text="Close", command=dlg.destroy,
                  bg=T("bg4"), fg=T("fg"), relief="flat",
                  font=("Segoe UI", 10), padx=12).pack(side="left", padx=4)

        dlg.wait_window()

    # -----------------------------------------------------------------------
    # Folder pair management  (auto-save on every change)
    # -----------------------------------------------------------------------

    def _refresh_pair_tree(self):
        for row in self.pair_tree.get_children():
            self.pair_tree.delete(row)
        for p in self.folder_pairs:
            self.pair_tree.insert("", "end", values=(p["remote"], p["local"]))

    def _save_pairs(self):
        """Persist folder pairs to disk immediately."""
        cfg = core.load_config()
        cfg["folder_pairs"] = self.folder_pairs
        core.save_config(cfg)

    def add_pair(self):
        creds = self._collect_credentials()
        dlg   = PairDialog(self.root, credentials=creds)
        if dlg.result:
            self.folder_pairs.append(dlg.result)
            self._refresh_pair_tree()
            self._ps_refresh_pair_combo()
            self._save_pairs()
            self.log(f"[Pairs] Added: {dlg.result['remote']} -> {dlg.result['local']}")

    def edit_pair(self):
        sel = self.pair_tree.selection()
        if not sel: return
        idx   = self.pair_tree.index(sel[0])
        creds = self._collect_credentials()
        dlg   = PairDialog(self.root, credentials=creds, existing=self.folder_pairs[idx])
        if dlg.result:
            self.folder_pairs[idx] = dlg.result
            self._refresh_pair_tree()
            self._ps_refresh_pair_combo()
            self._save_pairs()

    def remove_pair(self):
        sel = self.pair_tree.selection()
        if not sel: return
        idx = self.pair_tree.index(sel[0])
        removed = self.folder_pairs.pop(idx)
        self._refresh_pair_tree()
        self._ps_refresh_pair_combo()
        self._save_pairs()
        self.log(f"[Pairs] Removed: {removed['remote']}")

    # -----------------------------------------------------------------------
    # Credentials - collect from UI, decrypt for use, never log
    # -----------------------------------------------------------------------

    def _collect_credentials(self) -> dict:
        """
        Returns a dict with host/port/user plus _plaintext_pass.
        The plaintext password is ONLY used in memory for connecting,
        never written to disk here.
        """
        try: port = int(self.port_var.get().strip() or 21)
        except ValueError: port = 21
        return {
            "host":            self.host_var.get().strip(),
            "port":            port,
            "user":            self.user_var.get().strip(),
            "_plaintext_pass": self.pass_var.get(),
        }

    # -----------------------------------------------------------------------
    # Sync control
    # -----------------------------------------------------------------------

    def _make_sync_config(self) -> dict:
        """Build config dict for SyncWorker, including encrypted password."""
        creds = self._collect_credentials()
        cfg   = core.load_config()
        cfg["host"]               = creds["host"]
        cfg["port"]               = creds["port"]
        cfg["user"]               = creds["user"]
        cfg["folder_pairs"]       = self.folder_pairs
        cfg["interval"]           = self.interval_var.get()
        cfg["parallel_downloads"] = self.parallel_var.get()
        # Set encrypted password so SyncWorker can call get_password(cfg)
        core.set_password(cfg, creds["_plaintext_pass"])
        return cfg

    def start_sync(self):
        cfg   = self._make_sync_config()
        warns = core.validate_config(cfg)
        if warns:
            messagebox.showerror("Cannot Start", "\n".join(warns))
            return

        # Save credentials to disk if user asked
        if self.save_creds_var.get():
            disk_cfg = core.load_config()
            disk_cfg["save_credentials"] = True
            disk_cfg["host"]             = cfg["host"]
            disk_cfg["port"]             = cfg["port"]
            disk_cfg["user"]             = cfg["user"]
            disk_cfg["password_enc"]     = cfg.get("password_enc", "")
            core.save_config(disk_cfg)
            self.log("[Config] Credentials saved (encrypted).")

        self.status_var.set("Syncing")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.worker = core.SyncWorker(
            config=cfg, history=self.history,
            on_log=self.log,
            on_transfer_start=self._pt_start,
            on_transfer_progress=self._pt_progress,
            on_transfer_done=self._pt_done,
            on_transfer_error=self._on_transfer_error,
            on_cycle_done=lambda: self.root.after(0, self._notif_cycle_done),
            debug=self.debug_var.get(),
        )
        self.worker.start()
        self._update_tray_icon(syncing=True)

    def stop_sync(self):
        if self.worker:
            self.worker.stop()   # closes active FTP sockets immediately
            self.worker = None
        self.status_var.set("Idle")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.log("[Sync] Stopped.")
        self._notif_cycle_done()
        self._update_tray_icon(syncing=False)

    def sync_now(self):
        cfg = self._make_sync_config()
        warns = core.validate_config(cfg)
        if warns:
            messagebox.showerror("Cannot Sync", "\n".join(warns))
            return
        # Reuse self.worker so the Stop button works exactly the same way
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A sync is already running.")
            return

        self.status_var.set("Syncing")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        self.worker = core.SyncWorker(
            config=cfg, history=self.history,
            on_log=self.log,
            on_transfer_start=self._pt_start,
            on_transfer_progress=self._pt_progress,
            on_transfer_done=self._pt_done,
            on_transfer_error=self._on_transfer_error,
            debug=self.debug_var.get(),
        )

        def _run_once():
            self.worker._sync_all()
            # Re-enable UI when done (unless continuous sync took over)
            self.root.after(0, _sync_now_done)

        def _sync_now_done():
            # Only reset UI if we're still in the "one-shot" state
            # (continuous start_sync sets worker.is_alive() = True beyond _sync_all)
            if self.worker and not self.worker.is_alive():
                self.worker = None
                self.status_var.set("Idle")
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                self._notif_cycle_done()
                self._update_tray_icon(syncing=False)

        threading.Thread(target=_run_once, daemon=True).start()

    def _export_settings(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Export Settings",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=f"ftpsync_settings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        )
        if not path:
            return
        try:
            core.export_settings(path, include_password=False)
            messagebox.showinfo("Export Complete",
                                f"Settings exported (password stripped):\n{path}\n\n"
                                "Re-enter your password after importing on the new machine.",
                                parent=self.root)
            self.log(f"[Settings] Exported to {path}")
        except Exception as e:
            messagebox.showerror("Export Failed", str(e), parent=self.root)

    def _import_settings(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Import Settings",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            warns = core.import_settings(path)
            cfg   = core.load_config()
            # Reload UI from fresh config
            self.folder_pairs = cfg.get("folder_pairs", [])
            self._ps_refresh_pair_combo()
            self.interval_var.set(cfg.get("interval", 5))
            self.parallel_var.set(cfg.get("parallel_downloads", 3))
            self._refresh_pair_tree()
            self._refresh_ignore_tree()
            msg = "Settings imported successfully."
            if warns:
                msg += "\n\nWarnings:\n" + "\n".join(f"  - {w}" for w in warns)
            messagebox.showinfo("Import Complete", msg, parent=self.root)
            self.log(f"[Settings] Imported from {path}")
        except Exception as e:
            messagebox.showerror("Import Failed", str(e), parent=self.root)

    def save_settings(self):
        cfg = core.load_config()
        cfg["interval"]           = self.interval_var.get()
        cfg["parallel_downloads"] = self.parallel_var.get()
        cfg["notif_mode"]         = self._notif_mode.get()
        cfg["notif_batch"]        = self._notif_batch_var.get()
        cfg["theme"]              = self._theme_var.get()
        core.save_config(cfg)
        messagebox.showinfo("Saved", "Settings saved to settings.json.")

    # -----------------------------------------------------------------------
    # Transfer table
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Theme
    # -----------------------------------------------------------------------

    def _apply_theme_live(self):
        """
        Apply the selected theme immediately for as much of the UI as possible,
        then save. A full rebuild requires restart, but the most visible colors
        (window bg, notebook, tray icon) update right away.
        """
        global _current_theme
        name = self._theme_var.get()
        if name not in THEMES:
            return
        _current_theme = THEMES[name]

        # Root window background
        self.root.configure(bg=T("bg"))

        # Rebuild ttk styles so Notebook, Treeview, Combobox etc pick up new colors
        self._apply_ttk_styles()

        # Update tray icon to new theme colors
        if self._tray_icon and _TRAY_AVAILABLE:
            syncing = (self.worker is not None and self.worker.is_alive())
            self._tray_icon.icon = self._make_tray_image(syncing=syncing)

        # Save immediately
        cfg = core.load_config()
        cfg["theme"] = name
        core.save_config(cfg)

        messagebox.showinfo(
            "Theme Changed",
            f"Theme set to {name!r}.\n\nMost colors update live. Restart for a full refresh.",
            parent=self.root,
        )

    # -----------------------------------------------------------------------
    # Notification logic
    # -----------------------------------------------------------------------

    def _notif_mode_changed(self):
        """Show/hide the batch size spinbox based on selected mode."""
        if self._notif_mode.get() == "batch":
            self._notif_batch_frame.pack(anchor="w", padx=32, pady=(2, 4))
        else:
            self._notif_batch_frame.pack_forget()

    def _tray_notify(self, title: str, message: str):
        """Fire a tray notification if tray is available."""
        if self._tray_icon and _TRAY_AVAILABLE:
            try:
                self._tray_icon.notify(message, title)
            except Exception:
                pass

    def _on_file_downloaded(self, filename: str):
        """
        Called each time a file finishes downloading (status == Done).
        Decides whether to fire a notification based on the current mode.
        """
        mode = self._notif_mode.get()

        if mode == "off":
            return

        self._notif_count      += 1
        self._notif_batch_acc  += 1

        if mode == "every":
            self._tray_notify("FTP Sync — Downloaded", filename)

        elif mode == "batch":
            n = self._notif_batch_var.get()
            if self._notif_batch_acc >= n:
                self._tray_notify(
                    "FTP Sync",
                    f"{self._notif_batch_acc} files downloaded"
                )
                self._notif_batch_acc = 0

        # "cycle" mode is handled in _notif_cycle_done() instead

    def _notif_cycle_done(self):
        """
        Called when a sync cycle finishes (scheduled or one-shot).
        In cycle mode fires a single summary notification.
        In batch mode fires a final partial-batch notification if any remain.
        Resets per-cycle counters.
        """
        mode  = self._notif_mode.get()
        count = self._notif_count

        if count > 0:
            if mode == "cycle":
                self._tray_notify(
                    "FTP Sync — Cycle complete",
                    f"{count} file{'s' if count != 1 else ''} downloaded"
                )
            elif mode == "batch" and self._notif_batch_acc > 0:
                # Flush leftover batch
                self._tray_notify(
                    "FTP Sync",
                    f"{self._notif_batch_acc} files downloaded"
                )
                self._notif_batch_acc = 0

        # Reset per-cycle counters
        self._notif_count     = 0
        self._notif_batch_acc = 0

    def _clear_completed_transfers(self):
        """Remove rows that are done (not currently downloading)."""
        to_remove = []
        for iid in self.pt.get_children():
            status = self.pt.item(iid)["values"][3] if self.pt.item(iid)["values"] else ""
            if str(status).lower() != "downloading":
                to_remove.append(iid)
        for iid in to_remove:
            self.pt.delete(iid)
            # Clean up the name cache too
            try:
                tid = int(iid)
                self._transfer_names.pop(tid, None)
            except (ValueError, TypeError):
                pass

    def _pt_start(self, tid, filename):
        # Store the name in a dict keyed by tid - never rely on reading back
        # from the treeview since row values can be stale or mismatched when
        # multiple transfers fire rapidly and inserts/updates race each other.
        name = os.path.basename(filename)
        self._transfer_names[tid] = name
        def _insert():
            iid = str(tid)
            if not self.pt.exists(iid):
                self.pt.insert("", 0, iid=iid,
                               values=(name, "[          ]  0%", "-", "Downloading"))
        self.root.after(0, _insert)

    def _pt_progress(self, tid, pct, done, total):
        def _u():
            iid  = str(tid)
            name = self._transfer_names.get(tid, "")
            if not self.pt.exists(iid):
                # Row not inserted yet - insert it now so progress isn't lost
                self.pt.insert("", 0, iid=iid,
                               values=(name, "[          ]  0%", "-", "Downloading"))
            bar = "#" * (pct // 10) + "-" * (10 - pct // 10)
            sz  = (f"{done/1048576:.1f}MB / {total/1048576:.1f}MB"
                   if total >= 1048576
                   else f"{done/1024:.1f}KB / {total/1024:.1f}KB")
            self.pt.item(iid, values=(name, f"[{bar}] {pct}%", sz, "Downloading"))
        self.root.after(0, _u)

    def _pt_done(self, tid, status):
        def _u():
            iid  = str(tid)
            name = self._transfer_names.get(tid, "")
            if not self.pt.exists(iid):
                self.pt.insert("", 0, iid=iid,
                               values=(name, "[##########] 100%", "-", status))
            else:
                try:    sz = self.pt.item(iid)["values"][2]
                except: sz = "-"
                self.pt.item(iid, values=(name, "[##########] 100%", sz, status))
            # Route through notification logic (respects user's chosen mode)
            if status == "Done":
                self._on_file_downloaded(name)
        self.root.after(0, _u)

    # -----------------------------------------------------------------------
    # System tray
    # -----------------------------------------------------------------------

    def _make_tray_image(self, syncing: bool = False) -> "Image.Image":
        """
        Draw a 64x64 tray icon programmatically — no external image file needed.
        Uses theme accent colors for idle/syncing states.
        """
        size   = 64
        img    = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw   = ImageDraw.Draw(img)
        colour = T("tray_sync") if syncing else T("tray_idle")
        if isinstance(colour, str):
            # Convert hex to RGBA tuple
            c = colour.lstrip("#")
            colour = (int(c[0:2],16), int(c[2:4],16), int(c[4:6],16), 255)

        # Filled circle background
        draw.ellipse([2, 2, size - 2, size - 2], fill=colour)

        # Down-arrow (three lines forming a chevron)
        cx, cy = size // 2, size // 2
        w = 14
        draw.polygon([
            (cx - w, cy - 6),
            (cx + w, cy - 6),
            (cx,     cy + 10),
        ], fill=(255, 255, 255, 230))
        draw.rectangle([cx - 5, cy - 20, cx + 5, cy - 4], fill=(255, 255, 255, 230))
        return img

    def _run_tray(self):
        """Runs the pystray icon on its own thread (blocking call)."""
        if not _TRAY_AVAILABLE:
            return

        def on_show(icon, item):
            self.root.after(0, self._show_window)

        def on_sync_now(icon, item):
            self.root.after(0, self.sync_now)

        def on_start(icon, item):
            self.root.after(0, self.start_sync)

        def on_stop(icon, item):
            self.root.after(0, self.stop_sync)

        def on_quit(icon, item):
            self._quitting = True
            icon.stop()
            self.root.after(0, self._do_quit)

        menu = pystray.Menu(
            pystray.MenuItem("Show FTP Sync",   on_show,     default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sync Now",         on_sync_now),
            pystray.MenuItem("Start Syncing",    on_start),
            pystray.MenuItem("Stop",             on_stop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit",             on_quit),
        )

        self._tray_icon = pystray.Icon(
            "FTPSync",
            self._make_tray_image(syncing=False),
            "FTP Sync",
            menu,
        )
        self._tray_icon.run()   # blocks until icon.stop() is called

    def _update_tray_icon(self, syncing: bool):
        """Swap the tray icon colour to reflect sync state."""
        if self._tray_icon and _TRAY_AVAILABLE:
            try:
                self._tray_icon.icon  = self._make_tray_image(syncing=syncing)
                self._tray_icon.title = "FTP Sync — Syncing…" if syncing else "FTP Sync"
            except Exception:
                pass

    def _show_window(self):
        """Restore window from tray."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.root.state("normal")

    def _hide_window(self):
        """Minimise to tray (hide the window)."""
        self.root.withdraw()

    def _on_close(self):
        """Called when the user clicks the window X button."""
        if _TRAY_AVAILABLE:
            self._hide_window()
        else:
            self._do_quit()

    def _do_quit(self):
        """Hard quit — stop worker, stop tray, destroy window."""
        self._quitting = True
        if self.worker:
            try:
                self.worker.stop()
            except Exception:
                pass
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()

    # -----------------------------------------------------------------------
    # Log
    # -----------------------------------------------------------------------

    def log(self, msg):
        self.log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(200, self._poll_log)

    def _fmt_size(self, b):
        if b >= 1_073_741_824: return f"{b/1_073_741_824:.1f} GB"
        if b >= 1_048_576:     return f"{b/1_048_576:.1f} MB"
        if b >= 1024:          return f"{b/1024:.1f} KB"
        return f"{b} B"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Single-instance enforcement
# ---------------------------------------------------------------------------
_SINGLE_INSTANCE_PORT = 19847   # arbitrary fixed localhost port

def _try_become_primary():
    """
    Single-instance enforcement using a bound TCP socket.

    On Windows we set SO_EXCLUSIVEADDRUSE (not just clearing SO_REUSEADDR —
    that does NOT prevent another socket with SO_REUSEADDR from stealing the
    port). On all other platforms we simply omit SO_REUSEADDR so the OS
    returns EADDRINUSE when a second instance tries to bind the same port.

    Returns (server_sock, True)  — this is the first/only instance.
    Returns (None,        False) — another instance is running; we've sent
                                   it a FOCUS signal and this process should exit.
    """
    import socket as _sock, sys as _sys
    srv = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    # On Windows use SO_EXCLUSIVEADDRUSE so a second socket with SO_REUSEADDR
    # cannot steal our port.  On POSIX just leave SO_REUSEADDR unset.
    if _sys.platform == "win32":
        SO_EXCLUSIVEADDRUSE = 0x0080    # winsock2.h constant
        try:
            srv.setsockopt(_sock.SOL_SOCKET, SO_EXCLUSIVEADDRUSE, 1)
        except (OSError, AttributeError):
            pass
    try:
        srv.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        srv.listen(5)
        return srv, True
    except OSError:
        srv.close()
        # Another instance holds the port — send it a FOCUS signal then exit.
        try:
            c = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
            c.settimeout(1.0)
            c.connect(("127.0.0.1", _SINGLE_INSTANCE_PORT))
            c.sendall(b"FOCUS\n")
            c.close()
        except Exception:
            pass
        return None, False

def _start_focus_listener(sock, root):
    """Daemon thread: accept connections from future second-launch attempts."""
    import socket as _sock, threading as _thr
    def _listen():
        sock.settimeout(1.0)
        while True:
            try:
                conn, _ = sock.accept()
                data = conn.recv(64)
                conn.close()
                if b"FOCUS" in data:
                    root.after(0, _bring_to_front)
            except _sock.timeout:
                continue
            except Exception:
                break
    def _bring_to_front():
        try:
            root.deiconify()
            root.lift()
            root.focus_force()
            root.attributes("-topmost", True)
            root.after(200, lambda: root.attributes("-topmost", False))
        except Exception:
            pass
    _thr.Thread(target=_listen, daemon=True).start()


if __name__ == "__main__":
    import argparse as _ap, json as _json

    _parser = _ap.ArgumentParser(description="FTP Remote Sync")
    _parser.add_argument("--session", metavar="FILE",
                         help="Path to a .session.json file — "
                              "opens the Browser tab and auto-connects.")
    _args, _ = _parser.parse_known_args()

    # Load session file if provided
    _session = None
    if _args.session:
        try:
            with open(_args.session, "r", encoding="utf-8") as _sf:
                _session = _json.load(_sf)
        except Exception as _e:
            import tkinter.messagebox as _mb
            import tkinter as _tk_tmp
            _r_tmp = _tk_tmp.Tk(); _r_tmp.withdraw()
            _mb.showerror("Session load failed",
                          f"Could not read session file:\n{_args.session}\n\n{_e}")
            _r_tmp.destroy()
            _session = None

    # ── Single-instance check ────────────────────────────────────────────────
    # If another copy is already running, send it a FOCUS signal and exit.
    _si_sock, _is_primary = _try_become_primary()
    if not _is_primary:
        import sys as _sys_si
        _sys_si.exit(0)   # graceful — existing window was already raised

    # ── Ensure tkinterdnd2 is installed (only runs in the primary instance) ─
    if not _DND_AVAILABLE:
        try:
            import subprocess as _sp, sys as _sys2
            _sp.check_call(
                [_sys2.executable, "-m", "pip", "install", "tkinterdnd2", "--quiet"],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            from tkinterdnd2 import TkinterDnD, DND_FILES
            _DND_AVAILABLE = True
        except Exception:
            pass

    # ── Create root window ───────────────────────────────────────────────────
    # TkinterDnD.Tk() is required for OS-level drag-and-drop from Explorer.
    # Falls back to plain tk.Tk() if tkinterdnd2 is unavailable.
    if _DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app  = FTPSyncApp(root)

    # Start the focus listener so a second launch brings this window to front
    _start_focus_listener(_si_sock, root)

    if _session:
        # Apply session credentials to config so browser can connect
        cfg = core.load_config()
        cfg["host"]         = _session.get("host", cfg.get("host", ""))
        cfg["port"]         = _session.get("port", cfg.get("port", 21))
        cfg["user"]         = _session.get("user", cfg.get("user", ""))
        if _session.get("password_enc"):
            cfg["password_enc"] = _session["password_enc"]
        core.save_config(cfg)

        # Pre-fill local dir from session
        local_dir = _session.get("local_dir", "")
        if local_dir:
            app._br_local_dir.set(local_dir)

        # Switch to Browser tab
        try:
            app.notebook.select(app._br_tab_index)
        except Exception:
            pass

        # Auto-connect after the UI is ready, starting at saved path
        start_path = _session.get("start_path", "/")
        def _auto_connect():
            app._br_cfg = cfg
            app._br_connect_to_path(start_path)
        root.after(400, _auto_connect)

    root.mainloop()
