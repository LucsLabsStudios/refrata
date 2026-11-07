"""End-to-end test battery for Refrata.

Boots the real application (QML UI + libVLC engine) in-process with an isolated
data directory and synthetic media, then exercises every feature: library,
settings, playback, tracks, subtitles, equalizer, image adjustments, snapshots,
queue, playlists, network streams, resume, sleep timer, UI navigation with real
mouse/keyboard events, panels, fullscreen, drag & drop logic and shutdown.

Any Qt/QML warning emitted during the run fails the suite.

    python tests/e2e.py            # full run
    python tests/e2e.py -k queue   # only tests whose name contains "queue"
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# `Refrata.exe --selftest` creates the temp dir before the app modules load
TMP = Path(os.environ.get("REFRATA_E2E_TMP") or tempfile.mkdtemp(prefix="refrata_e2e_"))
os.environ["REFRATA_E2E_TMP"] = str(TMP)
os.environ["REFRATA_DATA"] = str(TMP / "data")
os.environ["REFRATA_NO_HOOK"] = "1"   # no system-wide keyboard hook while the battery types
(TMP / "data").mkdir(parents=True, exist_ok=True)
FROZEN = bool(getattr(sys, "frozen", False))
if not FROZEN:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402  (configures libVLC paths + Qt message capture)
from PySide6.QtCore import QMetaObject, QObject, QPoint, QPointF, Qt, Q_ARG, Q_RETURN_ARG  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QWindow  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from tests.fixtures import make_all  # noqa: E402

# ----------------------------------------------------------------- harness
RESULTS: list[tuple[str, bool, str]] = []
CURRENT = {"test": ""}


class Fail(Exception):
    pass


def check(cond, what, detail=""):
    if not cond:
        raise Fail(f"{what}" + (f" — {detail}" if detail else ""))


def pump(seconds=0.05):
    end = time.time() + seconds
    app = QGuiApplication.instance()
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait(cond, timeout=10.0, what="condition"):
    end = time.time() + timeout
    app = QGuiApplication.instance()
    while time.time() < end:
        app.processEvents()
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.01)
    raise Fail(f"timeout waiting for {what}")


def call(obj, method, *args):
    """Invoke a QML JS function (all params are QVariant)."""
    ret = Q_RETURN_ARG("QVariant")
    ok = QMetaObject.invokeMethod(obj, method, Qt.ConnectionType.DirectConnection, ret,
                                  *[Q_ARG("QVariant", a) for a in args])
    return ok


def center_of(item: QQuickItem) -> QPoint:
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(int(p.x()), int(p.y()))


def click(parts, name_or_item, button=Qt.MouseButton.LeftButton, double=False, at=None):
    item = parts.find(name_or_item) if isinstance(name_or_item, str) else name_or_item
    check(item.isVisible(), f"'{name_or_item}' is visible before clicking")
    pos = center_of(item) if at is None else at
    QTest.mouseMove(parts.win, pos)
    pump(0.03)
    if double:
        QTest.mouseDClick(parts.win, button, Qt.KeyboardModifier.NoModifier, pos)
    else:
        QTest.mouseClick(parts.win, button, Qt.KeyboardModifier.NoModifier, pos)
    pump(0.08)


FOCUS_LOSSES = []


def ensure_focus(parts):
    """Window shortcuts only fire in the active window. If another app (the person using the
    PC while the suite runs) took the foreground, bring ours back before sending keys."""
    if QGuiApplication.focusWindow() is parts.win:
        return
    FOCUS_LOSSES.append(CURRENT["test"])
    end = time.time() + 4
    while time.time() < end and QGuiApplication.focusWindow() is not parts.win:
        parts.ctl.activate(parts.win)
        pump(0.15)
    check(QGuiApplication.focusWindow() is parts.win,
          "test window is active for keyboard input (another app holds the foreground)")


def key(parts, k, mod=Qt.KeyboardModifier.NoModifier):
    ensure_focus(parts)
    QTest.keyClick(parts.win, k, mod)
    pump(0.08)


def QCursorPos():
    from PySide6.QtGui import QCursor
    return QCursor.pos()


def move_mouse(parts, pos: QPoint):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent
    ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(pos), QPointF(parts.win.mapToGlobal(pos)),
                     Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QGuiApplication.sendEvent(parts.win, ev)
    pump(0.1)


def type_text(parts, text):
    """QTest.keyClicks has no QWindow overload in PySide6: send key events with text."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    ensure_focus(parts)
    for ch in text:
        code = ord(ch.upper()) if ch.isascii() and ch.isalnum() else Qt.Key.Key_unknown
        for typ in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QGuiApplication.sendEvent(parts.win, QKeyEvent(typ, code, Qt.KeyboardModifier.NoModifier, ch))
    pump(0.1)


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# ------------------------------------------------------------------ fixtures
class Ctx:
    parts: main.AppParts
    fx: dict
    messages: list
    http_port: int


C = Ctx()


def start_http(root: Path) -> int:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))

    class Quiet(handler.func):  # type: ignore[misc]
        def log_message(self, *a):
            pass

        def do_GET(self):
            # /download/<name> answers 302 like GitHub release assets do
            if self.path.startswith("/download/"):
                self.send_response(302)
                self.send_header("Location", "/" + self.path[len("/download/"):])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            super().do_GET()

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def eng():
    return C.parts.engine


def win():
    return C.parts.win


def stop_playback():
    eng().clearQueue()
    pump(0.3)


# ===================================================================== TESTS
@test
def t01_app_boots_without_qml_warnings():
    check(C.parts.win.isVisible(), "main window visible")
    check(C.parts.win.width() >= 1040, "window respects minimum width")
    check(not C.boot_messages, "no Qt/QML warnings at boot", "; ".join(C.boot_messages[:5]))
    if FROZEN:
        app_dir = Path(sys.executable).parent
        check(main.VLC_DIR is not None and app_dir in Path(main.VLC_DIR).parents,
              "packaged build uses its bundled libVLC, not a system VLC", str(main.VLC_DIR))
        check(C.parts.ctl.about.get("vlcBundled") is True, "About reports the bundled engine")
    for name in ["sidebar", "homePage", "videosPage", "musicPage", "queuePage", "networkPage",
                 "settingsPage", "miniPlayer", "playerView", "mediaMenu", "toast", "dropArea"]:
        C.parts.find(name)


@test
def t02_library_scan_counts_and_titles():
    lib = C.parts.library
    lib.rescan()
    wait(lambda: not lib.scanning and lib.videoCount > 0, 20, "library scan")
    check(lib.videoCount == 3, "3 videos found (2 mkv + corrupt mp4)", str(lib.videoCount))
    check(lib.audioCount == 3, "3 audio files found", str(lib.audioCount))
    info = lib.info(str(C.fx["video"]))
    check(info.get("title") == "Filme Teste 2024", "scene-style filename cleaned into a nice title", info.get("title"))
    check(info.get("ext") == "MKV" and info.get("folder") == "Filmes", "ext / folder metadata", str(info))


@test
def t03_library_durations_thumbnails_cache():
    lib = C.parts.library
    v = str(C.fx["video"])
    wait(lambda: lib.info(v).get("thumb"), 30, "video thumbnail")
    info = lib.info(v)
    thumb = Path(QUrlLocal(info["thumb"]))
    check(thumb.exists(), "thumbnail file exists", str(thumb))
    img = QImage(str(thumb))
    check(not img.isNull() and img.width() == 640, "thumbnail decodes at source width", f"{img.width()}x{img.height()}")
    check(abs(info["duration"] - 30000) < 600, "video duration parsed", str(info["duration"]))
    a = str(C.fx["tone_a"])
    wait(lambda: lib.info(a).get("duration", 0) > 0, 20, "audio duration")
    check(abs(lib.info(a)["duration"] - 3000) < 300, "audio duration ~3 s", str(lib.info(a)["duration"]))
    check(lib.totalHours > 0, "total hours stat computed")
    wait(lambda: (TMP / "data" / "library.json").exists(), 8, "library cache written")
    cache = json.loads((TMP / "data" / "library.json").read_text(encoding="utf8"))
    check(v in cache and cache[v].get("done"), "cache entry for the video")
    check(lib.revision > 0, "library revision bumps for QML bindings")


def QUrlLocal(u):
    from PySide6.QtCore import QUrl
    return QUrl(u).toLocalFile()


@test
def t04_library_filter_search_sort():
    lib = C.parts.library
    vm, mm, sm = lib.videoModel, lib.musicModel, lib.searchModel
    vm.search = "segundo"
    pump(0.1)
    check(vm.count == 1 and vm.get(0)["title"] == "Segundo Video", "video filter by text", str(vm.count))
    vm.search = ""
    pump(0.1)
    check(vm.count == 3, "filter cleared")
    sm.search = "tom"
    pump(0.1)
    check(sm.count == 3, "global search finds the 3 tones", str(sm.count))
    sm.search = "tom 660"
    pump(0.1)
    check(sm.count == 1 and "660" in sm.get(0)["title"], "multi-word search", str(sm.count))
    sm.search = ""
    mm.sortMode = "name"
    pump(0.1)
    titles = [mm.get(i)["title"] for i in range(mm.count)]
    check(titles == sorted(titles, key=str.lower), "sort by name", str(titles))
    vm.sortMode = "duration"
    pump(0.1)
    durs = [vm.get(i)["duration"] for i in range(vm.count)]
    check(durs == sorted(durs, reverse=True), "sort by duration (desc)", str(durs))
    vm.sortMode = "size"
    pump(0.1)
    sizes = [vm.get(i)["size"] for i in range(vm.count)]
    check(sizes == sorted(sizes, reverse=True), "sort by size (desc)", str(sizes))
    vm.sortMode = "recent"
    pump(0.1)
    check(len(vm.paths()) == 3, "paths() helper")
    check(vm.get(99) == {}, "get() out of range returns empty")


@test
def t05_settings_theme_accent_persistence():
    s, th = C.parts.settings, C.parts.theme
    s.theme = "light"
    pump(0.1)
    check(not th.dark and th.bg.name().lower() == "#f1eee9", "light palette applied", th.bg.name())
    s.theme = "dark"
    pump(0.1)
    check(th.dark and th.bg.name().lower() == "#0c0d10", "dark palette applied")
    s.accent = "#3E91FF"
    pump(0.1)
    check(th.accent.name().lower() == "#3e91ff", "accent applied")
    check(th.onAccent.name().lower() == "#ffffff", "readable text on accent")
    s.accent = "#FF7A2F"
    check(len(th.accents) == 8, "8 accent swatches")
    check(th.iconFont in ("Segoe Fluent Icons", "Segoe MDL2 Assets"), "icon font resolved", th.iconFont)
    s.save()
    data = json.loads((TMP / "data" / "settings.json").read_text(encoding="utf8"))
    check(data["settings"]["accent"] == "#FF7A2F" and data["settings"]["theme"] == "dark", "settings persisted to disk")
    C.parts.ctl.styleWindow(C.parts.win, True, th.bg)  # DWM call must not raise


@test
def t06_settings_folders_trigger_rescan():
    s, lib = C.parts.settings, C.parts.library
    aux = str(C.fx["tone_long"].parent)
    s.addFolder(Path(aux).as_uri())
    pump(0.2)
    wait(lambda: not lib.scanning and lib.audioCount == 5, 20, "rescan after adding folder")
    check(os.path.normpath(aux) in s.folders, "folder stored normalized")
    s.addFolder(aux)  # duplicate is ignored
    check(s.folders.count(os.path.normpath(aux)) == 1, "no duplicate folder")
    s.removeFolder(os.path.normpath(aux))
    pump(0.2)
    wait(lambda: not lib.scanning and lib.audioCount == 3, 20, "rescan after removing folder")


