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
    if sys.platform.startswith("linux"):
        return len(prefer_system_ffmpeg())
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


FFMPEG_LIBS = ("avutil", "swresample", "swscale", "avcodec", "avformat")   # dependency order


def prefer_system_ffmpeg() -> list[str]:
    """Linux: when the system's FFmpeg has the same major version (soname) as the copy Qt brings
    for QtMultimedia, load the system's first and let both Qt and libVLC share it.

    A process holds one library per soname: whichever loads first serves everyone. Qt's copy
    (FFmpeg 7.0 in PySide6 6.8) loaded first left libVLC's avcodec plugin, built against the
    system's newer 7.1 (Debian 13), without a symbol it needs: no video decoder at all, sound with
    a black picture. Same major means same ABI, newer minors only add: the system's one serves both.
    Returns the paths loaded."""
    import ctypes
    import re
    import subprocess
    try:
        import PySide6
        qt_lib = Path(PySide6.__file__).parent / "Qt" / "lib"
    except Exception:
        return []
    bundled = {}
    for name in FFMPEG_LIBS:
        found = sorted(qt_lib.glob(f"lib{name}.so.*"))
        if found:
            bundled[name] = re.match(r"(lib\w+\.so\.\d+)", found[0].name).group(1)
    if not bundled:
        return []
    try:
        listing = subprocess.run(["/sbin/ldconfig", "-p"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    system = {}
    for line in listing.splitlines():
        m = re.match(r"\s*(\S+) \((libc6,x86-64|libc6,AArch64)[^)]*\) => (\S+)", line)
        if m and m.group(1) not in system:
            system[m.group(1)] = m.group(3)
    if bundled.get("avcodec") not in system:
        return []           # a different major (Ubuntu 24.04 has FFmpeg 6): no clash, nothing to do
    loaded = []
    for name in FFMPEG_LIBS:
        soname = bundled.get(name)
        path = system.get(soname or "")
        if not path:
            continue
        try:
            ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
            loaded.append(path)
        except OSError:
            break
    return loaded
