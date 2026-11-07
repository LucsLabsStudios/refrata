"""Locate libVLC before python-vlc is imported.

Search order (first hit wins):
  1. PYTHON_VLC_LIB_PATH already set by the caller
  2. a bundled copy next to the app: <app>/vlc/libvlc.dll   (frozen build: _internal/vlc)
  3. a repo-local copy: vendor/vlc/libvlc.dll               (tools/build.py fills it)
  4. a system VLC 3 install (Program Files)

The packaged app therefore needs neither Python nor VLC installed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    """Folder holding qml/, assets/ and (when bundled) vlc/."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def candidates() -> list[Path]:
    root = app_root()
    out = [root / "vlc", Path(sys.executable).parent / "vlc", root / "vendor" / "vlc"]
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", ""),
                 os.environ.get("ProgramFiles(x86)", "")):
        if base:
            out.append(Path(base) / "VideoLAN" / "VLC")
    return out


def preload_qt() -> int:
    """Load the bundled Qt and FFmpeg DLLs by full path (frozen build only); returns how many loaded.

    Qt loads QML plugins from qml/<module>/ and their Qt6*.dll dependencies are found through
    PATH. Inside an MSIX package Windows never searches PATH (packaged DLL search order), so e.g.
    qtquickdialogsplugin.dll failed with "module not found". Once a DLL is in the process, later
    loads by name are satisfied from the loaded-module list, in every install mode alike.
    """
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return 0
    import ctypes
    qt = app_root() / "PySide6"
    # Qt6*.dll plus what Qt plugins import from there (FFmpeg for the multimedia backend);
    # opengl32sw.dll is Qt's software-GL fallback and is loaded only on demand
    pending = sorted(p for p in qt.glob("*.dll") if p.name.lower() != "opengl32sw.dll")
    loaded = 0
    while pending:  # dependency order is unknown: retry until a pass makes no progress
        failed = []
        for dll in pending:
            try:
                ctypes.WinDLL(str(dll))
                loaded += 1
            except OSError:
                failed.append(dll)
        if len(failed) == len(pending):
            break
        pending = failed
    return loaded


def configure() -> Path | None:
    """Point python-vlc at a libVLC; returns the folder used (None if none was found)."""
    if os.environ.get("PYTHON_VLC_LIB_PATH"):
        return Path(os.environ["PYTHON_VLC_LIB_PATH"]).parent
    if sys.platform != "win32":
        return None
    for cand in candidates():
        if (cand / "libvlc.dll").exists() and (cand / "libvlccore.dll").exists():
            os.environ["PYTHON_VLC_LIB_PATH"] = str(cand / "libvlc.dll")
            os.environ["PYTHON_VLC_MODULE_PATH"] = str(cand)
            if (cand / "plugins").is_dir():
                os.environ.setdefault("VLC_PLUGIN_PATH", str(cand / "plugins"))
            os.add_dll_directory(str(cand))
            os.environ["PATH"] = str(cand) + os.pathsep + os.environ.get("PATH", "")
            return cand
    return None