@test
def t07_play_video_from_ui_function():
    call(win(), "playPaths", [str(C.fx["video"])], 0)
    wait(lambda: eng().state == "playing" and eng()._frames_seen > 5, 15, "video playing with frames")
    check(win().property("playerOpen") is True, "player view opened for video")
    check(eng().videoWidth == 640 and eng().videoHeight == 360, "decoded size", f"{eng().videoWidth}x{eng().videoHeight}")
    check(not eng().isAudio, "video not flagged as audio")
    check(eng().title == "Filme Teste 2024", "title shown", eng().title)
    check(eng().hasMedia and eng().seekable, "hasMedia / seekable")
    t0 = eng().time
    pump(1.2)
    check(eng().time > t0 + 600, "time advances while playing", f"{t0} -> {eng().time}")
    check(abs(eng().length - 30000) < 600, "length known", str(eng().length))


@test
def t08_pause_resume_seek_skip_framestep():
    e = eng()
    e.togglePlay()
    wait(lambda: e.state == "paused", 5, "paused")
    t = e.time
    pump(0.6)
    check(abs(e.time - t) < 150, "time frozen while paused")
    e.togglePlay()
    wait(lambda: e.state == "playing", 5, "playing again")
    e.seek(15000)
    wait(lambda: abs(e.time - 15000) < 1500, 5, "seek to 15 s")
    e.skip(-10000)
    wait(lambda: abs(e.time - 5000) < 1500, 5, "skip back 10 s")
    e.skip(10000)
    wait(lambda: abs(e.time - 15000) < 1800, 5, "skip forward 10 s")
    e.seek(-500)
    wait(lambda: e.time < 1500, 5, "seek clamps below zero")
    e.seek(10 ** 9)
    pump(0.3)
    check(e.time <= e.length, "seek clamps past the end")
    e.seek(10000)
    pump(0.4)
    e.pause()
    wait(lambda: e.state == "paused", 5, "paused for frame step")
    f0 = e._frames_seen
    e.frameStep()
    wait(lambda: e._frames_seen > f0, 5, "frame step renders a new frame")
    e.play()
    wait(lambda: e.state == "playing", 5, "play()")


@test
def t09_audio_tracks_and_delays():
    e = eng()
    wait(lambda: len(e.audioTracks) >= 3, 10, "audio tracks listed (disable + 2)")
    names = [t["name"] for t in e.audioTracks]
    check(names[0] == "Desativado", "'Disable' translated", str(names))
    second = e.audioTracks[2]["id"]
    e.setAudioTrack(second)
    pump(0.5)
    check(e.audioTrack == second, "audio track switched", f"{e.audioTrack} != {second}")
    e.setAudioDelay(-200)
    check(e.audioDelay == -200, "audio delay", str(e.audioDelay))
    e.setAudioDelay(0)


@test
def t10_external_subtitle_load_select_delay():
    e = eng()
    before = len(e.subtitleTracks)
    e.addSubtitle(Path(C.fx["srt"]).as_uri())
    wait(lambda: len(e.subtitleTracks) > before, 8, "subtitle track added")
    sub = [t for t in e.subtitleTracks if t["id"] != -1][0]["id"]
    e.setSubtitleTrack(sub)
    pump(0.3)
    check(e.subtitleTrack == sub, "subtitle selected")
    e.setSubtitleDelay(500)
    check(e.subtitleDelay == 500, "subtitle delay", str(e.subtitleDelay))
    e.setSubtitleDelay(0)
    e.setSubtitleTrack(-1)
    pump(0.2)
    check(e.subtitleTrack == -1, "subtitles disabled")
    check(any("Legenda carregada" in m for m in C.messages), "toast for loaded subtitle")


@test
def t11_rate_and_volume_clamping():
    e = eng()
    e.rate = 1.5
    check(abs(e.rate - 1.5) < 1e-6, "rate 1.5")
    e.rate = 10
    check(e.rate == 4.0, "rate clamps to 4x")
    e.rate = 0.01
    check(e.rate == 0.25, "rate clamps to 0.25x")
    e.rate = 1.0
    e.volume = 50
    check(e.volume == 50, "volume 50")
    e.volume = 400
    check(e.volume == 150, "volume clamps to 150")
    e.volume = -3
    check(e.volume == 0, "volume clamps to 0")
    e.volume = 30
    check(C.parts.settings.value("volume") == 30, "volume persisted in settings")
    check(not e.muted, "changing the volume un-mutes (like every player)")
    was_muted = e.muted
    e.toggleMute()
    check(e.muted != was_muted, "mute toggles")
    e.toggleMute()
    check(e.muted == was_muted, "mute toggles back")
    if not e.muted:
        e.toggleMute()


@test
def t12_equalizer():
    e = eng()
    presets = e.eqPresets
    check(len(presets) == 18 and presets[0] == "Plano", "18 presets, translated", str(presets[:3]))
    check(len(e.eqFrequencies) == 10 and e.eqFrequencies[-1] == "16k", "10 bands", str(e.eqFrequencies))
    e.setEqPreset(3)
    check(e.eqEnabled and e.eqPreset == 3, "preset applied and EQ enabled")
    check(any(abs(b) > 0.1 for b in e.eqBands), "preset has non-flat bands", str(e.eqBands))
    e.setEqBand(0, 7.5)
    check(e.eqPreset == -1 and abs(e.eqBands[0] - 7.5) < 0.01, "manual band edit -> custom")
    e.setEqPreamp(4)
    check(abs(e.eqPreamp - 4) < 0.01, "preamp")
    e.setEqEnabled(False)
    check(not e.eqEnabled, "EQ disabled")


@test
def t13_video_adjustments():
    e = eng()
    e.setAdjust("brightness", 1.4)
    e.setAdjust("hue", 90)
    check(abs(e.adjust["brightness"] - 1.4) < 1e-6 and e.adjust["hue"] == 90, "adjust values stored")
    e.setAdjust("nonsense", 3)
    check("nonsense" not in e.adjust, "unknown key ignored")
    f0 = e._frames_seen
    wait(lambda: e._frames_seen > f0 + 3, 5, "video keeps rendering with adjust filter")
    e.resetAdjust()
    check(e.adjust["brightness"] == 1.0 and e.adjust["hue"] == 0.0, "reset")


@test
def t14_snapshot():
    e = eng()
    e.snapshot()
    snap = Path(e.lastSnapshot)
    check(snap.exists() and snap.parent == TMP / "snaps", "snapshot written to configured folder", str(snap))
    img = QImage(str(snap))
    check(img.width() == 640 and img.height() == 360, "snapshot has video resolution")


@test
def t15_sleep_timer():
    e = eng()
    e.setSleepTimer(30)
    check(e.sleepMinutes == 30, "sleep timer 30 min", str(e.sleepMinutes))
    e.setSleepTimer(0)
    check(e.sleepMinutes == 0, "sleep timer off")
    if e.state != "playing":
        e.play()
        wait(lambda: e.state == "playing", 5)
    e.setSleepTimer(1)
    e._sleep_deadline = time.time() + 0.5  # fast-forward the clock
    wait(lambda: e.state == "paused", 5, "sleep timer pauses playback")
    check(e.sleepMinutes == 0, "sleep timer cleared after firing")
    check(any("Timer de sono" in m for m in C.messages), "sleep toast")


@test
def t16_queue_operations():
    e = eng()
    a, b, c = (str(C.fx[k]) for k in ("tone_a", "tone_b", "tone_c"))
    e.openFiles([a, b, c])
    wait(lambda: e.state == "playing", 8, "queue playing")
    check(len(e.queue) == 3 and e.currentIndex == 0 and e.isAudio, "queue of 3, audio flagged")
    e.next()
    wait(lambda: e.currentIndex == 1, 3, "next")
    e.previous()
    wait(lambda: e.currentIndex == 0, 3, "previous (early in track)")
    e.playAt(2)
    wait(lambda: e.currentIndex == 2, 3, "playAt")
    e.moveItem(2, 0)
    check(e.currentIndex == 0 and e.queue[0]["path"] == c, "move current item to top")
    e.moveItem(1, 2)
    check([q["path"] for q in e.queue] == [c, b, a], "move non-current item", str([Path(q["path"]).name for q in e.queue]))
    e.removeAt(1)
    check([q["path"] for q in e.queue] == [c, a] and e.currentIndex == 0, "remove item")
    e.enqueue([b])
    check(len(e.queue) == 3 and e.queue[-1]["path"] == b, "enqueue appends")
    e.playNext(str(C.fx["tone_b"]))
    check(e.queue[1]["path"] == b and len(e.queue) == 4, "playNext inserts after current")
    e.removeAt(0)
    wait(lambda: e.currentIndex == 0 and e.queue[0]["path"] == b, 3, "removing current plays the next one")
    e.enqueue([str(TMP / "does-not-exist.mp3")])
    check(len(e.queue) == 3, "missing files are ignored")
    e.clearQueue()
    pump(0.2)
    check(not e.hasMedia and e.queue == [] and e.currentIndex == -1, "clear queue")


@test
def t17_autoplay_repeat_shuffle():
    e, s = eng(), C.parts.settings
    a, b = str(C.fx["tone_a"]), str(C.fx["tone_b"])
    e.openFiles([a, b])
    wait(lambda: e.currentIndex == 1 and e.state == "playing", 10, "auto-advance to next item")
    wait(lambda: e.state in ("ended", "stopped"), 10, "stops at end of queue (repeat off)")
    # repeat all
    e.cycleRepeat()
    check(e.repeat == 1, "repeat all")
    e.openFiles([a, b], 1)
    wait(lambda: e.currentIndex == 1 and e.state == "playing", 5)
    wait(lambda: e.currentIndex == 0 and e.state == "playing", 10, "wraps around with repeat all")
    # repeat one
    e.cycleRepeat()
    check(e.repeat == 2, "repeat one")
    e.openFiles([a, b])
    wait(lambda: e.state == "playing" and e.time > 2000, 8)
    wait(lambda: e.state == "playing" and e.time < 1500 and e.currentIndex == 0, 8, "repeat one restarts the same item")
    e.cycleRepeat()
    check(e.repeat == 0, "repeat off again")
    # autoplay disabled
    s.autoplay = False
    e.openFiles([a, b])
    wait(lambda: e.state in ("ended", "stopped"), 10, "stops after first item when autoplay is off")
    check(e.currentIndex == 0, "did not advance")
    s.autoplay = True
    # shuffle
    e.toggleShuffle()
    check(e.shuffle, "shuffle on")
    e.openFiles([a, b, str(C.fx["tone_c"])])
    wait(lambda: e.state == "playing", 5)
    for _ in range(3):
        cur = e.currentIndex
        e.next()
        wait(lambda: e.currentIndex != cur, 3, "shuffle picks a different item")
    e.toggleShuffle()
    check(not e.shuffle, "shuffle off")
    e.togglePlay()  # pause before restart test
    stop_playback()
    e.openFiles([a])
    wait(lambda: e.state in ("ended", "stopped"), 10)
    e.togglePlay()
    wait(lambda: e.state == "playing", 5, "togglePlay restarts an ended queue")
    stop_playback()


