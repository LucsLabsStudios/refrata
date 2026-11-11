r"""The app the way a person starts it: real processes, real files, with time and resource budgets.

    python tests/launch.py                                  # source tree
    python tests/launch.py --exe dist\Refrata\Refrata.exe   # the packaged build (the release gate)

The other batteries drive one app from the inside. This one stays outside: it double-clicks.
  1. cold start with a video: one window, playing it, the first picture within the budget
  2. a second file while Refrata is open: handed over within the budget, no second Refrata
  3. a second file while the first Refrata is still starting, and three launches at once: one Refrata
  4. sitting idle: almost no CPU, memory within the budget
  5. playing 1080p30 and 4K60: CPU within the budget, (almost) no dropped pictures
Every run has its own temporary data folder; only the processes started here are ever closed.
Budgets are generous on purpose (the machine may be busy): they catch regressions, not noise.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERF = ROOT / "build" / "perf"
CLIPS = {"1080p30.mp4": ("1920x1080", 30, "8M"), "4k60.mp4": ("3840x2160", 60, "25M")}
# what counts as a regression (measured on the reference machine: see the numbers each run prints)
BUDGET = {
    "cold_first_picture_s": {"exe": 6.0, "src": 7.0},
    "handover_s": 1.5,
    "idle_cpu_ms_per_s": 40.0,
    "idle_private_mb": 700,
    "play_cores": {"1080p30.mp4": 0.6, "4k60.mp4": 1.6},
    "dropped_pct": 3.0,
}
RESULTS, NUMBERS = [], {}
k32, u32, psapi = ctypes.windll.kernel32, ctypes.windll.user32, ctypes.windll.psapi


class PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def handle(pid):
    k32.OpenProcess.restype = wt.HANDLE
    return k32.OpenProcess(0x1000 | 0x0400 | 0x0010, False, pid)


def cpu_s(h):
    c, e, kt, ut = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
    k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut))
    f = lambda t: (t.dwHighDateTime << 32 | t.dwLowDateTime) / 1e7  # noqa: E731
    return f(kt) + f(ut)


def private_mb(h):
    m = PMC(cb=ctypes.sizeof(PMC))
    psapi.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb)
    return m.PrivateUsage / 2**20


def titles(pid) -> list[str]:
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _):
        p = wt.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(512)
            u32.GetWindowTextW(h, buf, 512)
            if buf.value:
                out.append(buf.value)
        return True
    u32.EnumWindows(cb, 0)
    return out


class Run:
    """One isolated data folder and the processes started in it."""

    def __init__(self, exe):
        self.exe = exe
        self.dir = tempfile.mkdtemp(prefix="refrata_launch_")
        with open(os.path.join(self.dir, "settings.json"), "w", encoding="utf8") as f:   # empty library: no scan of the person's folders
            json.dump({"settings": {"folders": [], "quickOpenReset": True, "volume": 0}, "history": []}, f)
        self.procs = []

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if k not in ("REFRATA_E2E_TMP", "REFRATA_TRACE")}
        e.update(REFRATA_DATA=self.dir, REFRATA_NO_HOOK="1", REFRATA_NO_MEDIAKEYS="1", REFRATA_LAUNCH_TEST="1", **extra)
        return e

    def start(self, *args, **extra):
        cmd = [self.exe, *args] if self.exe else [sys.executable, str(ROOT / "main.py"), *args]
        p = subprocess.Popen(cmd, env=self.env(**extra), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def alive(self):
        return [p for p in self.procs if p.poll() is None]

    def close(self):
        for p in self.alive():          # only what this run started, by handle
            p.kill()


def report(ok, name, detail):
    RESULTS.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {detail}", flush=True)


def wait(cond, seconds, step=0.1):
    end = time.time() + seconds
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return cond()


def trace_ms(path, label):
    try:
        for line in open(path, encoding="utf8"):
            if label in line:
                return float(line.split("ms")[0])
    except OSError:
        pass
    return None


def cold_start_and_handover(exe, fx):
    v1, v2 = fx["video"], str(Path(fx["video"]).with_name("Segundo Video.mkv"))
    r = Run(exe)
    try:
        trace = os.path.join(r.dir, "trace.txt")
        first = r.start(v1, REFRATA_TRACE=trace)
        shown = wait(lambda: trace_ms(trace, "first video frame"), 25)
        window = wait(lambda: trace_ms(trace, "first window frame on screen"), 5)
        budget = BUDGET["cold_first_picture_s"]["exe" if exe else "src"]
        NUMBERS["cold start: first picture (s)"] = round(shown / 1000, 2) if shown else None
        NUMBERS["cold start: window on screen (s)"] = round(window / 1000, 2) if window else None
        report(bool(shown) and shown / 1000 <= budget, "cold start with a video shows its picture in time",
               f"picture after {shown / 1000:.2f} s, window after {(window or 0) / 1000:.2f} s (budget {budget} s)" if shown
               else "no picture within 25 s")
        seen = wait(lambda: next((t for t in titles(first.pid) if Path(v1).stem[:10].lower() in t.lower() or "refrata" in t.lower()), ""), 8)
        report(bool(seen), "one window is on screen", repr(seen))

        # a second file, Refrata open: handed over, the second process leaves at once
        t0 = time.perf_counter()
        second = r.start(v2)
        left = wait(lambda: second.poll() is not None, 10, 0.01)
        took = time.perf_counter() - t0
        NUMBERS["second file: hand-over (s)"] = round(took, 2)
        report(bool(left) and second.returncode == 0 and took <= BUDGET["handover_s"],
               "a second file is handed to the open Refrata in time",
               f"{took:.2f} s, exit {second.returncode} (budget {BUDGET['handover_s']} s)")
        playing = wait(lambda: any("segundo video" in t.lower() for t in titles(first.pid)), 12, 0.25)
        report(bool(playing) and len(r.alive()) == 1, "the open Refrata plays the second file; still one Refrata",
               f"{len(r.alive())} running, windows {titles(first.pid)}")
    finally:
        r.close()


def races(exe, fx):
    v1, v2 = fx["video"], str(Path(fx["video"]).with_name("Segundo Video.mkv"))
    for name, launches, gaps, expect in (
            ("a second file while the first Refrata is still starting", [[v1], [v2]], [0.6, 0], "segundo video"),
            ("three launches at once", [[v1], [v2], [v1]], [0.05, 0.05, 0], "refrata")):
        r = Run(exe)
        try:
            for files, gap in zip(launches, gaps):
                r.start(*files)
                time.sleep(gap)
            wait(lambda: len(r.alive()) <= 1, 40, 0.25)
            alive = r.alive()
            others = [p.returncode for p in r.procs if p not in alive]
            seen = wait(lambda: next((t for t in titles(alive[0].pid) if expect in t.lower()), ""), 20, 0.5) if alive else ""
            report(len(alive) == 1 and all(c == 0 for c in others) and bool(seen), name + ": one Refrata, playing the right file",
                   f"{len(alive)} running, others exited {others}, window {seen!r}")
        finally:
            r.close()


def idle(exe):
    r = Run(exe)
    try:
        p = r.start("--new-window")
        h = handle(p.pid)
        time.sleep(9)                    # start, the page warm-up, the first checks
        c0 = cpu_s(h)
        time.sleep(12)
        ms = (cpu_s(h) - c0) / 12 * 1000
        mem = private_mb(h)
        NUMBERS["idle: CPU (ms per second)"] = round(ms, 1)
        NUMBERS["idle: private memory (MB)"] = round(mem)
        report(p.poll() is None and ms <= BUDGET["idle_cpu_ms_per_s"], "idle, the app uses almost no CPU",
               f"{ms:.1f} ms of CPU per second (budget {BUDGET['idle_cpu_ms_per_s']})")
        report(mem <= BUDGET["idle_private_mb"], "idle memory within the budget",
               f"{mem:.0f} MB private (budget {BUDGET['idle_private_mb']})")
    finally:
        r.close()


def make_clips():
    PERF.mkdir(parents=True, exist_ok=True)
    for name, (size, fps, rate) in CLIPS.items():
        out = PERF / name
        if not out.exists():
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "20", "-c:v", "libx264", "-preset", "veryfast",
                            "-pix_fmt", "yuv420p", "-b:v", rate, "-c:a", "aac", str(out)], check=True)


def playback(exe, clip, seconds=8):
    r = Run(exe)
    try:
        log, shot = os.path.join(r.dir, "shot.log"), os.path.join(r.dir, "shot.png")
        p = r.start("--screenshot", shot, "--delay", str((seconds + 4) * 1000), "--log", log, str(PERF / clip))
        h = handle(p.pid)
        time.sleep(4)
        c0 = cpu_s(h)
        time.sleep(seconds)
        cores = (cpu_s(h) - c0) / seconds
        try:
            p.wait(40)
        except subprocess.TimeoutExpired:
            pass
        text = open(log, encoding="utf8", errors="replace").read() if os.path.exists(log) else ""
        m = re.search(r"\[shot\].*frames=(\d+) dropped=(\d+) size=(\d+x\d+)", text)
        w = re.search(r"\[warm\] frames=(\d+) dropped=(\d+)", text)
        frames, total_dropped = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        # pictures decoded before the window was up are not stutter: count drops once it has settled
        dropped = total_dropped - int(w.group(2)) if w else total_dropped
        steady = frames - int(w.group(1)) if w else frames
        fps = CLIPS[clip][1]
        pct = 100 * dropped / max(1, steady + dropped)
        NUMBERS[f"{clip}: pictures dropped while starting"] = int(w.group(2)) if w else None
        NUMBERS[f"{clip}: CPU (cores)"] = round(cores, 2)
        NUMBERS[f"{clip}: pictures shown / dropped"] = f"{frames} / {dropped}"
        budget = BUDGET["play_cores"][clip]
        report(frames >= fps * seconds * 0.8, f"{clip} plays at full rate",
               f"{frames} pictures in ~{seconds + 4} s of a {fps} fps video" if m else "no playback report")
        report(cores <= budget, f"{clip} CPU within the budget", f"{cores:.2f} cores (budget {budget})")
        lost = int(w.group(2)) if w else 0
        report(lost <= fps * 0.2, f"{clip} starts from its beginning (nothing played before the window was up)",
               f"{lost} pictures gone by before the window showed (limit {fps * 0.2:.0f})")
        report(pct <= BUDGET["dropped_pct"], f"{clip} drops (almost) no pictures", f"{dropped} dropped, {pct:.1f}% (budget {BUDGET['dropped_pct']}%)")
    finally:
        r.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("-k", default="", help="only: start, races, idle, play")
    a = ap.parse_args()
    if sys.platform != "win32":
        print("launch battery: Windows only for now")
        return
    ready = Path(tempfile.gettempdir()) / "refrata_robust_cache" / "ready.json"
    if not ready.exists():
        raise SystemExit("run tests/robust.py once first (it builds the test media)")
    fx = json.loads(ready.read_text())["fx"]
    exe = str(Path(a.exe).resolve()) if a.exe else ""
    want = lambda n: not a.k or n in a.k   # noqa: E731
    t0 = time.time()
    if want("start"):
        cold_start_and_handover(exe, fx)
    if want("races"):
        races(exe, fx)
    if want("idle"):
        idle(exe)
    if want("play"):
        make_clips()
        for clip in CLIPS:
            playback(exe, clip)
    ok = sum(1 for _, good in RESULTS if good)
    print("\n  measured: " + "; ".join(f"{k} = {v}" for k, v in NUMBERS.items()))
    print(f"\n{ok}/{len(RESULTS)} launch checks passed in {time.time() - t0:.0f}s")
    out = Path(a.report) if a.report else ROOT / "build" / "launch-last.json"
    try:
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps({"results": [{"name": n, "ok": g} for n, g in RESULTS], "numbers": NUMBERS},
                                  indent=1, ensure_ascii=False), encoding="utf8")
    except OSError:
        pass
    sys.exit(0 if ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
