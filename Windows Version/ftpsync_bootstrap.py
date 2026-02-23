"""
ftpsync_bootstrap.py  -  PyInstaller runtime hook
=================================================
Runs automatically before any app code when the EXE starts.

THE PROBLEM this solves:
  PyInstaller installs a FrozenImporter into sys.meta_path that intercepts
  imports of baked-in modules before sys.path (PathFinder) is consulted.
  Simply doing sys.path.insert(0, updates_dir) is NOT enough - the
  FrozenImporter catches 'import ftp_core' first and returns the baked-in
  copy regardless of what is on sys.path.

THE FIX (four steps, all required):
  1. Insert updates/ at the front of sys.path
  2. Move PathFinder BEFORE FrozenImporter in sys.meta_path so the .py
     files on disk are found first
  3. Call importlib.invalidate_caches() so newly visible files are found
  4. Remove any already-cached frozen module entries from sys.modules

Updates folder: <EXE directory>/updates/
Supported files: ftp_core.py, ftp_gui.py, ftp_web.py
"""
import importlib
import os
import sys


def _install_overrides():
    if not getattr(sys, "frozen", False):
        return  # Running as plain .py - nothing to do

    exe_dir     = os.path.dirname(sys.executable)
    updates_dir = os.path.join(exe_dir, "updates")

    # Create the folder on first run so users know where to put files
    try:
        os.makedirs(updates_dir, exist_ok=True)
    except OSError:
        return

    known = {"ftp_core.py", "ftp_gui.py", "ftp_web.py"}
    try:
        found = [f for f in os.listdir(updates_dir) if f in known]
    except OSError:
        return

    if not found:
        return

    # ── Step 1: Insert updates/ at the FRONT of sys.path ──────────────────
    if updates_dir not in sys.path:
        sys.path.insert(0, updates_dir)

    # ── Step 2: Reorder sys.meta_path ─────────────────────────────────────
    # PyInstaller's FrozenImporter normally sits BEFORE PathFinder.
    # We move PathFinder ahead of it so disk files win over frozen copies.
    try:
        frozen_idx = None
        path_idx   = None
        for i, finder in enumerate(sys.meta_path):
            name = type(finder).__name__
            if name == "FrozenImporter":
                frozen_idx = i
            elif name == "PathFinder":
                path_idx = i
        if frozen_idx is not None and path_idx is not None and path_idx > frozen_idx:
            path_finder = sys.meta_path.pop(path_idx)
            sys.meta_path.insert(frozen_idx, path_finder)
    except Exception:
        pass

    # ── Step 3: Invalidate import caches ──────────────────────────────────
    # Forces Python to rescan sys.path entries for newly visible modules.
    try:
        importlib.invalidate_caches()
    except Exception:
        pass

    # ── Step 4: Clear frozen copies from sys.modules ──────────────────────
    # If any baked-in version was already cached, remove it so the next
    # import picks up the .py file from updates/ instead.
    for filename in found:
        mod_name = filename[:-3]  # strip .py
        sys.modules.pop(mod_name, None)

    # Write marker so the running app can report which overrides are active
    try:
        marker = os.path.join(exe_dir, ".active_overrides")
        with open(marker, "w") as mf:
            mf.write("\n".join(found))
    except OSError:
        pass


_install_overrides()
