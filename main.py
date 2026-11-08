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

if __name__ == "__main__":
    # `import main` (tests) must see this very module, not a second copy
    sys.modules.setdefault("main", sys.modules[__name__])

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
from PySide6.QtCore import QObject, QTimer, QUrl, qInstallMessageHandler  # noqa: E402
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
from refrata.single import InstanceServer, send_to_running, server_name  # noqa: E402
from refrata.theme import Theme  # noqa: E402
from refrata.updater import Updater  # noqa: E402
from refrata.quicklook import QuickLook  # noqa: E402
from refrata.tray import Tray  # noqa: E402
from refrata.i18n import I18n  # noqa: E402
from refrata.media import is_media_path  # noqa: E402

ROOT = app_root()

# Every Qt/QML warning goes through here; tests install a collector.
QT_MESSAGES: list[str] = []
_IGNORED = (
    "propagateComposedEvents",
    # Qt's clipboard watcher, when another app holds the Windows clipboard at that instant
    # (the user copying something while the app runs); Qt retries and recovers by itself
    "Retrying to obtain clipboard",
    # QtMultimedia's FFmpeg (quick player) on MKVs without a stream duration: it falls back by itself
    "AVStream duration",
    # font fallback walking past fonts without a script (e.g. Devanagari for "हिन्दी") before it
    # reaches one that has it (Nirmala UI); the text renders with the fallback
    "OpenType support missing for",
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
        # persist everything first; releasing libVLC is the risky part
        if self.instance:
            self.instance.close()
        self.quicklook.shutdown()
        self.tray.shutdown()
        self.engine.save_state()
        self.settings.save()
        self.library.shutdown()
        self.engine.shutdown()


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


def build(argv=None, page="", panel="", theme_name="", instance_name: str | None = "", start_hidden=False) -> AppParts:
    """instance_name: "" = default per-user name, None = no single-instance server."""
    redirect_native_stderr(DATA_DIR / "native.log")
    QGuiApplication.setApplicationName("Refrata")
    QGuiApplication.setOrganizationName("LucsLabs Studios")
    QGuiApplication.setApplicationVersion(__version__)
    app = QGuiApplication.instance() or QApplication(argv or sys.argv)
    app.setWindowIcon(QIcon(str(ROOT / "assets" / "icon.ico")))

    settings = Settings()
    if theme_name:
        settings.setValue("theme", theme_name)
    # the translator goes in first: everything built after it (engine messages, tray menu) speaks it
    i18n = I18n(settings)
    theme = Theme(settings)
    f = QFont(theme.textFont)
    f.setPixelSize(14)
    app.setFont(f)
    engine = Engine(settings)
    library = Library(settings)
    ctl = AppController()
    updater = Updater(settings)
    updater.quitRequested.connect(app.quit)
    quicklook = QuickLook(settings)
    tray = Tray(settings, quicklook, ROOT / "assets" / "icon.ico")
    i18n.changed.connect(tray.retranslate)
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
    i18n.attach(qml)
    ctx.setContextProperty("startHidden", bool(start_hidden))
    ctx.setContextProperty("startPage", page)
    ctx.setContextProperty("startPanel", panel)
    ctx.setContextProperty("assetsUrl", QUrl.fromLocalFile(str(ROOT / "assets") + "/").toString())
    qml.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))
    if not qml.rootObjects():
        raise RuntimeError("Main.qml failed to load:\n" + "\n".join(QT_MESSAGES))
    root = qml.rootObjects()[0]
    win = shiboken6.wrapInstance(shiboken6.getCppPointer(root)[0], QQuickWindow)

    instance = None
    if instance_name is not None:
        instance = InstanceServer(instance_name or None, app)
        instance.listen()
        instance.received.connect(ctl.handOver)
        instance.activateRequested.connect(lambda: QTimer.singleShot(0, lambda: root.showMain()))
    tray.showRequested.connect(lambda: QTimer.singleShot(0, lambda: root.showMain()))
    return AppParts(app, qml, win, settings, theme, engine, library, ctl, updater, quicklook, tray, i18n, instance)


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

    if args.selftest:
        from tests import e2e
        e2e.run(args.k, report=args.report or None)  # exits the process
        return

    files = _norm_args(args.files)
    single = not (args.screenshot or args.new_window)
    if single:
        # hand the files to a running instance and leave (the QGuiApplication is reused by build)
        QApplication(sys.argv)
        if send_to_running(files):
            os._exit(0)

    # a single media file from Explorer opens in the quick player; --background starts in the tray
    from refrata.settings import Settings as _S  # noqa: PLC0415
    quick_open = bool(_S().value("quickOpen", False)) if files else False
    hidden = args.background or (quick_open and len(files) == 1 and is_media_path(files[0]))
    parts = build(sys.argv, args.page, args.panel, args.theme, instance_name="" if single else None,
                  start_hidden=hidden and not args.screenshot)
    engine, library, win, app = parts.engine, parts.library, parts.win, parts.app
    if args.screenshot:
        w, h = (int(x) for x in args.size.split("x"))
        win.setWidth(w)
        win.setHeight(h)

    library.rescan()
    if not args.screenshot:
        parts.updater.start()
    if args.screenshot:
        engine.toggleMute()  # automated captures stay silent
    if files:
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

        QTimer.singleShot(args.delay, shot)

    app.aboutToQuit.connect(parts.cleanup)
    code = app.exec()
    # libVLC worker threads may still call back into Python; skip interpreter and DLL teardown.
    hard_exit(code)


if __name__ == "__main__":
    main()
