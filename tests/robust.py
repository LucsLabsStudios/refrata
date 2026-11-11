"""Robustness suite: every scenario runs in its own Refrata process and leaves through the real
quit path (aboutToQuit → cleanup → hard_exit), so crashes at exit, corrupted data, stress and
odd media all show up the way a user would hit them.

    python tests/robust.py                     # everything (source tree)
    python tests/robust.py -k quit,corrupt     # scenarios whose name contains any of these
    python tests/robust.py --exe dist/Refrata/Refrata.exe   # against the frozen build
    python tests/robust.py --monkey-seconds 300 --seed 7

Each scenario passes when: the process exits with code 0 in time, cleanup raised nothing,
crash.log is empty, no Qt/QML warning was printed, settings.json is still valid JSON, and the
scenario's own checks (inside the app, and on the data it left behind) hold.

Inside the app the module is entered through `main.py --scenario NAME` (see child()).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCENARIOS: dict[str, "Scenario"] = {}


class Fail(Exception):
    pass


# Scenarios that need the keyboard focus (Qt hands shortcuts only to the active window) or that
# register system-wide hooks run alone, after the parallel batch (see main_cli).
SOLO_MARKERS = ("keyClick", "keyPress", "keySequence", "mediakeys")


class Scenario:
    def __init__(self, fn, setup=None, verify=None, timeout=90, data=None, env=None, solo=None):
        self.fn, self.setup, self.verify, self.timeout = fn, setup, verify, timeout
        if solo is None:
            import inspect
            try:
                src = inspect.getsource(fn)
            except OSError:   # inside the packaged app there is no source; only the runner needs this
                src = ""
            solo = any(m in src for m in SOLO_MARKERS) or "mediakeys" in fn.__name__
        self.solo = solo
        self.env = env or {}
        self.name = fn.__name__
        self.data = data   # extra settings for settings.json, merged before launch


def scenario(setup=None, verify=None, timeout=90, data=None, env=None, solo=None):
    def deco(fn):
        SCENARIOS[fn.__name__] = Scenario(fn, setup, verify, timeout, data, env, solo)
        return fn
    return deco


def check(cond, what, detail=""):
    if not cond:
        raise Fail(what + (f" — {detail}" if detail else ""))


# ============================================================ inside the app
class H:
    """Handles for scenario code running inside the app process."""
    parts = None
    fx: dict = {}
    fmt: dict = {}
    notes: list = []
    errors: list = []
    heartbeat: float = 0.0


def pump(seconds=0.05):
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance()
    end = time.time() + seconds
    while time.time() < end:
        H.heartbeat = time.time()
        app.processEvents()
        time.sleep(0.004)


def wait(cond, timeout=10.0, what="condition"):
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance()
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.01)
    raise Fail(f"timeout waiting for {what}")


def eng():
    return H.parts.engine


def win():
    return H.parts.win


def quick_window():
    from PySide6.QtGui import QWindow
    return H.parts.qml.rootObjects()[0].findChild(QWindow, "quickPlayer")


def open_quick(path, mode):
    from PySide6.QtCore import QMetaObject, Q_ARG
    from refrata import quicklook as Q
    QMetaObject.invokeMethod(quick_window(), "open", Q_ARG("QVariant", str(path)),
                             Q_ARG("QVariant", Q.media_siblings(str(path))), Q_ARG("QVariant", mode))


def rss_mb() -> float:
    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    ctypes.windll.psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return pmc.PrivateUsage / 1048576


def picture_problem(e=None) -> str:
    """What is wrong with the picture on screen right now ("" if it shows a real picture). Frames
    arriving is not enough: after a GPU-decoded item every picture came through empty, a green
    screen (YUV all zero), while the frame count looked healthy."""
    e = e or eng()
    f = e._last_frame
    if f is None:
        return "no picture yet"
    img = f.toImage()
    if img.isNull():
        return "the frame does not convert to an image"
    w, h = img.width(), img.height()
    px = [img.pixelColor(int(w * x / 6), int(h * y / 5)) for x in range(1, 6) for y in range(1, 5)]
    rgb = [(c.red(), c.green(), c.blue()) for c in px]
    if all(r < 12 and b < 12 and 110 < g < 160 for r, g, b in rgb):
        return f"green screen (an empty YUV picture): {rgb[0]}"
    if max(max(abs(a - b) for a, b in zip(rgb[0], c)) for c in rgb) < 6:
        return f"a flat picture, one colour everywhere: {rgb[0]}"
    return ""


def _final_picture_check():
    """Every scenario: a video still playing at the end must show a real picture."""
    e = eng()
    if e.state in ("playing", "paused") and not e.isAudio and e._frames_seen > 5 and e.castingTo == "":
        t_end = time.time() + 2
        while picture_problem(e) and time.time() < t_end:
            pump(0.05)
        problem = picture_problem(e)
        check(not problem, f"at the end, the video on screen shows a picture ({Path(e.currentPath).name})", problem)


def playing_media(timeout=10, video=None):
    e = eng()
    if video is None:
        cond = lambda: e.state == "playing" and (e.isAudio or e._frames_seen > 2)
    elif video:
        cond = lambda: e.state == "playing" and e._frames_seen > 2
    else:
        cond = lambda: e.state == "playing" and e.time > 300
    wait(cond, timeout, f"media playing ({Path(e.currentPath).name})")
    if video or (video is None and not e.isAudio):
        # and the picture is a picture (the test videos are colourful test patterns)
        t_end = time.time() + 3
        while picture_problem(e) and time.time() < t_end:
            pump(0.05)
        problem = picture_problem(e)
        check(not problem, f"the picture shows ({Path(e.currentPath).name})", problem)


def child(name: str):
    """Entry point inside the app: build it like main() does, run the scenario, quit for real."""
    import main
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    sc = SCENARIOS[name]
    out = Path(os.environ["REFRATA_SCENARIO_OUT"])
    # a hang shows up as a timeout: a watchdog thread writes every thread's stack when the
    # event loop stops answering (pump() beats the heart)
    hang_file = Path(os.environ["REFRATA_DATA"]) / "hang.txt"

    def watchdog():
        import sys as _s
        reported = False
        while True:
            time.sleep(2)
            if time.time() - H.heartbeat > 8 and not reported:
                reported = True
                lines = [f"event loop silent for {time.time() - H.heartbeat:.0f}s\n"]
                for tid, frame in _s._current_frames().items():
                    lines.append(f"\n--- thread {tid}\n")
                    lines += traceback.format_stack(frame)
                hang_file.write_text("".join(lines), encoding="utf8")
    H.heartbeat = time.time()
    threading.Thread(target=watchdog, daemon=True, name="hang-watchdog").start()
    # and a native one (needs no GIL): every thread's Python stack shortly before the kill
    import faulthandler
    limit = float(os.environ.get("REFRATA_SCENARIO_TIMEOUT", sc.timeout))
    H.hang_dump = open(Path(os.environ["REFRATA_DATA"]) / "hang_native.txt", "w")
    faulthandler.dump_traceback_later(max(10, limit - 15), exit=False, file=H.hang_dump)
    # and a native one (needs no GIL): every thread's Python stack shortly before the kill
    import faulthandler
    limit = float(os.environ.get("REFRATA_SCENARIO_TIMEOUT", sc.timeout))
    H.hang_dump = open(Path(os.environ["REFRATA_DATA"]) / "hang_native.txt", "w")
    faulthandler.dump_traceback_later(max(10, limit - 15), exit=False, file=H.hang_dump)
    result = {"name": name, "ok": False, "error": "", "cleanup_error": "", "qt": [], "notes": H.notes,
              "pyerrors": H.errors}

    def excepthook(t, v, tb):   # exceptions inside Qt slots / QML-invoked Python
        H.errors.append("".join(traceback.format_exception(t, v, tb))[-1500:])
        sys.__excepthook__(t, v, tb)
    sys.excepthook = excepthook

    def write():
        result["qt"] = list(main.QT_MESSAGES)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf8")

    try:
        H.fx = {k: (Path(v) if isinstance(v, str) else v) for k, v in json.loads(os.environ["REFRATA_FX"]).items()}
        H.fmt = json.loads(os.environ["REFRATA_FMT"])
        app = QApplication.instance() or QApplication(sys.argv)
        H.parts = main.build(sys.argv, instance_name=None)
        H.parts.win.setWidth(1280)
        H.parts.win.setHeight(800)
        if not eng().muted:
            eng().toggleMute()           # silent
        H.parts.library.rescan()

        def on_quit():
            print("[robust] quitting: cleanup starts", flush=True)
            try:
                H.parts.cleanup()
                print("[robust] cleanup done", flush=True)
            except Exception:
                result["cleanup_error"] = traceback.format_exc()[-2000:]
            write()
        app.aboutToQuit.connect(on_quit)
        pump(0.3)
        sc.fn()
        _final_picture_check()
        result["ok"] = True
    except Exception as exc:
        result["error"] = (str(exc) if isinstance(exc, Fail) else traceback.format_exc())[-3000:]
        if H.parts:
            e = H.parts.engine
            result["error"] += f"\n  state={e.state} path={Path(e.currentPath).name} time={e.time}/{e.length}"
    write()
    if H.parts is None:
        main.hard_exit(3)
    QTimer.singleShot(0, QApplication.instance().quit)
    code = QApplication.instance().exec()
    main.hard_exit(code)


# ============================================================ scenarios: quit in every state
def _resume_saved(min_ms=10_000):
    def verify(data: Path):
        s = json.loads((data / "settings.json").read_text(encoding="utf8"))
        hist = s.get("history", [])
        check(hist and hist[0].get("pos", 0) >= min_ms, "resume position saved at quit",
              str(hist[:1]))
    return verify


@scenario()
def quit_idle():
    pass


@scenario()
def quit_while_loading_video():
    eng().openFiles([str(H.fx["video"])])     # quit before libVLC even reports a state


@scenario(verify=_resume_saved())
def quit_playing_video():
    e = eng()
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    e.seek(15_000)
    wait(lambda: e.time >= 14_000, 5, "seek")


@scenario(verify=_resume_saved())
def quit_paused_video():
    e = eng()
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    e.seek(15_000)
    wait(lambda: e.time >= 14_000, 5)
    e.pause()
    wait(lambda: e.state == "paused", 3, "paused")


@scenario()
def quit_ended_media():
    e = eng()
    e.openFiles([str(H.fx["tone_a"])])
    wait(lambda: e.state == "ended", 10, "track ends")


@scenario()
def quit_music_mode_after_track_switch():
    """The 1.3.0 exit crash: music mode, seek, switch track, quit."""
    e = eng()
    e.openFiles([str(H.fx["tone_long"]), str(H.fx["tone_alt"]), str(H.fx["tone_a"])], 1)
    playing_media(video=False)
    win().setProperty("playerOpen", True)
    pump(0.8)
    e.seek(e.length // 2)
    pump(0.4)
    e.next(); pump(0.6)
    e.previous(); pump(0.6)
    e.playAt(0)
    playing_media(video=False)
    pump(0.5)


@scenario()
def quit_fullscreen_video():
    e = eng()
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    win().setProperty("playerOpen", True)
    from PySide6.QtGui import QWindow
    H.parts.win.showFullScreen()
    pump(0.8)
    check(H.parts.win.visibility() == QWindow.Visibility.FullScreen, "went fullscreen")


@scenario()
def quit_with_equalizer_and_adjustments():
    e = eng()
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    e.setEqEnabled(True)
    e.setEqPreset(3)
    e.setAdjust("brightness", 1.3)
    e.setAdjust("contrast", 1.2)
    e.rate = 1.5
    pump(0.6)


@scenario()
def quit_quick_player_compact():
    q = quick_window()
    open_quick(H.fx["video"], "compact")
    wait(lambda: q.isVisible(), 4, "quick player open")
    wait(lambda: q.findChild(__import__("PySide6.QtCore", fromlist=["QObject"]).QObject, "quickSeek").property("value") > 800,
         8, "quick player plays")


@scenario()
def quit_quick_player_preview_over_music():
    e = eng()
    e.openFiles([str(H.fx["tone_long"])])
    playing_media(video=False)
    q = quick_window()
    open_quick(H.fx["video"], "preview")
    wait(lambda: q.isVisible() and e.state == "paused", 5, "preview open, music paused")


def _big_library(data: Path, count=2500, videos=120):
    """count audio + videos hard links: a big library on disk at almost no cost."""
    # built once in the shared cache (NTFS allows 1023 links per file: one source copy per 800)
    cache = Path(os.environ["REFRATA_ROBUST_CACHE"])
    lib = cache / "biglib"
    done = cache / "biglib.ready"
    if done.exists():
        return lib
    shutil.rmtree(lib, ignore_errors=True)
    srcs = cache / "biglib_src"
    srcs.mkdir(parents=True, exist_ok=True)
    fx = json.loads(os.environ["REFRATA_FX"])

    def src(kind, i):
        orig = Path(fx["tone_a"] if kind == "a" else fx["video"])
        p = srcs / f"{kind}{i // 800}{orig.suffix}"
        if not p.exists():
            shutil.copyfile(orig, p)
        return p
    for i in range(count):
        d = lib / "Musicas" / f"Artista {i // 50:03d}" / f"Album {i // 10 % 5}"
        d.mkdir(parents=True, exist_ok=True)
        os.link(src("a", i), d / f"{i:05d} Faixa {i}.wav")
    for i in range(videos):
        d = lib / "Filmes" / f"Serie {i // 20}"
        d.mkdir(parents=True, exist_ok=True)
        os.link(src("v", i), d / f"Episodio {i:03d}.mkv")
    done.write_text("ok")
    return lib


def _setup_big(data: Path):
    lib = _big_library(data)
    return {"folders": [str(lib)]}


@scenario(setup=_setup_big, timeout=120)
def quit_during_library_scan():
    lib = H.parts.library
    lib.rescan()
    pump(0.05)          # scan thread is walking the folders right now


@scenario(setup=_setup_big, timeout=120)
def quit_during_thumbnails():
    lib = H.parts.library
    wait(lambda: not lib._scanning and lib._model.rowCount() > 2000, 60, "scan finished")
    wait(lambda: lib._thumbs._q.qsize() > 0, 5, "thumbnails queued")
    pump(1.5)           # the thumbnailer is mid-file


@scenario(timeout=60)
def quit_during_seek_storm():
    e = eng()
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    rnd = random.Random(1)
    for _ in range(150):
        e.seek(rnd.randint(0, 29_000))
        pump(0.005)


@scenario(timeout=60)
def quit_playing_http_stream():
    import functools, http.server, socketserver
    root = H.fx["media_root"]
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))

    class Quiet(handler.func):  # type: ignore[misc]
        def log_message(self, *a):
            pass
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    e = eng()
    e.openUrl(f"http://127.0.0.1:{srv.server_address[1]}/Filmes/Segundo%20Video.mkv")
    playing_media(video=True)


# ============================================================ scenarios: stress
@scenario(timeout=120)
def stress_next_prev_storm():
    e = eng()
    items = [str(H.fx[k]) for k in ("tone_a", "video", "tone_b", "corrupt", "video2", "tone_c")] * 8
    e.openFiles(items)
    rnd = random.Random(2)
    for i in range(300):
        rnd.choice((e.next, e.previous, lambda: e.playAt(rnd.randrange(len(items)))))()
        pump(0.003 if i % 10 else 0.05)
    e.playAt(1)     # a good video
    playing_media(video=True, timeout=10)
    check(e.currentIndex == 1, "ends on the requested item")


@scenario(timeout=120)
def feature_every_way_to_switch_videos_keeps_the_picture():
    """Each way of going from a video that is showing to another (or the same one again), the
    new one shows its picture. After a GPU-decoded video every following one came out green with
    the sound playing, whatever the switch; a fast storm of switches never let a picture settle,
    so it passed."""
    e, w = eng(), win()
    a, b = str(H.fx["video"]), str(H.fx["video2"])
    w.setProperty("playerOpen", True)
    e.openFiles([a, b])
    playing_media(video=True)
    steps = [
        ("Next", lambda: e.next(), b),
        ("Previous", lambda: (e.seek(0), e.previous()), a),
        ("a click in the queue", lambda: e.playAt(1), b),
        ("opening another file", lambda: e.openFiles([a]), a),
        ("the same file again, at a point", lambda: e.openFilesAt([a], 0, 6000), a),
        ("pause, then another file", lambda: (e.pause(), pump(0.3), e.openFiles([b])), b),
    ]
    for what, go, expect in steps:
        playing_media(video=True)
        pump(0.8)                                 # the current one really is on screen
        go()
        wait(lambda: Path(e.currentPath).name == Path(expect).name, 5, f"{what}: switched")
        playing_media(video=True)
        H.notes.append(f"{what}: picture ok")
    # the end of a video moves on to the next one by itself
    e.openFiles([a, b])
    playing_media(video=True)
    e.seek(max(0, e.length - 1500))
    wait(lambda: Path(e.currentPath).name == Path(b).name, 8, "the end of a video moves on to the next")
    playing_media(video=True)
    # and with hardware decoding off
    H.parts.settings.setValue("hwdec", False)
    pump(1.5)
    e.openFiles([a, b])
    playing_media(video=True)
    pump(0.8)
    e.next()
    wait(lambda: Path(e.currentPath).name == Path(b).name, 5)
    playing_media(video=True)


@scenario(timeout=90)
def stress_open_while_loading():
    e = eng()
    files = [str(H.fx["video"]), str(H.fx["tone_a"]), str(H.fx["video2"])]
    for i in range(120):
        e.openFiles([files[i % 3]])
        pump(0.002)
    playing_media(timeout=10)
    check(e.currentPath == files[119 % 3], "the last file opened is the one playing", e.currentPath)


@scenario(timeout=120)
def stress_queue_of_1000():
    e = eng()
    base = [str(H.fx[k]) for k in ("tone_a", "tone_b", "tone_c", "video")]
    t0 = time.time()
    e.openFiles(base * 250)
    took = time.time() - t0
    check(took < 3, "1000 items queued quickly", f"{took:.2f}s")
    playing_media()
    win().setProperty("page", 3)      # queue page renders 1000 rows
    pump(1.0)
    t0 = time.time()
    for i in range(200):
        e.moveItem(999 - i, i)
    for i in range(100):
        e.removeAt(500)
    check(len(e.queue) == 900, "removals applied", str(len(e.queue)))
    check(time.time() - t0 < 6, "reorder/remove stays fast", f"{time.time() - t0:.2f}s")
    e.clearQueue()
    pump(0.3)
    check(not e.hasMedia, "queue cleared")


@scenario(setup=_setup_big, timeout=150)
def stress_big_library_scan_and_search():
    lib = H.parts.library
    t0 = time.time()
    wait(lambda: not lib._scanning and lib._model.rowCount() >= 2600, 60, "big scan done")
    took = time.time() - t0
    H.notes.append(f"scan of {lib._model.rowCount()} files: {took:.1f}s")
    check(took < 30, "2620 files scanned in under 30 s", f"{took:.1f}s")
    # typing in search must not freeze the UI: filter on every keystroke
    win().setProperty("page", 0)
    pump(0.3)
    field = H.parts.find("homeSearch")
    worst = 0.0
    for txt in ("e", "ep", "epi", "episodio 1", "faixa 12", "artista 004", "a", ""):
        t = time.time()
        field.setProperty("text", txt)   # what typing in the home search box does
        lib.videos.setProperty("search", txt)
        pump(0.01)
        worst = max(worst, time.time() - t)
    H.notes.append(f"slowest search keystroke: {worst:.2f}s")
    check(worst < 1.0, "search answers fast", f"{worst:.2f}s")
    field.setProperty("text", "a")
    pump(0.3)
    check(H.parts.find("searchMore").isVisible(), "thousands of matches: only the first ones are built")
    for page in (0, 1, 2, 0):
        win().setProperty("page", page)
        t = time.time()
        pump(0.3)
        check(time.time() - t < 2.5, f"page {page} renders without freezing")


@scenario(setup=lambda d: {"folders": [str(_vanishing(d))]}, timeout=90)
def stress_files_vanish_during_use():
    lib, e = H.parts.library, eng()
    wait(lambda: not lib._scanning and lib._model.rowCount() >= 30, 30, "scan")
    root = Path(H.parts.settings.value("folders")[0])
    victims = sorted(root.rglob("*.wav"))[:10]
    for v in victims:
        v.unlink()
    (root / "renomeada").mkdir(exist_ok=True)
    for v in sorted(root.rglob("*.mkv"))[:3]:
        v.rename(root / "renomeada" / v.name)
    e.openFiles([str(victims[0])])      # stale library entry
    pump(1.5)
    check(e.state in ("error", "idle", "stopped", "ended") or not e.hasMedia, "deleted file reported, not played", e.state)
    lib.rescan()
    wait(lambda: not lib._scanning, 30)
    pump(0.5)
    e.openFiles([str(H.fx["tone_a"])])
    playing_media(video=False)


def _vanishing(data: Path) -> Path:
    root = data.parent / "vanish"
    fx = json.loads(os.environ["REFRATA_FX"])
    for i in range(40):
        d = root / f"d{i % 4}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fx["tone_a"], d / f"musica {i}.wav")
    for i in range(6):
        shutil.copyfile(fx["video"], root / f"video {i}.mkv")
    return root


@scenario(timeout=240)
def stress_memory_track_changes():
    e = eng()
    seq = [str(H.fx["video"]), str(H.fx["tone_a"]), str(H.fx["video2"]), str(H.fx["tone_long"])]
    e.openFiles(seq)
    win().setProperty("playerOpen", True)
    playing_media()
    for i in range(20):                       # warm-up: caches, QML components, codecs
        e.playAt(i % 4); pump(0.25)
    import gc
    gc.collect(); pump(1.0)
    base = rss_mb()
    for i in range(200):
        e.playAt(i % 4)
        pump(0.15)
    gc.collect(); pump(1.5)
    grown = rss_mb() - base
    H.notes.append(f"memory after 200 switches: +{grown:.0f} MB (base {base:.0f} MB)")
    check(grown < 120, "no runaway memory growth across 200 track changes", f"+{grown:.0f} MB")


# ============================================================ scenarios: corrupted data
def _write(data: Path, name: str, content: bytes | str):
    p = data / name
    p.write_bytes(content if isinstance(content, bytes) else content.encode("utf8"))


@scenario(setup=lambda d: _write(d, "settings.json", b"\x00\xff{{ not json"))
def corrupt_settings_garbage():
    e = eng()
    e.openFiles([str(H.fx["tone_a"])])
    playing_media(video=False)


@scenario(setup=lambda d: _write(d, "settings.json", ""))
def corrupt_settings_empty():
    e = eng()
    e.openFiles([str(H.fx["tone_a"])])
    playing_media(video=False)


@scenario(setup=lambda d: _write(d, "settings.json", json.dumps({
    "settings": {"volume": "alto", "folders": None, "theme": 7, "accent": "not-a-color", "resume": "sim",
                 "language": ["pt"], "hwdec": None, "snapshotDir": 3},
    "history": {"oops": 1}})))
def corrupt_settings_wrong_types():
    e = eng()
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    for p in (0, 1, 2, 5):
        win().setProperty("page", p)
        pump(0.3)


@scenario(setup=lambda d: _write(d, "settings.json", json.dumps({"settings": [], "history": [1, "x", None, {"path": 5}]})))
def corrupt_settings_wrong_shapes():
    e = eng()
    e.openFiles([str(H.fx["tone_a"])])
    playing_media(video=False)
    win().setProperty("page", 0)
    pump(0.5)


@scenario(setup=lambda d: _write(d, "library.json", "[1, 2, 3]"))
def corrupt_library_cache_shape():
    lib = H.parts.library
    wait(lambda: not lib._scanning and lib._model.rowCount() > 3, 20, "library scanned despite bad cache")


@scenario(setup=lambda d: _write(d, "library.json", json.dumps({"C:/x.mkv": "oops", "y": {"mtime": "z", "thumb": 5}})))
def corrupt_library_cache_entries():
    lib = H.parts.library
    wait(lambda: not lib._scanning and lib._model.rowCount() > 3, 20, "library scanned despite bad entries")


def _readonly(d: Path):
    _write(d, "settings.json", json.dumps({"settings": {"folders": [str(Path(json.loads(os.environ["REFRATA_FX"])["media_root"]))]},
                                           "history": []}))
    _write(d, "library.json", "{}")
    for n in ("settings.json", "library.json"):
        os.chmod(d / n, 0o444)


@scenario(setup=_readonly)
def corrupt_readonly_data_files():
    e = eng()
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    H.parts.settings.setValue("volume", 55)
    pump(1.2)     # the 800 ms save timer fires against a read-only file


# ============================================================ scenarios: media matrix
def _formats():
    f = H.fmt
    return f


@scenario(timeout=300)
def media_every_format_plays():
    e = eng()
    bad = []
    for it in _formats()["video"] + _formats()["audio"]:
        p, size, nm = it["path"], it["size"], it["name"]
        e.openFiles([p])
        try:
            if size:
                wait(lambda: e.state == "playing" and e._frames_seen > 3, 12, nm)
                exp_w, exp_h = size       # full size, up to 4K
                # some demuxers (Ogg) describe the visible size only after ~2 s of playback;
                # until then the decoder's padding rows can show
                try:
                    wait(lambda: abs(e.videoWidth - exp_w) <= 4 and abs(e.videoHeight - exp_h) <= 4, 5, "size")
                except Fail:
                    pass
                w, h = e.videoWidth, e.videoHeight
                if abs(w - exp_w) > 4 or abs(h - exp_h) > 4:
                    bad.append(f"{nm}: frame {w}x{h}, expected {exp_w}x{exp_h}")
            else:
                wait(lambda: e.state == "playing" and e.time > 400, 12, nm)
            # libVLC's clock moves in container-sized steps (a cluster, an Ogg page): ~1 s
            t0 = e.time
            try:
                wait(lambda: e.time > t0, 3.0, "clock")
            except Fail:
                bad.append(f"{nm}: clock does not advance ({t0} ms)")
            if e.length <= 0 and not (it.get("length", 1) == 0 and not size):
                bad.append(f"{nm}: no duration")
            elif e.length <= 0:
                H.notes.append(f"{nm}: the file declares no length (shown as live)")
        except Fail as exc:
            bad.append(f"{nm}: {exc} (state={e.state})")
    e.clearQueue()
    check(not bad, "every format plays with the right picture, clock and duration", "; ".join(bad))


@scenario(timeout=120)
def media_broken_files_are_reported():
    e = eng()
    msgs = []
    e.message.connect(msgs.append)
    for kind, p in _formats()["bad"].items():
        msgs.clear()
        e.openFiles([p])
        t0 = time.time()
        while time.time() - t0 < 10 and e.state not in ("error",) and not (e.state == "playing" and e.time > 500):
            pump(0.1)
        playable = kind in ("no_extension", "wrong_extension", "truncated")
        if playable:
            check(e.state in ("playing", "error", "ended"), f"{kind}: settles (plays or reports)", e.state)
        else:
            check(e.state == "error" or msgs, f"{kind}: the user is told it does not open", f"{e.state} {msgs}")
        H.notes.append(f"{kind}: {e.state}")
    # the app is still fine afterwards
    e.openFiles([str(H.fx["tone_a"])])
    playing_media(video=False)


@scenario(timeout=150)
def media_unusual_names_and_long_path():
    e = eng()
    bad = []
    paths = list(_formats()["names"]) + ([_formats()["long_path"]] if _formats().get("long_path") else [])
    for p in paths:
        e.openFiles([p])
        try:
            wait(lambda: e.state == "playing" and e._frames_seen > 2, 10, Path(p).name[:40])
        except Fail:
            bad.append(f"{Path(p).name[:50]} ({len(p)} chars): {e.state}")
    check(not bad, "files with any name and long paths play", "; ".join(bad))


@scenario(timeout=150)
def media_subtitle_formats():
    e = eng()
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    bad = []
    for kind, p in _formats()["subs"].items():
        before = len(e.subtitleTracks)
        e.addSubtitle(Path(p).as_uri())
        try:
            wait(lambda: len(e.subtitleTracks) > before, 6, kind)
        except Fail:
            bad.append(f"{kind}: no new track ({before} → {len(e.subtitleTracks)})")
    check(not bad, "srt / vtt / ass / latin-1 srt load as tracks", "; ".join(bad))


@scenario(timeout=200)
def media_library_reads_every_format():
    lib = H.parts.library
    H.parts.settings.setValue("folders", [str(Path(_formats()["video"][0]["path"]).parent),
                                          str(Path(_formats()["audio"][0]["path"]).parent)])
    wait(lambda: not lib._scanning and lib._model.rowCount() >= 15, 30, "format folders scanned")
    def done():
        want = [it for it in _formats()["video"] + _formats()["audio"] if it.get("length", 1) or it["size"]]
        rows = [lib._model.item(str(it["path"])) for it in want]
        return all(r and r.get("duration", 0) > 0 for r in rows)
    try:
        wait(done, 120, "durations for every format")
    except Fail:
        pass
    missing = []
    for it in _formats()["video"] + _formats()["audio"]:
        r = lib._model.item(str(it["path"]))
        if not r:
            missing.append(f"{it['name']}: not in library")
        elif r.get("duration", 0) <= 0 and not (it.get("length", 1) == 0 and not it["size"]):
            missing.append(f"{it['name']}: no duration")
        elif it["size"] and not r.get("thumb"):
            missing.append(f"{it['name']}: no thumbnail")
    check(not missing, "library knows duration (and thumbnail) of every format", "; ".join(missing))


# ============================================================ scenarios: features
def _frame_image():
    f = eng()._last_frame
    check(f is not None, "a video frame is on screen")
    return f.toImage().scaled(480, 270)


def _count(img, pred, top=0.55):
    n = 0
    for y in range(int(img.height() * top), img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if pred(c.red(), c.green(), c.blue()):
                n += 1
    return n


def _subtitle_frame():
    """A frame from a stretch where the test subtitle is on screen."""
    e = eng()
    e.seek(6000)
    wait(lambda: 5800 <= e.time < 9000, 5, "seek into the subtitle")
    n0 = e._frames_seen
    wait(lambda: e._frames_seen > n0 + 4, 5, "fresh frames")
    return _frame_image()


@scenario(timeout=120)
def feature_subtitle_style_and_rebuild():
    e = eng()
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    white = lambda r, g, b: r > 215 and g > 215 and b > 215
    yellow = lambda r, g, b: r > 200 and g > 170 and b < 120
    # the test video has white text of its own: compare with the same stretch without a subtitle
    base = _count(_subtitle_frame(), white, top=0.72)
    e.addSubtitle(Path(H.fx["srt"]).as_uri())
    wait(lambda: e.subtitleTrack not in (-1, 0), 8, "subtitle selected")
    playing_media(video=True)
    normal = _count(_subtitle_frame(), white, top=0.72)
    check(normal > base + 30, "the subtitle is drawn on the picture (GPU decoding hands over to software)",
          f"without {base}, with {normal}")
    t_before = e.time
    e.setSubtitleStyle("subSize", "huge")
    playing_media(video=True)
    wait(lambda: e.time > 0, 4, "clock after the rebuild")
    check(abs(e.time - t_before) < 4000, "a new subtitle style keeps the playback position", f"{t_before} → {e.time}")
    wait(lambda: e.subtitleTrack not in (-1, 0), 6, "hand-added subtitle back on after the rebuild")
    huge = _count(_subtitle_frame(), white, top=0.72)
    check(huge > normal * 1.3, "bigger subtitle covers more of the picture", f"{normal} → {huge}")
    e.setSubtitleStyle("subColor", "yellow")
    playing_media(video=True)
    wait(lambda: e.subtitleTrack not in (-1, 0), 6)
    ys = _count(_subtitle_frame(), yellow, top=0.72)
    check(ys > 30, "yellow subtitles are yellow", str(ys))
    e.setSubtitleStyle("subBackground", "box")
    playing_media(video=True)
    e.setSubtitleStyle("subEncoding", "Windows-1252")
    playing_media(video=True)
    check("--subsdec-encoding=Windows-1252" in e._vlc_args, "encoding goes to libVLC")
    e.pause()
    wait(lambda: e.state == "paused", 3)
    e.setSubtitleStyle("subSize", "normal")
    wait(lambda: e.state == "paused" and e.time > 0, 8, "a rebuild while paused stays paused")


@scenario(timeout=90)
def feature_normalizer_and_hwdec_live():
    e, s = eng(), H.parts.settings
    e.openFiles([str(H.fx["tone_long"])])
    playing_media(video=False)
    e.seek(20_000)
    wait(lambda: e.time >= 19_500, 4)
    e.setNormalize(True)
    check(any("normvol" in a for a in e._vlc_args), "normalizer is a libVLC audio filter")
    wait(lambda: e.state == "playing" and e.time >= 19_000, 8, "playback carries on after turning it on")
    check(e.normalize, "normalize reads back on")
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    s.setValue("hwdec", False)
    wait(lambda: "--avcodec-hw=none" in e._vlc_args, 3, "hardware decoding off applies right away")
    playing_media(video=True)
    s.setValue("hwdec", True)
    playing_media(video=True)
    e.setNormalize(False)
    playing_media(video=True)


@scenario(timeout=60)
def feature_audio_output_devices():
    e = eng()
    t0 = time.time()
    e.audioDevices                       # the first read never waits for the system...
    check(time.time() - t0 < 0.1, "reading the outputs list does not block the UI", f"{time.time() - t0:.2f}s")
    wait(lambda: not getattr(e, "_devices_busy", False), 15, "the outputs list arrives")   # ...it arrives after
    devs = e.audioDevices
    check(devs and devs[0]["id"] == "", "the list starts with the Windows default", str(devs[:1]))
    H.notes.append(f"{len(devs) - 1} audio output(s) found")
    e.openFiles([str(H.fx["tone_long"])])
    playing_media(video=False)
    if len(devs) > 1:
        e.setAudioDevice(devs[1]["id"])
        check(e.audioDevice == devs[1]["id"], "chosen output is remembered")
        pump(1.0)
        check(e.state == "playing", "playback continues on the new output")
    e.setAudioDevice("")
    check(e.audioDevice == "", "back to the Windows default")
    e.refreshAudioDevices()
    pump(0.5)


@scenario(timeout=60)
def feature_chapters():
    e = eng()
    ch = H.fmt.get("chapters")
    check(ch, "chapter fixture exists")
    e.openFiles([ch])
    playing_media(video=False)
    wait(lambda: len(e.chapters) == 3, 5, "three chapters read")
    check([c["name"] for c in e.chapters] == ["Abertura", "Meio", "Final"], "chapter names", str(e.chapters))
    win().setProperty("playerOpen", True)
    pump(0.6)
    pv = H.parts.find("playerView")
    pv.setProperty("panel", "chapters")
    pump(0.5)
    H.parts.find("chapter_2")
    e.setChapter(2)
    wait(lambda: e.chapter == 2 and e.time >= 7800, 4, "jump to the last chapter")
    e.previousChapter()   # more than 3 s into it? no: goes to the start of this one first
    pump(0.3)
    e.setChapter(1)
    wait(lambda: e.chapter == 1, 3)
    e.nextChapter()
    wait(lambda: e.chapter == 2, 3, "next chapter")
    e.openFiles([str(H.fx["tone_a"])])
    playing_media(video=False)
    check(e.chapters == [] and e.chapter == -1, "chapters cleared for a file without them")


@scenario(timeout=60)
def feature_picture_geometry_and_deinterlace():
    e = eng()
    e.openFiles([str(H.fx["video"]), str(H.fx["video2"])])
    playing_media(video=True)
    win().setProperty("playerOpen", True)
    pump(0.6)
    pv, vo = H.parts.find("playerView"), H.parts.find("videoOutput")
    pv.setProperty("videoRotation", 90)
    pv.setProperty("videoAspect", "4:3")
    pv.setProperty("videoZoom", 1.5)
    pump(0.3)
    check(vo.property("orientation") == 90, "rotation reaches the video output")
    ratio = vo.property("width") / vo.property("height")
    check(abs(ratio - 4 / 3) < 0.02, "4:3 box", f"{ratio:.3f}")
    for mode in ("on", "off", "auto"):
        e.setDeinterlace(mode)
        check(e.deinterlace == mode, f"deinterlace {mode}")
        n0 = e._frames_seen
        wait(lambda: e._frames_seen > n0 + 5 or e._frames_seen < n0, 6, f"frames keep coming ({mode})")
    e.next()
    playing_media(video=True)
    pump(0.3)
    check(pv.property("videoRotation") == 0 and pv.property("videoAspect") == "" and pv.property("videoZoom") == 1,
          "the next file starts with straight picture")


@scenario(timeout=60)
def feature_media_info_and_panels():
    e = eng()
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    pump(0.8)
    info = e.mediaInfo()
    kinds = sorted(t["type"] for t in info["tracks"])
    check(kinds.count("audio") == 2 and "video" in kinds, "tracks listed", str(kinds))
    v = [t for t in info["tracks"] if t["type"] == "video"][0]
    check(v["codec"] == "h264" and v["width"] == 640 and v["height"] == 360, "video codec and size", str(v))
    check(info["container"] == "MKV" and info["size"] > 0 and info["bitrate"] > 0, "container, size, bitrate", str(info)[:300])
    win().setProperty("playerOpen", True)
    pump(0.6)
    pv = H.parts.find("playerView")
    for panel in ("tracks", "speed", "eq", "adjust", "sleep", "info"):
        pv.setProperty("panel", panel)
        pv.setProperty("controlsVisible", True)
        pump(0.45)
        pop = H.parts.find("popover")
        check(pop.property("opacity") > 0.5, f"{panel} panel shows")
        check(pop.property("width") <= win().width() - 90, f"{panel} panel fits the window")
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import QMetaObject, Qt as _Qt
    btn = H.parts.find("infoCopy")
    QGuiApplication.clipboard().clear()
    QMetaObject.invokeMethod(btn, "clicked")
    pump(0.2)
    txt = QGuiApplication.clipboard().text()
    check("H264" in txt and "640 × 360" in txt, "copy puts the details on the clipboard", txt[:200])


@scenario(timeout=60)
def feature_cast_panel_and_quit_while_searching():
    e = eng()
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    win().setProperty("playerOpen", True)
    pump(0.6)
    pv = H.parts.find("playerView")
    e.startCastDiscovery()
    pv.setProperty("panel", "cast")
    pump(0.5)
    check(e.discovering, "looking for Chromecasts on the network")
    H.parts.find("castLocal")
    pump(3.0)
    H.notes.append(f"{len(e.renderers)} cast device(s) on this network")
    e.setRenderer(-1)                 # this computer: nothing changes
    check(e.castingTo == "" and e.state == "playing", "stays on this computer")
    e.setNormalize(True)              # a rebuild while searching starts the search again
    playing_media(video=True)
    check(e.discovering, "search restarted on the new libVLC instance")


@scenario(timeout=90)
def feature_switch_while_casting_path():
    """Track changes while casting run on a worker thread (no real TV: the flag alone takes the
    same path while the picture stays on this PC)."""
    e = eng()
    e._cast_ptr = 1        # pretend a cast target is active
    files = [str(H.fx[k]) for k in ("video", "tone_a", "video2", "tone_long")]
    e.openFiles(files)
    playing_media()
    worst = 0.0
    for i in range(30):
        t = time.time()
        (e.next if i % 3 else e.previous)()
        pump(0.05)
        worst = max(worst, time.time() - t)
    wait(lambda: e._switching == 0, 10, "switches finish")
    playing_media(timeout=10)
    check(worst < 1.0, "the UI never waits on a switch", f"{worst:.2f}s")
    e.playAt(2)
    wait(lambda: e._switching == 0 and e.currentIndex == 2, 8)
    playing_media(video=True)
    e._cast_ptr = None


@scenario(timeout=60)
def feature_mediakeys_overlay():
    """The Windows media session mirrors Refrata (only this scenario turns it on, briefly; no
    real key is pressed: the person's own media keys are left alone)."""
    mk = H.parts.mediakeys
    if mk is None or not mk.available:
        H.notes.append("Windows media controls not available on this machine")
        return
    from winrt.windows.media import SystemMediaTransportControlsButton as B
    e = eng()
    e.openFiles([str(H.fx["tone_long"]), str(H.fx["video"])])
    playing_media(video=False)
    wait(lambda: mk.snapshot().get("enabled") and mk.snapshot().get("status") == 3, 4, "session shows playing")
    snap = mk.snapshot()
    check(snap["type"] == "music" and snap["title"], "music session with a title", str(snap))
    mk._act(int(B.PAUSE))
    wait(lambda: e.state == "paused", 3, "pause button pauses")
    wait(lambda: mk.snapshot().get("status") == 4, 3, "session shows paused")
    mk._act(int(B.PLAY))
    wait(lambda: e.state == "playing", 3, "play button plays")
    mk._act(int(B.NEXT))
    playing_media(video=True)
    wait(lambda: mk.snapshot().get("type") == "video", 4, "video session after next")
    mk._act(int(B.PREVIOUS))
    wait(lambda: e.currentIndex == 0, 4, "previous")
    e.clearQueue()
    wait(lambda: not mk.snapshot().get("enabled"), 3, "session hidden when nothing plays")


def _overlay_cover_bytes(mk) -> int:
    """How many bytes Windows gets when it reads the cover the media overlay was given."""
    import asyncio
    ref = mk._smtc.display_updater.thumbnail
    if ref is None:
        return 0
    out = []

    async def read():
        return (await ref.open_read_async()).size

    def work():
        try:
            out.append(asyncio.run(read()))
        except Exception as exc:
            out.append(f"unreadable: {exc}")
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(10)
    return out[0] if out else 0


@scenario(timeout=90)
def feature_mediakeys_cover():
    """The media overlay shows the cover of what plays: the library's thumbnail for a video, kept
    when the next item has the same cover (two songs of one album), dropped when it has none."""
    mk = H.parts.mediakeys
    if mk is None or not mk.available:
        H.notes.append("Windows media controls not available on this machine")
        return
    e, lib = eng(), H.parts.library
    video, video2, tone = str(H.fx["video"]), str(H.fx["video2"]), str(H.fx["tone_long"])
    wait(lambda: lib.thumbFor(video) and lib.thumbFor(video2), 40, "the library made the videos' thumbnails")
    e.openFiles([video, video2, tone])
    playing_media(video=True)
    wait(lambda: mk.snapshot().get("cover"), 6, "the overlay gets a cover for the video")
    size = _overlay_cover_bytes(mk)
    check(isinstance(size, int) and size > 500, "and it is a readable picture", str(size))
    check(mk._art == lib.thumbFor(video), "the video's own thumbnail", mk._art)
    # same cover on the next item (an album): it must not vanish with the title change
    one = lib.thumbFor(video)

    class OneCover:
        def thumbFor(self, path):
            return one
    mk._library = OneCover()
    e.next()
    playing_media(video=True)
    wait(lambda: "Segundo" in (mk.snapshot().get("title") or ""), 4, "the overlay names the next item")
    pump(0.6)
    check(mk.snapshot().get("cover"), "the cover stays when the next item shares it")
    size = _overlay_cover_bytes(mk)
    check(isinstance(size, int) and size > 500, "still a readable picture", str(size))
    # an item without any cover: the old one must not linger
    mk._library = lib
    e.next()
    playing_media(video=False)
    wait(lambda: mk.snapshot().get("type") == "music", 4, "music session")
    pump(0.6)
    check(not lib.thumbFor(tone) and not e.artwork, "the tone has no cover of its own")
    check(not mk.snapshot().get("cover"), "no cover shown for an item without one")


class _FakeTV:
    """Stands in for a Chromecast found on the network. Handed to libVLC it means "no renderer",
    so the picture stays on this PC while the app takes every casting path."""
    _as_parameter_ = None

    def __init__(self, name):
        self._name, self.released = name, 0

    def name(self):
        return self._name.encode("utf8")

    def type(self):
        return b"chromecast"

    def release(self):
        self.released += 1


@scenario(timeout=120)
def feature_cast_to_a_tv_and_back_by_mouse():
    """Transmitir, by mouse: a TV that answers the search is listed; clicking it casts from the
    same point, says so on screen and locks PiP; "Este computador" brings it back; a TV that
    leaves the network hands playback back by itself."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    e, w = eng(), win()
    pv = H.parts.find("playerView")
    msgs = []
    e.message.connect(msgs.append)

    def controls(panel=None):
        if panel is not None:
            pv.setProperty("panel", panel)
        QTest.mouseMove(w, QPoint(w.width() // 2, w.height() // 3))
        pump(0.1)
        pv.setProperty("controlsVisible", True)
        pump(0.35)

    def settled():
        wait(lambda: e._switching == 0 and e.state == "playing", 10, "playback settles on the new output")
    e.openFiles([str(H.fx["video"])])
    w.setProperty("playerOpen", True)
    playing_media(video=True)
    controls()
    _click_on(H.parts.find("pvCast"), "cast")
    check(pv.property("panel") == "cast" and e.discovering, "the cast button opens its panel and starts looking")
    check(_visible_item("cast_0") is None, "no TV listed before one answers")
    tv, ptr = _FakeTV("TV da Sala"), 0x7E57
    e._on_renderer((tv, ptr), True)
    pump(0.4)
    check(e.renderers == [{"name": "TV da Sala", "type": "chromecast"}], "the TV that answered is listed", str(e.renderers))
    row = H.parts.find("cast_0")
    check(row.property("text") == "TV da Sala" and not row.property("selected"), "by its name, not yet chosen", row.property("text"))
    check(H.parts.find("castLocal").property("selected"), "this computer is the one marked")
    wait(lambda: e.time > 2500, 8, "a few seconds in")
    at = e.time
    controls("cast")
    _click_on(row, "the TV")
    check(e.castingTo == "TV da Sala", "clicking the TV casts to it", e.castingTo)
    check(any("TV da Sala" in m for m in msgs), "and says so", str(msgs))
    settled()
    check(e.time >= at - 1500, "the video goes on from where it was, not from the start", f"{at} -> {e.time}")
    pump(0.4)
    banner = H.parts.find("castingBanner")
    check(banner.isVisible(), "the player shows it is casting")
    controls("cast")
    check(H.parts.find("cast_0").property("selected") and not H.parts.find("castLocal").property("selected"),
          "the TV is the one marked in the list")
    check(H.parts.find("pvCast").property("checked"), "the cast button stays lit")
    check(not H.parts.find("pvPip").property("enabled"), "picture-in-picture is off while casting")
    e.pause()
    wait(lambda: e.state == "paused", 4, "pause works while casting")
    e.play()
    wait(lambda: e.state == "playing", 4, "and play")
    # back to this computer, by mouse
    at = e.time
    controls("cast")
    _click_on(H.parts.find("castLocal"), "this computer")
    check(e.castingTo == "", "Este computador stops casting", e.castingTo)
    settled()
    playing_media(video=True)
    check(e.time >= at - 1500, "again from the same point", f"{at} -> {e.time}")
    pump(0.3)
    check(not banner.isVisible() and H.parts.find("pvPip").property("enabled"), "the banner goes and PiP is back")
    # cast again; the TV leaves the network
    controls("cast")
    _click_on(H.parts.find("cast_0"), "the TV again")
    check(e.castingTo == "TV da Sala", "casting again")
    settled()
    e._on_renderer((tv, ptr), False)
    pump(0.4)
    check(e.castingTo == "" and e.renderers == [], "a TV that leaves the network is dropped and casting ends",
          f"{e.castingTo!r} {e.renderers}")
    check(tv.released == 1, "its handle is given back once", str(tv.released))
    check(_visible_item("cast_0") is None, "it leaves the list")
    settled()
    playing_media(video=True)
    check(not banner.isVisible(), "the picture is back on this computer")


@scenario(timeout=90)
def feature_show_in_folder_by_mouse():
    """"Mostrar na pasta": the button asks the system's file manager to select the playing file
    (the request is captured, no Explorer window opens); off for links; a file that is gone asks
    for nothing."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from refrata import app as A
    asked = []

    class Sub:                                   # only this module's Popen is replaced
        def __getattr__(self, name):
            return getattr(subprocess_real, name)

        def Popen(self, cmd, *a, **k):
            asked.append([str(c) for c in cmd])
    import subprocess as subprocess_real
    A.subprocess = Sub()
    A._show_item_dbus = lambda path: asked.append(["dbus", path]) or True
    e, w = eng(), win()
    pv = H.parts.find("playerView")

    def controls():
        QTest.mouseMove(w, QPoint(w.width() // 2, w.height() // 3))
        pump(0.1)
        pv.setProperty("controlsVisible", True)
        pump(0.35)
    video = str(H.fx["video2"])                  # "Segundo Video.mkv": a name with a space
    e.openFiles([video])
    w.setProperty("playerOpen", True)
    playing_media(video=True)
    controls()
    btn = H.parts.find("pvReveal")
    check(btn.property("enabled"), "the button is on for a file on disk")
    _click_on(btn, "show in folder")
    wait(lambda: asked, 2, "the click asks the file manager")
    if sys.platform == "win32":
        check(asked == [["explorer", "/select,", os.path.normpath(video)]], "Explorer is asked to select that file, as one argument",
              str(asked))
    elif sys.platform == "darwin":
        check(asked == [["open", "-R", video]], "Finder is asked to show that file", str(asked))
    else:
        check(asked == [["dbus", video]], "the file manager is asked to show that file", str(asked))
    check(e.state == "playing", "and the video keeps playing")
    # the same by a file:// address, and a file that is no longer there
    del asked[:]
    from PySide6.QtCore import QUrl
    H.parts.ctl.reveal(QUrl.fromLocalFile(video).toString())
    check(len(asked) == 1 and os.path.normpath(asked[0][-1]) == os.path.normpath(video), "a file:// address is understood", str(asked))
    del asked[:]
    H.parts.ctl.reveal(video + ".gone")
    check(asked == [], "a file that is gone asks for nothing", str(asked))
    # a link has no folder
    base = _local_http()
    e.openFiles([f"{base}/media/Filmes/Segundo%20Video.mkv"])
    playing_media(video=True)
    controls()
    btn = H.parts.find("pvReveal")
    check(not btn.property("enabled"), "the button is off for a link")
    from PySide6.QtCore import Qt
    check(btn.isVisible(), "it stays in the bar, dimmed")
    QTest.mouseClick(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _item_center(btn))
    pump(0.3)
    check(asked == [], "and clicking it asks for nothing", str(asked))


def _local_http():
    import functools, http.server, socketserver
    root = H.fx["media_root"].parent
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))

    class Quiet(handler.func):  # type: ignore[misc]
        def log_message(self, *a):
            pass
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


@scenario(timeout=90)
def feature_web_links_flow():
    """The page-link path without the internet: yt-dlp's answer is simulated (video stream +
    separate audio stream, like YouTube) and served locally; failures move on to the next item."""
    from refrata import websites
    base = _local_http()
    calls = []

    def fake(url):
        calls.append(url)
        time.sleep(0.6)                       # a lookup takes a moment
        if "privado" in url:
            raise RuntimeError("Private video")
        return {"title": "Vídeo do site", "video": f"{base}/media/Filmes/Segundo%20Video.mkv",
                "audio": f"{base}/extras/tone_alt.wav", "headers": {"User-Agent": "Refrata-test"},
                "duration": 30000, "thumbnail": "", "live": False, "at": time.time()}
    websites.resolve = fake
    e = eng()
    msgs = []
    e.message.connect(msgs.append)
    ok_url, bad_url = "https://www.youtube.com/watch?v=teste", "https://youtu.be/privado"
    check(websites.is_page_url(ok_url) and websites.is_page_url(bad_url) and not websites.is_page_url(f"{base}/x.mp4"),
          "page links recognised, direct files not")
    e.openFiles([bad_url, ok_url, str(H.fx["tone_a"])])
    wait(lambda: any("abrir este link" in m for m in msgs), 8, "a private video is reported")
    wait(lambda: e.currentIndex == 1 and e.state == "playing" and e._frames_seen > 2, 15, "the next link plays")
    check(e.title == "Vídeo do site" and e.queue[1]["title"] == "Vídeo do site", "the page title replaces the link", e.title)
    wait(lambda: len(e.audioTracks) >= 2, 6, "the separate audio stream is attached")
    e.seek(12_000)
    wait(lambda: e.time >= 11_000, 5)
    e.next()
    playing_media(video=False)
    e.playAt(1)                               # back to the link: the lookup is reused, the position too
    wait(lambda: e.state == "playing" and e.time > 9_000, 15, "link resumes where it stopped")
    check(calls.count(ok_url) == 1, "one lookup per link while it is fresh", str(calls))
    e.openFiles([ok_url])
    e.openFiles([str(H.fx["tone_b"])])        # leaving while the lookup runs
    playing_media(video=False)
    pump(1.0)
    check(e.currentPath.endswith("Tom B 660.wav") and e.state == "playing", "a late lookup does not take over")


def _decoded_audio(e, field="played_abuffers") -> int:
    """libVLC's count of audio blocks for the current media (played by the output, by default)."""
    import vlc
    m = e._mp.get_media()
    if m is None:
        return 0
    try:
        st = vlc.MediaStats()
        m.get_stats(st)
        return int(getattr(st, field))
    finally:
        m.release()


@scenario(timeout=60)
def feature_audio_track_off_and_back():
    """A YouTube link (video and audio in separate streams): Faixas › Desativado, then Trilha 1.
    The sound must come back right away; in 1.4.3 it only came back after a seek."""
    from refrata import websites
    base = _local_http()
    websites.resolve = lambda url: {"title": "Video do site", "video": f"{base}/media/Filmes/Segundo%20Video.mkv",
                                    "audio": f"{base}/extras/tone_alt.wav", "headers": {}, "duration": 30000,
                                    "thumbnail": "", "live": False, "at": time.time()}
    e = eng()
    e.openFiles(["https://www.youtube.com/watch?v=faixas"])
    wait(lambda: e.state == "playing" and e._frames_seen > 2, 15, "the link plays")
    wait(lambda: len(e.audioTracks) >= 2, 6, "audio tracks listed")
    pump(1.0)
    on = [t for t in e.audioTracks if t["id"] != -1][0]["id"]
    check(_decoded_audio(e) > 0, "audio is decoded")
    e.setAudioTrack(-1)
    pump(1.0)
    off = _decoded_audio(e)
    pump(1.0)
    check(e.audioTrack == -1 and e._mp.audio_get_volume() == 0, "Desativado silences the audio and shows it off",
          f"track {e.audioTrack} volume {e._mp.audio_get_volume()}")
    t0 = e.time
    e.setAudioTrack(on)
    before = _decoded_audio(e)
    lost = _decoded_audio(e, "lost_abuffers")
    pump(2.0)
    check(_decoded_audio(e) > before + 20, "Trilha 1 brings the sound back without a seek",
          f"played {before} -> {_decoded_audio(e)}, lost {lost} -> {_decoded_audio(e, 'lost_abuffers')}")
    check(e.time >= t0, "the video keeps its place", f"{t0} -> {e.time}")


@scenario(timeout=90)
def feature_ytdlp_self_update():
    """yt-dlp updates itself from PyPI (a local stand-in here): a bad checksum is refused, a link
    that fails with the installed copy fetches a newer one and plays, and the switch in Ajustes
    turns it all off."""
    import hashlib, http.server, io, socketserver, zipfile
    from refrata import websites, ytdlp
    base = _local_http()
    stream = f"{base}/media/Filmes/Segundo%20Video.mkv"

    def wheel(version, works, evil=False):
        body = (f"__version__ = {version!r}\n"
                "class YoutubeDL:\n"
                "    def __init__(self, opts): pass\n"
                "    def __enter__(self): return self\n"
                "    def __exit__(self, *a): return False\n"
                "    def extract_info(self, url, download=False):\n"
                + ("        return {'title': 'Pelo yt-dlp ' + __version__, 'url': %r, 'duration': 30}\n" % stream
                   if works else "        raise RuntimeError('Unable to extract (site changed)')\n"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("yt_dlp/__init__.py", body)
            z.writestr("yt_dlp/version.py", f"__version__ = {version!r}\n")
            z.writestr("yt_dlp-%s.dist-info/METADATA" % version, "Name: yt-dlp\n")
            if evil:   # a path escaping the folder: the whole wheel must be refused
                z.writestr("yt_dlp/../../../fora.py", "x = 1\n")
        return buf.getvalue()

    files = {"/old.whl": wheel("2099.1.1", works=False), "/new.whl": wheel("2099.2.2", works=True),
             "/evil.whl": wheel("2099.1.1", works=True, evil=True)}
    served = {"json": b""}

    def publish(version, path, sha=None):
        served["json"] = json.dumps({"info": {"version": version}, "urls": [{
            "packagetype": "bdist_wheel", "filename": f"yt_dlp-{version}-py3-none-any.whl",
            "url": f"http://127.0.0.1:{port}{path}",
            "digests": {"sha256": sha or hashlib.sha256(files[path]).hexdigest()}}]}).encode()

    class Pypi(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = served["json"] if self.path.startswith("/pypi") else files.get(self.path, b"")
            self.send_response(200 if body else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Pypi)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["REFRATA_PYPI_URL"] = f"http://127.0.0.1:{port}/pypi/yt-dlp/json"
    check(ytdlp.allowed(), "auto-update is on by default")

    publish("2099.1.1", "/old.whl", sha="0" * 64)
    check(not ytdlp.check(force=True) and not ytdlp._installed(), "a wheel with the wrong SHA-256 is refused")
    ytdlp._last_forced = 0
    publish("2099.1.1", "/evil.whl")
    check(not ytdlp.check(force=True) and not ytdlp._installed(), "a wheel writing outside its folder is refused")
    check(not (ytdlp.ROOT.parent.parent / "fora.py").exists() and not (ytdlp.ROOT / "fora.py").exists(), "nothing written outside")
    check(not list(ytdlp.ROOT.glob("*.part")), "no half-installed folder left behind")
    ytdlp._last_forced = 0
    publish("2099.1.1", "/old.whl")
    check(ytdlp.check(force=True) and [v for v, _ in ytdlp._installed()] == ["2099.1.1"], "a newer yt-dlp is installed",
          str(ytdlp._installed()))
    check(ytdlp.current_version() == "2099.1.1", "and counts as the current one")

    # a link fails with the installed copy -> the app fetches a newer one and tries again
    publish("2099.2.2", "/new.whl")
    ytdlp._last_forced = 0
    e = eng()
    msgs = []
    e.message.connect(msgs.append)
    e.openUrl("https://www.youtube.com/watch?v=atualiza")
    try:
        wait(lambda: e.state == "playing" and e._frames_seen > 2, 20, "the link plays after the update")
    except Exception as exc:   # say which step broke: the lookup, the update or the stream
        raise AssertionError(f"{exc} | messages={msgs} active={ytdlp.active()!r} installed={ytdlp._installed()} "
                             f"resolved={e._queue[0].get('resolved') if e._queue else None}") from None
    check(e.title == "Pelo yt-dlp 2099.2.2", "the new yt-dlp found the stream", e.title)
    check(sys.modules["yt_dlp"].__version__ == "2099.2.2" and ytdlp.active() == "2099.2.2", "the new copy is the one loaded")

    # Ajustes › automatic updates off: no more downloads
    H.parts.settings.setValue("autoUpdate", False)
    ytdlp._last_forced = ytdlp._last_check = 0
    publish("2099.3.3", "/new.whl")
    check(not ytdlp.check(force=True) and not ytdlp.check(), "nothing is downloaded with updates off")
    H.parts.settings.setValue("autoUpdate", True)


@scenario(timeout=60)
def feature_link_loading_feedback():
    """A song is playing and a YouTube link is opened: while yt-dlp looks it up, the player must
    drop the song (title, cover, music mode) and say the link is opening, with a way to cancel."""
    from refrata import websites
    base = _local_http()

    def slow(url):
        time.sleep(3.0)
        return {"title": "Vídeo do site", "video": f"{base}/media/Filmes/Segundo%20Video.mkv", "audio": "",
                "headers": {}, "duration": 30000, "thumbnail": "", "live": False, "at": time.time()}
    websites.resolve = slow
    e = eng()
    e.openFiles([str(H.fx["tone_a"])])
    win().setProperty("playerOpen", True)
    playing_media(video=False)
    wait(lambda: e.time > 2000, 6, "the song plays for a while")
    song = e.title
    url = "https://www.youtube.com/watch?v=carregando"
    e.openUrl(url)
    pump(0.3)
    banner = H.parts.find("resolvingBanner")
    check(e.resolving and e.resolvingSite == "YouTube", "the lookup is reported", f"{e.resolving} {e.resolvingSite!r}")
    check(not e.isAudio and e.title != song and e.time == 0, "the song is gone from the player", f"{e.isAudio} {e.title!r} {e.time}")
    check(not H.parts.find("musicBackdrop").property("visible"), "music mode is left")
    wait(lambda: banner.property("visible") and banner.property("opacity") > 0.9, 2, "the 'opening link' message shows")
    if os.environ.get("REFRATA_SHOTS"):
        win().grabWindow().save(str(Path(os.environ["REFRATA_SHOTS"]) / "link_loading.png"))
    wait(lambda: e.state == "playing" and e._frames_seen > 2, 15, "the video then plays")
    check(not e.resolving and e.title == "Vídeo do site", "the message goes away")
    wait(lambda: not banner.property("visible"), 2, "the banner hides")
    # cancel: the late answer must not start playing
    e.openUrl("https://youtu.be/cancelado")
    pump(0.3)
    check(e.resolving, "second lookup running")
    H.parts.find("resolvingCancel").clicked.emit()
    pump(0.2)
    check(not e.resolving and e.state == "stopped", "Cancel stops the lookup", f"{e.resolving} {e.state}")
    pump(3.5)
    check(e.state == "stopped" and not e.playing and not e.resolving and e.time == 0,
          "a cancelled link does not start later", f"{e.state} {e.time}")


@scenario(timeout=60)
def feature_picture_in_picture():
    """Ctrl+P / the PiP button: a small always-on-top window with the same video (no restart),
    the app minimised; back to the app, close (pauses), and the app coming back by itself."""
    from PySide6.QtCore import QMetaObject, QObject, Qt
    from PySide6.QtGui import QWindow
    e, w = eng(), win()
    e.openFiles([str(H.fx["video"])])
    w.setProperty("playerOpen", True)
    playing_media(video=True)
    pump(0.5)
    pip = H.parts.qml.rootObjects()[0].findChild(QWindow, "pipWindow")
    check(pip is not None and not pip.isVisible(), "PiP window exists, hidden")
    H.parts.find("pvPip").clicked.emit()
    pump(0.8)
    check(pip.isVisible() and w.property("pipOn"), "the PiP button opens it")
    check(pip.flags() & Qt.WindowType.WindowStaysOnTopHint, "PiP stays on top")
    check(pip.transientParent() is None, "PiP is not owned by the main window (minimising it would hide the PiP)")
    if sys.platform == "win32":
        import ctypes
        check(ctypes.windll.user32.IsWindowVisible(int(pip.winId())), "the PiP window is really on screen")
    check(w.visibility() == QWindow.Visibility.Minimized, "the app is minimised", str(w.visibility()))
    t0 = e.time
    sink = pip.findChild(QObject, "pipVideo").property("videoSink")
    wait(lambda: sink.videoFrame().isValid(), 4, "PiP shows the video")
    wait(lambda: e.time > t0 + 500, 4, "the video keeps playing (not restarted)")
    ar = pip.width() / pip.height()
    check(abs(ar - e.videoWidth / e.videoHeight) < 0.05, "PiP has the video's shape", f"{pip.width()}x{pip.height()}")
    check(pip.x() + pip.width() <= pip.screen().availableGeometry().right() + 1, "PiP fits on screen")
    if os.environ.get("REFRATA_SHOTS"):
        pip.grabWindow().save(str(Path(os.environ["REFRATA_SHOTS"]) / "pip.png"))
    pip.findChild(QObject, "pipPlay").clicked.emit()
    pump(0.4)
    check(not e.playing, "pause from PiP")
    pip.findChild(QObject, "pipPlay").clicked.emit()
    wait(lambda: e.playing, 3, "play from PiP")
    pip.findChild(QObject, "pipBack").clicked.emit()
    pump(0.8)
    check(not pip.isVisible() and not w.property("pipOn"), "back to the app closes PiP")
    check(w.visibility() != QWindow.Visibility.Minimized and w.property("playerOpen"), "the app shows the player again", str(w.visibility()))
    check(e.playing, "still playing after coming back")
    # Ctrl+P function, then the ✕: pauses, PiP closes
    QMetaObject.invokeMethod(w, "togglePip")
    pump(0.8)
    check(pip.isVisible(), "togglePip opens it again")
    pip.findChild(QObject, "pipClose").clicked.emit()
    pump(0.5)
    check(not pip.isVisible() and not e.playing, "close pauses and hides PiP")
    # from full screen and from a maximised window: leaving full screen is asynchronous, and its late
    # "windowed" notice used to close the PiP a moment after it opened (seen in 1.4.3)
    e.togglePlay()
    for start in ("fullscreen", "maximized"):
        if start == "fullscreen":
            QMetaObject.invokeMethod(w, "toggleFullscreen")
        else:
            w.showMaximized()
        pump(1.0)
        QMetaObject.invokeMethod(w, "togglePip")
        pump(2.5)
        check(pip.isVisible() and w.property("pipOn"), f"PiP stays open when entered from {start}",
              f"pip={pip.isVisible()} visibility={w.visibility()}")
        if sys.platform == "win32":   # what is really on screen (Qt keeps saying "visible" for an owned window)
            import ctypes
            hwnd = int(pip.winId())
            check(ctypes.windll.user32.IsWindowVisible(hwnd) and not ctypes.windll.user32.IsIconic(hwnd),
                  f"the PiP window is really on screen ({start})")
        check(w.visibility() == QWindow.Visibility.Minimized, f"the app is minimised ({start})", str(w.visibility()))
        pip.findChild(QObject, "pipBack").clicked.emit()
        pump(1.0)
        check(not pip.isVisible(), f"back to the app ({start})")
        if w.property("fullscreen"):
            QMetaObject.invokeMethod(w, "toggleFullscreen")
        w.showNormal()
        pump(0.6)
    # the app restored from the taskbar while PiP is up: PiP goes away by itself
    QMetaObject.invokeMethod(w, "togglePip")
    pump(1.6)
    w.showNormal()
    pump(0.8)
    check(not pip.isVisible() and not w.property("pipOn"), "restoring the app ends PiP")


@scenario(timeout=90, env={"REFRATA_OPENSUBTITLES_KEY": "test-key"})
def feature_online_subtitles():
    """Search + download + load, against a local stand-in for the OpenSubtitles API (the real
    one needs the project's key)."""
    import http.server, socketserver
    from refrata import subsearch as S
    srt = Path(H.fmt["subs"]["srt"]).read_bytes()
    seen = {}

    class Api(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            seen.setdefault("key", self.headers.get("Api-Key"))
            seen.setdefault("ua", self.headers.get("User-Agent"))
            if self.path.startswith("/api/v1/subtitles"):
                seen.setdefault("queries", []).append(self.path)
                exact = "moviehash=" in self.path
                self._json({"data": [{"attributes": {"language": "pt-BR", "release": "Filme Teste 2024" + (" (exata)" if exact else ""),
                                                     "download_count": 10 if exact else 999, "moviehash_match": exact,
                                                     "files": [{"file_id": 7 if exact else 8, "file_name": "a.srt"}]}}]})
            elif self.path == "/file.srt":
                self.send_response(200)
                self.send_header("Content-Length", str(len(srt)))
                self.end_headers()
                self.wfile.write(srt)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            seen["download"] = json.loads(self.rfile.read(n) or b"{}")
            self._json({"link": f"http://127.0.0.1:{self.server.server_address[1]}/file.srt"})

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Api)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["REFRATA_OPENSUBTITLES_URL"] = f"http://127.0.0.1:{srv.server_address[1]}/api/v1"
    check(S.guess_query("C:/x/The.Movie.2019.1080p.x264.mkv") == "The Movie 2019", "name guess from a release name")
    e, ss = eng(), H.parts.subsearch
    check(ss.property("available"), "the feature shows when a key is configured")
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    win().setProperty("playerOpen", True)
    pump(0.5)
    pv = H.parts.find("playerView")
    pv.setProperty("panel", "tracks")
    pump(0.4)
    H.parts.find("findSubBtn")
    ss.find("pt-br,en")
    wait(lambda: not ss.property("busy") and ss.property("results"), 10, "results arrive")
    res = ss.property("results")
    check(res[0]["exact"] and res[0]["fileId"] == 7, "the exact match for this file comes first", str(res))
    check(seen.get("key") == "test-key" and "Refrata" in (seen.get("ua") or ""), "API key and user agent sent", str(seen))
    check(any("moviehash=" in q for q in seen["queries"]), "searched by the file's hash", str(seen["queries"]))
    pv.setProperty("panel", "subs")
    pump(0.4)
    lst = H.parts.find("subResults")
    check(lst.isVisible() and lst.property("count") == len(res), "the panel lists every result found",
          f"{lst.property('count')} vs {len(res)}")
    first = H.parts.find("subResult_0")
    check("✓" in first.property("text") and res[0]["name"] in first.property("text"),
          "the exact match is marked and named", first.property("text"))
    before = len(e.subtitleTracks)
    pv.setProperty("controlsVisible", True)
    pump(0.3)
    _click_on(first, "the first subtitle found")
    wait(lambda: not ss.property("busy"), 10, "download finishes")
    check(seen.get("download") == {"file_id": 7}, "downloads the chosen file", str(seen.get("download")))
    wait(lambda: len(e.subtitleTracks) > before, 6, "downloaded subtitle loaded")
    check(any(p.suffix == ".srt" for p in (Path(os.environ["REFRATA_DATA"]) / "subtitles").iterdir()), "kept in the data folder")
    os.environ["REFRATA_OPENSUBTITLES_URL"] = "http://127.0.0.1:9/api/v1"   # nothing listens there
    ss.find("en")
    wait(lambda: not ss.property("busy"), 20, "a failed search ends")
    check("Não foi possível" in ss.property("status"), "a failed search is reported", ss.property("status"))


@scenario(timeout=120)
def network_youtube_link_plays():
    """A real YouTube link (needs the internet; reported, not failed, when offline)."""
    import socket
    try:
        socket.create_connection(("www.youtube.com", 443), timeout=5).close()
    except OSError:
        H.notes.append("offline: real YouTube check skipped")
        return
    e = eng()
    e.openUrl("https://www.youtube.com/watch?v=jNQXAC9IVRw")
    wait(lambda: e.state == "playing" and e._frames_seen > 3 and e.time > 1000, 60, "YouTube video plays")
    check("zoo" in e.title.lower(), "YouTube title", e.title)
    H.notes.append(f"YouTube: {e.videoWidth}×{e.videoHeight}, {len(e.audioTracks) - 1} audio track(s)")

@scenario(timeout=150)
def network_youtube_audio_track_off_and_back():
    """Real YouTube (DASH: video and audio are separate streams): Desativado, then Trilha 1 must bring
    the sound back without a seek (needs the internet; reported, not failed, when offline)."""
    import socket
    try:
        socket.create_connection(("www.youtube.com", 443), timeout=5).close()
    except OSError:
        H.notes.append("offline: real YouTube audio-track check skipped")
        return
    e = eng()
    e.openUrl("https://www.youtube.com/watch?v=aqz-KE-bpKQ")   # Big Buck Bunny (Blender Foundation)
    wait(lambda: e.state == "playing" and e._frames_seen > 3 and e.time > 3000, 90, "YouTube video plays")
    wait(lambda: len(e.audioTracks) >= 2, 10, "audio tracks listed")
    on = [t for t in e.audioTracks if t["id"] != -1][0]["id"]
    t_on = e.time
    e.setAudioTrack(-1)
    pump(2.0)
    t0 = e.time
    H.notes.append(f"time before Desativado {t_on}, 2 s later {t0} (libVLC {e._mp.get_time()})")
    check(t0 >= t_on, "the clock keeps running with the audio off", f"{t_on} -> {t0}")
    before = _decoded_audio(e)
    lost = _decoded_audio(e, "lost_abuffers")
    e.setAudioTrack(on)
    pump(4.0)
    H.notes.append(f"audio back: played {before} -> {_decoded_audio(e)}, lost {lost} -> "
                   f"{_decoded_audio(e, 'lost_abuffers')}, time {t0} -> {e.time}")
    check(_decoded_audio(e) > before + 20, "Trilha 1 brings the sound back without a seek",
          f"played {before} -> {_decoded_audio(e)}")
    check(e.time >= t_on, "the video keeps its place (no jump back)", f"{t_on} -> {e.time}")


# ------------------------------------------------ from a YouTuber's review of 1.3.1 (portable zip)
def _item_center(item):
    from PySide6.QtCore import QPointF
    return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()


def _scroll_to(page, item, margin=220):
    from PySide6.QtCore import Q_ARG, QMetaObject, QPointF, Qt
    if page.objectName() == "settingsPage" and not item.isVisible():   # one tab at a time
        QMetaObject.invokeMethod(page, "reveal", Qt.ConnectionType.DirectConnection, Q_ARG("QVariant", item))
        pump(0.2)
    y = item.mapToItem(page.property("contentItem"), QPointF(0, 0)).y()
    page.setProperty("contentY", max(0.0, y - margin))
    pump(0.4)


def _click(item):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    pos = _item_center(item)
    QTest.mouseMove(win(), pos)
    pump(0.05)
    QTest.mouseClick(win(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    pump(0.3)


def _wheel(pos, steps):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QGuiApplication, QWheelEvent
    for _ in range(abs(steps)):
        ev = QWheelEvent(QPointF(pos), QPointF(win().mapToGlobal(pos)), QPoint(0, 0), QPoint(0, 120 if steps > 0 else -120),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QGuiApplication.sendEvent(win(), ev)
        pump(0.06)


def _focus():
    w = win()
    w.show()
    w.raise_()
    w.requestActivate()
    pump(0.3)


@scenario(timeout=60)
def feature_review_settings_and_open_with():
    """The 'Abrir arquivos no player rápido' switch did not move when clicked (it read the setting
    once). The portable copy never showed up in Explorer's 'Abrir com', so double-clicks went to
    Windows' own player: Ajustes now registers it (here inside a throwaway registry key)."""
    import sys
    import winreg
    from refrata import assoc
    if not getattr(sys, "frozen", False):
        os.environ["REFRATA_ASSOC_EXE"] = sys.executable
    assoc.clear_test_keys()
    w = win()
    w.setProperty("page", 5)
    pump(0.8)
    page = H.parts.find("settingsPage")
    toggle = H.parts.find("toggleQuickOpen")
    _scroll_to(page, toggle)
    check(toggle.property("checked") is False and H.parts.settings.value("quickOpen") is False, "quick player off by default")
    _click(toggle)
    check(H.parts.settings.value("quickOpen") is True, "a click turns the quick player on")
    check(toggle.property("checked") is True, "and the switch shows it at once")
    _click(toggle)
    check(H.parts.settings.value("quickOpen") is False and toggle.property("checked") is False, "a second click turns it off")

    wa = H.parts.assoc
    wa.refresh()
    check(not wa.registered, "nothing registered in the sandbox yet")
    row, btn = H.parts.find("defaultAppRow"), H.parts.find("defaultAppBtn")
    wa.changed.emit()
    pump(0.3)
    check(row.property("visible"), "Ajustes shows the 'Player padrão do Windows' row")
    _scroll_to(page, btn)
    _click(btn)
    check(wa.registered and not wa.isDefault, "the button registers this copy", f"{wa.registered} {wa.isDefault}")
    exe = os.environ.get("REFRATA_ASSOC_EXE") or sys.executable
    root = assoc.TEST_ROOT + "\\"
    rd = lambda sub, name="": assoc._read(winreg.HKEY_CURRENT_USER, root + sub, name)  # noqa: E731
    check(rd(r"Software\RegisteredApplications", "Refrata") == assoc.CAP_KEY, "listed in Apps padrão")
    check(rd(rf"Software\Classes\{assoc.PROGID}\shell\open\command") == f'"{exe}" "%1"', "open command runs this exe")
    check(rd(r"Software\Classes\.mkv\OpenWithProgids", assoc.PROGID) == "", ".mkv lists Refrata in Abrir com")
    check(rd(r"Software\Classes\.srt\OpenWithProgids", assoc.PROGID) is None, "subtitles are not claimed")
    check(rd(assoc.CAP_KEY + r"\FileAssociations", ".mp3") == assoc.PROGID, "capabilities cover music too")
    # the user picks Refrata in Windows' settings (UserChoice), then comes back to the app
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + assoc._FILEEXTS + r"\.mp4\UserChoice") as k:
        winreg.SetValueEx(k, "ProgId", 0, winreg.REG_SZ, assoc.PROGID)
    wa.refresh()
    pump(0.2)
    check(wa.isDefault, "seen as the default player")
    check(btn.property("text") != "Tornar padrão", "the button no longer asks to make it default", btn.property("text"))
    # the portable folder moved: "Abrir com" points at an exe that is gone, and gets repaired
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + rf"Software\Classes\{assoc.PROGID}\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, '"C:\\pasta-que-sumiu\\Refrata.exe" "%1"')
    wa.refresh()
    check(not wa.registered, "a registration pointing at a missing exe does not count")
    wa.repair()
    check(rd(rf"Software\Classes\{assoc.PROGID}\shell\open\command") == f'"{exe}" "%1"' and wa.registered,
          "repair points it at this copy again")
    assoc.clear_test_keys()
    check(assoc._read(winreg.HKEY_CURRENT_USER, assoc.TEST_ROOT + r"\Software\RegisteredApplications", "Refrata") is None,
          "test keys removed")


@scenario(timeout=60)
def feature_review_wheel_over_queue():
    """Scrolling the 'A seguir' list past its top changed the volume (the list let the wheel through
    to the player underneath). Over a panel the wheel never reaches the volume; over the video it does."""
    e, w = eng(), win()
    vids = [str(H.fx["video"])] + [str(p) for p in sorted(Path(H.fx["media_root"]).rglob("*.mkv"))][:6]
    e.openFiles(vids)
    w.setProperty("playerOpen", True)
    playing_media(video=True)
    e.volume = 50
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    H.parts.find("pvQueue").clicked.emit()
    panel = H.parts.find("queuePanel")
    QTest.mouseMove(w, QPoint(w.width() // 2, w.height() // 2))   # the controls (and the panel) show
    pump(0.8)
    QTest.mouseMove(w, _item_center(panel))
    pump(0.3)
    check(panel.property("visible") and panel.property("x") < w.width() - 50, "the queue panel is open")
    _wheel(_item_center(panel), 4)
    _wheel(_item_center(panel), -1)
    _wheel(_item_center(panel), 2)
    check(e.volume == 50, "the wheel over the queue leaves the volume alone", str(e.volume))
    _wheel(QPoint(int(w.width() * 0.25), int(w.height() * 0.4)), 1)
    check(e.volume == 55, "the wheel over the video still sets the volume", str(e.volume))


@scenario(timeout=60)
def feature_subtitle_on_then_off_quickly():
    """Adding a subtitle reopens a GPU-decoded video in software, and the reopened item turns that
    subtitle on by itself; switching it off while the reopen was still opening came back on."""
    e = eng()
    win().setProperty("playerOpen", True)
    for key, delay in (("video", 0.0), ("video2", 0.3)):   # each video goes to software once
        e.openFiles([str(H.fx[key])])
        playing_media(video=True)
        e.addSubtitle(Path(H.fx["srt"]).as_uri())
        pump(delay)
        e.setSubtitleTrack(-1)
        playing_media(video=True)
        pump(3.0)
        check(e.subtitleTrack in (-1, 0), f"{key}: subtitles stay off (off {delay:.1f}s after adding)", str(e.subtitleTrack))
        subs = [t["id"] for t in e.subtitleTracks if t["id"] != -1]
        check(subs, f"{key}: the added subtitle is still listed", str(e.subtitleTracks))
        e.setSubtitleTrack(subs[-1])
        pump(2.0)
        check(e.subtitleTrack == subs[-1], f"{key}: chosen again, it stays on", str(e.subtitleTrack))


def _key_to_focus(key, what):
    """A key the way a keyboard delivers it: to whichever window the system has in front, through
    Qt's shortcut matching. (QTest.keyClick(some_window) picks the window itself, which is how
    the main window's shortcuts eating the preview's keys went unnoticed.)"""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    target = QGuiApplication.focusWindow()
    check(target is not None, f"a window has the keyboard for {what}")
    QTest.keyClick(target, key)
    pump(0.15)
    return target


def _ui_stall(fn, seconds):
    """Run fn, then keep the event loop going for `seconds`: the longest the UI went without a
    turn (a 10 ms timer that should tick on time), in seconds."""
    from PySide6.QtCore import QTimer
    gaps, last = [0.0], [time.perf_counter()]

    def tick():
        now = time.perf_counter()
        gaps[0] = max(gaps[0], now - last[0])
        last[0] = now
    t = QTimer()
    t.setInterval(10)
    t.timeout.connect(tick)
    t.start()
    last[0] = time.perf_counter()
    fn()
    tick()
    pump(seconds)
    t.stop()
    return gaps[0]


@scenario(timeout=90, solo=True)
def feature_quick_preview_over_the_open_app():
    """Space on a file in Explorer while Refrata is open with a video: the preview takes the keys
    (← → browse, Space closes) and the player behind is left alone; Enter opens the file in the
    player at the same point, with its picture, without the UI stalling."""
    from PySide6.QtCore import QMetaObject, Qt
    from PySide6.QtGui import QGuiApplication
    e, w, q = eng(), win(), quick_window()
    a, b = str(H.fx["video"]), str(H.fx["video2"])
    e.openFiles([a])
    w.setProperty("playerOpen", True)
    playing_media(video=True)
    e.seek(8000)
    pump(0.5)

    H.parts.quicklook.previewRequested.emit(a, [a, b])
    wait(lambda: q.isVisible() and QGuiApplication.focusWindow() is q, 5, "the preview opens with the keyboard")
    wait(lambda: e.state == "paused", 3, "the player behind pauses for the preview")
    check(not w.isActive(), "the main window is not active behind the preview (its shortcuts would fire)")
    behind, vol = e.time, e.volume
    pump(0.4)                                   # past the guard for the Space that opened it
    _key_to_focus(Qt.Key.Key_Right, "→")
    check(q.property("index") == 1, "→ goes to the next file in the preview", str(q.property("index")))
    _key_to_focus(Qt.Key.Key_Left, "←")
    check(q.property("index") == 0, "← comes back", str(q.property("index")))
    check(abs(e.time - behind) < 400 and e.state == "paused", "← → left the player behind alone",
          f"{behind} -> {e.time}, {e.state}")
    _key_to_focus(Qt.Key.Key_Up, "↑")
    check(e.volume == min(150, vol + 5), "↑ raises the volume once (not twice: preview and player)", f"{vol} -> {e.volume}")
    e.volume = vol
    _key_to_focus(Qt.Key.Key_Space, "Space")
    wait(lambda: not q.isVisible(), 3, "Space closes the preview")
    wait(lambda: e.state == "playing", 4, "the player behind picks up again")
    check(abs(e.time - behind) < 1500, "from where it was", f"{behind} -> {e.time}")

    # again, and Enter: the file opens in the player at the preview's point
    H.parts.quicklook.previewRequested.emit(b, [a, b])
    wait(lambda: q.isVisible() and QGuiApplication.focusWindow() is q, 5, "the preview opens again")
    wait(lambda: q.property("index") == 1, 2)
    pump(2.5)                                    # it plays a little
    # how it feels: the time to the picture, and the longest gap between two drawn frames
    swaps, picture_at = [], [0.0]
    on_swap = lambda: swaps.append(time.perf_counter())
    w.frameSwapped.connect(on_swap)
    t_enter = time.perf_counter()

    def enter_and_watch():
        _key_to_focus(Qt.Key.Key_Return, "Enter")
        end = time.perf_counter() + 3
        while time.perf_counter() < end and not picture_at[0]:
            if Path(e.currentPath).name == Path(b).name and e._frames_seen > 1 and not picture_problem(e):
                picture_at[0] = time.perf_counter()
            pump(0.01)
    stall = _ui_stall(enter_and_watch, 0.8)
    w.frameSwapped.disconnect(on_swap)
    wait(lambda: not q.isVisible() and Path(e.currentPath).name == Path(b).name, 4, "Enter opens it in the player")
    playing_media(video=True)                    # with its picture (it came out green after a GPU item)
    to_picture = (picture_at[0] - t_enter) if picture_at[0] else 99
    gaps = [b2 - a2 for a2, b2 in zip(swaps, swaps[1:])]
    worst_frame = max(gaps) if gaps else 99
    H.notes.append(f"preview → player: picture after {to_picture * 1000:.0f} ms, {len(swaps)} frames drawn, "
                   f"worst frame gap {worst_frame * 1000:.0f} ms")
    check(to_picture < 1.0, "the player shows the picture within a second of Enter", f"{to_picture * 1000:.0f} ms")
    check(worst_frame < 0.25, "the window keeps drawing smoothly through the hand-over", f"{worst_frame * 1000:.0f} ms gap")
    check(e.time > 1500, "at the point the preview had reached", str(e.time))
    check(w.property("playerOpen") is True, "in the player view")
    check(QGuiApplication.focusWindow() is w, "and the main window has the keyboard",
          str(QGuiApplication.focusWindow() and QGuiApplication.focusWindow().objectName()))
    H.notes.append(f"preview → player: longest UI stall {stall * 1000:.0f} ms")
    check(stall < 0.35, "the hand-over does not freeze the UI", f"{stall * 1000:.0f} ms")
    # the keys are the player's again
    t = e.time
    _key_to_focus(Qt.Key.Key_Right, "→ in the player")
    wait(lambda: e.time > t + 5000, 3, "→ in the player skips forward")


@scenario(timeout=90, solo=True)
def feature_quick_preview_to_player_from_every_window_state():
    """Enter in the preview opens the file in the player whatever state the app was in: minimised,
    hidden in the tray, or open on another page. The window comes up, in front, with the
    keyboard, showing the picture at the preview's point."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QWindow
    e, w, q = eng(), win(), quick_window()
    H.parts.tray.background = True               # hidden = still running in the tray
    a, b = str(H.fx["video"]), str(H.fx["video2"])
    for state in ("minimised", "hidden in the tray", "on the Home page", "maximised, then minimised"):
        e.openFiles([a])
        w.setProperty("playerOpen", True)
        playing_media(video=True)
        if state == "maximised, then minimised":
            w.showMaximized()
            wait(lambda: w.visibility() == QWindow.Visibility.Maximized, 3)
            pump(0.4)
            w.showMinimized()
        elif state == "minimised":
            w.showMinimized()
        elif state == "hidden in the tray":
            w.hide()
        else:
            w.setProperty("playerOpen", False)
            w.setProperty("page", 0)
        pump(0.6)
        H.parts.quicklook.previewRequested.emit(b, [a, b])
        wait(lambda: q.isVisible() and QGuiApplication.focusWindow() is q, 5, f"{state}: the preview opens with the keyboard")
        pump(1.5)
        from PySide6.QtCore import QObject
        at = q.findChild(QObject, "quickSeek").property("value")
        H.notes.append(f"{state}: preview at {at:.0f} ms on Enter")
        _key_to_focus(Qt.Key.Key_Return, "Enter")
        wait(lambda: not q.isVisible() and Path(e.currentPath).name == Path(b).name, 4, f"{state}: Enter opens it in the player")
        wait(lambda: w.isVisible() and w.visibility() not in (QWindow.Visibility.Minimized, QWindow.Visibility.Hidden), 4,
             f"{state}: the app window comes up")
        wait(lambda: QGuiApplication.focusWindow() is w, 4, f"{state}: in front, with the keyboard")
        check(w.property("playerOpen") is True, f"{state}: in the player view")
        if state.startswith("maximised"):
            check(w.visibility() == QWindow.Visibility.Maximized, "it comes back maximised, as it was", str(w.visibility()))
            w.showNormal()
        # the clock on screen starts at the preview's point and never shows 0:00 meanwhile (libVLC
        # reports 0 for its first second: the clock read 0:00, then jumped)
        seen = []
        t_end = time.time() + 1.6
        while time.time() < t_end:
            if e.state == "playing" and Path(e.currentPath).name == Path(b).name:
                seen.append(e.time)
            pump(0.05)
        check(seen and min(seen) > at - 700, f"{state}: the clock shows the preview's point from the start",
              f"preview at {at:.0f}, clock went {seen[:3]}…")
        playing_media(video=True)
    H.parts.tray.background = False


@scenario(timeout=60, solo=True)
def feature_second_launch_and_tray_bring_the_window_back_as_it_was():
    """A file opened from Explorer while Refrata sits minimised (maximised before), and the tray's
    "show": the window comes back in front, maximised as it was, playing the file with its picture."""
    from PySide6.QtGui import QGuiApplication, QWindow
    e, w = eng(), win()
    H.parts.tray.background = True
    a, b = str(H.fx["video"]), str(H.fx["video2"])
    e.openFiles([a])
    w.setProperty("playerOpen", True)
    playing_media(video=True)
    w.showMaximized()
    wait(lambda: w.visibility() == QWindow.Visibility.Maximized, 3)
    pump(0.4)
    w.showMinimized()
    pump(0.6)
    # what a second Refrata.exe hands over (refrata/single.py → main.build)
    H.parts.ctl.handOver([b])
    H.parts.instance.activateRequested.emit() if H.parts.instance is not None else H.parts.tray.showRequested.emit()
    wait(lambda: Path(e.currentPath).name == Path(b).name, 4, "the file handed over plays")
    wait(lambda: w.visibility() == QWindow.Visibility.Maximized, 4, "the window comes back maximised", )
    wait(lambda: QGuiApplication.focusWindow() is w, 4, "in front, with the keyboard")
    playing_media(video=True)
    # hidden in the tray, then the tray's show
    w.hide()
    pump(0.6)
    H.parts.tray.showRequested.emit()
    wait(lambda: w.isVisible() and w.visibility() == QWindow.Visibility.Maximized, 4, "the tray brings it back maximised")
    playing_media(video=True)
    w.showNormal()
    H.parts.tray.background = False


def _click_on(item, what="", window=None):
    """A real mouse click at the centre of `item`, on the window it lives in (main by default; the
    PiP or the preview passed in). Not item.window(): PySide then drops the Python side of the
    main window, which main.build wrapped by hand, and every later call on it fails."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    check(item is not None, f"{what or 'the control'} exists")
    check(item.isVisible() and item.opacity() > 0.5, f"{what or item.objectName()} is on screen before the click")
    win_ = window or win()
    pos = _item_center(item)
    check(0 <= pos.x() < win_.width() and 0 <= pos.y() < win_.height(), f"{what or item.objectName()} is inside its window",
          f"{pos.x()},{pos.y()} in {win_.width()}x{win_.height()}")
    QTest.mouseMove(win_, pos)
    pump(0.05)
    QTest.mouseClick(win_, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    pump(0.25)


def _find_in(window, name):
    from PySide6.QtCore import QObject
    return window.findChild(QObject, name)


@scenario(timeout=120)
def feature_every_button_does_what_it_says():
    """Clicked with the mouse, each control changes what it promises: the player's A-B, info and
    picture controls (rotate, mirror, zoom, aspect), the PiP's skip buttons, the music mode's
    repeat / mute / speed / timer, and the preview's play button. (They were only ever clicked at
    random, checking nothing but that the app survived.)"""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QWindow
    from PySide6.QtTest import QTest
    e, w = eng(), win()
    pv = H.parts.find("playerView")
    e.openFiles([str(H.fx["video"])])
    w.setProperty("playerOpen", True)
    playing_media(video=True)

    def controls():
        QTest.mouseMove(w, QPoint(w.width() // 2, w.height() // 3))
        pump(0.1)
        pv.setProperty("controlsVisible", True)
        pump(0.35)

    # A-B: start, end, off
    controls()
    e.seek(4000); pump(0.4)
    _click_on(H.parts.find("pvLoop"), "A-B")
    check(e.loopA >= 3000 and e.loopB < 0, "A-B first click marks the start", f"{e.loopA}/{e.loopB}")
    pump(1.2); controls()
    _click_on(H.parts.find("pvLoop"), "A-B")
    check(e.loopB > e.loopA, "second click marks the end", f"{e.loopA}/{e.loopB}")
    controls()
    _click_on(H.parts.find("pvLoop"), "A-B")
    check(e.loopA < 0 and e.loopB < 0, "third click clears it", f"{e.loopA}/{e.loopB}")
    # info panel
    controls()
    _click_on(H.parts.find("pvInfo"), "info")
    check(pv.property("panel") == "info", "the info button opens the info panel", pv.property("panel"))
    controls()
    _click_on(H.parts.find("pvInfo"), "info")
    check(pv.property("panel") == "", "and closes it")
    # picture: rotate, mirror, zoom, aspect
    controls()
    _click_on(H.parts.find("pvAdjust"), "Imagem")
    check(pv.property("panel") == "adjust", "Imagem opens the picture panel")
    for name, prop, expect in (("rotateRight", "videoRotation", 90), ("rotateLeft", "videoRotation", 0),
                               ("mirror", "videoMirror", True), ("zoom_1.5", "videoZoom", 1.5),
                               ("aspect_16:9", "videoAspect", "16:9"), ("aspect_auto", "videoAspect", ""),
                               ("zoom_1", "videoZoom", 1), ("mirror", "videoMirror", False)):
        controls()
        _click_on(H.parts.find(name), name)
        got = pv.property(prop)
        ok = abs(got - expect) < 0.01 if isinstance(expect, float) else got == expect
        check(ok, f"{name} sets {prop} to {expect!r}", repr(got))
        check(not picture_problem(e), f"the picture still shows after {name}", picture_problem(e))
    pv.setProperty("panel", "")

    # PiP's own buttons
    pip = H.parts.qml.rootObjects()[0].findChild(QWindow, "pipWindow")
    H.parts.find("pvPip").clicked.emit()
    wait(lambda: pip.isVisible(), 3, "PiP open")
    pump(0.8)
    if pip.width() < 300:
        pip.setWidth(420); pip.setHeight(236); pump(0.4)
    QTest.mouseMove(pip, QPoint(pip.width() // 2, pip.height() // 2)); pump(0.4)
    e.seek(12000); pump(0.5)
    t = e.time
    _click_on(_find_in(pip, "pipFwd10"), "PiP +10 s", pip)
    wait(lambda: e.time > t + 8000, 2, "PiP +10 s skips forward")
    t = e.time
    QTest.mouseMove(pip, QPoint(pip.width() // 2, pip.height() // 2)); pump(0.2)
    _click_on(_find_in(pip, "pipBack10"), "PiP -10 s", pip)
    wait(lambda: e.time < t - 7000, 2, "PiP -10 s skips back")
    from PySide6.QtCore import QMetaObject
    QMetaObject.invokeMethod(H.parts.qml.rootObjects()[0], "leavePip", Qt_arg_bool(True))
    wait(lambda: not pip.isVisible(), 3, "back from the PiP")

    # music mode
    e.openFiles([str(H.fx["tone_long"]), str(H.fx["tone_a"])])
    playing_media(video=False)
    wait(lambda: e.isAudio, 3)
    pump(0.8)
    rep = e.repeat
    _click_on(H.parts.find("mpRepeat"), "repeat")
    check(e.repeat == (rep + 1) % 3, "repeat cycles", f"{rep} -> {e.repeat}")
    muted = e.muted
    _click_on(H.parts.find("mpMute"), "mute")
    check(e.muted != muted, "mute toggles", f"{muted} -> {e.muted}")
    _click_on(H.parts.find("mpMute"), "mute")
    _click_on(H.parts.find("mpSpeed"), "speed")
    check(pv.property("panel") == "speed", "speed opens the speed panel", pv.property("panel"))
    _click_on(H.parts.find("speed_1.5"), "1.5×")
    check(abs(e.rate - 1.5) < 0.01, "1.5× sets the speed", str(e.rate))
    _click_on(H.parts.find("speed_1"), "1×")      # JavaScript writes 1.0 as "1"
    pv.setProperty("panel", "")
    pump(0.3)
    _click_on(H.parts.find("mpSleep"), "sleep timer")
    check(pv.property("panel") == "sleep", "the timer button opens the sleep panel", pv.property("panel"))
    pv.setProperty("panel", "")
    while e.repeat != 0:
        e.cycleRepeat()

    # the preview's play button
    q = quick_window()
    open_quick(H.fx["video"], "preview")
    wait(lambda: q.isVisible(), 3)
    seek = _find_in(q, "quickSeek")
    wait(lambda: seek.property("value") > 300, 4, "the preview plays")
    QTest.mouseMove(q, QPoint(q.width() // 2, q.height() // 2)); pump(0.3)
    _click_on(_find_in(q, "quickPlay"), "preview play/pause", q)
    v = seek.property("value"); pump(0.6)
    check(abs(seek.property("value") - v) < 120, "the preview's button pauses it", f"{v} -> {seek.property('value')}")
    _click_on(_find_in(q, "quickPlay"), "preview play/pause", q)
    pump(0.6)
    check(seek.property("value") > v + 250, "and plays it again", f"{v} -> {seek.property('value')}")
    _click_on(_find_in(q, "quickClose"), "preview close", q)
    wait(lambda: not q.isVisible(), 3, "the close button closes the preview")


def Qt_arg_bool(v):
    from PySide6.QtCore import Q_ARG
    return Q_ARG("QVariant", v)


@scenario(timeout=60)
def feature_start_with_the_system_entry():
    """"Iniciar com o Windows" writes the entry that starts Refrata in the tray, and removes it again.
    In its own throwaway registry key: the person's real Run entry is read before and after and
    must not change."""
    if sys.platform != "win32":
        return
    import winreg
    from refrata import tray as T

    def real():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, T.RUN_KEY) as k:
                return winreg.QueryValueEx(k, T.RUN_NAME)[0]
        except OSError:
            return None

    def mine():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, T._run_key()) as k:
                return winreg.QueryValueEx(k, T.RUN_NAME)[0]
        except OSError:
            return None
    before = real()
    check(T._run_key() != T.RUN_KEY and "RefrataTest" in T._run_key(), "the tests write to their own key", T._run_key())
    tray = H.parts.tray
    check(tray.canAutostart and tray.autostart is False, "off to begin with")
    tray.autostart = True
    value = mine()
    check(value is not None and value.endswith(" --background") and value.startswith('"'),
          "on: the entry starts this Refrata in the tray", str(value))
    check(os.path.isfile(value.split('"')[1]), "and points at a program that exists", str(value))
    check(tray.autostart is True and tray.background is True, "the switch shows on, and staying in the tray comes with it")
    tray.background = False                     # leaving the tray takes the entry away too
    check(mine() is None and tray.autostart is False, "staying in the tray off removes the entry")
    tray.autostart = True
    tray.autostart = False
    check(mine() is None, "off: the entry is gone")
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, T._run_key())
    except OSError:
        pass
    check(real() == before, "the person's real start-with-Windows entry was never touched", f"{before!r} -> {real()!r}")


@scenario(timeout=90, solo=True, env={"REFRATA_NO_HOOK": ""})
def feature_space_preview_from_a_real_explorer_window():
    """The Space preview end to end, short of pressing a real key: a real Explorer window on a folder
    of the test media, a file selected in it, Explorer in front. The foreground watcher installs the
    keyboard hook, the hook's own handler (given a Space) reads the selection through Explorer's
    automation and the preview opens on that file, with the folder's other media to browse."""
    if sys.platform != "win32":
        return
    import ctypes
    import subprocess
    from ctypes import wintypes
    from PySide6.QtAxContainer import QAxObject
    from refrata import quicklook as Q
    ql, q, e = H.parts.quicklook, quick_window(), eng()
    u = ctypes.windll.user32
    folder = Path(H.fx["media_root"]) / "Filmes"
    target = Path(H.fx["video2"])
    check(target.parent == folder, "the test video lives in the folder opened", str(target))
    if not ql.active:
        ql.start()
    check(ql.active, "the preview is watching for Explorer")

    before = set()
    shell = QAxObject("Shell.Application")
    wins = shell.querySubObject("Windows()")
    for i in range(int(wins.property("Count") or 0)):
        w_ = wins.querySubObject("Item(QVariant)", i)
        if w_ is not None and not w_.isNull():
            before.add(int(w_.property("HWND") or 0))
    subprocess.Popen(f'explorer.exe /select,"{target}"')   # the folder, with the file selected
    mine = [None]

    def opened():
        ws = shell.querySubObject("Windows()")
        for i in range(int(ws.property("Count") or 0)):
            w_ = ws.querySubObject("Item(QVariant)", i)
            if w_ is None or w_.isNull():
                continue
            try:
                same = os.path.normcase(str(w_.querySubObject("Document").querySubObject("Folder").querySubObject("Self").property("Path"))) == os.path.normcase(str(folder))
            except Exception:
                same = False
            if same and int(w_.property("HWND") or 0) not in before:
                mine[0] = w_
                return True
        return False
    wait(opened, 15, "an Explorer window opens on the test folder")
    hwnd = int(mine[0].property("HWND"))
    mine[0] = wins = shell = None               # no automation object of ours outlives the window
    try:
        pump(1.0)
        from refrata.app import force_foreground
        end = time.time() + 6
        while time.time() < end and u.GetForegroundWindow() != hwnd:
            force_foreground(hwnd)
            pump(0.3)
        check(u.GetForegroundWindow() == hwnd, "the Explorer window is in front")
        wait(lambda: ql._hook is not None, 8, "with Explorer in front, the keyboard hook is on")
        kind, top, focus = Q.foreground_context()
        H.notes.append(f"foreground: {kind}, focus class {focus}")
        check(kind == "explorer" and top == hwnd, "the foreground is recognised as an Explorer folder", f"{kind} {top} vs {hwnd}")
        sel = Q.selected_items(hwnd, "explorer", Q.active_tab_title(getattr(Q.foreground_context, "focus_hwnd", 0)))
        check([os.path.normcase(p) for p in sel] == [os.path.normcase(str(target))], "the selection read from Explorer is the file",
              str(sel))
        check(Q.should_preview(kind, focus, 5), "with the file list focused, Space means preview", focus)
        # the hook's handler, given a Space key-down as Windows would hand it
        k = Q.KBDLLHOOKSTRUCT(vkCode=Q.VK_SPACE)
        t0 = time.perf_counter()
        ql._on_key(0, Q.WM_KEYDOWN, ctypes.addressof(k))
        wait(lambda: q.isVisible(), 4, "Space on the selected file opens the preview")
        H.notes.append(f"Space → preview window: {(time.perf_counter() - t0) * 1000:.0f} ms")
        check(os.path.normcase(q.property("path")) == os.path.normcase(str(target)), "previewing the selected file", q.property("path"))
        files = q.property("files")
        files = files.toVariant() if hasattr(files, "toVariant") else files
        check(len(files) >= 2, "with the folder's other media to browse", str(len(files)))
        seek = q.findChild(__import__("PySide6.QtCore", fromlist=["QObject"]).QObject, "quickSeek")
        wait(lambda: seek.property("value") > 300, 6, "the preview plays")
        check(time.perf_counter() - t0 < 4, "from Space to a playing preview in a few seconds")
        # typing a name in Explorer is not a preview: a Space right after letters does nothing
        from PySide6.QtCore import QMetaObject
        QMetaObject.invokeMethod(q, "close")
        wait(lambda: not q.isVisible(), 3)
        wait(lambda: u.GetForegroundWindow() == hwnd, 4, "closing the preview gives Explorer the focus back")
        letter = Q.KBDLLHOOKSTRUCT(vkCode=0x41)
        ql._on_key(0, Q.WM_KEYDOWN, ctypes.addressof(letter))
        ql._on_key(0, Q.WM_KEYDOWN, ctypes.addressof(k))
        pump(1.0)
        check(not q.isVisible(), "a Space right after a letter (typing a file name) opens nothing")
    finally:
        u.PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0)   # WM_CLOSE: only the window this test opened
    pump(0.8)
    wait(lambda: ql._hook is None or u.GetForegroundWindow() != hwnd, 4)


def _visible_item(name):
    try:
        it = H.parts.find(name)
    except LookupError:
        return None
    return it if it.isVisible() and it.opacity() > 0.5 else None


def _show_setting(name):
    """Open the settings tab holding the control and bring it into view."""
    page = H.parts.find("settingsPage")
    it = H.parts.find(name)
    _scroll_to(page, it)
    pump(0.3)
    return it


@scenario(timeout=120)
def feature_settings_tabs_and_sound_controls_by_mouse():
    """Ajustes by mouse: the gear in the sidebar, every tab, the normaliser switch, the audio
    output picker and the subtitle look."""
    e, w = eng(), win()
    w.setProperty("page", 0)
    pump(0.4)
    _click_on(H.parts.find("nav5"), "the gear in the sidebar")
    wait(lambda: w.property("page") == 5, 2, "the gear opens Ajustes")
    pump(0.5)
    page = H.parts.find("settingsPage")
    check(H.parts.find("settingsTabs").isVisible(), "the tab bar shows")
    tabs = page.property("tabs")
    tabs = tabs.toVariant() if hasattr(tabs, "toVariant") else tabs
    for key in [t["key"] for t in tabs][::-1]:
        page.setProperty("contentY", 0)
        pump(0.15)
        _click_on(H.parts.find("settingsTab_" + key), f"tab {key}")
        check(page.property("tab") == key, f"the {key} tab opens", page.property("tab"))
    check(H.parts.find("languageCard").isVisible(), "Geral shows the language card")
    check(not H.parts.find("soundSubsCard").isVisible(), "and not the sound card (one tab at a time)")
    # sound: normaliser, output device
    before = e.normalize
    _click_on(_show_setting("settingsNormalize"), "normalise switch")
    wait(lambda: e.normalize != before, 3, "the switch turns the volume normaliser on")
    check(H.parts.find("soundSubsCard").isVisible(), "the sound card is the one showing")
    _click_on(_show_setting("settingsNormalize"), "normalise switch")
    wait(lambda: e.normalize == before, 3, "and off again")
    wait(lambda: len(e.audioDevices) > 1 or not getattr(e, "_devices_busy", False), 10)
    devs = e.audioDevices
    drop = _show_setting("settingsDevices")
    _click_on(drop, "audio output picker")
    wait(lambda: drop.property("opened"), 2, "the picker opens its list")
    check(_visible_item("settingsDev_0") is not None, "the list starts with the system default")
    if len(devs) > 1:
        _click_on(H.parts.find("settingsDev_1"), "second output")
        wait(lambda: e.audioDevice == devs[1]["id"], 2, "picking an output selects it")
        check(not drop.property("opened"), "and closes the list")
        e.setAudioDevice("")
    else:
        _click_on(H.parts.find("settingsDev_0"), "default output")
    H.notes.append(f"{len(devs) - 1} audio output(s) in the picker")
    # subtitles: size by mouse
    _click_on(_show_setting("settings_subSize_large"), "large subtitles")
    wait(lambda: e.subtitleStyle["subSize"] == "large", 2, "Grande sets large subtitles")
    _click_on(_show_setting("settings_subColor_yellow"), "yellow subtitles")
    wait(lambda: e.subtitleStyle["subColor"] == "yellow", 2, "Amarela sets yellow subtitles")
    _click_on(_show_setting("settings_subSize_normal"), "normal size")
    _click_on(_show_setting("settings_subColor_white"), "white")
    wait(lambda: e.subtitleStyle["subSize"] == "normal" and e.subtitleStyle["subColor"] == "white", 2, "and back")
    # no update on offer: no button to press
    page.setProperty("tab", "system")
    pump(0.3)
    check(not H.parts.find("updateNow").isVisible(), "with nothing to update, no update button shows")


@scenario(timeout=120)
def feature_player_side_controls_by_mouse():
    """In the player: the picture box, chapters (button, marks on the bar), the tracks panel's
    output list and subtitle size, the normaliser in its panel, and the cast button."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    e, w = eng(), win()
    pv = H.parts.find("playerView")

    def controls():
        QTest.mouseMove(w, QPoint(w.width() // 2, w.height() // 3))
        pump(0.1)
        pv.setProperty("controlsVisible", True)
        pump(0.35)
    e.openFiles([str(H.fx["video"])])
    w.setProperty("playerOpen", True)
    playing_media(video=True)
    box = H.parts.find("videoBox")
    check(box.isVisible() and box.width() > 300 and box.height() > 150, "the picture box fills the player", f"{box.width()}x{box.height()}")
    check(not H.parts.find("castingBanner").isVisible(), "no casting banner while playing here")
    controls()
    check(_visible_item("pvChapters") is None, "no chapters button for a file without chapters")
    # tracks panel: output list, subtitle size
    controls()
    _click_on(H.parts.find("pvTracks"), "Faixas")
    check(pv.property("panel") == "tracks", "Faixas opens the tracks panel")
    wait(lambda: len(e.audioDevices) > 1 or not getattr(e, "_devices_busy", False), 10)
    pump(0.4)
    lst = H.parts.find("audioDevices")
    check(lst.isVisible() and lst.property("count") == len(e.audioDevices), "the panel lists the audio outputs",
          f"{lst.property('count')} vs {len(e.audioDevices)}")
    if len(e.audioDevices) > 1:
        controls()
        _click_on(H.parts.find("adev_1"), "second output")
        wait(lambda: e.audioDevice == e.audioDevices[1]["id"], 2, "clicking an output selects it")
        e.setAudioDevice("")
    controls()
    _click_on(H.parts.find("subSize_large"), "large subtitles")
    wait(lambda: e.subtitleStyle["subSize"] == "large", 2, "the panel's Grande sets large subtitles")
    playing_media(video=True)                     # the player was rebuilt for it: the picture is back
    e.setSubtitleStyle("subSize", "normal")
    playing_media(video=True)
    # the normaliser lives in one of the panels
    found = None
    for panel in ("eq", "speed", "tracks", "adjust"):
        pv.setProperty("panel", panel)
        controls()
        if _visible_item("normalizeToggle") is not None:
            found = panel
            break
    check(found is not None, "the volume normaliser switch is in a player panel")
    before = e.normalize
    _click_on(H.parts.find("normalizeToggle"), "normaliser")
    wait(lambda: e.normalize != before, 3, "the switch toggles the normaliser")
    playing_media(video=True)
    pv.setProperty("panel", found); controls()
    _click_on(H.parts.find("normalizeToggle"), "normaliser")
    wait(lambda: e.normalize == before, 3, "and back")
    playing_media(video=True)
    pv.setProperty("panel", "")
    # cast: the button starts looking and opens its panel
    controls()
    _click_on(H.parts.find("pvCast"), "cast")
    check(pv.property("panel") == "cast" and e.discovering, "the cast button opens its panel and starts looking")
    check(_visible_item("castLocal") is not None, "with this computer listed")
    pv.setProperty("panel", "")
    e.stopCastDiscovery() if hasattr(e, "stopCastDiscovery") else None
    # chapters: button, panel, marks on the seek bar (a video with chapters, made here with ffmpeg)
    import shutil
    import subprocess
    chaptered = Path(os.environ["REFRATA_DATA"]) / "com capitulos.mkv"
    if shutil.which("ffmpeg"):
        meta = chaptered.with_suffix(".txt")
        meta.write_text(";FFMETADATA1\n" + "".join(
            f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={a}\nEND={b}\ntitle={t}\n"
            for a, b, t in ((0, 8000, "Abertura"), (8000, 18000, "Meio"), (18000, 30000, "Final"))), encoding="utf8")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(H.fx["video"]), "-i", str(meta), "-map_metadata", "1",
                        "-map_chapters", "1", "-codec", "copy", str(chaptered)], check=True)
        e.openFiles([str(chaptered)])
    else:
        H.notes.append("no ffmpeg here: the chapter button and marks were not exercised")
        e.openFiles([H.fmt["chapters"]])
    wait(lambda: len(e.chapters) == 3, 8, "the chaptered file lists its chapters")
    w.setProperty("playerOpen", True)
    pump(0.8)
    if not e.isAudio:
        playing_media(video=True)
        controls()
        _click_on(H.parts.find("pvChapters"), "Capítulos")
        check(pv.property("panel") == "chapters", "Capítulos opens the chapters panel")
        controls()
        _click_on(H.parts.find("chapter_1"), "second chapter")
        wait(lambda: e.chapter == 1, 3, "clicking a chapter jumps to it")
        marks = [m for m in (_visible_item(f"chapterMark_{i}") for i in range(3)) if m is not None]
        check(len(marks) == 2, "the seek bar marks where each later chapter starts", str(len(marks)))
    else:
        H.notes.append("chapter fixture is audio: chapter button and marks belong to the video bar")


@scenario(timeout=90)
def feature_music_page_and_stage_by_mouse():
    """Music: the play button on a row starts that song; the stage shows what plays (title, artist,
    clock, progress ring) and the carousel of the queue."""
    e, w = eng(), win()
    lib = H.parts.library
    wait(lambda: not lib._scanning, 30, "library scanned")
    w.setProperty("playerOpen", False)
    w.setProperty("page", 2)
    pump(1.0)
    from PySide6.QtTest import QTest
    row = H.parts.find("music_0")
    check(row.isVisible(), "the music page lists the songs")

    def play_button():
        stack = [row]
        while stack:
            it = stack.pop()
            for c in it.childItems():
                if c.objectName() == "rowPlay":
                    return c
                stack.append(c)
    btn = play_button()
    check(btn is not None and not btn.isVisible(), "a row's play button stays out of the way until the mouse is over the row")
    QTest.mouseMove(w, _item_center(row))
    wait(lambda: btn.isVisible() and btn.opacity() > 0.9, 2, "hovering a row shows its play button")
    _click_on(btn, "the first song's play button")
    playing_media(video=False)
    check(e.isAudio, "a song plays", e.currentPath)
    first = e.currentPath
    w.setProperty("playerOpen", True)
    pump(1.2)
    now = H.parts.find("mpNow")
    check(now.isVisible(), "the stage shows the now-playing block")
    artist = H.parts.find("mpArtist").property("value")
    check(isinstance(artist, str) and artist, "with an artist line (or 'unknown artist')", repr(artist))
    clock = H.parts.find("mpTime")
    check("/" in clock.property("text"), "the clock shows position / length", clock.property("text"))
    ring = H.parts.find("mpRing")
    v0 = ring.property("value")
    pump(1.2)
    check(0 <= v0 < ring.property("value") <= 1, "the progress ring advances with the song", f"{v0} -> {ring.property('value')}")
    car = H.parts.find("musicCarousel")
    check(car.isVisible() and car.width() > 200, "the carousel of the queue shows")
    if len(e.queue) > 1:
        e.next()
        wait(lambda: e.currentPath != first and e.state == "playing", 5, "next song")
        pump(0.6)
        check(H.parts.find("mpTime").property("text").startswith("0:0"), "the clock restarts with the new song",
              H.parts.find("mpTime").property("text"))


@scenario(timeout=60)
def feature_quick_player_title_and_pin():
    """The quick player names what it plays; in compact mode its pin keeps it above other windows."""
    from PySide6.QtCore import Qt
    q = quick_window()
    open_quick(H.fx["video"], "preview")
    wait(lambda: q.isVisible(), 3)
    title = _find_in(q, "quickTitle")
    wait(lambda: title.property("value"), 3, "the preview shows a title")
    check("Filme" in title.property("value"), "the file's own title", title.property("value"))
    keys = _find_in(q, "quickKeys")
    held = q.activeFocusItem()
    check(held is not None and held.objectName() == "quickKeys" and keys.property("focus"),
          "the preview's key handler is what holds the keyboard in its window",
          held.objectName() if held is not None else "nothing")
    check(not _find_in(q, "quickPin").isVisible(), "a preview has no pin (it is always on top)")
    check(bool(q.flags() & Qt.WindowType.WindowStaysOnTopHint), "and stays on top")
    from PySide6.QtCore import QMetaObject
    QMetaObject.invokeMethod(q, "close")
    wait(lambda: not q.isVisible(), 3)
    open_quick(H.fx["video2"], "compact")
    wait(lambda: q.isVisible(), 3)
    pump(0.6)
    pin = _find_in(q, "quickPin")
    check(pin.isVisible(), "the compact player has a pin")
    on = bool(q.property("pinned"))
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    QTest.mouseMove(q, QPoint(q.width() // 2, q.height() // 2)); pump(0.3)
    _click_on(pin, "pin", q)
    wait(lambda: bool(q.property("pinned")) != on, 2, "the pin toggles")
    pump(0.3)
    check(bool(q.flags() & Qt.WindowType.WindowStaysOnTopHint) == (not on), "and the window follows it",
          f"pinned={q.property('pinned')}")
    check(q.isVisible(), "the window is still there after the change")
    QMetaObject.invokeMethod(q, "close")
    wait(lambda: not q.isVisible(), 3)


def _setup_empty_library(data: Path):
    return {"folders": []}


@scenario(timeout=60, setup=_setup_empty_library)
def feature_first_run_empty_home_offers_what_to_do():
    """With nothing in the library, Home offers the two ways in: open files, add a folder. Each
    asks the app for exactly that."""
    w = win()
    lib = H.parts.library
    wait(lambda: not lib._scanning, 15)
    w.setProperty("page", 0)
    pump(0.8)
    home = H.parts.find("homePage")
    asked = []
    home.openFiles.connect(lambda: asked.append("files"))
    home.openFolder.connect(lambda: asked.append("folder"))
    for name, what in (("emptyOpenFiles", "files"), ("emptyAddFolder", "folder")):
        b = _visible_item(name)
        check(b is not None, f"the empty Home shows '{name}'")
        _click_on(b, name)
        pump(0.6)
        check(asked and asked[-1] == what, f"{name} asks to open {what}", str(asked))
        _close_native_dialogs()
    check(asked == ["files", "folder"], "each button asked once", str(asked))


def _close_native_dialogs():
    """Close any file / folder dialog the app opened (they belong to this process only)."""
    from PySide6.QtCore import QMetaObject, QObject
    for o in H.parts.qml.rootObjects()[0].findChildren(QObject):
        name = o.metaObject().className()
        if "FileDialog" in name or "FolderDialog" in name:
            try:
                if o.property("visible"):
                    QMetaObject.invokeMethod(o, "close")
            except RuntimeError:
                pass
    pump(0.5)


@scenario(timeout=60, solo=True)
def feature_pip_keys_drive_the_video_once():
    """With the PiP window in front, its keys (Space, → ←) act on the video exactly once: the
    minimised main window must not answer them too."""
    from PySide6.QtCore import QMetaObject, Q_ARG, QObject, Qt
    from PySide6.QtGui import QGuiApplication, QWindow
    e, w = eng(), win()
    e.openFiles([str(H.fx["video"])])
    w.setProperty("playerOpen", True)
    playing_media(video=True)
    pip = H.parts.qml.rootObjects()[0].findChild(QWindow, "pipWindow")
    H.parts.find("pvPip").clicked.emit()
    wait(lambda: pip.isVisible(), 3, "PiP open")
    QMetaObject.invokeMethod(H.parts.ctl, "activateWindow", Q_ARG(QObject, pip))
    wait(lambda: QGuiApplication.focusWindow() is pip, 4, "the PiP window has the keyboard")
    check(not w.isActive(), "the main window is not active behind it")
    _key_to_focus(Qt.Key.Key_Space, "Space")
    wait(lambda: e.state == "paused", 2, "Space in the PiP pauses")
    pump(0.4)
    check(e.state == "paused", "and stays paused (not toggled twice)", e.state)
    t = e.time
    _key_to_focus(Qt.Key.Key_Right, "→")
    wait(lambda: e.time > t + 8000, 2, "→ in the PiP skips 10 s")
    pump(0.4)
    check(e.time < t + 13000, "once (not 20 s: two windows answering)", f"{t} -> {e.time}")
    _key_to_focus(Qt.Key.Key_Space, "Space")
    wait(lambda: e.state == "playing", 2, "Space plays again")
    playing_media(video=True)
    _key_to_focus(Qt.Key.Key_Escape, "Esc")
    wait(lambda: not pip.isVisible() and w.visibility() != QWindow.Visibility.Minimized, 4, "Esc goes back to the app")
    playing_media(video=True)
    # a maximised app comes back from the PiP maximised (Qt forgets it once minimised)
    w.showMaximized()
    wait(lambda: w.visibility() == QWindow.Visibility.Maximized, 3)
    pump(0.4)
    H.parts.find("pvPip").clicked.emit()
    wait(lambda: pip.isVisible() and w.visibility() == QWindow.Visibility.Minimized, 4, "PiP again, app minimised")
    pump(1.5)
    QMetaObject.invokeMethod(H.parts.ctl, "activateWindow", Q_ARG(QObject, pip))
    wait(lambda: QGuiApplication.focusWindow() is pip, 4)
    _key_to_focus(Qt.Key.Key_Escape, "Esc")
    wait(lambda: not pip.isVisible() and w.visibility() == QWindow.Visibility.Maximized, 4,
         "back from the PiP, maximised as it was")
    w.showNormal()


@scenario(timeout=60)
def feature_live_scrubbing():
    """Dragging the seek bar moves the picture while the button is still down (playing and paused),
    not only on release; the drop point is where it ends up."""
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtTest import QTest
    e, w = eng(), win()
    e.openFiles([str(H.fx["video"])])
    w.setProperty("playerOpen", True)
    playing_media(video=True)
    wait(lambda: e.length > 20_000, 5, "the video length is known")
    bar = H.parts.find("seekBar")

    def at(frac):
        return bar.mapToScene(QPointF(bar.width() * frac, bar.height() / 2)).toPoint()

    def drag(fracs, settle=0.35):
        """Press at the first point, move through the rest; returns libVLC's clock and the frames
        shown after each move, all before the button goes up."""
        QTest.mouseMove(w, QPoint(w.width() // 2, w.height() // 2))
        pump(0.6)                                  # the controls slide in (hidden, the bar sits below the window)
        QTest.mouseMove(w, at(fracs[0]))
        pump(0.2)
        QTest.mousePress(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(fracs[0]))
        seen = []
        for f in fracs[1:]:
            for k in range(1, 5):                  # a few small moves, like a real hand
                prev = fracs[fracs.index(f) - 1]
                QTest.mouseMove(w, at(prev + (f - prev) * k / 4))
                pump(0.02)
            frames = e._frames_seen
            t0 = time.time()
            while time.time() - t0 < settle + 1.5 and not (abs(e._mp.get_time() - f * e.length) < 1500
                                                          and e._frames_seen > frames):
                pump(0.03)
            seen.append((f, e._mp.get_time(), e._frames_seen - frames))
        return seen

    from PySide6.QtCore import Qt
    moves = drag([0.1, 0.35, 0.6, 0.85])
    for f, t, frames in moves:
        check(abs(t - f * e.length) < 1500, f"playing: the clock follows the drag to {f:.0%} before release",
              f"{t} vs {int(f * e.length)}")
        check(frames > 0, f"playing: a new picture at {f:.0%} while dragging", str(frames))
    QTest.mouseRelease(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(0.5))
    wait(lambda: abs(e._mp.get_time() - 0.5 * e.length) < 1500, 3, "released: it lands on the drop point")

    e.pause()
    wait(lambda: e.state == "paused", 3, "paused")
    moves = drag([0.5, 0.2, 0.7])
    for f, t, frames in moves:
        check(abs(t - f * e.length) < 1500, f"paused: the clock follows the drag to {f:.0%}", f"{t} vs {int(f * e.length)}")
        check(frames > 0, f"paused: the still picture changes at {f:.0%}", str(frames))
    QTest.mouseRelease(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(0.7))
    pump(0.5)
    check(e.state == "paused", "still paused after the drag", e.state)


@scenario(timeout=60)
def feature_review_custom_shortcuts():
    """Shortcuts can be changed in Ajustes: click a key, press the new one. The key moves away from
    the action that had it, the change applies at once and is saved; the player does not react to
    the key being recorded; one button restores every default."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    e, w, keys = eng(), win(), H.parts.keys
    e.openFiles([str(H.fx["tone_a"])])
    playing_media(video=False)
    _focus()
    keys.assign("mute", "K")
    pump(0.2)
    check(keys.map["mute"] == "K" and H.parts.settings.value("shortcuts") == {"mute": "K"}, "the new key is stored",
          str(H.parts.settings.value("shortcuts")))
    was = e.muted   # the battery starts muted, so compare against the state before each key
    QTest.keyClick(w, Qt.Key.Key_M)
    pump(0.3)
    check(e.muted == was, "the old key no longer mutes", f"focus={type(QGuiApplication.focusObject()).__name__}")
    QTest.keyClick(w, Qt.Key.Key_K)
    pump(0.3)
    check(e.muted != was, "the new key mutes / unmutes")
    QTest.keyClick(w, Qt.Key.Key_K)
    pump(0.3)
    check(e.muted == was, "and toggles back")
    # record a key from Ajustes: Space goes to "mute" and leaves play/pause without a key
    w.setProperty("page", 5)
    pump(0.8)
    page, chip = H.parts.find("settingsPage"), H.parts.find("key_mute")
    _scroll_to(page, chip)
    _click(chip)
    check(H.parts.find("keysCard").property("capturing") == "mute", "clicking a key starts listening")
    playing = e.playing
    QTest.keyClick(w, Qt.Key.Key_Space)
    pump(0.4)
    check(e.playing == playing, "Space while recording is not play/pause")
    check(keys.map["mute"] == "Space" and keys.map["playPause"] == "", "Space moved to Silenciar", str(keys.map))
    check(H.parts.find("keysCard").property("capturing") == "", "listening ends after one key")
    _click(chip)
    QTest.keyClick(w, Qt.Key.Key_Escape)
    pump(0.3)
    check(keys.map["mute"] == "Space" and H.parts.find("keysCard").property("capturing") == "", "Esc cancels")
    check(keys.assign("pip", "Esc") == "reserved" and keys.map["pip"] == "Ctrl+P", "Esc can't be taken")
    reset = H.parts.find("keysReset")
    _scroll_to(page, reset)
    _click(reset)
    check(keys.map["mute"] == "M" and keys.map["playPause"] == "Space" and not keys.customized, "defaults restored")
    check(H.parts.settings.value("shortcuts") == {}, "nothing custom left in the settings")


@scenario(timeout=90)
def feature_review_library_lists_duplicates_once():
    """Adding all of C:\\ listed Windows' sample clips several times: the same file is hard-linked
    from several folders (WinSxS) or copied, with the same name, size and date. Each shows once; a
    different file with the same name stays; overlapping folders list a file once."""
    import shutil
    lib, st = H.parts.library, H.parts.settings
    root = Path(os.environ["REFRATA_E2E_TMP"]) / "disco"
    shutil.rmtree(root, ignore_errors=True)
    src = Path(H.fx["video"])
    mine = root / "Filmes" / "amostra.mkv"
    mine.parent.mkdir(parents=True)
    shutil.copyfile(src, mine)
    link = root / "Windows" / "WinSxS" / "pacote" / "amostra.mkv"
    link.parent.mkdir(parents=True)
    os.link(mine, link)                                   # hard link: same file
    copy = root / "Sistema" / "amostra.mkv"
    copy.parent.mkdir(parents=True)
    shutil.copy2(mine, copy)                              # a copy (Explorer keeps the date too)
    diff = root / "Outro" / "amostra.mkv"
    diff.parent.mkdir(parents=True)
    data = bytearray(src.read_bytes())
    data[-10] ^= 0xFF                                     # same name and size, different tail
    diff.write_bytes(bytes(data))
    os.utime(diff, (time.time() - 86400, time.time() - 86400))   # another file: written another day
    renamed = root / "Outro" / "episodio 2.mkv"
    os.link(mine, renamed)                                # same file, another name: a separate entry    wait(lambda: not lib.scanning, 20, "the startup scan ends")   # a rescan asked during it is ignored
    st.setValue("folders", [str(root), str(root / "Filmes")])
    pump(0.3)
    wait(lambda: not lib.scanning, 20, "scan ends")
    lib.rescan()
    pump(0.3)
    wait(lambda: not lib.scanning, 20, "rescan ends")
    paths = [os.path.normcase(it["path"]) for it in lib._model._items]
    same = [p for p in paths if p in {os.path.normcase(str(x)) for x in (mine, link, copy)}]
    check(len(same) == 1, "the sample (link, copy, overlapping folder) is listed once", str(paths))
    check(os.path.normcase(str(diff)) in paths, "a different file with the same name stays", str(paths))
    check(os.path.normcase(str(renamed)) in paths, "the same file under another name stays", str(paths))



# ============================================================ layout: every language, every screen
def _overflowing_texts(root_item, where):
    """Texts that do not fit: a fixed width narrower than the text with no eliding/wrapping, or
    a text running past the right edge of the window. Elided text (titles) is intended."""
    from PySide6.QtCore import QPointF
    out = []
    W = win().width()
    stack = [root_item]
    while stack:
        it = stack.pop()
        if not it.isVisible() or it.opacity() < 0.05:
            continue
        stack.extend(it.childItems())
        if it.metaObject().className() not in ("QQuickText",):
            continue
        txt = it.property("text") or ""
        if not txt.strip():
            continue
        # enum properties (elide, wrapMode, textFormat) do not convert through PySide: read them in QML
        from PySide6.QtQml import QQmlExpression
        ev = lambda expr: QQmlExpression(H.parts.qml.rootContext(), it, expr).evaluate()[0]  # noqa: E731
        elide, wrap, rich = ev("elide"), ev("wrapMode"), ev("textFormat === Text.RichText || textFormat === Text.StyledText")
        if rich and ("<" in txt):
            continue
        w, iw = it.width(), it.implicitWidth()
        if w <= 0:
            continue
        p = it.mapToScene(QPointF(0, 0))
        if p.y() > win().height() or p.y() + it.height() < 0 or p.x() > W:
            continue   # scrolled out of view
        NONE = 3   # Qt::ElideNone (ElideLeft=0, Right=1, Middle=2)
        if elide != NONE and it.property("truncated"):
            # "…" is fine for a title, not for the label of a button, chip or segment
            anc, kinds = it.parentItem(), []
            for _ in range(4):
                if anc is None:
                    break
                kinds.append(anc.metaObject().className())
                anc = anc.parentItem()
            if any(k.startswith(("PillButton", "Chip", "Segmented", "Toggle")) for k in kinds):
                out.append(f"{where}: button label cut “{txt[:50]}”")
            continue
        if elide == NONE and wrap == 0 and iw > w + 3:
            out.append(f"{where}: “{txt[:50]}” needs {iw:.0f}px, has {w:.0f}")
        elif elide == NONE and p.x() + min(w, iw) > W + 2:
            out.append(f"{where}: “{txt[:50]}” runs off the window")
    return out


@scenario(timeout=600)
def layout_every_language_fits():
    from refrata.i18n import CODES
    from PySide6.QtQml import QQmlComponent
    # the detector itself: a deliberately too-narrow text must be reported
    comp = QQmlComponent(H.parts.qml)
    comp.setData(b'import QtQuick\nText { x: 10; y: 10; width: 30; text: "texto largo demais"; font.pixelSize: 16 }', "probe.qml")
    probe = comp.create()
    probe.setParentItem(win().contentItem())
    pump(0.1)
    found = _overflowing_texts(win().contentItem(), "probe")
    check(any("texto largo demais" in f for f in found), "the overflow detector catches a known overflow", str(found))
    probe.setParentItem(None)
    probe.deleteLater()
    pump(0.1)
    e, i18n = eng(), H.parts.i18n
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    problems = []
    part, parts = (int(x) for x in os.environ.get("REFRATA_LAYOUT_PART", "0/1").split("/"))
    for code in list(CODES)[part::parts]:      # one slice of the languages per process (see below)
        i18n.setProperty("language", code)
        pump(0.4)
        win().setProperty("playerOpen", False)
        for page in range(6):
            win().setProperty("page", page)
            pump(0.35)
            problems += _overflowing_texts(win().contentItem(), f"{code}/page{page}")
        if True:   # settings: every tab, each checked in slices while scrolling
            win().setProperty("page", 5)
            sp = H.parts.find("settingsPage")
            tabs = sp.property("tabs")
            tabs = tabs.toVariant() if hasattr(tabs, "toVariant") else tabs
            for tab in [t["key"] for t in tabs]:
                sp.setProperty("tab", tab)
                pump(0.2)
                for y in range(0, int(sp.property("contentHeight")), 500):
                    sp.setProperty("contentY", y)
                    pump(0.15)
                    problems += _overflowing_texts(win().contentItem(), f"{code}/settings:{tab}@{y}")
            sp.setProperty("contentY", 0)
        win().setProperty("playerOpen", True)
        pump(0.6)
        pv = H.parts.find("playerView")
        for panel in ("", "tracks", "speed", "eq", "adjust", "sleep", "info", "cast"):
            pv.setProperty("panel", panel)
            pv.setProperty("controlsVisible", True)
            pump(0.35)
            problems += _overflowing_texts(win().contentItem(), f"{code}/player:{panel or 'controls'}")
        pv.setProperty("panel", "")
    i18n.setProperty("language", "auto")
    uniq = sorted(set(problems))
    H.notes.append(f"{len(uniq)} overflowing text(s)")
    check(not uniq, "every text fits in every language", "\n          " + "\n          ".join(uniq[:60]))




# 14 languages x every page, tab and panel take minutes in one process: four processes, a slice each
LAYOUT_PARTS = 4
for _i in range(LAYOUT_PARTS):
    _sc = Scenario(layout_every_language_fits, timeout=300, env={"REFRATA_LAYOUT_PART": f"{_i}/{LAYOUT_PARTS}"},
                   solo=False)
    _sc.name = f"layout_every_language_fits_{_i + 1}of{LAYOUT_PARTS}"
    SCENARIOS[_sc.name] = _sc
del SCENARIOS["layout_every_language_fits"]

@scenario(timeout=400, env={"QT_SCALE_FACTOR": "1.5"})
def layout_smallest_window_at_150_percent():
    """Windows display scale 150 % and the window at its minimum size (1040×660), with the
    longest languages: nothing may spill out or get cut."""
    e, i18n = eng(), H.parts.i18n
    win().setWidth(1040)
    win().setHeight(660)
    pump(0.5)
    check(abs(win().devicePixelRatio() - 1.5) < 0.01, "running at 150 %", str(win().devicePixelRatio()))
    e.openFiles([str(H.fx["video"])])
    playing_media(video=True)
    problems = []
    for code in ("pt", "de", "ru", "fr", "hi"):
        i18n.setProperty("language", code)
        pump(0.4)
        win().setProperty("playerOpen", False)
        for page in range(6):
            win().setProperty("page", page)
            pump(0.35)
            problems += _overflowing_texts(win().contentItem(), f"{code}/page{page}")
        win().setProperty("page", 5)
        sp = H.parts.find("settingsPage")
        for y in range(0, int(sp.property("contentHeight")), 400):
            sp.setProperty("contentY", y)
            pump(0.15)
            problems += _overflowing_texts(win().contentItem(), f"{code}/settings@{y}")
        sp.setProperty("contentY", 0)
        win().setProperty("playerOpen", True)
        pump(0.6)
        pv = H.parts.find("playerView")
        for panel in ("", "tracks", "speed", "eq", "adjust", "sleep", "info", "cast"):
            pv.setProperty("panel", panel)
            pv.setProperty("controlsVisible", True)
            pump(0.35)
            problems += _overflowing_texts(win().contentItem(), f"{code}/player:{panel or 'controls'}")
            if panel:
                pop = H.parts.find("popover")
                if pop.property("y") < 0 or pop.property("x") + pop.property("width") > win().width() + 1:
                    problems.append(f"{code}/player:{panel}: panel leaves the window")
        pv.setProperty("panel", "")
    i18n.setProperty("language", "auto")
    uniq = sorted(set(problems))
    check(not uniq, "everything fits at 150 % in the smallest window", "\n          " + "\n          ".join(uniq[:60]))


# ============================================================ scenario: monkey
MONKEY_DENY = ("Open", "AddFolder", "addSubBtn", "pvReveal", "aboutContact", "aboutSource", "aboutChip_",
               "toggleAutostart", "toggleAutoUpdate", "toggleBackground", "toggleQuickLook", "update",
               "settingsAddFolder", "rescanBtn", "adev_", "settingsDev_",
               "defaultAppBtn", "keysReset", "key_",   # registry / remapping: own scenarios
               "pvPip",   # minimises the window the monkey is clicking in (covered by its own scenario)
               "cast_")   # never cast to a real TV on the person's network


@scenario(timeout=900)
def monkey_random_use():
    """Random clicks on named controls and random shortcut keys, with media playing on and off.
    Fails on any Qt/QML warning, Python error or a frozen event loop."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    seconds = float(os.environ.get("REFRATA_MONKEY_SECONDS", "120"))
    rnd = random.Random(int(os.environ.get("REFRATA_MONKEY_SEED", "1")))
    e, w = eng(), H.parts.win
    files = [str(H.fx[k]) for k in ("video", "tone_a", "tone_long", "video2", "tone_b")]
    keys = [Qt.Key.Key_Space, Qt.Key.Key_F, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
            Qt.Key.Key_M, Qt.Key.Key_N, Qt.Key.Key_P, Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight,
            Qt.Key.Key_Equal, Qt.Key.Key_E, Qt.Key.Key_L, Qt.Key.Key_T, Qt.Key.Key_Escape, Qt.Key.Key_S]
    log = []

    def visible_named():
        out, stack = [], [w.contentItem()]
        while stack:
            it = stack.pop()
            for c in it.childItems():
                if not c.isVisible() or c.opacity() < 0.05:
                    continue
                n = c.objectName()
                if n and c.width() > 4 and c.height() > 4 and not any(d in n for d in MONKEY_DENY):
                    out.append(c)
                stack.append(c)
        return out

    end = time.time() + seconds
    start = time.time()
    trace = open(Path(os.environ["REFRATA_DATA"]) / "monkey.trace", "w", encoding="utf8")
    worst_gap = 0.0
    steps = 0
    from PySide6.QtGui import QGuiApplication
    behind = None                              # the main player as the preview found it
    while time.time() < end:
        steps += 1
        r = rnd.random()
        t = time.time()
        q = quick_window()
        previewing = q.isVisible() and q.property("shown") and QGuiApplication.focusWindow() is q
        if previewing:
            if behind is None:
                behind = (e.currentPath, e.state, e.time)
            r = 0.2 + rnd.random() * 0.25     # with the preview in front, only keys (what a person does)
        if r < 0.08:
            k = rnd.randint(1, 3)
            e.openFiles(rnd.sample(files, k))
            log.append("open")
        elif r < 0.12:
            win().setProperty("playerOpen", not win().property("playerOpen"))
            log.append("player")
        elif r < 0.16:
            win().setProperty("page", rnd.randrange(6))
            log.append("page")
        elif r < 0.19:
            # the Space preview over whatever is going on (Explorer would ask for it)
            if not quick_window().isVisible():
                f = rnd.choice(files)
                H.parts.quicklook.previewRequested.emit(f, files)
                log.append(f"preview {Path(f).name}")
            else:
                log.append("preview (already open)")
        elif r < 0.45:
            # to the window the system has in front, as a keyboard would (the preview's keys
            # reached the main window's shortcuts this way, never by sending to a chosen window)
            from PySide6.QtGui import QGuiApplication
            k = rnd.choice(keys + [Qt.Key.Key_Return])
            target = QGuiApplication.focusWindow() or w
            QTest.keyClick(target, k)
            log.append(f"key {k.name} -> {target.objectName() or 'main'}")
        else:
            items = visible_named()
            if items:
                it = rnd.choice(items)
                p = it.mapToScene(QPointF(it.width() * rnd.uniform(0.2, 0.8), it.height() * rnd.uniform(0.2, 0.8))).toPoint()
                if 0 <= p.x() < w.width() and 0 <= p.y() < w.height():
                    name = it.objectName()   # the click may close what holds it
                    trace.write(f"{time.time() - start:7.2f} about to click {name}\n")
                    trace.flush()
                    QTest.mouseClick(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
                    log.append(f"click {name}")
        trace.write(f"{time.time() - start:7.2f} {log[-1] if log else ''}\n")
        trace.flush()
        pump(rnd.choice((0.01, 0.05, 0.15, 0.4)))
        worst_gap = max(worst_gap, time.time() - t)
        # rule: while the preview has the keyboard, the player behind it is left alone
        q = quick_window()
        if behind is not None:
            still = q.isVisible() and q.property("shown")
            if still and QGuiApplication.focusWindow() is q:
                path, state, at = behind
                moved = e.currentPath != path or (state == "paused" and (e.state != "paused" or abs(e.time - at) > 1500))
                if moved:
                    raise Fail(f"a key in the preview drove the player behind it: {Path(path).name} {state} {at} -> "
                               f"{Path(e.currentPath).name} {e.state} {e.time}; steps: " + " | ".join(log[-8:]))
            elif not still:
                behind = None

        import main
        if main.QT_MESSAGES or H.errors:
            raise Fail("warning or error after steps: " + " | ".join(log[-12:]) +
                       " :: " + "; ".join(main.QT_MESSAGES[:2] + H.errors[:1])[:900])
        if quick_window().isVisible() and rnd.random() < 0.08:
            from PySide6.QtCore import QMetaObject
            QMetaObject.invokeMethod(quick_window(), "close")
        if steps % 10 == 0 and not quick_window().isVisible():
            try:
                _final_picture_check()        # a video on screen shows a picture, every so often
            except Fail as exc:
                raise Fail(f"{exc} after steps: " + " | ".join(log[-12:]))
        if H.parts.win.visibility() == __import__("PySide6.QtGui", fromlist=["QWindow"]).QWindow.Visibility.FullScreen and rnd.random() < 0.3:
            H.parts.win.showNormal()
    H.notes.append(f"monkey: {steps} steps, worst step {worst_gap:.2f}s")
    check(worst_gap < 3.0, "the UI never froze for more than 3 s", f"{worst_gap:.2f}s")


# ============================================================ orchestrator
def _fixtures(cache: Path):
    """Fixtures are generated by a helper process (needs Qt + libVLC), cached between runs."""
    marker = cache / "ready.json"
    if marker.exists():
        return json.loads(marker.read_text(encoding="utf8"))
    code = (
        "import sys, json; sys.path.insert(0, %r)\n"
        "import main\n"
        "from PySide6.QtGui import QGuiApplication\n"
        "from pathlib import Path\n"
        "app = QGuiApplication([])\n"
        "from tests.fixtures import make_all\n"
        "from tests.formats import make_formats\n"
        "c = Path(%r)\n"
        "fx = {k: str(v) for k, v in make_all(c / 'fx').items()}\n"
        "fm = make_formats(c / 'formats')\n"
        "conv = lambda o: str(o) if isinstance(o, Path) else o\n"
        "fm = {k: ([{**i, 'path': str(i['path'])} for i in v] if k in ('video', 'audio') else\n"
        "          {kk: str(vv) for kk, vv in v.items()} if isinstance(v, dict) else\n"
        "          [conv(x) for x in v] if isinstance(v, list) else conv(v)) for k, v in fm.items()}\n"
        "Path(%r).write_text(json.dumps({'fx': fx, 'fmt': fm}), encoding='utf8')\n"
    ) % (str(ROOT), str(cache), str(marker))
    cache.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-c", code], check=True, cwd=str(ROOT))
    return json.loads(marker.read_text(encoding="utf8"))


_SETUP_LOCK = threading.Lock()


def run_one(sc: Scenario, fixtures: dict, exe: str | None, work: Path, extra_env: dict) -> dict:
    base = work / sc.name
    shutil.rmtree(base, ignore_errors=True)
    data = base / "data"
    data.mkdir(parents=True)
    settings = {"folders": [fixtures["fx"]["media_root"]], "snapshotDir": str(base / "snaps"), "volume": 30}
    # REFRATA_E2E_TMP: no real autostart entry, file types or update check (refrata/tray.py, assoc.py)
    env = dict(os.environ, REFRATA_DATA=str(data), REFRATA_E2E_TMP=str(base), REFRATA_NO_HOOK="1", REFRATA_LANG="pt",
               REFRATA_NO_MEDIAKEYS="0" if "mediakeys" in sc.name else "1",
               REFRATA_FX=json.dumps(fixtures["fx"]), REFRATA_FMT=json.dumps(fixtures["fmt"]),
               REFRATA_SCENARIO_OUT=str(base / "result.json"), PYTHONIOENCODING="utf8", **extra_env)
    env.update(sc.env)
    env["REFRATA_SCENARIO_TIMEOUT"] = str(sc.timeout)
    env["REFRATA_SCENARIO_TIMEOUT"] = str(sc.timeout)
    os.environ.update({"REFRATA_FX": env["REFRATA_FX"]})   # setups read it
    with _SETUP_LOCK:                 # setups share the fixture cache (the big library is built once)
        extra = sc.setup(data) if sc.setup else None
    if not (data / "settings.json").exists():
        settings.update(extra or {})
        (data / "settings.json").write_text(json.dumps({"settings": settings, "history": []}), encoding="utf8")
    cmd = [exe] if exe else ([sys.executable, "-m", "coverage", "run", "--rcfile", str(ROOT / "tools" / "coveragerc"), str(ROOT / "main.py")]
                              if os.environ.get("REFRATA_COVERAGE") else [sys.executable, str(ROOT / "main.py")])
    cmd += ["--scenario", sc.name, "--log", str(base / "app.log")]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, env=env, cwd=str(ROOT), timeout=sc.timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        code = proc.returncode
        out_text = proc.stdout.decode("utf8", "replace")
    except subprocess.TimeoutExpired as exc:
        # subprocess.run has already killed this scenario's own process (never kill by image
        # name: the person's own Refrata.exe may be running)
        code, out_text = "timeout", (exc.stdout or b"").decode("utf8", "replace")
    took = time.time() - t0
    res = {"name": sc.name, "took": round(took, 1), "code": code, "problems": [], "notes": []}
    rf = base / "result.json"
    r = json.loads(rf.read_text(encoding="utf8")) if rf.exists() else None
    if code != 0:
        res["problems"].append(f"exit code {code}" + (" (0xC0000005 access violation)" if code in (3221225477, -1073741819) else ""))
    if r is None:
        res["problems"].append("no result written (crashed or hung before quitting)")
    else:
        res["notes"] = r.get("notes", [])
        if r.get("error"):
            res["problems"].append("scenario: " + r["error"])
        if r.get("cleanup_error"):
            res["problems"].append("cleanup: " + r["cleanup_error"])
        if r.get("qt"):
            res["problems"].append("Qt/QML warnings: " + " | ".join(r["qt"][:4]))
        if r.get("pyerrors"):
            res["problems"].append("Python errors: " + " | ".join(r["pyerrors"][:2]))
    crash = data / "crash.log"
    if crash.exists() and crash.stat().st_size:
        res["problems"].append("crash.log: " + crash.read_text(encoding="utf8", errors="replace")[:1500])
    sj = data / "settings.json"
    if sj.exists() and "readonly" not in sc.name:
        try:
            json.loads(sj.read_text(encoding="utf8"))
        except Exception as exc:
            res["problems"].append(f"settings.json left invalid: {exc}")
    if sc.verify and not res["problems"]:
        try:
            sc.verify(data)
        except Exception as exc:
            res["problems"].append("after quit: " + str(exc))
    if res["problems"]:
        res["log_tail"] = out_text[-2500:]
    for n in ("settings.json", "library.json"):
        try:
            os.chmod(data / n, 0o666)
        except OSError:
            pass
    return res


def _last_durations(report: str) -> dict:
    """Scenario times from the previous report (orders the parallel batch); {} without one."""
    for p in (report, str(ROOT / "build" / "robust-release.json"), str(ROOT / "build" / "robust-last.json")):
        try:
            return {r["name"]: r["took"] for r in json.loads(Path(p).read_text(encoding="utf8"))}
        except Exception:
            continue
    return {}


def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    ap.add_argument("--exe", default="")
    ap.add_argument("--cache", default=str(Path(tempfile.gettempdir()) / "refrata_robust_cache"))
    ap.add_argument("--monkey-seconds", type=float, default=120)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--report", default="")
    ap.add_argument("--jobs", type=int, default=min(8, max(1, (os.cpu_count() or 2) // 3)),
                    help="scenarios at once (each is its own app process); 1 = one by one")
    a = ap.parse_args()
    t = time.time()
    fixtures = _fixtures(Path(a.cache))
    print(f"fixtures ready ({time.time() - t:.0f}s); skipped formats: {fixtures['fmt'].get('skipped')}", flush=True)
    keys = [k for k in a.k.split(",") if k] or [""]
    chosen = [s for n, s in SCENARIOS.items() if any(k in n for k in keys)]
    if "monkey" in a.k or not a.k:
        for s in chosen:
            if s.name.startswith("monkey"):
                s.timeout = int(a.monkey_seconds + 120)
    for old in Path(tempfile.gettempdir()).glob("refrata_robust_*"):
        # only stale ones: another run going on right now keeps its logs
        if old.name != Path(a.cache).name and old.is_dir() and time.time() - old.stat().st_mtime > 6 * 3600:
            shutil.rmtree(old, ignore_errors=True)
    os.environ["REFRATA_ROBUST_CACHE"] = a.cache
    work = Path(tempfile.mkdtemp(prefix="refrata_robust_"))
    extra = {"REFRATA_MONKEY_SECONDS": str(a.monkey_seconds), "REFRATA_MONKEY_SEED": str(a.seed)}
    results = []

    def report(r):
        results.append(r)
        good = not r["problems"]
        print(f"  {'PASS' if good else 'FAIL'}  {r['name']}  ({r['took']}s)" +
              ("".join(f"\n        - {n}" for n in r["notes"])) +
              ("".join(f"\n        ! {p}" for p in r["problems"])), flush=True)

    t_all = time.time()
    batch = [s for s in chosen if not s.solo] if a.jobs > 1 else []
    alone = [s for s in chosen if s not in batch]
    if batch:
        # longest first, so the long ones are not left running alone at the end
        last = _last_durations(a.report)
        batch.sort(key=lambda s: -last.get(s.name, s.timeout / 3))
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(a.jobs) as pool:
            for f in as_completed([pool.submit(run_one, s, fixtures, a.exe or None, work, extra) for s in batch]):
                report(f.result())
    for sc in alone:                   # the window must be in front: one at a time
        report(run_one(sc, fixtures, a.exe or None, work, extra))
    ok = sum(1 for r in results if not r["problems"])
    print(f"\n{ok}/{len(chosen)} scenarios passed in {time.time() - t_all:.0f}s "
          f"({len(batch)} in parallel x{a.jobs}, {len(alone)} alone)  (work dir: {work})")
    out = Path(a.report) if a.report else ROOT / "build" / "robust-last.json"
    try:
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf8")
    except OSError:
        pass
    sys.exit(0 if ok == len(chosen) else 1)


if __name__ == "__main__":
    main_cli()
