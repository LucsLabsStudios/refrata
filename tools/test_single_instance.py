"""One Refrata per user, however fast files are opened (Windows).

    python tools/test_single_instance.py [path\\to\\Refrata.exe]

1. A video is opened, and while that Refrata is still starting a second video is opened: the second
   launch must hand its file over and exit; one Refrata stays, and it ends up playing the second video.
2. Three launches at once: still one Refrata.
Each run uses its own temporary data folder (never the copy in use).
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = sys.argv[1] if len(sys.argv) > 1 else ""
FX = json.loads((Path(tempfile.gettempdir()) / "refrata_robust_cache" / "ready.json").read_text())["fx"] \
    if (Path(tempfile.gettempdir()) / "refrata_robust_cache" / "ready.json").exists() else {}
u32 = ctypes.windll.user32


def cmd(*files):
    return [EXE, *files] if EXE else [sys.executable, str(ROOT / "main.py"), *files]


def titles(pids) -> dict:
    out = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _):
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids and u32.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(512)
            u32.GetWindowTextW(h, buf, 512)
            if buf.value:
                out.setdefault(pid.value, []).append(buf.value)
        return True
    u32.EnumWindows(cb, 0)
    return out


def run_case(name, launches, gaps, expect_title):
    d = tempfile.mkdtemp(prefix="refrata_single_")
    env = dict(os.environ, REFRATA_DATA=d, REFRATA_NO_HOOK="1", REFRATA_NO_MEDIAKEYS="1")
    procs = []
    for files, gap in zip(launches, gaps):
        procs.append(subprocess.Popen(cmd(*files), env=env))
        time.sleep(gap)
    deadline = time.time() + 40
    while time.time() < deadline and sum(p.poll() is None for p in procs) > 1:
        time.sleep(0.25)
    alive = [p for p in procs if p.poll() is None]
    ok = len(alive) == 1 and all(p.returncode == 0 for p in procs if p not in alive)
    seen = ""
    if alive:
        t_end = time.time() + 20
        while time.time() < t_end:
            seen = " | ".join(t for ts in titles({alive[0].pid}).values() for t in ts)
            if expect_title.lower() in seen.lower():
                break
            time.sleep(0.5)
        ok = ok and expect_title.lower() in seen.lower()
    for p in alive:
        p.kill()
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {len(alive)} Refrata running, others exited "
          f"{[p.returncode for p in procs if p not in alive]}, window: {seen!r}", flush=True)
    return ok


def main():
    if not FX:
        raise SystemExit("run the robustness battery once first (it builds the test media)")
    v1 = FX["video"]
    v2 = str(Path(FX["video"]).with_name("Segundo Video.mkv"))
    results = [
        run_case("second video while the first Refrata is starting", [[v1], [v2]], [0.6, 0], "Segundo Video"),
        run_case("second video after the first one is up", [[v1], [v2]], [8.0, 0], "Segundo Video"),
        run_case("three launches at once", [[v1], [v2], [v1]], [0.05, 0.05, 0], "Refrata"),
    ]
    print("all ok" if all(results) else "FAILED")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