@test
def t18_resume_playback_and_history():
    e, s = eng(), C.parts.settings
    p = str(C.fx["tone_long"])
    e.openFiles([p])
    wait(lambda: e.state == "playing" and e.length > 0, 8)
    e.seek(20000)
    wait(lambda: e.time >= 19500, 5)
    e.stop()
    pump(0.2)
    pos = s.resume_position(p)
    check(18000 < pos < 23000, "resume position saved on stop", str(pos))
    C.messages.clear()
    e.openFiles([p])
    wait(lambda: e.state == "playing" and e.time > 0, 8)
    check(e.time < 5000 and not any("Continuando" in m for m in C.messages),
          "a short song starts from the top instead of resuming", str(e.time))
    e.seek(20000)
    wait(lambda: e.time >= 19500, 5)
    e.stop()
    pump(0.2)
    e.audio_resume_min = 0   # treat the tone as long audio (podcast/audiobook)
    e.openFiles([p])
    wait(lambda: e.state == "playing" and e.time > 0, 8)
    check(e.time >= 18000, "playback resumes where it stopped", str(e.time))
    check(any("Continuando" in m for m in C.messages), "resume toast")
    e.stop()
    s.resume = False
    e.openFiles([p])
    wait(lambda: e.state == "playing" and e.time > 0, 8)
    check(e.time < 5000, "resume can be disabled", str(e.time))
    s.resume = True
    e.audio_resume_min = 20 * 60_000
    e.seek(39000)
    wait(lambda: e.state in ("ended", "stopped"), 10, "finish long tone")
    check(s.resume_position(p) == 0, "finished media resets resume point")
    hist = [h["path"] for h in s.history]
    check(p in hist and hist[0] == p, "history ordered newest first")
    s.removeHistory(p)
    check(p not in [h["path"] for h in s.history], "remove from history")
    stop_playback()


@test
def t19_playlists_and_folder_expansion():
    ctl, e = C.parts.ctl, eng()
    a, b, c = (str(C.fx[k]) for k in ("tone_a", "tone_b", "tone_c"))
    norm = lambda xs: [os.path.normcase(os.path.normpath(x)) for x in xs]  # noqa: E731
    check(norm(ctl.expand([str(C.fx["m3u"])])) == norm([a, b]), "M3U (relative + absolute paths)")
    check(norm(ctl.expand([str(C.fx["pls"])])) == norm([a, b]), "PLS ordered by FileN")
    check(norm(ctl.expand([str(C.fx["xspf"])])) == norm([c, a]), "XSPF with file:// URIs")
    check(ctl.expand([str(C.fx["hls"])]) == [str(C.fx["hls"])], "HLS manifest stays a stream")
    folder = ctl.expand([Path(C.fx["music_dir"]).as_uri()])
    check(len(folder) == 3, "folder expanded to its media files", str(folder))
    check(ctl.isSubtitle(Path(C.fx["srt"]).as_uri()) and not ctl.isSubtitle(a), "isSubtitle")
    check(ctl.isMedia(a) and ctl.isMedia(str(C.fx["music_dir"])) and not ctl.isMedia(str(C.fx["srt"])), "isMedia")
    e.openFiles([str(C.fx["pls"])])
    wait(lambda: len(e.queue) == 2 and e.state == "playing", 8, "engine opens playlist files directly")
    stop_playback()


@test
def t20_network_stream_http():
    e = eng()
    url = f"http://127.0.0.1:{C.http_port}/Filmes/Segundo%20Video.mkv"
    e.openUrl(url)
    wait(lambda: e.state == "playing" and e._frames_seen > 5, 15, "HTTP stream playing with video")
    check(e.queue[0]["isUrl"] and e.currentPath == url, "queued as URL")
    check(e.title, "stream has a title", e.title)
    e.snapshot()
    check(Path(e.lastSnapshot).name.startswith("stream_"), "snapshot of a stream gets a safe name", e.lastSnapshot)
    stop_playback()


@test
def t21_errors_and_bad_input():
    e = eng()
    C.messages.clear()
    e.openFiles([str(C.fx["corrupt"])])
    wait(lambda: e.state in ("error", "ended", "stopped"), 15, "corrupt file fails gracefully")
    check(e._frames_seen == 0, "no frames from a corrupt file")
    e.togglePlay()  # must not crash
    pump(1.0)
    # a broken item inside a queue is skipped automatically
    e.openFiles([str(C.fx["corrupt"]), str(C.fx["tone_a"])])
    wait(lambda: e.currentIndex == 1 and e.state == "playing", 15, "broken item skipped, next one plays")
    stop_playback()
    e.openFiles([str(C.fx["corrupt"])])
    wait(lambda: e.state in ("error", "ended", "stopped"), 15)
    e.openFiles([str(TMP / "missing.mkv")])
    check(e.currentPath == str(C.fx["corrupt"]) or not e.hasMedia, "missing file does not replace the queue")
    e.openFiles([])
    e.openUrl("")
    e.playAt(99)
    e.removeAt(99)
    e.moveItem(5, 9)
    e.setEqBand(0, 0)
    stop_playback()
    check(not main.QT_MESSAGES, "no Qt warnings so far", "; ".join(main.QT_MESSAGES[:5]))


@test
def t22_sidebar_navigation_with_mouse():
    win().setProperty("playerOpen", False)
    pump(0.6)
    for i in [1, 2, 3, 4, 5, 0]:
        click(C.parts, f"nav{i}")
        wait(lambda: win().property("page") == i, 2, f"page {i} after clicking nav{i}")
        pump(0.35)
        img = C.parts.win.grabWindow()
        check(not img.isNull(), f"page {i} renders")
        img.save(str(TMP / f"page_{i}.png"))
    click(C.parts, "navOpen")  # opens a native file dialog; close it
    pump(0.5)
    dlg = None
    for w in QGuiApplication.topLevelWindows():
        if w is not C.parts.win and w.isVisible() and w.title() == "Abrir mídia":
            dlg = w
    if dlg is not None:
        dlg.close()
    pump(0.3)
    check(not main.QT_MESSAGES, "no Qt warnings while navigating", "; ".join(main.QT_MESSAGES[:5]))


@test
def t23_home_search_typing():
    win().setProperty("page", 0)
    pump(0.4)
    field = C.parts.find("homeSearch")
    click(C.parts, field)
    type_text(C.parts, "tom")
    pump(0.3)
    check(C.parts.library.searchModel.search == "tom", "typing in the home search filters", C.parts.library.searchModel.search)
    check(C.parts.library.searchModel.count == 3, "search results")
    for _ in range(3):
        key(C.parts, Qt.Key.Key_Backspace)
    pump(0.2)
    check(C.parts.library.searchModel.search == "", "search cleared")


@test
def t24_context_menu_actions():
    menu = C.parts.find("mediaMenu")
    p = str(C.fx["tone_a"])
    call(menu, "openAt", p, 300, 300)
    pump(0.2)
    check(menu.property("visible") is True and menu.property("path") == p, "menu opens for a path")
    call(menu, "close")
    check(menu.property("visible") is False, "menu closes")
    e = eng()
    call(win(), "handleMenu", "play", p)
    wait(lambda: e.currentPath == p and e.state == "playing", 5, "menu → play")
    call(win(), "handleMenu", "queue", str(C.fx["tone_b"]))
    check(len(e.queue) == 2, "menu → add to queue")
    call(win(), "handleMenu", "next", str(C.fx["tone_c"]))
    check(e.queue[1]["path"] == str(C.fx["tone_c"]), "menu → play next")
    check(win().property("playerOpen") is False, "audio keeps the library view (mini player)")
    mini = C.parts.find("miniPlayer")
    wait(lambda: mini.property("visible") is True, 3, "mini player visible while audio plays")
    stop_playback()


@test
def t25_drop_logic():
    e = eng()
    r = None
    ret = Q_RETURN_ARG("QVariant")
    QMetaObject.invokeMethod(win(), "handleDrop", Qt.ConnectionType.DirectConnection, ret,
                             Q_ARG("QVariant", [Path(C.fx["films_dir"]).as_uri()]))
    wait(lambda: e.state == "playing" and len(e.queue) >= 2, 8, "dropping a folder plays its media")
    check(win().property("playerOpen") is True, "video drop opens the player")
    before = len(e.subtitleTracks)
    QMetaObject.invokeMethod(win(), "handleDrop", Qt.ConnectionType.DirectConnection, ret,
                             Q_ARG("QVariant", [Path(C.fx["srt"]).as_uri()]))
    wait(lambda: len(e.subtitleTracks) > before, 8, "dropping a .srt loads it as subtitle")
    C.messages.clear()
    QMetaObject.invokeMethod(win(), "handleDrop", Qt.ConnectionType.DirectConnection, ret,
                             Q_ARG("QVariant", [Path(C.fx["tone_long"].parent / "frame.png").as_uri()]))
    pump(0.2)
    toast = C.parts.find("toast")
    check(toast.property("text") == "Nenhuma mídia reconhecida", "unsupported drop shows a toast", str(toast.property("text")))
    del r


@test
def t26_player_view_panels_and_controls():
    e, pv = eng(), C.parts.find("playerView")
    if e.state != "playing":
        e.openFiles([str(C.fx["video"])])
        wait(lambda: e.state == "playing", 8)
    win().setProperty("playerOpen", True)
    wait(lambda: pv.property("y") == 0, 3, "player slid in")
    pump(0.3)
    for name in ["tracks", "speed", "eq", "adjust", "sleep"]:
        call(pv, "togglePanel", name)
        pop = C.parts.find("popover")
        wait(lambda: pop.property("opacity") > 0.95, 2, f"{name} panel visible")
        pump(0.25)
        img = C.parts.win.grabWindow()
        img.save(str(TMP / f"panel_{name}.png"))
        check(not img.isNull(), f"{name} panel renders")
        call(pv, "togglePanel", name)
        wait(lambda: pop.property("opacity") < 0.05, 2, f"{name} panel hides")
    pv.setProperty("queueOpen", True)
    qp = C.parts.find("queuePanel")
    wait(lambda: qp.property("visible") is True, 3, "up-next panel slides in")
    pump(0.4)
    C.parts.win.grabWindow().save(str(TMP / "panel_queue.png"))
    pv.setProperty("queueOpen", False)
    fill0 = pv.property("fillMode")
    pv.setProperty("fillMode", 2)
    check(pv.property("fillMode") == 2, "fill mode can change")
    pv.setProperty("fillMode", fill0)
    check(not main.QT_MESSAGES, "no Qt warnings in player panels", "; ".join(main.QT_MESSAGES[:5]))


@test
def t27_controls_autohide_and_mouse():
    e, pv = eng(), C.parts.find("playerView")
    if e.state != "playing":
        e.openFiles([str(C.fx["video"])])
        wait(lambda: e.state == "playing", 8)
    win().setProperty("playerOpen", True)
    wait(lambda: pv.property("y") == 0, 3, "player visible")
    call(pv, "poke")
    check(pv.property("controlsVisible") is True, "controls visible after activity")
    wait(lambda: pv.property("controlsVisible") is False, 6, "controls auto-hide while playing")
    # move while the fade-out is still running (regression: used to be ignored)
    check(C.parts.find("chrome").property("opacity") > 0.5, "move happens during the fade-out")
    QTest.mouseMove(C.parts.win, QPoint(400, 300))
    pump(0.1)
    move_mouse(C.parts, QPoint(420, 320))
    wait(lambda: pv.property("controlsVisible") is True, 2, "mouse move reveals controls")
    # big play button
    click(C.parts, "bigPlay")
    wait(lambda: e.state == "paused", 3, "big play button pauses")
    click(C.parts, "bigPlay")
    wait(lambda: e.state == "playing", 3, "big play button resumes")
    # seek bar click at ~75 %
    seek = C.parts.find("seekBar")
    p = seek.mapToScene(QPointF(seek.width() * 0.75, seek.height() / 2))
    click(C.parts, seek, at=QPoint(int(p.x()), int(p.y())))
    wait(lambda: abs(e.time - e.length * 0.75) < 2500, 4, "clicking the seek bar seeks to ~75 %")
    # single click on video toggles play (after double-click window)
    click(C.parts, pv, at=QPoint(700, 300))
    wait(lambda: e.state == "paused", 3, "click on video pauses")
    click(C.parts, pv, at=QPoint(700, 300))
    wait(lambda: e.state == "playing", 3, "click on video resumes")


