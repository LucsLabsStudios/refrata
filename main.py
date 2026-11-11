"""Refrata – a modern player built on the VLC engine (libVLC), by LucsLabs Studios.

Usage:
    Refrata [files, folders, playlists or URLs...]
    Refrata --screenshot out.png [--page home|videos|music|queue|network|settings|player]
            [--panel tracks|speed|eq|adjust|sleep|queue] [--delay ms] [--size WxH] [--theme dark|light]
    Refrata --selftest [-k filter] [--report results.json]   (runs the E2E battery inside the app)
    Refrata --new-window ...                                  (skip the single-instance hand-off)
"""
import argparse
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from refrata import trace  # noqa: E402  (REFRATA_TRACE: startup timeline)

trace.mark("python ready")
# Opening a file while Refrata runs: hand it over before Qt and libVLC load (refrata/fastlaunch.py)
if __name__ == "__main__":
    from refrata.fastlaunch import try_hand_over
    FAST_ID = try_hand_over()   # exits here when the running Refrata took the file
else:
    FAST_ID = ""

if __name__ == "__main__":
    # `import main` (tests) must see this very module, not a second copy
    sys.modules.setdefault("main", sys.modules[__name__])

# Every video frame passes through Python twice (libVLC's decoder thread, then the UI thread). With
# another Python thread busy (library scan, yt-dlp, thumbnails) each pass waited up to the default
# 5 ms GIL switch interval; at 1 ms a 4K60 video under that load went from ~3 to ~20 frames a second
# (tools/perf.py). Lower than that, the threads spend their time handing the GIL back and forth.
sys.setswitchinterval(float(os.environ.get("REFRATA_SWITCH_INTERVAL", "0.001")))

# Tests (REFRATA_E2E_TMP) keep out of the person's own files: Qt's shader pipeline cache lives in
# the user's AppData/Refrata/cache, and parallel test apps fought over its lock file there
if os.environ.get("REFRATA_E2E_TMP") or "--selftest" in sys.argv or "--scenario" in sys.argv:
    os.environ.setdefault("QSG_RHI_DISABLE_DISK_CACHE", "1")

# --selftest must isolate the data dir before refrata.settings computes DATA_DIR
if "--selftest" in sys.argv and not os.environ.get("REFRATA_E2E_TMP"):
    _tmp = Path(tempfile.mkdtemp(prefix="refrata_e2e_"))
    os.environ["REFRATA_E2E_TMP"] = str(_tmp)
    os.environ["REFRATA_DATA"] = str(_tmp / "data")

from refrata import __version__  # noqa: E402
from refrata.vlcpath import app_root, configure as configure_vlc, preload_qt  # noqa: E402

VLC_DIR = configure_vlc()
QT_PRELOADED = preload_qt()
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

import shiboken6  # noqa: E402
from PySide6.QtCore import QLoggingCategory, QObject, QTimer, QUrl, Slot, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QFont, QGuiApplication, QIcon  # noqa: E402
# QApplication (widgets): the tray icon and Explorer's COM automation (QuickLook-style preview) need it
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickWindow  # noqa: E402

from refrata.app import AppController  # noqa: E402
from refrata.engine import Engine  # noqa: E402
from refrata.library import Library  # noqa: E402
from refrata.nativelog import redirect_native_stderr  # noqa: E402
from refrata.settings import DATA_DIR, Settings  # noqa: E402
from refrata.single import InstanceServer  # noqa: E402
from refrata.theme import Theme  # noqa: E402
from refrata.updater import Updater  # noqa: E402
from refrata.quicklook import QuickLook  # noqa: E402
from refrata.tray import Tray  # noqa: E402
from refrata.i18n import I18n  # noqa: E402
from refrata.media import SUB_EXT, is_audio_path, is_media_path  # noqa: E402
from refrata.mediakeys import MediaKeys  # noqa: E402
from refrata.subsearch import SubtitleSearch  # noqa: E402

ROOT = app_root()
trace.mark("imports done (Qt, libVLC, app modules)")

