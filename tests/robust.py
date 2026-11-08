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


class Scenario:
    def __init__(self, fn, setup=None, verify=None, timeout=90, data=None, env=None):
        self.fn, self.setup, self.verify, self.timeout = fn, setup, verify, timeout
        self.env = env or {}
        self.name = fn.__name__
        self.data = data   # extra settings for settings.json, merged before launch


def scenario(setup=None, verify=None, timeout=90, data=None, env=None):
    def deco(fn):
        SCENARIOS[fn.__name__] = Scenario(fn, setup, verify, timeout, data, env)
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


def playing_media(timeout=10, video=None):
    e = eng()
    if video is None:
        cond = lambda: e.state == "playing" and (e.isAudio or e._frames_seen > 2)
    elif video:
        cond = lambda: e.state == "playing" and e._frames_seen > 2
    else:
        cond = lambda: e.state == "playing" and e.time > 300
    wait(cond, timeout, f"media playing ({Path(e.currentPath).name})")


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
    e.addSubtitle(Path(H.fx["srt"]).as_uri())
    wait(lambda: e.subtitleTrack not in (-1, 0), 5, "subtitle selected")
    white = lambda r, g, b: r > 215 and g > 215 and b > 215
    yellow = lambda r, g, b: r > 200 and g > 170 and b < 120
    normal = _count(_subtitle_frame(), white)
    check(normal > 30, "the subtitle is drawn on the picture", str(normal))
    t_before = e.time
    e.setSubtitleStyle("subSize", "huge")
    playing_media(video=True)
    wait(lambda: e.time > 0, 4, "clock after the rebuild")
    check(abs(e.time - t_before) < 4000, "a new subtitle style keeps the playback position", f"{t_before} → {e.time}")
    wait(lambda: e.subtitleTrack not in (-1, 0), 6, "hand-added subtitle back on after the rebuild")
    huge = _count(_subtitle_frame(), white)
    check(huge > normal * 1.3, "bigger subtitle covers more of the picture", f"{normal} → {huge}")
    e.setSubtitleStyle("subColor", "yellow")
    playing_media(video=True)
    wait(lambda: e.subtitleTrack not in (-1, 0), 6)
    ys = _count(_subtitle_frame(), yellow)
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
    H.parts.find("subResult_0")
    before = len(e.subtitleTracks)
    ss.fetch(0)
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
    for code in CODES:
        i18n.setProperty("language", code)
        pump(0.4)
        win().setProperty("playerOpen", False)
        for page in range(6):
            win().setProperty("page", page)
            pump(0.35)
            problems += _overflowing_texts(win().contentItem(), f"{code}/page{page}")
        if True:   # settings is long: check it in slices while scrolling
            win().setProperty("page", 5)
            sp = H.parts.find("settingsPage")
            for y in range(0, int(sp.property("contentHeight")), 500):
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
        pv.setProperty("panel", "")
    i18n.setProperty("language", "auto")
    uniq = sorted(set(problems))
    H.notes.append(f"{len(uniq)} overflowing text(s)")
    check(not uniq, "every text fits in every language", "\n          " + "\n          ".join(uniq[:60]))


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
    while time.time() < end:
        steps += 1
        r = rnd.random()
        t = time.time()
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
        elif r < 0.45:
            k = rnd.choice(keys)
            if w.isActive() or True:
                QTest.keyClick(w, k)
            log.append(f"key {k.name}")
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
        import main
        if main.QT_MESSAGES or H.errors:
            raise Fail("warning or error after steps: " + " | ".join(log[-12:]) +
                       " :: " + "; ".join(main.QT_MESSAGES[:2] + H.errors[:1])[:900])
        if quick_window().isVisible():
            from PySide6.QtCore import QMetaObject
            QMetaObject.invokeMethod(quick_window(), "close")
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


def run_one(sc: Scenario, fixtures: dict, exe: str | None, work: Path, extra_env: dict) -> dict:
    base = work / sc.name
    shutil.rmtree(base, ignore_errors=True)
    data = base / "data"
    data.mkdir(parents=True)
    settings = {"folders": [fixtures["fx"]["media_root"]], "snapshotDir": str(base / "snaps"), "volume": 30}
    env = dict(os.environ, REFRATA_DATA=str(data), REFRATA_NO_HOOK="1", REFRATA_LANG="pt",
               REFRATA_NO_MEDIAKEYS="0" if "mediakeys" in sc.name else "1",
               REFRATA_FX=json.dumps(fixtures["fx"]), REFRATA_FMT=json.dumps(fixtures["fmt"]),
               REFRATA_SCENARIO_OUT=str(base / "result.json"), PYTHONIOENCODING="utf8", **extra_env)
    env.update(sc.env)
    env["REFRATA_SCENARIO_TIMEOUT"] = str(sc.timeout)
    env["REFRATA_SCENARIO_TIMEOUT"] = str(sc.timeout)
    os.environ.update({"REFRATA_FX": env["REFRATA_FX"]})   # setups read it
    extra = sc.setup(data) if sc.setup else None
    if not (data / "settings.json").exists():
        settings.update(extra or {})
        (data / "settings.json").write_text(json.dumps({"settings": settings, "history": []}), encoding="utf8")
    cmd = [exe] if exe else [sys.executable, str(ROOT / "main.py")]
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


def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    ap.add_argument("--exe", default="")
    ap.add_argument("--cache", default=str(Path(tempfile.gettempdir()) / "refrata_robust_cache"))
    ap.add_argument("--monkey-seconds", type=float, default=120)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--report", default="")
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
        if old.name != Path(a.cache).name and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
    os.environ["REFRATA_ROBUST_CACHE"] = a.cache
    work = Path(tempfile.mkdtemp(prefix="refrata_robust_"))
    extra = {"REFRATA_MONKEY_SECONDS": str(a.monkey_seconds), "REFRATA_MONKEY_SEED": str(a.seed)}
    results, ok = [], 0
    for sc in chosen:
        r = run_one(sc, fixtures, a.exe or None, work, extra)
        results.append(r)
        good = not r["problems"]
        ok += good
        print(f"  {'PASS' if good else 'FAIL'}  {sc.name}  ({r['took']}s)" +
              ("".join(f"\n        - {n}" for n in r["notes"])) +
              ("".join(f"\n        ! {p}" for p in r["problems"])), flush=True)
    print(f"\n{ok}/{len(chosen)} scenarios passed  (work dir: {work})")
    if a.report:
        Path(a.report).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf8")
    sys.exit(0 if ok == len(chosen) else 1)


if __name__ == "__main__":
    main_cli()