@test
def t28_fullscreen_toggle():
    w = C.parts.win
    call(win(), "toggleFullscreen")
    wait(lambda: w.visibility() == QWindow.Visibility.FullScreen, 4, "fullscreen")
    check(win().property("fullscreen") is True, "fullscreen flag")
    pump(0.5)
    w.grabWindow().save(str(TMP / "fullscreen.png"))
    call(win(), "toggleFullscreen")
    wait(lambda: w.visibility() != QWindow.Visibility.FullScreen, 4, "back to windowed")


@test
def t29_keyboard_shortcuts():
    e, w = eng(), C.parts.win
    w.requestActivate()
    wait(lambda: QGuiApplication.focusWindow() is w, 5, "window focused for shortcuts")
    if e.state != "playing":
        e.play()
        wait(lambda: e.state == "playing", 5)
    key(C.parts, Qt.Key.Key_Space)
    wait(lambda: e.state == "paused", 3, "Space pauses")
    key(C.parts, Qt.Key.Key_Space)
    wait(lambda: e.state == "playing", 3, "Space plays")
    e.seek(5000)
    pump(0.3)
    t = e.time
    key(C.parts, Qt.Key.Key_Right)
    wait(lambda: e.time > t + 8000, 3, "→ skips +10 s")
    key(C.parts, Qt.Key.Key_Left)
    wait(lambda: e.time < t + 6000, 3, "← skips -10 s")
    v = e.volume
    key(C.parts, Qt.Key.Key_Up)
    check(e.volume == min(150, v + 5), "↑ volume +5")
    key(C.parts, Qt.Key.Key_Down)
    check(e.volume == v, "↓ volume -5")
    m = e.muted
    key(C.parts, Qt.Key.Key_M)
    check(e.muted != m, "M toggles mute")
    key(C.parts, Qt.Key.Key_M)
    key(C.parts, Qt.Key.Key_BracketRight)
    check(abs(e.rate - 1.25) < 1e-6, "] speeds up", str(e.rate))
    key(C.parts, Qt.Key.Key_BracketLeft)
    check(abs(e.rate - 1.0) < 1e-6, "[ slows down")
    key(C.parts, Qt.Key.Key_BracketRight)
    key(C.parts, Qt.Key.Key_Equal)
    check(e.rate == 1.0, "= resets speed")
    n = len(list((TMP / "snaps").glob("*.png")))
    key(C.parts, Qt.Key.Key_S)
    check(len(list((TMP / "snaps").glob("*.png"))) == n + 1, "S takes a snapshot")
    key(C.parts, Qt.Key.Key_F)
    wait(lambda: w.visibility() == QWindow.Visibility.FullScreen, 3, "F enters fullscreen")
    key(C.parts, Qt.Key.Key_Escape)
    wait(lambda: w.visibility() != QWindow.Visibility.FullScreen, 3, "Esc leaves fullscreen")
    win().setProperty("playerOpen", True)
    pump(0.5)
    key(C.parts, Qt.Key.Key_Escape)
    wait(lambda: win().property("playerOpen") is False, 3, "Esc closes the player view")
    e.openFiles([str(C.fx["tone_a"]), str(C.fx["tone_b"])])
    wait(lambda: e.state == "playing", 5)
    key(C.parts, Qt.Key.Key_N)
    wait(lambda: e.currentIndex == 1, 3, "N → next")
    key(C.parts, Qt.Key.Key_P)
    wait(lambda: e.currentIndex == 0, 3, "P → previous")
    key(C.parts, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    wait(lambda: win().property("page") == 4, 3, "Ctrl+L opens Streams")
    key(C.parts, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    wait(lambda: win().property("page") == 5, 3, "Ctrl+, opens Settings")
    stop_playback()


@test
def t30_url_field_typing_plays_stream():
    e = eng()
    win().setProperty("page", 4)
    pump(0.5)
    field = C.parts.find("urlField")
    click(C.parts, field)
    type_text(C.parts, f"http://127.0.0.1:{C.http_port}/Filmes/Segundo%20Video.mkv")
    key(C.parts, Qt.Key.Key_Return)
    wait(lambda: e.state == "playing" and e._frames_seen > 3, 15, "URL typed in the Streams page plays")
    check(win().property("playerOpen") is True, "player opens for streams")
    stop_playback()
    win().setProperty("playerOpen", False)


@test
def t31_light_theme_renders_everywhere():
    s = C.parts.settings
    s.theme = "light"
    s.accent = "#22C3A6"
    pump(0.3)
    for i in range(6):
        win().setProperty("page", i)
        pump(0.8)
        check(win().property("page") == i, f"page {i} active in light theme", str(win().property("page")))
        check(C.parts.find("sidebar").property("current") == i, "sidebar highlight follows the page")
        C.parts.win.grabWindow().save(str(TMP / f"light_page_{i}.png"))
    s.theme = "dark"
    s.accent = "#FF7A2F"
    win().setProperty("page", 0)
    pump(0.3)
    check(not main.QT_MESSAGES, "no Qt warnings with the light theme", "; ".join(main.QT_MESSAGES[:5]))


def find_under(root, name):
    stack = [root]
    while stack:
        it = stack.pop()
        for c in it.childItems():
            if c.objectName() == name:
                return c
            stack.append(c)
    raise Fail(f"'{name}' not found under {root.objectName() or root}")


def scroll_into_view(item):
    """Scroll the nearest Flickable ancestor so `item` is on screen."""
    w = C.parts.win
    flick = item.parentItem()
    while flick is not None and flick.property("contentY") is None:
        flick = flick.parentItem()
    if flick is None:
        return
    top = item.mapToScene(QPointF(0, 0)).y()
    if top < 80 or top + item.height() > w.height() - 120:
        flick.setProperty("contentY", max(0.0, flick.property("contentY") + top - w.height() / 3))
        pump(0.15)


def ui_click(parent_or_name, name=None, double=False, frac=None):
    """Click a named control (optionally searched under a parent), scrolling to it first."""
    if name is None:
        item = C.parts.find(parent_or_name)
    else:
        parent = C.parts.find(parent_or_name) if isinstance(parent_or_name, str) else parent_or_name
        item = find_under(parent, name)
    scroll_into_view(item)
    at = None
    if frac is not None:
        fx, fy = frac
        p = item.mapToScene(QPointF(item.width() * fx, item.height() * fy))
        at = QPoint(int(p.x()), int(p.y()))
    click(C.parts, item, double=double, at=at)
    return item


def open_player_with(paths, index=0):
    e = eng()
    e.openFiles([str(p) for p in paths], index)
    wait(lambda: e.state == "playing" and (e.isAudio or e._frames_seen > 2), 10, "media playing")
    win().setProperty("playerOpen", True)
    pv = C.parts.find("playerView")
    wait(lambda: pv.property("y") == 0, 3, "player visible")
    call(pv, "poke")
    pump(0.3)
    return pv


@test
def t33_player_controls_by_mouse():
    e = eng()
    pv = open_player_with([C.fx["video"], C.fx["video2"]])
    poke = lambda: call(pv, "poke")  # noqa: E731  keep controls up between clicks
    e.seek(5000)
    pump(0.4)
    t = e.time
    ui_click("pvSkipFwd"); poke()
    wait(lambda: e.time > t + 8000, 3, "skip-forward button")
    ui_click("pvSkipBack"); poke()
    wait(lambda: e.time < t + 6000, 3, "skip-back button")
    lab = ui_click("pvTimeLabel"); poke()
    check(lab.property("text").startswith("−"), "time label toggles to remaining", lab.property("text"))
    ui_click("pvTimeLabel"); poke()
    n = len(list((TMP / "snaps").glob("*.png")))
    ui_click("pvSnapshot"); poke()
    check(len(list((TMP / "snaps").glob("*.png"))) == n + 1, "snapshot button")
    modes = []
    for _ in range(3):
        ui_click("pvAspect"); poke()
        modes.append(pv.property("fillMode"))
    check(len(set(modes)) == 3, "aspect button cycles 3 fill modes", str(modes))
    check(C.parts.find("pvReveal").property("enabled") is True, "reveal-in-Explorer enabled for local files")
    m = e.muted
    ui_click("pvMute"); poke()
    check(e.muted != m, "mute button")
    ui_click("pvMute"); poke()
    ui_click("pvVolume", frac=(1 / 3, 0.5)); poke()
    check(35 <= e.volume <= 65, "volume slider click ~50 %", str(e.volume))
    e.toggleMute() if not e.muted else None
    ui_click("pvShuffle"); poke()
    check(e.shuffle, "shuffle button on")
    ui_click("pvShuffle"); poke()
    check(not e.shuffle, "shuffle button off")
    reps = []
    for _ in range(3):
        ui_click("pvRepeat"); poke()
        reps.append(e.repeat)
    check(reps == [1, 2, 0], "repeat button cycles", str(reps))
    # tracks panel
    e.addSubtitle(Path(C.fx["srt"]).as_uri())
    wait(lambda: len(e.subtitleTracks) >= 2, 8, "subtitle loaded")
    ui_click("pvTracks"); poke()
    check(pv.property("panel") == "tracks", "tracks panel opens")
    pop = C.parts.find("popover")
    wait(lambda: pop.property("opacity") > 0.95, 2)
    ui_click(pop, "atrack_2"); poke()
    check(e.audioTrack == e.audioTracks[2]["id"], "audio track chosen by click")
    ui_click(pop, "strack_1"); poke()
    check(e.subtitleTrack == e.subtitleTracks[1]["id"], "subtitle chosen by click")
    ui_click(pop, "strack_0"); poke()
    check(e.subtitleTrack == -1, "subtitles off by click")
    ui_click("pvTracks"); poke()
    check(pv.property("panel") == "", "tracks panel closes")
    # speed panel
    ui_click("pvSpeed"); poke()
    wait(lambda: pop.property("opacity") > 0.95, 2)
    ui_click(pop, "speed_1.5"); poke()
    check(abs(e.rate - 1.5) < 1e-6, "speed chip", str(e.rate))
    ui_click(pop, "speed_1"); poke()
    check(e.rate == 1.0, "speed back to 1x")
    # equalizer panel
    ui_click("pvEq"); poke()
    wait(lambda: pop.property("opacity") > 0.95, 2)
    ui_click(pop, "eqp_2"); poke()
    check(e.eqPreset == 2 and e.eqEnabled, "EQ preset chip")
    ui_click(pop, "eqband_0", frac=(0.5, 0.08)); poke()
    check(e.eqBands[0] > 12 and e.eqPreset == -1, "EQ band slider click", str(e.eqBands[0]))
    ui_click(pop, "eqToggle"); poke()
    check(not e.eqEnabled, "EQ toggle off")
    # adjust panel
    ui_click("pvAdjust"); poke()
    wait(lambda: pop.property("opacity") > 0.95, 2)
    ui_click(pop, "adj_brightness", frac=(0.75, 0.5)); poke()
    check(1.3 < e.adjust["brightness"] < 1.7, "brightness slider click", str(e.adjust["brightness"]))
    ui_click(pop, "adjReset"); poke()
    check(e.adjust["brightness"] == 1.0, "adjust reset chip")
    ui_click(pop, "seg_2"); poke()
    check(pv.property("fillMode") == 2, "fill mode segmented")
    ui_click(pop, "seg_1"); poke()
    # sleep panel
    ui_click("pvSleep"); poke()
    wait(lambda: pop.property("opacity") > 0.95, 2)
    ui_click(pop, "sleep_15"); poke()
    check(e.sleepMinutes == 15, "sleep chip 15 min")
    ui_click(pop, "sleepOff"); poke()
    check(e.sleepMinutes == 0, "sleep off chip")
    ui_click("pvSleep"); poke()
    # up next panel
    ui_click("pvQueue"); poke()
    qp = C.parts.find("queuePanel")
    wait(lambda: qp.property("x") < pv.property("width") - 100, 3, "queue panel open")
    pump(0.4)
    ui_click(qp, "qitem_1"); poke()
    wait(lambda: e.currentIndex == 1, 4, "click item in up-next panel")
    ui_click("pvQueue"); poke()
    ui_click("pvPrev"); poke()
    wait(lambda: e.currentIndex == 0, 4, "previous button")
    ui_click("pvNext"); poke()
    wait(lambda: e.currentIndex == 1, 4, "next button")
    ui_click("pvFullscreen"); poke()
    wait(lambda: C.parts.win.visibility() == QWindow.Visibility.FullScreen, 3, "fullscreen button")
    pump(0.4); poke()
    ui_click("pvFullscreen"); poke()
    wait(lambda: C.parts.win.visibility() != QWindow.Visibility.FullScreen, 3, "exit fullscreen button")
    pump(0.4); poke()
    ui_click("pvMinimize")
    wait(lambda: win().property("playerOpen") is False, 3, "minimize button")
    stop_playback()


@test
def t34_mini_player_by_mouse():
    e = eng()
    win().setProperty("playerOpen", False)
    win().setProperty("page", 2)
    e.openFiles([str(C.fx[k]) for k in ("tone_a", "tone_b", "tone_c")])
    wait(lambda: e.state == "playing", 5)
    mini = C.parts.find("miniPlayer")
    wait(lambda: mini.property("y") < win().height() - 50, 3, "mini player slid in")
    pump(0.5)
    ui_click("miniPlay")
    wait(lambda: e.state == "paused", 3, "mini play/pause → pause")
    ui_click("miniPlay")
    wait(lambda: e.state == "playing", 3, "mini play/pause → play")
    ui_click("miniNext")
    wait(lambda: e.currentIndex == 1, 3, "mini next")
    ui_click("miniPrev")
    wait(lambda: e.currentIndex == 0, 3, "mini previous")
    m = e.muted
    ui_click("miniMute")
    check(e.muted != m, "mini mute")
    ui_click("miniVolume", frac=(0.5, 0.5))
    check(60 <= e.volume <= 90, "mini volume slider", str(e.volume))
    if not e.muted:
        e.toggleMute()
    ui_click("miniExpand")
    wait(lambda: win().property("playerOpen") is True, 3, "mini expand opens the player")
    win().setProperty("playerOpen", False)
    stop_playback()


@test
def t35_pages_by_mouse():
    e, s, lib = eng(), C.parts.settings, C.parts.library
    # home
    win().setProperty("page", 0)
    pump(0.4)
    home = C.parts.find("homePage")
    home.setProperty("contentY", 0)
    pump(0.2)
    ui_click(home, "stat_1")
    wait(lambda: win().property("page") == 2, 2, "stat card navigates to Music")
    win().setProperty("page", 0)
    pump(0.4)
    ui_click(home, "heroPlay")
    wait(lambda: e.state == "playing", 8, "hero play button")
    check(win().property("playerOpen") is True, "hero opens the player for a video")
    win().setProperty("playerOpen", False)
    stop_playback()
    # videos
    win().setProperty("page", 1)
    pump(0.4)
    vp = C.parts.find("videosPage")
    ui_click(find_under(vp, "videoSort"), "seg_name")
    check(lib.videoModel.sortMode == "name", "video sort segmented")
    ui_click(vp, "video_1")
    wait(lambda: e.state in ("playing", "error") and e.currentIndex == 1, 8, "click video card plays that card")
    check(len(e.queue) == 3, "card click queues the whole grid")
    win().setProperty("playerOpen", False)
    stop_playback()
    ui_click(vp, "videosPlayAll")
    # sorted by name the corrupt file comes first: it must be skipped automatically
    check(e.queue[0]["path"] == str(C.fx["corrupt"]), "play-all queues the grid in display order")
    wait(lambda: len(e.queue) == 3 and e.currentIndex >= 1 and e.state == "playing", 10,
         "play-all skips the unplayable first item")
    win().setProperty("playerOpen", False)
    stop_playback()
    lib.videoModel.sortMode = "recent"
    # music
    win().setProperty("page", 2)
    pump(0.4)
    mp_ = C.parts.find("musicPage")
    ui_click(mp_, "musicPlayAll")
    wait(lambda: len(e.queue) == 3 and e.state == "playing", 5, "play-all music")
    check(win().property("playerOpen") is False, "music keeps the library view")
    ui_click(mp_, "music_1")
    wait(lambda: e.currentIndex == 1 and e.state == "playing", 4, "one click on a track plays it")
    ui_click(mp_, "music_1")
    check(e.state == "paused", "click on the playing track pauses at once", e.state)
    ui_click(mp_, "music_1")
    check(e.state == "playing", "click again resumes at once", e.state)
    pump(0.6)
    check(e.state == "playing", "optimistic state is confirmed by libVLC", e.state)
    ui_click(mp_, "music_2", double=True)
    wait(lambda: e.currentIndex == 2, 4, "double-click a track plays it")
    pump(0.8)
    check(e.state == "playing", "double-click does not pause the track it just started", e.state)
    ui_click(mp_, "musicShuffle")
    check(e.shuffle, "music shuffle button")
    e.toggleShuffle()
    # queue page
    win().setProperty("page", 3)
    pump(0.4)
    qpage = C.parts.find("queuePage")
    ui_click(qpage, "queue_1")
    wait(lambda: e.currentIndex == 1, 4, "one click in the queue page plays the item")
    ui_click(qpage, "queue_0", double=True)
    wait(lambda: e.currentIndex == 0, 4, "double-click in queue page")
    pump(0.8)
    check(e.state == "playing", "double-click in the queue keeps playing", e.state)
    win().setProperty("playerOpen", False)
    ui_click(qpage, "queueRemove_0")
    check(len(e.queue) == 2, "remove button in queue page")
    ui_click(qpage, "queueClear")
    check(len(e.queue) == 0, "clear queue button")
    # settings
    win().setProperty("page", 5)
    pump(0.4)
    sp = C.parts.find("settingsPage")
    sp.setProperty("contentY", 0)
    pump(0.2)
    ui_click(find_under(sp, "themeSeg"), "seg_light")
    check(s.theme == "light", "theme segmented → light")
    ui_click(find_under(sp, "themeSeg"), "seg_dark")
    check(s.theme == "dark", "theme segmented → dark")
    ui_click(sp, "accent_2")
    check(s.accent.lower() == "#3e91ff", "accent swatch click", s.accent)
    ui_click(sp, "accent_0")
    for name, key in (("toggleResume", "resume"), ("toggleAutoplay", "autoplay"), ("toggleHwdec", "hwdec")):
        v = s.value(key)
        ui_click(sp, name)
        check(s.value(key) == (not v), f"{name} switch")
        ui_click(sp, name)
        check(s.value(key) == v, f"{name} switch back")
    ui_click(sp, "rescanBtn")
    wait(lambda: not lib.scanning and lib.videoCount == 3, 15, "rescan button")
    # network
    win().setProperty("page", 4)
    pump(0.4)
    url = f"http://127.0.0.1:{C.http_port}/Filmes/Segundo%20Video.mkv"
    QGuiApplication.clipboard().setText(url)
    ui_click("pasteBtn")
    check(C.parts.find("urlField").property("text") == url, "paste button fills the URL")
    ui_click("goBtn")
    wait(lambda: e.state == "playing" and e.currentPath == url, 10, "play button streams the URL")
    win().setProperty("playerOpen", False)
    stop_playback()
    win().setProperty("page", 0)


@test
def t36_open_watchdog_reports_stuck_media():
    e = eng()
    stop_playback()
    e.openFiles([str(C.fx["tone_a"])])
    wait(lambda: e.state == "playing", 5)
    e.stop()
    pump(0.2)
    C.messages.clear()
    e._state = "idle"
    e._mp.stop()
    e._open_deadline = time.time() - 1  # libVLC "never started"
    wait(lambda: e.state == "error", 3, "watchdog flips to error")
    check(any("Não foi possível abrir" in m for m in C.messages), "watchdog toast")
    stop_playback()


# ------------------------------------------------ native dialogs (real Windows file pickers)
def drive_dialog(opener, title, **kw):
    """Start a Win32 driver for `title`, run `opener` (a real mouse click), wait for it to finish."""
    from tests.windialogs import DialogDriver
    drv = DialogDriver(title, **kw).start()
    opener()
    wait(lambda: drv.done, 25, f"native dialog '{title}' handled")
    check(not drv.error, f"dialog '{title}' driven", drv.error + " | " + "; ".join(drv.steps))
    pump(0.4)
    return drv


def go_page(i):
    win().setProperty("playerOpen", False)
    win().setProperty("page", i)
    pump(0.5)


@test
def t37_native_open_files_dialog_multi_select():
    e = eng()
    stop_playback()
    go_page(0)
    music = C.fx["music_dir"]
    drv = drive_dialog(lambda: click(C.parts, "navOpen"), "Abrir mídia",
                       folder=str(music), names=["Tom A 440.wav", "Tom B 660.wav"])
    check("Mídia" in drv.seen_filter, "media name filter is the default", drv.seen_filter)
    wait(lambda: len(e.queue) == 2 and e.state == "playing", 10, "two picked files queued and playing")
    check(sorted(Path(q["path"]).name for q in e.queue) == ["Tom A 440.wav", "Tom B 660.wav"],
          "exactly the picked files", str([q["path"] for q in e.queue]))
    check(all(os.path.isabs(q["path"]) and "/" not in q["path"] for q in e.queue), "native, normalized paths")
    check(not win().property("playerOpen"), "audio keeps the library visible")
    # Ctrl+O opens the same dialog; cancelling it changes nothing
    before = [q["path"] for q in e.queue]
    drive_dialog(lambda: key(C.parts, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier), "Abrir mídia", cancel=True)
    check([q["path"] for q in e.queue] == before, "cancel leaves the queue alone")
    stop_playback()


@test
def t38_native_play_folder_dialog():
    e = eng()
    stop_playback()
    go_page(0)
    films = C.fx["films_dir"]
    drive_dialog(lambda: ui_click("homeOpenFolder"), "Reproduzir pasta", path=str(films))
    wait(lambda: len(e.queue) >= 2, 10, "folder contents queued")
    names = [Path(q["path"]).name for q in e.queue]
    check("Filme.Teste.2024.1080p.x264.mkv" in names and "Segundo Video.mkv" in names, "folder videos queued", str(names))
    check(all(Path(q["path"]).parent == films for q in e.queue), "only files from the picked folder", str(names))
    wait(lambda: e.state == "playing" and e._frames_seen > 2, 10, "first video plays")
    wait(lambda: win().property("playerOpen") is True, 3, "player view opens for video")
    stop_playback()
    go_page(0)


@test
def t39_native_add_folder_dialogs_and_cancel():
    s, lib = C.parts.settings, C.parts.library
    extra = TMP / "extra lib"
    extra.mkdir(exist_ok=True)
    (extra / "Extra Tom.wav").write_bytes(C.fx["tone_c"].read_bytes())
    go_page(5)
    before = list(s.folders)
    C.messages.clear()
    drive_dialog(lambda: ui_click("settingsAddFolder"), "Adicionar pasta à biblioteca", path=str(extra))
    wait(lambda: str(extra) in list(s.folders), 5, "picked folder added to settings")
    toast = C.parts.find("toast")
    check(toast.property("text") == "Pasta adicionada à biblioteca" and toast.property("shown"), "confirmation toast",
          str(toast.property("text")))
    wait(lambda: not lib.scanning and lib.audioCount == 4, 20, "rescan picks up the new folder")
    # the Videos page button opens the same picker; cancel must not add anything
    go_page(1)
    drive_dialog(lambda: ui_click("videosAddFolder"), "Adicionar pasta à biblioteca", cancel=True)
    check(list(s.folders) == before + [str(extra)], "cancel adds nothing", str(list(s.folders)))
    s.removeFolder(str(extra))
    wait(lambda: not lib.scanning and lib.audioCount == 3, 20, "library back to the fixtures")
    go_page(0)


@test
def t40_native_subtitle_dialog():
    e = eng()
    stop_playback()
    pv = open_player_with([C.fx["video"]])
    n0 = len(e.subtitleTracks)
    pv.setProperty("panel", "tracks")
    pump(0.6)
    from PySide6.QtCore import SIGNAL
    requests = []
    QObject.connect(pv, SIGNAL("loadSubtitle()"), lambda: requests.append(time.time()))
    drv = drive_dialog(lambda: ui_click("playerView", "addSubBtn"), "Carregar legenda",
                       folder=str(C.fx["srt"].parent), names=[C.fx["srt"].name])
    check(len(requests) == 1, "one click asks for one dialog", str(len(requests)))
    check("Legendas" in drv.seen_filter, "subtitle name filter is the default", drv.seen_filter)
    wait(lambda: len(e.subtitleTracks) > n0, 8, "picked subtitle becomes a track")
    pv.setProperty("panel", "")
    stop_playback()
    go_page(0)


# ------------------------------------------------ single instance / command line
def _child_cmd(*args):
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, str(ROOT / "main.py"), *args]


def local_request(name: str, payload: bytes) -> bytes:
    """Non-blocking QLocalSocket round trip on the GUI thread (the server lives here too)."""
    from PySide6.QtNetwork import QLocalSocket
    s = QLocalSocket()
    s.connectToServer(name)
    wait(lambda: s.state() == QLocalSocket.LocalSocketState.ConnectedState, 3, "local socket connected")
    s.write(payload)
    s.flush()
    wait(lambda: s.bytesAvailable() > 0, 3, "server reply")
    data = bytes(s.readAll().data())
    s.disconnectFromServer()
    return data


@test
def t41_second_launch_hands_files_to_running_instance():
    import subprocess
    e = eng()
    stop_playback()
    inst = C.parts.instance
    check(inst is not None, "single-instance server running")
    # a real second process, exactly what Explorer does on double-click
    child = subprocess.Popen(_child_cmd(str(C.fx["tone_c"])), env=dict(os.environ),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait(lambda: child.poll() is not None, 30, "second launch exits after handing over")
    finally:
        if child.poll() is None:
            child.kill()
    check(child.returncode == 0, "second launch exit code", str(child.returncode))
    # the app is open: the file plays right here (no quick player floating over it)
    from PySide6.QtGui import QWindow
    quick = C.parts.qml.rootObjects()[0].findChild(QWindow, "quickPlayer")
    wait(lambda: e.state == "playing" and e.queue and Path(e.queue[0]["path"]) == C.fx["tone_c"], 10,
         "handed-over file plays here")
    check(not quick.isVisible(), "no quick player over the open app")
    stop_playback()
    # app off screen: the same hand-over opens the quick player
    with main_hidden():
        child = subprocess.Popen(_child_cmd(str(C.fx["tone_c"])), env=dict(os.environ),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait(lambda: child.poll() is not None, 30, "second launch exits after handing over")
        wait(lambda: quick.isVisible() and Path(quick.property("path")) == C.fx["tone_c"], 10,
             "with the app hidden, the file opens in the quick player")
        QMetaObject.invokeMethod(quick, "close")
        wait(lambda: not quick.isVisible(), 2)
    # a subtitle handed over while a video plays is attached, not queued
    open_player_with([C.fx["video"]])
    n0 = len(e.subtitleTracks)
    reply = local_request(inst.name, json.dumps({"open": [str(C.fx["srt"])]}).encode("utf8") + b"\n")
    check(reply.startswith(b"ok"), "running instance acknowledged", repr(reply))
    wait(lambda: len(e.subtitleTracks) > n0, 8, "handed-over subtitle attached")
    check(len(e.queue) == 1, "subtitle not queued")
    # garbage on the pipe is rejected without side effects
    reply = local_request(inst.name, b"this is not json\n")
    check(reply.startswith(b"bad"), "garbage rejected", repr(reply))
    pump(0.3)
    check(len(e.queue) == 1, "queue untouched by garbage")
    stop_playback()
    go_page(0)


@test
def t42_command_line_files_route_to_the_ui():
    e, ctl = eng(), C.parts.ctl
    stop_playback()
    go_page(0)
    from PySide6.QtGui import QWindow
    quick = C.parts.qml.rootObjects()[0].findChild(QWindow, "quickPlayer")
    with main_hidden():
        ctl.handOver([str(C.fx["video"])])
        wait(lambda: quick.isVisible() and quick.property("mode") == "compact", 5, "video from argv opens the quick player")
        check(e.state not in ("playing", "loading"), "the main player waits for the expand")
        QMetaObject.invokeMethod(quick, "close")
        wait(lambda: not quick.isVisible(), 2)
    ctl.handOver([str(C.fx["video"])])
    wait(lambda: e.state == "playing" and win().property("playerOpen") is True, 10, "with the app open, the video plays in it")
    check(not quick.isVisible(), "no quick player over the open app")
    n0 = len(e.subtitleTracks)
    ctl.handOver([str(C.fx["srt"])])
    wait(lambda: len(e.subtitleTracks) > n0, 8, "subtitle from argv attaches")
    ctl.handOver([str(C.fx["m3u"])])
    wait(lambda: len(e.queue) == 2 and e.state == "playing", 8, "playlist from argv expands")
    ctl.handOver([str(TMP / "nao existe.txt")])
    pump(0.3)
    check(len(e.queue) == 2, "unknown argument ignored")
    stop_playback()
    go_page(0)


# ------------------------------------------------ brand / about
@test
def t43_brand_logo_and_about_card():
    go_page(0)
    logo = C.parts.find("brandLogo")
    wait(lambda: logo.property("progress") == 1.0 and logo.property("implicitWidth") > 0, 5, "brand logo image loaded")
    click(C.parts, logo)
    wait(lambda: win().property("page") == 5, 3, "logo opens Ajustes")
    card = C.parts.find("aboutCard")
    pump(0.6)
    top = card.mapToScene(QPointF(0, 0)).y()
    check(-1 <= top and top + card.height() <= win().height() + 1, "About card scrolled fully into view", str(top))
    about = C.parts.ctl.about
    from refrata import __version__
    check(about["version"] == __version__ and about["studio"] == "LucsLabs Studios", "about metadata", str(about))
    check(about["vlcVersion"].startswith("3."), "libVLC 3 reported", about["vlcVersion"])
    chip = find_under(card, "aboutChip_0")
    check(chip.isVisible(), "version chip shown")
    check(C.parts.find("aboutDefaults").isVisible() == bool(about["installed"]), "default-apps button only when installed")
    img = QImage(C.parts.win.grabWindow())
    check(not img.isNull(), "window renders with the brand card")
    go_page(0)


@test
def t44_legacy_best_vlc_data_is_migrated():
    import refrata.settings as rs
    legacy, new = TMP / "legacy" / "BestVLC", TMP / "legacy" / "Refrata"
    legacy.mkdir(parents=True)
    (legacy / "settings.json").write_text(json.dumps({"theme": "light", "history": [{"path": "C:/x.mkv"}]}), "utf8")
    (legacy / "crash.log").write_text("old", "utf8")
    saved = rs.DATA_DIR, rs.LEGACY_DIR, os.environ.pop("REFRATA_DATA")
    try:
        rs.DATA_DIR, rs.LEGACY_DIR = new, legacy
        rs._migrate_legacy()
        check(json.loads((new / "settings.json").read_text("utf8"))["theme"] == "light", "settings copied over")
        check(not (new / "crash.log").exists(), "old logs left behind")
        (new / "settings.json").write_text("{}", "utf8")
        rs._migrate_legacy()
        check((new / "settings.json").read_text("utf8") == "{}", "migration runs only once")
        os.environ["REFRATA_DATA"] = str(TMP / "legacy" / "explicit")
        rs.DATA_DIR = TMP / "legacy" / "explicit"
        rs._migrate_legacy()
        check(not rs.DATA_DIR.exists(), "an explicit REFRATA_DATA is never seeded")
    finally:
        rs.DATA_DIR, rs.LEGACY_DIR, os.environ["REFRATA_DATA"] = saved


@test
def t45_volume_slider_marks_and_snaps_to_100():
    e = eng()
    pv = open_player_with([C.fx["video"]])
    poke = lambda: call(pv, "poke")  # noqa: E731
    if e.muted:
        e.toggleMute()
    slider = C.parts.find("pvVolume")
    mark = slider.findChild(QQuickItem, "sliderMark")
    boost = slider.findChild(QQuickItem, "boostFill")
    check(mark is not None and mark.isVisible(), "100 % mark drawn on the volume slider")
    x_mark = mark.x() + mark.width() / 2
    check(abs(x_mark / slider.width() - 100 / 150) < 0.02, "mark sits at 100 %", f"{x_mark}/{slider.width()}")
    ui_click("pvVolume", frac=(0.68, 0.5)); poke()  # ≈102 %
    check(e.volume == 100, "a click near the mark snaps to 100 %", str(e.volume))
    ui_click("pvVolume", frac=(0.62, 0.5)); poke()  # ≈93 %, outside the snap zone
    check(88 <= e.volume <= 97, "no snapping away from the mark", str(e.volume))
    check(not boost.isVisible(), "no boost fill at or below 100 %")
    ui_click("pvVolume", frac=(0.9, 0.5)); poke()  # ≈135 %
    check(130 <= e.volume <= 140, "boost range still reachable", str(e.volume))
    check(boost.isVisible() and boost.width() > 0, "boost fill shown above 100 %")
    mini = C.parts.find("miniVolume")
    # the mini player is hidden while the full player is open: check the slider's own setup
    check(mini.property("hasMark") is True and mini.property("mark") == 100, "mini player volume has the mark too")
    e.volume = 30
    pump(0.2)
    check(not boost.isVisible(), "boost fill hides again")
    if not e.muted:
        e.toggleMute()  # silent run
    stop_playback()


@test
def t46_music_mode_carousel_and_dock_by_mouse():
    e = eng()
    tracks = [C.fx["tone_long"], C.fx["tone_alt"], C.fx["tone_a"]]
    pv = open_player_with(tracks, 1)
    stage, dock = C.parts.find("musicStage"), C.parts.find("musicDock")
    check(stage.isVisible() and dock.isVisible(), "music stage and dock shown for audio")
    check(not C.parts.find("pvSnapshot").isVisible(), "video-only tools hidden in music mode")
    check(not C.parts.find("pvVolume").isVisible(), "volume lives in the dock in music mode")
    for i in range(3):
        C.parts.find(f"mcard_{i}")
    pump(0.8)  # carousel slide
    cur = C.parts.find("mcard_1")
    side = C.parts.find("mcard_0")
    check(cur.property("z") > side.property("z") and cur.property("scale") > side.property("scale"),
          "current track is the front card", f"z {cur.property('z')}/{side.property('z')}")
    check(C.parts.find("mpTitle").property("text") != "", "dock shows the title")

    # dock controls
    was = e.playing
    ui_click("mpPlay")
    wait(lambda: e.playing != was, 3, "dock play/pause")
    ui_click("mpPlay")
    wait(lambda: e.playing == was, 3, "dock play/pause back")
    wait(lambda: e.length > 0, 5, "track length known")
    ui_click("mpSeek", frac=(0.5, 0.5))
    wait(lambda: 0.35 * e.length < e.time < 0.65 * e.length, 3, "progress line seeks to the middle")
    ui_click("mpNext")
    wait(lambda: e.currentIndex == 2, 3, "dock next")
    ui_click("mpPrev")
    wait(lambda: e.currentIndex == 1, 3, "dock previous")

    # a stray click on the music backdrop must not pause the song
    wait(lambda: e.state == "playing", 3)
    eyebrow_at = (0.5, 0.04)
    ui_click("playerView", frac=eyebrow_at)
    pump(0.5)
    check(e.state == "playing", "background click in music mode keeps playing", e.state)

    # the progress line glides between engine polls (60 fps clock, never backwards)
    track_fill = []
    clock_items = [c for c in C.parts.find("musicStage").findChildren(QObject) if c.metaObject().className().startswith("SmoothTime")]
    check(len(clock_items) >= 1, "music stage has a smooth clock")
    for _ in range(30):
        pump(0.02)
        track_fill.append(clock_items[0].property("value"))
    steps = len(set(round(v) for v in track_fill))
    check(steps >= 15 and all(b >= a for a, b in zip(track_fill, track_fill[1:])),
          "progress advances every frame and monotonically", f"{steps} distinct values")

    # hovering a card shows what a click will do (topmost card only)
    pump(0.8)
    side0 = C.parts.find("mcard_0")
    sp = side0.mapToScene(QPointF(side0.width() * 0.3, side0.height() * 0.4))
    for dx in (0, 3, 6):
        QTest.mouseMove(C.parts.win, QPoint(int(sp.x()) + dx, int(sp.y())), 15)
    pump(0.5)
    check(C.parts.find("mcardPlay_0").property("opacity") > 0.9, "hovering a side card shows its play button")
    check(C.parts.find("mcardPlay_2").property("opacity") < 0.1, "other cards stay clean")

    # clicking a side card plays it
    pump(0.3)
    ui_click("mcard_0")
    wait(lambda: e.currentIndex == 0, 3, "side card click plays that track")
    pump(0.8)
    check(C.parts.find("mcard_0").property("scale") > C.parts.find("mcard_1").property("scale"),
          "carousel brings the new track to the front")

    # panels and queue from the dock
    ui_click("mpEq")
    check(pv.property("panel") == "eq", "dock opens the equalizer")
    wait(lambda: C.parts.find("popover").property("opacity") > 0.9, 2, "equalizer popover visible")
    ui_click("mpEq")
    check(pv.property("panel") == "", "equalizer closes again")
    ui_click("mpQueue")
    check(pv.property("queueOpen") is True, "dock opens the queue")
    pump(0.6)  # the dock slides left to make room for the queue panel
    ui_click("mpQueue")
    pump(0.6)  # the dock slides back to the centre once the queue panel closes
    ui_click("mpShuffle")
    check(e.shuffle, "dock shuffle")
    ui_click("mpShuffle")
    ui_click("mpVolume", frac=(0.4, 0.5))
    check(50 <= e.volume <= 70, "dock volume slider", str(e.volume))

    # music mode keeps its controls while playing
    pump(3.4)
    check(pv.property("controlsVisible") is True, "dock does not auto-hide in music mode")
    if not e.muted:
        e.toggleMute()
    win().setProperty("playerOpen", False)
    stop_playback()


@test
def t47_in_app_update_check_download_verify_install():
    """Updater against a fake GitHub Releases API served locally (no real network)."""
    import hashlib
    from refrata import updater as U
    up = C.parts.updater
    check(U.parse_version("v1.10.2") > U.parse_version("1.9.9") and U.parse_version("1.1") == (1, 1, 0),
          "version comparison is numeric")
    rel = TMP / "release"
    rel.mkdir(exist_ok=True)
    blob = os.urandom(400_000)
    (rel / "Refrata-Setup-9.9.9.exe").write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    port = start_http(rel)
    base = f"http://127.0.0.1:{port}"

    def publish(tag, digest):
        (rel / "latest.json").write_text(json.dumps({
            "tag_name": tag, "body": "Notas de teste", "html_url": base + "/page",
            "assets": [{"name": "Refrata-Setup-9.9.9.exe", "size": len(blob), "digest": "sha256:" + digest,
                        "browser_download_url": base + "/download/Refrata-Setup-9.9.9.exe"}]}), "utf8")

    up._url = base + "/latest.json"
    up._kind = "installer"
    dry = TMP / "update_dryrun.json"
    os.environ["REFRATA_UPDATE_DRYRUN"] = str(dry)
    try:
        # newer release → banner + settings, all by mouse
        publish("v9.9.9", sha)
        win().setProperty("page", 5)
        pump(0.4)
        sp = C.parts.find("settingsPage")
        ui_click(sp, "updateCheck")
        wait(lambda: up.state == "available", 5, "check finds the newer release")
        check(up.latest == "9.9.9" and up.canSelfUpdate, "latest version parsed", up.latest)
        banner = C.parts.find("updateBanner")
        wait(lambda: banner.property("opacity") > 0.95, 2, "update banner slides in")
        ui_click("updateAction")
        wait(lambda: up.state in ("ready", "error"), 10, "download finished")
        check(up.state == "ready", "installer downloaded and verified", up.error)
        target = Path(up.downloadedPath)
        check(target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == sha,
              "downloaded file matches the published SHA-256")
        check(abs(up.progress - 1.0) < 1e-6, "progress reaches 100%")
        ui_click("updateAction")   # "Reiniciar e atualizar"
        wait(lambda: dry.exists(), 3, "installer launched")
        cmd = json.loads(dry.read_text("utf8"))
        check(cmd["exe"] == str(target) and "/VERYSILENT" in cmd["args"] and "/relaunch=1" in cmd["args"],
              "setup runs silently and relaunches the app", str(cmd["args"]))
        check(up.state == "installing", "state shows installing")

        # tampered download is refused
        up._set("idle")
        publish("v9.9.9", "0" * 64)
        up.checkNow()
        wait(lambda: up.state == "available", 5)
        up.download()
        wait(lambda: up.state == "error", 10, "bad checksum is rejected")
        check("SHA-256" in up.error and not any((TMP / "data" / "updates").glob("*.exe")),
              "nothing is kept after a checksum mismatch", up.error)
        check(banner.property("opacity") > 0.5, "banner explains the failure")
        pump(0.4)  # the pill re-sizes to the error text before the click
        ui_click("updateDismiss")
        wait(lambda: banner.property("opacity") < 0.05, 2, "banner can be dismissed")

        # a broken download is reported as such (never as a checksum problem)
        up._set("idle")
        publish("v9.9.9", sha)
        data = json.loads((rel / "latest.json").read_text("utf8"))
        data["assets"][0]["browser_download_url"] = base + "/download/missing.exe"
        (rel / "latest.json").write_text(json.dumps(data), "utf8")
        up.checkNow()
        wait(lambda: up.state == "available", 5)
        up.download()
        wait(lambda: up.state == "error", 10, "missing asset fails")
        check("download falhou" in up.error, "missing asset reads as a failed download", up.error)

        # same version → up to date, no banner
        publish("v" + up.current, sha)
        ui_click(sp, "updateCheck")
        wait(lambda: up.state == "uptodate", 5, "same version reports up to date")
        check(not up.bannerVisible, "no banner when up to date")

        # offline: a manual check explains, a background one stays quiet
        up._url = "http://127.0.0.1:9/none.json"
        up.checkNow()
        wait(lambda: up.state == "error", 20, "manual check reports the network error")
        up.check(False)
        wait(lambda: up.state == "idle", 20, "background check fails silently")
    finally:
        os.environ.pop("REFRATA_UPDATE_DRYRUN", None)
        up._url = U.API_LATEST
        up._set("idle")
        win().setProperty("page", 0)


class main_hidden:
    """The main window off screen (as when Refrata was started for a file from Explorer); the
    tray mode keeps the app alive meanwhile so closing the quick player does not quit the run."""
    def __enter__(self):
        self.bg = C.parts.tray.background
        C.parts.tray.background = True
        C.parts.win.hide()
        pump(0.2)

    def __exit__(self, *exc):
        QMetaObject.invokeMethod(C.parts.qml.rootObjects()[0], "showMain")
        wait(lambda: C.parts.win.isVisible(), 3)
        C.parts.tray.background = self.bg
        pump(0.2)


def wkey(window, k):
    """Key press+release delivered to a specific window (the quick player)."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    if not window.isActive():   # keys go to the focused item of the *active* window
        QMetaObject.invokeMethod(C.parts.ctl, "activateWindow", Q_ARG(QObject, window))
        wait(lambda: window.isActive(), 3, "quick player has the keyboard focus")
    for typ in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QGuiApplication.sendEvent(window, QKeyEvent(typ, k, Qt.KeyboardModifier.NoModifier))
    pump(0.05)


def wclick(window, item):
    """Real mouse click at the centre of `item`, on its own window (not the main one)."""
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseMove(window, c)
    pump(0.05)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
    pump(0.08)


@test
def t48_quick_player_preview_compact_and_background():
    """Space preview / quick player window and background (tray) mode."""
    from PySide6.QtGui import QWindow
    from refrata import quicklook as Q
    e, ql, tray = eng(), C.parts.quicklook, C.parts.tray
    root = C.parts.qml.rootObjects()[0]
    quick = root.findChild(QWindow, "quickPlayer")
    video, video2 = str(C.fx["video"]), str(C.fx["video2"])

    # the hook's decision: only Explorer / desktop file lists, never while typing
    check(Q.should_preview("explorer", "DirectUIHWND", 5) and Q.should_preview("desktop", "SysListView32", 5),
          "Space previews from Explorer and the desktop")
    check(not Q.should_preview("explorer", "DirectUIHWND", 0.2), "Space while typing a name does nothing")
    check(not Q.should_preview("explorer", "Edit", 5) and not Q.should_preview("other", "DirectUIHWND", 5),
          "search box, rename box and other apps are left alone")
    sib = Q.media_siblings(video)
    check(video in sib and video2 in sib, "folder siblings for ← →", str(len(sib)))

    # preview: opens, plays, browses, closes with Space
    QMetaObject.invokeMethod(quick, "open", Q_ARG("QVariant", video), Q_ARG("QVariant", sib), Q_ARG("QVariant", "preview"))
    wait(lambda: quick.isVisible(), 3, "preview window shows")
    card = C.parts.find("quickCard")
    wait(lambda: card.property("opacity") > 0.99 and abs(card.property("scale") - 1) < 0.01, 2, "preview grows in")
    check(quick.property("path") == video, "previews the selected file")
    wait(lambda: quick.findChild(QObject, "quickSeek").property("value") > 300, 8, "preview plays")
    check(e.state in ("idle", "stopped", "ended") or not e.playing, "the main player is left alone")
    pump(0.35)   # past the guard for the Space that opened it
    wait(lambda: quick.isActive(), 2, "preview has the keyboard focus")
    i0 = quick.property("index")
    wkey(quick, Qt.Key.Key_Right)
    wait(lambda: quick.property("index") == (i0 + 1) % len(sib), 2, "→ goes to the next file in the folder")
    wkey(quick, Qt.Key.Key_Left)
    wait(lambda: quick.property("index") == i0, 2, "← goes back")
    wkey(quick, Qt.Key.Key_Space)
    wait(lambda: not quick.isVisible(), 2, "Space closes the preview")

    # an immediate Space right after opening (the key that triggered it) must not close it
    QMetaObject.invokeMethod(quick, "open", Q_ARG("QVariant", video), Q_ARG("QVariant", sib), Q_ARG("QVariant", "preview"))
    wait(lambda: quick.isVisible(), 3)
    wkey(quick, Qt.Key.Key_Space)
    pump(0.3)
    check(quick.isVisible(), "the Space that opened the preview does not close it")
    wkey(quick, Qt.Key.Key_Escape)
    wait(lambda: not quick.isVisible(), 2, "Esc closes")

    # files from Explorer → compact quick player; Space pauses there; expand hands over to Refrata
    tray.background = True       # keeps the app alive while its window is hidden
    C.parts.win.hide()
    pump(0.2)
    ok = QMetaObject.invokeMethod(root, "openFromExplorer", Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", [video]))
    check(ok is True, "a single file from Explorer opens in the quick player")
    wait(lambda: quick.isVisible() and quick.property("mode") == "compact", 3, "compact quick player")
    wait(lambda: quick.findChild(QObject, "quickSeek").property("value") > 1200, 8, "compact plays")
    pump(0.35)
    QMetaObject.invokeMethod(C.parts.ctl, "activateWindow", Q_ARG(QObject, quick))
    wait(lambda: quick.isActive(), 3, "compact player has the keyboard focus")
    wkey(quick, Qt.Key.Key_Space)
    pump(0.3)
    paused_at = quick.findChild(QObject, "quickSeek").property("value")
    pump(0.6)
    check(abs(quick.findChild(QObject, "quickSeek").property("value") - paused_at) < 150, "Space pauses in compact mode")
    wclick(quick, C.parts.find("quickExpand"))
    wait(lambda: e.state == "playing" and e.currentPath == video, 8, "expand plays it in Refrata")
    wait(lambda: e.time > 0, 5)
    check(e.time >= paused_at - 1500, "Refrata continues where the quick player was", f"{e.time} vs {paused_at}")
    wait(lambda: not quick.isVisible(), 2, "quick player leaves after expanding")
    check(win().property("playerOpen") is True and C.parts.win.isVisible(), "full player is shown")
    tray.background = False
    # the reported bug: with the app open on a video, a second file from Explorer must play in
    # the app (replacing the old one), not in a quick player floating over it
    ok = QMetaObject.invokeMethod(root, "openFromExplorer", Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", [video2]))
    check(ok is False and not quick.isVisible(), "with the app open, Explorer files go to the app")
    QMetaObject.invokeMethod(root, "playPaths", Q_ARG("QVariant", [video2]), Q_ARG("QVariant", 0))
    wait(lambda: e.currentPath == video2 and e.state == "playing", 8, "the new file replaces the old one in the app")
    win().setProperty("playerOpen", False)
    stop_playback()
    settings_off = C.parts.settings
    settings_off.setValue("quickOpen", False)
    with main_hidden():
        ok = QMetaObject.invokeMethod(root, "openFromExplorer", Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", [video]))
        check(ok is False and not quick.isVisible(), "the quick player can be turned off")
    settings_off.setValue("quickOpen", True)

    # background mode: closing the window keeps the app (and the preview) alive in the tray
    from PySide6.QtWidgets import QApplication
    tray.background = True
    check(QApplication.instance().quitOnLastWindowClosed() is False, "background mode keeps the app running")
    C.parts.win.close()
    pump(0.4)
    check(not C.parts.win.isVisible(), "closing the window hides it to the tray")
    QMetaObject.invokeMethod(root, "showMain")
    wait(lambda: C.parts.win.isVisible(), 2, "tray brings the window back")
    tray.background = False
    check(QApplication.instance().quitOnLastWindowClosed() is True, "background mode off restores normal quitting")
    check(tray.autostart is False, "the battery never sees or touches the real start-with-Windows entry")


@test
def t49_ab_repeat_and_always_on_top():
    e = eng()
    pv = open_player_with([C.fx["tone_long"], C.fx["tone_alt"]])
    wait(lambda: e.state == "playing" and e.length > 0, 8)
    if not e.muted:
        e.toggleMute()
    e.seek(5000)
    wait(lambda: e.time >= 5000, 3)
    key(C.parts, Qt.Key.Key_L)
    check(e.loopA >= 4500 and e.loopB < 0, "L marks the start of the stretch", str(e.loopA))
    wait(lambda: e.time >= e.loopA + 2200, 5, "plays past the start")
    ui_click("mpLoop")   # music mode: the dock button
    a, b = e.loopA, e.loopB
    check(b > a + 1500, "second press marks the end", f"{a}-{b}")
    check(C.parts.find("loopRange").property("visible") is True or e.isAudio, "the stretch shows on the seek bar")
    t_end = time.time() + 4.5
    seen_max = 0
    while time.time() < t_end:
        pump(0.1)
        seen_max = max(seen_max, e.time)
        check(e.time <= b + 700, "never plays past the end of the stretch", f"{e.time} > {b}")
    check(e.time >= a - 300, "jumps back to the start", f"{e.time} vs {a}")
    key(C.parts, Qt.Key.Key_L)
    check(e.loopA < 0 and e.loopB < 0, "third press turns it off")
    key(C.parts, Qt.Key.Key_L)
    e.next()
    wait(lambda: e.currentIndex == 1, 4)
    check(e.loopA < 0, "a new track clears the stretch")

    # always on top: T toggles the window flag
    from PySide6.QtCore import Qt as _Qt
    key(C.parts, Qt.Key.Key_T)
    pump(0.3)
    check(bool(C.parts.win.flags() & _Qt.WindowType.WindowStaysOnTopHint), "T keeps the window on top")
    check(C.parts.find("pvOnTop").property("checked") is True or e.isAudio, "the pin shows it")
    key(C.parts, Qt.Key.Key_T)
    pump(0.3)
    check(not (C.parts.win.flags() & _Qt.WindowType.WindowStaysOnTopHint), "T again releases it")
    win().setProperty("playerOpen", False)
    stop_playback()


@test
def t99_shutdown_persists_state():
    e, s = eng(), C.parts.settings
    p = str(C.fx["tone_long"])
    e.openFiles([p])
    wait(lambda: e.state == "playing" and e.length > 0, 8)
    e.seek(25000)
    wait(lambda: e.time >= 24500, 5)
    C.parts.engine.save_state()
    s.save()
    C.parts.library.shutdown()
    data = json.loads((TMP / "data" / "settings.json").read_text(encoding="utf8"))
    entry = [h for h in data["history"] if h["path"] == p][0]
    check(24000 < entry["pos"] < 28000, "resume position written on shutdown", str(entry["pos"]))
    check((TMP / "data" / "library.json").exists(), "library cache written on shutdown")
    check(not C.parts.library._thumbs.is_alive(), "thumbnailer thread stopped")
    C.parts.engine.shutdown()
    check(not main.QT_MESSAGES, "zero Qt warnings during the whole run", "; ".join(main.QT_MESSAGES[:5]))


# ====================================================================== run
def run(filter_text="", report=None):
    print(f"Refrata E2E — temp dir: {TMP} — {'frozen build' if FROZEN else 'source tree'} — libVLC: {main.VLC_DIR}")
    from PySide6.QtWidgets import QApplication
    app = QGuiApplication.instance() or QApplication(sys.argv)
    t = time.time()
    C.fx = make_all(TMP / "fx")
    print(f"fixtures ready in {time.time() - t:.1f}s")
    (TMP / "data" / "settings.json").write_text(json.dumps({
        "settings": {"folders": [str(C.fx["media_root"])], "snapshotDir": str(TMP / "snaps"), "volume": 30},
        "history": [],
    }), encoding="utf8")
    C.http_port = start_http(C.fx["media_root"])
    C.parts = main.build(sys.argv)
    C.parts.win.setWidth(1440)
    C.parts.win.setHeight(900)
    C.messages = []
    C.parts.engine.message.connect(C.messages.append)
    C.parts.engine.toggleMute()  # silent run
    C.parts.library.rescan()
    pump(1.5)
    C.boot_messages = list(main.QT_MESSAGES)

    keys = [k for k in filter_text.split(",") if k] or [""]
    selected = sorted((f for f in TESTS if any(k in f.__name__ for k in keys)), key=lambda f: f.__name__)
    ok = 0
    for fn in selected:
        name = fn.__name__
        t0 = time.time()
        CURRENT["test"] = name
        try:
            main.QT_MESSAGES.clear()
            fn()
            # zero-warning policy: any Qt/QML warning during a test fails it
            check(not main.QT_MESSAGES, "no Qt/QML warnings during the test", "; ".join(main.QT_MESSAGES[:4]))
            RESULTS.append((name, True, ""))
            ok += 1
            print(f"  PASS  {name}  ({time.time() - t0:.1f}s)")
        except Exception as exc:
            detail = str(exc) if isinstance(exc, Fail) else traceback.format_exc()
            RESULTS.append((name, False, detail))
            print(f"  FAIL  {name}  ({time.time() - t0:.1f}s)\n        {detail}")
            e = C.parts.engine
            print(f"        state={e.state} idx={e.currentIndex} queue={[Path(q['path']).name for q in e.queue]} "
                  f"time={e.time}/{e.length} frames={e._frames_seen} playerOpen={C.parts.win.property('playerOpen')} "
                  f"msgs={C.messages[-3:]}")
            if main.QT_MESSAGES:
                print("        qt:", main.QT_MESSAGES[-3:])
            main.QT_MESSAGES.clear()
    if FOCUS_LOSSES:
        print(f"\nnote: window focus was taken by another app and restored {len(FOCUS_LOSSES)}x "
              f"(in {sorted(set(FOCUS_LOSSES))})")
    print(f"\n{ok}/{len(selected)} tests passed")
    if report:
        Path(report).write_text(json.dumps({
            "passed": ok, "total": len(selected), "frozen": FROZEN, "vlc": str(main.VLC_DIR),
            "package": main.package_family(), "about": dict(C.parts.ctl.about) if C.parts else {},
            "results": [{"name": n, "ok": good, "detail": d} for n, good, d in RESULTS],
        }, indent=2, ensure_ascii=False), encoding="utf8")
    sys.stdout.flush()
    os._exit(0 if ok == len(selected) else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    run(a.k, a.report or None)