# Every Qt/QML warning goes through here; tests install a collector.
QT_MESSAGES: list[str] = []
_IGNORED = (
    "propagateComposedEvents",
    # Qt's clipboard watcher, when another app holds the Windows clipboard at that instant
    # (the user copying something while the app runs); Qt retries and recovers by itself
    "Retrying to obtain clipboard", "Unable to obtain clipboard",
    # QtMultimedia's FFmpeg (quick player) on MKVs without a stream duration: it falls back by itself
    "AVStream duration",
    # font fallback walking past fonts without a script (e.g. Devanagari for "हिन्दी") before it
    # reaches one that has it (Nirmala UI); the text renders with the fallback
    "OpenType support missing for",
    # Linux machines without PipeWire, VA-API or a GPU (CI, containers, WSL): QtMultimedia (the
    # preview window) and the GL stack say so once; playback works, it is the environment talking
    "Couldn't load pipewire", "Couldn't resolve pipewire", "No HW decoder found", "eglSwapBuffers failed",
    "This plugin does not support",
)


def _qt_log(mode, ctx, msg):
    if any(s in msg for s in _IGNORED):
        return
    QT_MESSAGES.append(msg)
    try:
        sys.stderr.write(f"[qt] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


qInstallMessageHandler(_qt_log)
# The font fallback note is logged from the render thread while the GUI thread waits for it in
# sync; reaching the Python handler there needs the GIL, which the GUI thread may hold (a grab, a
# synthetic key in tests): a deadlock. Silenced at the category, it never leaves Qt.
QLoggingCategory.setFilterRules("qt.text.font.db.info=false\nqt.text.font.db.debug=false")


@dataclass
class AppParts:
    app: QGuiApplication
    qml: QQmlApplicationEngine
    win: QQuickWindow
    settings: Settings
    theme: Theme
    engine: Engine
    library: Library
    ctl: AppController
    updater: Updater
    quicklook: QuickLook
    tray: Tray
    i18n: I18n
    instance: InstanceServer | None = None
    mediakeys: object = None   # Windows media keys / overlay (refrata/mediakeys.py)
    subsearch: object = None   # online subtitles (refrata/subsearch.py)
    assoc: object = None       # "Abrir com" / Apps padrão registration (refrata/assoc.py)
    keys: object = None        # customizable shortcuts (refrata/shortcuts.py)

    def find(self, name: str) -> QObject:
        obj = self.win.findChild(QObject, name)
        if obj is None:
            # Repeater/ListView delegates are only reachable through the visual tree
            stack = [self.win.contentItem()]
            while stack and obj is None:
                it = stack.pop()
                for c in it.childItems():
                    if c.objectName() == name:
                        obj = c
                        break
                    stack.append(c)
        if obj is None:
            raise LookupError(f"QML object '{name}' not found")
        return obj

    def cleanup(self):
        # persist everything first; releasing libVLC is the risky part. Each step runs even if
        # an earlier one failed: a step that throws must not leave the hook installed or libVLC
        # running into the process teardown.
        steps = [self.quicklook.shutdown, self.tray.shutdown, self.engine.save_state, self.settings.save,
                 self.library.shutdown, self.engine.shutdown]
        if self.instance:
            steps.insert(0, self.instance.close)
        if self.mediakeys is not None:
            steps.insert(0, self.mediakeys.shutdown)
        if self.subsearch is not None:
            steps.insert(0, self.subsearch.shutdown)
        for step in steps:
            try:
                step()
            except Exception as exc:
                print(f"shutdown step {getattr(step, '__qualname__', step)} failed: {exc!r}")


def package_family() -> str:
    """MSIX package family name when running from the Store/MSIX package, else ""."""
    import ctypes
    try:
        n = ctypes.c_uint32(0)
        fn = ctypes.windll.kernel32.GetCurrentPackageFamilyName
        if fn(ctypes.byref(n), None) != 122:  # ERROR_INSUFFICIENT_BUFFER; 15700 = not packaged
            return ""
        buf = ctypes.create_unicode_buffer(n.value)
        return buf.value if fn(ctypes.byref(n), buf) == 0 else ""
    except (AttributeError, OSError):
        return ""


def _vlc_version() -> str:
    try:
        import vlc
        return vlc.libvlc_get_version().decode("utf8", "replace").split(" ")[0]
    except Exception:
        return "?"


def build(argv=None, page="", panel="", theme_name="", instance_name: str | None = "", start_hidden=False,
          instance=None, preopen=None) -> AppParts:
    """instance_name: "" = default per-user name, None = no single-instance server."""
    redirect_native_stderr(DATA_DIR / "native.log")
    QGuiApplication.setApplicationName("Refrata")
    QGuiApplication.setOrganizationName("LucsLabs Studios")
    QGuiApplication.setApplicationVersion(__version__)
    app = QGuiApplication.instance() or QApplication(argv or sys.argv)
    app.setWindowIcon(QIcon(str(ROOT / "assets" / "icon.ico")))

    trace.mark("build: QApplication")
    settings = Settings()
    if theme_name:
        settings.setValue("theme", theme_name)
    # the translator goes in first: everything built after it (engine messages, tray menu) speaks it
    i18n = I18n(settings)
    theme = Theme(settings)
    # scripts the Latin UI fonts lack go straight to the Windows font made for them, instead of
    # Qt walking every installed font (slow, and old bitmap fonts like Fixedsys fail in DirectWrite)
    for fam in {theme.textFont, theme.displayFont}:
        QFont.insertSubstitutions(fam, ["Nirmala UI", "Microsoft YaHei UI", "Yu Gothic UI", "Malgun Gothic",
                                        "Segoe UI Symbol", "Segoe UI Emoji"])
    f = QFont(theme.textFont)
    f.setPixelSize(14)
    app.setFont(f)
    trace.mark("build: settings, translations, theme")
    engine = Engine(settings)
    trace.mark("build: engine (libVLC instance)")
    if preopen:
        # libVLC opens the file on its own threads while the interface below is built (~2 s): the
        # first picture is ready when the window is, instead of a second or so after it
        engine.holdStart()               # ...and waits there until the window is on screen
        engine.openFiles(preopen, 0)
        trace.mark("build: file handed to libVLC")
    library = Library(settings)
    trace.mark("build: library")
    ctl = AppController()
    updater = Updater(settings)
    updater.quitRequested.connect(app.quit)
    # yt-dlp refreshes itself (refrata/ytdlp.py) under the same switch as app updates
    from refrata import ytdlp
    ytdlp.allowed = lambda: bool(settings.value("autoUpdate", True))
    quicklook = QuickLook(settings)
    tray = Tray(settings, quicklook, ROOT / "assets" / "icon.ico")
    i18n.changed.connect(tray.retranslate)
    from refrata.assoc import Associations
    from refrata.shortcuts import Shortcuts
    assoc = Associations(app)
    if updater.kind == "portable":
        assoc.repair()
    keys = Shortcuts(settings, app)
    ctl.setAbout({
        "version": __version__,
        "vlcVersion": _vlc_version(),
        "vlcBundled": bool(VLC_DIR and "VideoLAN" not in str(VLC_DIR)),
        "studio": "LucsLabs Studios",
        "installed": bool(getattr(sys, "frozen", False) and (Path(sys.executable).parent / "unins000.exe").exists())
        or bool(package_family()),
        "package": package_family(),
        "contact": "lucslabs.studios@gmail.com",
    })

    trace.mark("build: updater, preview, tray, associations, shortcuts")
    qml = QQmlApplicationEngine()
    ctx = qml.rootContext()
    ctx.setContextProperty("settings", settings)
    ctx.setContextProperty("theme", theme)
    ctx.setContextProperty("player", engine)
    ctx.setContextProperty("library", library)
    ctx.setContextProperty("appctl", ctl)
    ctx.setContextProperty("updater", updater)
    ctx.setContextProperty("quicklook", quicklook)
    ctx.setContextProperty("tray", tray)
    ctx.setContextProperty("i18n", i18n)
    ctx.setContextProperty("winassoc", assoc)
    ctx.setContextProperty("keys", keys)
    subsearch = SubtitleSearch(engine, DATA_DIR, app)
    ctx.setContextProperty("subsearch", subsearch)
    i18n.attach(qml)
    ctx.setContextProperty("startHidden", bool(start_hidden))
    ctx.setContextProperty("startPage", page)
    ctx.setContextProperty("startPanel", panel)
    ctx.setContextProperty("assetsUrl", QUrl.fromLocalFile(str(ROOT / "assets") + "/").toString())
    trace.mark("build: QML context")
    qml.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))
    trace.mark("build: QML loaded (window created)")
    if not qml.rootObjects():
        raise RuntimeError("Main.qml failed to load:\n" + "\n".join(QT_MESSAGES))
    root = qml.rootObjects()[0]
    win = shiboken6.wrapInstance(shiboken6.getCppPointer(root)[0], QQuickWindow)

    if instance is None and instance_name is not None:
        instance = InstanceServer(instance_name or None, app)
        instance.listen()
    if instance is not None:
        instance.setParent(app)
        instance.received.connect(ctl.handOver)
        instance.activateRequested.connect(lambda: QTimer.singleShot(0, lambda: root.showMain()))
        instance.release()   # requests that came in while the window was being built
    tray.showRequested.connect(lambda: QTimer.singleShot(0, lambda: root.showMain()))
    if sys.platform.startswith("linux"):
        from refrata.mpris import Mpris   # the Linux media keys / media widget (MPRIS2 over D-Bus)
        mediakeys = Mpris(engine, library, app)
        mediakeys.raiseRequested.connect(lambda: QTimer.singleShot(0, lambda: root.showMain()))
        mediakeys.openRequested.connect(ctl.handOver)
    else:
        mediakeys = MediaKeys(engine, library, app)
    trace.mark("build: media keys, instance server")
    return AppParts(app, qml, win, settings, theme, engine, library, ctl, updater, quicklook, tray, i18n, instance,
                    mediakeys, subsearch, assoc, keys)


