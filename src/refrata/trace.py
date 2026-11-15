"""Startup timeline: REFRATA_TRACE=<file> writes how long each step took since the process started.

    REFRATA_TRACE=trace.txt Refrata.exe video.mp4

Off (and free) without the variable. Times are from the process's creation, so they include the
executable's own start (bootloader, Python) before any of our code runs.
"""
import os
import sys
import time

_PATH = os.environ.get("REFRATA_TRACE", "")
_lines: list[str] = []


def _process_start() -> float:
    """Wall-clock time the process was created."""
    try:
        if sys.platform == "win32":
            import ctypes
            import ctypes.wintypes as wt
            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = ctypes.c_void_p
            c, e, kt, ut = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
            k.GetProcessTimes(ctypes.c_void_p(k.GetCurrentProcess()), ctypes.byref(c), ctypes.byref(e),
                              ctypes.byref(kt), ctypes.byref(ut))
            return ((c.dwHighDateTime << 32 | c.dwLowDateTime) - 116444736000000000) / 1e7
        if sys.platform.startswith("linux"):
            ticks = os.sysconf("SC_CLK_TCK")
            start = int(open("/proc/self/stat").read().rsplit(")", 1)[1].split()[19]) / ticks
            boot = float(open("/proc/stat").read().split("btime ")[1].split()[0])
            return boot + start
    except Exception:
        pass
    return time.time()


_T0 = _process_start() if _PATH else 0.0


def mark(label: str):
    if not _PATH:
        return
    _lines.append(f"{(time.time() - _T0) * 1000:7.0f} ms  {label}")
    try:
        with open(_PATH, "w", encoding="utf8") as f:
            f.write("\n".join(_lines) + "\n")
    except OSError:
        pass


def enabled() -> bool:
    return bool(_PATH)
