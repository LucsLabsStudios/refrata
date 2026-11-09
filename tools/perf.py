r"""Measure the frozen Refrata (Windows): idle CPU, playback CPU + dropped frames, memory.

    python tools/perf.py [path\to\Refrata.exe]

Each run gets a temporary data folder with an empty library (never the copy in use: --new-window and
--screenshot skip the single-instance hand-over). Test clips (1080p30, 4K60 H.264) are made with ffmpeg
into build/perf on first use. Results: docs/ROTEIRO-DESEMPENHO-E-PLATAFORMAS.md, section 6.
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "Refrata", "Refrata.exe")
HERE = os.path.join(ROOT, "build", "perf")
CLIPS = {"1080p30.mp4": ("1920x1080", 30, "8M"), "4k60.mp4": ("3840x2160", 60, "25M")}


def make_clips():
    os.makedirs(HERE, exist_ok=True)
    for name, (size, fps, rate) in CLIPS.items():
        out = os.path.join(HERE, name)
        if not os.path.exists(out):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "20", "-c:v", "libx264", "-preset", "veryfast",
                            "-pix_fmt", "yuv420p", "-b:v", rate, "-c:a", "aac", out], check=True)
k32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi


class PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def handle(pid):
    k32.OpenProcess.restype = wt.HANDLE
    return k32.OpenProcess(0x1000 | 0x0400 | 0x0010, False, pid)   # query (limited) info + vm read


def cpu_s(h):
    c, e, kt, ut = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
    k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut))
    f = lambda t: (t.dwHighDateTime << 32 | t.dwLowDateTime) / 1e7  # noqa: E731
    return f(kt) + f(ut)


def mem_mb(h):
    m = PMC(cb=ctypes.sizeof(PMC))
    psapi.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb)
    return m.WorkingSetSize / 2**20, m.PrivateUsage / 2**20


def env():
    d = tempfile.mkdtemp(prefix="refrata_perf_")
    # empty library: otherwise the defaults (the user's Videos and Music) get scanned and thumbnailed
    import json
    with open(os.path.join(d, "settings.json"), "w", encoding="utf8") as f:
        json.dump({"settings": {"folders": [], "quickOpenReset": True}, "history": []}, f)
    return dict(os.environ, REFRATA_DATA=d, REFRATA_E2E_TMP=d), d


def idle(seconds=30):
    e, d = env()
    p = subprocess.Popen([EXE, "--new-window"], env=e)
    h = handle(p.pid)
    time.sleep(8)                       # startup, first library scan
    c0 = cpu_s(h)
    time.sleep(seconds)
    c1 = cpu_s(h)
    ws, priv = mem_mb(h)
    p.kill()
    return {"idle_cpu_pct": round((c1 - c0) / seconds * 100 / os.cpu_count(), 2),
            "idle_cpu_ms_per_s": round((c1 - c0) / seconds * 1000, 1), "ws_mb": round(ws), "private_mb": round(priv)}


def play(path, seconds=12):
    e, d = env()
    log = os.path.join(d, "shot.log")
    shot = os.path.join(d, "shot.png")
    p = subprocess.Popen([EXE, "--screenshot", shot, "--delay", str((seconds + 4) * 1000), "--log", log, path], env=e)
    h = handle(p.pid)
    time.sleep(4)                       # startup + open
    c0 = cpu_s(h)
    time.sleep(seconds)
    c1 = cpu_s(h)
    ws, priv = mem_mb(h)
    p.wait(60)
    text = open(log, encoding="utf8", errors="replace").read() if os.path.exists(log) else ""
    m = re.search(r"frames=(\d+) dropped=(\d+) size=(\d+x\d+)", text)
    return {"file": os.path.basename(path), "cpu_cores_used": round((c1 - c0) / seconds, 2),
            "frames": int(m.group(1)) if m else None, "dropped": int(m.group(2)) if m else None,
            "size": m.group(3) if m else None, "ws_mb": round(ws), "private_mb": round(priv)}


if __name__ == "__main__":
    make_clips()
    print("cores:", os.cpu_count())
    print(idle())
    for f in ("1080p30.mp4", "4k60.mp4"):
        print(play(os.path.join(HERE, f)))