def hard_exit(code: int = 0):
    """Leave without any native teardown. Everything is saved by now; what remains is libVLC's and
    Qt Multimedia's DLL detach code, which can crash at exit (seen after Windows invalidates the
    audio device mid-session: AUDCLNT_E_DEVICE_INVALIDATED). ExitProcess (os._exit) runs that
    detach code; TerminateProcess does not."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except Exception:
            pass
    cov = sys.modules.get("coverage")   # a measured test run (tools/coverage_run.py): keep its data
    if cov is not None:
        try:
            current = cov.Coverage.current()
            if current is not None:
                current.stop()
                current.save()
        except Exception:
            pass
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k32.TerminateProcess(k32.GetCurrentProcess(), code & 0xFFFFFFFF)
    os._exit(code)


def _norm_args(files: list[str]) -> list[str]:
    return [os.path.abspath(x) if os.path.exists(x) else x for x in files]


def _ensure_streams(log_path: Path | None):
    """Windowed (frozen) builds have no console: give print() somewhere to go."""
    if log_path is not None:
        fh = open(log_path, "w", encoding="utf8", buffering=1)
        sys.stdout = sys.stderr = fh
        return
    if sys.stdout is None or sys.stderr is None:
        fh = open(DATA_DIR / "stdout.log", "w", encoding="utf8", buffering=1)
        sys.stdout = sys.stdout or fh
        sys.stderr = sys.stderr or fh


def close_splash():
    """Hide the bootloader's splash (tools/build.py); nothing to do when run from source."""
    try:
        import pyi_splash  # noqa: PLC0415  (only exists inside the frozen app)
        if pyi_splash.is_alive():
            pyi_splash.close()
    except Exception:
        pass


