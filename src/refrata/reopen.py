"""Reopen Refrata in place: a new process takes over exactly where this one is.

Used when Windows HDR is switched on after Refrata started (hdroutput.py: Qt 6.8 can only make the
HDR window in a new process). What is kept: the queue, the item and the point in it, playing or
paused, the player open or not, the page, the window's place, size and state (maximised, full
screen). The new process waits for this one to end before it claims the single instance, so the
files are not handed back to the process that is leaving.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .settings import DATA_DIR

STATE = DATA_DIR / "reopen.json"
STALE_S = 120          # a state file older than this is from a reopen that never happened


def capture(engine, win) -> dict:
    """What the new process restores (also what the tests compare)."""
    from PySide6.QtGui import QWindow
    vis = win.visibility()
    V = QWindow.Visibility
    r = win.property("windowedRect")
    if r is None or not hasattr(r, "x"):
        g = win.geometry()
        rect = [g.x(), g.y(), g.width(), g.height()]
    else:
        rect = [int(r.x()), int(r.y()), int(r.width()), int(r.height())]
    if vis == V.Windowed:
        g = win.geometry()
        rect = [g.x(), g.y(), g.width(), g.height()]
    queue = [e.get("path", "") for e in engine._queue if e.get("path")]
    return {
        "written": time.time(),
        "files": queue,
        "index": max(0, engine._index) if queue else 0,
        "ms": int(engine.time or 0),
        "playing": engine.state == "playing",
        "playerOpen": bool(win.property("playerOpen")),
        "page": int(win.property("page") or 0),
        "visibility": "fullscreen" if vis == V.FullScreen else "maximized" if vis == V.Maximized else "windowed",
        "rect": rect,
    }


def command(pid: int) -> list[str]:
    """How to start this same Refrata again (installed exe or the source tree)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--reopen", str(pid)]
    main = Path(__file__).resolve().parent / "__main__.py"
    return [sys.executable, str(main), "--reopen", str(pid)]


def relaunch(engine, win, app) -> bool:
    """Write the state, start the new process, leave. False if the new process could not start."""
    state = capture(engine, win)
    try:
        STATE.write_text(json.dumps(state), encoding="utf8")
    except OSError:
        return False
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(command(os.getpid()), close_fds=True, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        try:
            STATE.unlink()
        except OSError:
            pass
        return False
    app.quit()
    return True


def wait_for(pid: int, seconds: float = 15.0):
    """The new process: let the old one finish before claiming the single instance."""
    if sys.platform != "win32" or pid <= 0:
        return
    import ctypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
    if h:
        k.WaitForSingleObject(ctypes.c_void_p(h), int(seconds * 1000))
        k.CloseHandle(ctypes.c_void_p(h))


def load() -> dict | None:
    """The state left by the process that reopened this one (read once, then removed)."""
    try:
        state = json.loads(STATE.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    try:
        STATE.unlink()
    except OSError:
        pass
    if time.time() - float(state.get("written", 0)) > STALE_S:
        return None
    return state


def restore(state: dict, engine, win):
    """Put the window and the playback back as they were."""
    from PySide6.QtCore import QRect, QRectF
    x, y, w, h = state.get("rect") or [0, 0, 0, 0]
    if w > 0 and h > 0:
        win.setProperty("windowedRect", QRectF(x, y, w, h))
        win.setGeometry(QRect(x, y, w, h))
    vis = state.get("visibility")
    if vis == "fullscreen":
        win.setProperty("restoreVisibility", 2)          # Window.Windowed, for leaving full screen
        win.showFullScreen()
    elif vis == "maximized":
        win.showMaximized()
    win.setProperty("page", int(state.get("page", 0)))
    files = [f for f in state.get("files", []) if f]
    if not files:
        return
    engine.openFilesAt(files, int(state.get("index", 0)), int(state.get("ms", 0)))
    win.setProperty("playerOpen", bool(state.get("playerOpen")))
    if not state.get("playing"):
        # paused before: paused again on the same picture once it is there
        def pause_once():
            if engine.state == "playing":
                try:
                    engine.stateChanged.disconnect(pause_once)
                except (RuntimeError, TypeError):
                    pass
                engine.pause()
        engine.stateChanged.connect(pause_once)
