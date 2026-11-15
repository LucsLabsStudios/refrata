"""How long each file took to open, step by step, in <data>/opening.log (the last 200 opens).

A double-click in Explorer starts a new Refrata.exe, which hands the file to the running one; that
one shows its window, libVLC opens the file, the sound starts, the first picture is drawn. Each
step is stamped here, in milliseconds from the moment the request started (the launcher process's
creation when the file came from Explorer), so a slow open says where its time went:

    12:30:01  Luis e Bruna.mp4  [hand-over]  launcher started 0 | file received 412 | window in front 430 | ...

Always on: a few timestamps per opened file and one short line written once it is complete.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

KEEP = 200
_current: dict | None = None
_launch: tuple[float, float] | None = None   # (launcher created, file received), wall clock
_early: list[tuple[str, float]] = []          # steps seen before the item began (the window comes up first)
_first = True                                 # the first file of this process: a cold start
_started = time.time()


def _log_path() -> Path:
    from .settings import DATA_DIR
    return DATA_DIR / "opening.log"


def launcher_started(created: float):
    """A launcher handed a file over; `created` is when that process was created (wall clock)."""
    global _launch
    _launch = (created, time.time())


def begin(name: str, how: str):
    """The engine starts opening `name`. `how`: what asked for it (hand-over, app, next...)."""
    global _current, _launch, _first
    _finish(timed_out=True)
    now = time.time()
    rec = {"name": name, "how": how, "t0": now, "marks": [], "done": set()}
    if _launch and now - _launch[1] < 5:
        created, received = _launch
        rec["t0"] = min(created, received)
        rec["marks"] += [("launcher started", created), ("file received", received)]
        rec["how"] = "hand-over"
    elif _first and now - _started < 30:
        from .trace import _process_start
        rec["t0"] = min(now, _process_start())
        rec["marks"].append(("process started", rec["t0"]))
        rec["how"] = "cold start"
    _first = False
    for step, t in _early:
        if t >= rec["t0"] - 0.05:
            rec["marks"].append((step, t))
            rec["done"].add(step)
    _early.clear()
    _launch = None
    _current = rec


def mark(step: str, once=True):
    rec = _current
    if rec is None:
        # the window is brought up before the item begins: kept for it (playback steps of an item
        # already logged are not)
        if step == "window in front":
            _early[:] = [(s, t) for s, t in _early if time.time() - t < 3] + [(step, time.time())]
        return
    if once and step in rec["done"]:
        return
    rec["done"].add(step)
    rec["marks"].append((step, time.time()))
    if {"sound", "first picture"} <= rec["done"] or ("sound" in rec["done"] and rec.get("audio")):
        _finish()


def audio_only():
    if _current is not None:
        _current["audio"] = True


def _finish(timed_out=False):
    global _current
    rec, _current = _current, None
    if rec is None or not rec["marks"]:
        return
    t0 = rec["t0"]
    steps = " | ".join(f"{s} {max(0, (t - t0) * 1000):.0f}" for s, t in sorted(rec["marks"], key=lambda m: m[1]))
    line = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)) + f"  {rec['name']}  [{rec['how']}]  {steps} ms"
            + ("  (stopped before it was complete)" if timed_out else ""))
    try:
        p = _log_path()
        lines = p.read_text("utf8").splitlines()[-(KEEP - 1):] if p.exists() else []
        p.write_text("\n".join(lines + [line]) + "\n", "utf8")
    except OSError:
        pass


def process_created(pid: int) -> float | None:
    """Wall-clock creation time of process `pid` (Windows), None if it cannot be read."""
    if sys.platform != "win32" or not pid:
        return None
    import ctypes
    import ctypes.wintypes as wt
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wt.HANDLE
    h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        c, e, kt, ut = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        if not k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
            return None
        return ((c.dwHighDateTime << 32 | c.dwLowDateTime) - 116444736000000000) / 1e7
    finally:
        k.CloseHandle(h)


def pipe_client_pid(sock) -> int:
    """The process at the other end of a QLocalSocket (Windows named pipe), 0 if unknown."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        pid = ctypes.c_ulong(0)
        handle = ctypes.c_void_p(int(sock.socketDescriptor()))
        if ctypes.windll.kernel32.GetNamedPipeClientProcessId(handle, ctypes.byref(pid)):
            return pid.value
    except Exception:
        pass
    return 0


def recent(n=20) -> list[str]:
    try:
        return _log_path().read_text("utf8").splitlines()[-n:]
    except OSError:
        return []