def main():
    import faulthandler

    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--screenshot")
    ap.add_argument("--page", default="")
    ap.add_argument("--panel", default="")
    ap.add_argument("--delay", type=int, default=3500)
    ap.add_argument("--size", default="1440x900")
    ap.add_argument("--theme", default="")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--scenario", default="")    # one robustness scenario (tests/robust.py)
    ap.add_argument("-k", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--log", default="")
    ap.add_argument("--new-window", action="store_true")
    ap.add_argument("--background", action="store_true")
    ap.add_argument("files", nargs="*")
    args, _ = ap.parse_known_args()

    _ensure_streams(Path(args.log) if args.log else None)
    crash_log = open(DATA_DIR / "crash.log", "w", encoding="utf8")
    faulthandler.enable(crash_log, all_threads=True)

    if args.selftest or args.scenario:
        close_splash()
    if args.selftest:
        from tests import e2e
        e2e.run(args.k, report=args.report or None)  # exits the process
        return
    if args.scenario:
        from tests import robust
        robust.child(args.scenario)  # exits the process
        return

    files = _norm_args(args.files)
    single = not (args.screenshot or args.new_window)
    early = None
    if single:
        # one Refrata per user: hand the files to the running one and leave, or become it and start
        # listening right away (requests wait until the window is ready; the QApplication is reused)
        QApplication(sys.argv)
        from refrata.single import claim_or_hand_over  # noqa: PLC0415
        early = claim_or_hand_over(files, msg_id=FAST_ID)
        if early is None:
            close_splash()
            os._exit(0)

    # a single media file from Explorer opens in the quick player; --background starts in the tray
    from refrata.settings import Settings as _S  # noqa: PLC0415
    quick_open = bool(_S().value("quickOpen", False)) if files else False
    hidden = args.background or (quick_open and len(files) == 1 and is_media_path(files[0]))
    # the full app opening files: start them before the interface is built (see build)
    from refrata.app import expand_paths  # noqa: PLC0415
    preopen = expand_paths(files) if files and not hidden else []
    if len(files) == 1 and os.path.splitext(files[0])[1].lower() in SUB_EXT:
        preopen = []   # a subtitle alone goes through the app (it needs a playing video)
    parts = build(sys.argv, args.page, args.panel, args.theme, instance_name="" if single else None,
                  start_hidden=hidden and not args.screenshot, instance=early, preopen=preopen)
    engine, library, win, app = parts.engine, parts.library, parts.win, parts.app
    if hidden or args.screenshot:
        close_splash()
    else:
        # the splash goes once the window has drawn its first frame (or after 20 s, whatever happens).
        # frameSwapped comes from the render thread: a main-thread QObject receives it queued.
        class _Splash(QObject):
            done = False

            @Slot()
            def close(self):
                if not self.done:
                    self.done = True
                    trace.mark("first window frame on screen")
                    close_splash()
                    win.frameSwapped.disconnect(self.close)
        splash = _Splash(app)
        win.frameSwapped.connect(splash.close)
        QTimer.singleShot(20000, close_splash)
    if args.screenshot:
        w, h = (int(x) for x in args.size.split("x"))
        win.setWidth(w)
        win.setHeight(h)

    trace.mark("window built, starting library scan and update check")
    if parts.quicklook.active:
        # the Space preview's link to Explorer, ready before the first Space (refrata/quicklook.py)
        from refrata.quicklook import warm_up  # noqa: PLC0415
        QTimer.singleShot(2500, warm_up)
    library.rescan()
    if not args.screenshot:
        parts.updater.start()
    if args.screenshot:
        engine.toggleMute()  # automated captures stay silent
    if preopen:
        # already opening (build): only show it the way the app does for files it is handed
        if not engine.isAudio and not is_audio_path(preopen[0]):
            parts.win.setProperty("playerOpen", True)
        # held on its first picture since build(): it starts with the window's first frame on screen
        # (or after a moment, should that frame never be reported)
        once = {"done": False}

        def start_playing():
            if not once["done"]:
                once["done"] = True
                engine.releaseStart()
                try:
                    parts.win.frameSwapped.disconnect(start_playing)
                except (RuntimeError, TypeError):
                    pass
        parts.win.frameSwapped.connect(start_playing)
        QTimer.singleShot(4000, start_playing)
    elif files:
        parts.ctl.handOver(files)

    if args.screenshot:
        def shot():
            print(f"[shot] state={engine.state} time={engine.time} len={engine.length} "
                  f"frames={engine._frames_seen} dropped={engine._dropped} size={engine.videoWidth}x{engine.videoHeight} "
                  f"vlc={VLC_DIR}")
            try:
                win.grabWindow().save(args.screenshot)
            finally:
                app.quit()

        # the same counters once playback has settled: the difference to the end is playback alone
        QTimer.singleShot(min(3500, args.delay // 2), lambda: print(
            f"[warm] frames={engine._frames_seen} dropped={engine._dropped}", flush=True))
        QTimer.singleShot(args.delay, shot)

    app.aboutToQuit.connect(parts.cleanup)
    code = app.exec()
    # libVLC worker threads may still call back into Python; skip interpreter and DLL teardown.
    hard_exit(code)


if __name__ == "__main__":
    main()
