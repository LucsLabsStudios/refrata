"""Small OS-level helpers for QML (window chrome, explorer, clipboard)."""
import ctypes
import os
import subprocess
import sys

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QWindow

from .media import SUB_EXT, is_media_path
from .playlists import is_playlist, read_playlist


def force_foreground(hwnd: int):
    """SetForegroundWindow from a background app: Windows only honours it for the thread that owns
    the current foreground input, so attach to that thread's input queue for the call."""
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    fg = u.GetForegroundWindow()
    fg_tid = u.GetWindowThreadProcessId(fg, None) if fg else 0
    me = k.GetCurrentThreadId()
    attached = bool(fg_tid and fg_tid != me and u.AttachThreadInput(me, fg_tid, True))
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(me, fg_tid, False)


class AppController(QObject):
    aboutChanged = Signal()
    # media handed over by a second launch (Explorer double-click, "Abrir com")
    openRequested = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._about = {}

    def setAbout(self, info: dict):
        self._about = dict(info)
        self.aboutChanged.emit()

    @Property("QVariantMap", notify=aboutChanged)
    def about(self):
        return self._about

    def handOver(self, args: list[str]):
        """Route command-line / second-instance arguments to the UI."""
        if len(args) == 1 and self.isSubtitle(args[0]):
            self.openRequested.emit([os.path.normpath(args[0])])
        else:
            self.openRequested.emit(self.expand(args))

    @Slot(str)
    def openExternal(self, url):
        QDesktopServices.openUrl(QUrl(url))

    def activate(self, window: QWindow):
        """Bring the window to the front (Windows only lets the foreground app steal focus)."""
        # show / restore through Qt only: a native ShowWindow left Qt believing a hidden window was
        # still hidden, so closing it (to the tray) did nothing afterwards
        if not window.isVisible():
            window.show()
        elif window.visibility() == QWindow.Visibility.Minimized:
            window.showNormal()
        window.raise_()
        window.requestActivate()
        if sys.platform == "win32":
            try:
                force_foreground(int(window.winId()))
            except Exception:
                pass

    @Slot(QObject)
    def activateWindow(self, window):
        """QML: bring one of our windows (e.g. the quick player) to the front with the focus."""
        if isinstance(window, QWindow):
            window.raise_()
            window.requestActivate()
            if sys.platform == "win32":
                try:
                    force_foreground(int(window.winId()))
                except Exception:
                    pass

    @Slot(QObject, bool, QColor)
    def styleWindow(self, window, dark, caption):
        """Dark/light native title bar tinted with the app background (Windows 11)."""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(window.winId())
            dwm = ctypes.windll.dwmapi
            val = ctypes.c_int(1 if dark else 0)
            dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)  # immersive dark mode
            ref = ctypes.c_int(caption.red() | (caption.green() << 8) | (caption.blue() << 16))
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(ref), 4)  # caption color
            border = ctypes.c_int(0xFFFFFFFE)  # DWMWA_COLOR_NONE
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), 4)
            corner = ctypes.c_int(2)  # rounded
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4)
        except Exception as exc:
            print("dwm:", exc)

    @Slot(str)
    def reveal(self, path):
        if path.startswith("file:"):
            path = QUrl(path).toLocalFile()
        if os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    @Slot(result=str)
    def clipboardText(self):
        return QGuiApplication.clipboard().text().strip()

    @Slot(str)
    def copyText(self, text):
        QGuiApplication.clipboard().setText(text)

    @Slot(str, result=bool)
    def isSubtitle(self, url):
        p = QUrl(url).toLocalFile() if url.startswith("file:") else url
        return os.path.splitext(p)[1].lower() in SUB_EXT

    @Slot(str, result=bool)
    def isPlaylist(self, url):
        p = QUrl(url).toLocalFile() if url.startswith("file:") else url
        return is_playlist(p)

    @Slot(str, result=bool)
    def isMedia(self, url):
        p = QUrl(url).toLocalFile() if url.startswith("file:") else url
        return is_media_path(p) or os.path.isdir(p)

    @Slot(list, result=list)
    def expand(self, urls):
        """Turn dropped files/folders into a flat, sorted list of media paths."""
        out = []
        for u in urls:
            p = QUrl(u).toLocalFile() if str(u).startswith("file:") else str(u)
            if "://" not in p:
                # QUrl gives forward slashes; libVLC rejects mixed separators on Windows
                p = os.path.normpath(p)
            if os.path.isdir(p):
                for root, _dirs, files in os.walk(p):
                    for f in sorted(files):
                        fp = os.path.join(root, f)
                        if is_media_path(fp):
                            out.append(fp)
            elif is_playlist(p):
                out.extend(read_playlist(p))
            elif is_media_path(p) or "://" in p:
                out.append(p)
        return out
