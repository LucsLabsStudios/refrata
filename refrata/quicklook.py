"""QuickLook-style preview: select a video or song in Explorer (or on the desktop) and press Space.

How it works
- A foreground-window event (SetWinEventHook) tells when an Explorer window or the desktop comes to
  the front; only then is a low-level keyboard hook installed, and it is removed when another app
  takes the front. Typing anywhere else never passes through Refrata (or waits on Python).
- The keyboard hook (WH_KEYBOARD_LL) sees the Space key. It never swallows
  keys and never does slow work inside the hook: it only checks, cheaply, that the foreground
  window is an Explorer folder view (or the desktop) with the file list focused, and that the
  user is not typing (type-ahead / rename), then queues the real work on the Qt event loop.
- The selected item is read through Explorer's own automation (Shell.Application ›
  Windows › Document.SelectedItems, or the desktop's view) via ActiveQt.
- If it is one media file, `previewRequested(path, siblings)` is emitted; the QML preview window
  plays it with its own lightweight player, so whatever Refrata is playing is left alone.

The hook only lives while Refrata runs; "Manter em segundo plano" keeps it in the tray.
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from .media import is_media_path

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
VK_SPACE = 0x20
# top-level windows that host a shell file view
EXPLORER_CLASSES = {"CabinetWClass", "ExploreWClass"}
DESKTOP_CLASSES = {"Progman", "WorkerW"}
# focused child classes that mean "the file list has focus" (not the address bar, search or rename box)
LIST_FOCUS_CLASSES = {"DirectUIHWND", "SysListView32"}
TYPING_GRACE_S = 0.8   # a Space right after letters is part of a typed name, not a preview


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


if sys.platform == "win32":
    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                      wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
else:   # the preview is a Windows Explorer feature; elsewhere the module only offers its helpers
    HOOKPROC = WINEVENTPROC = None
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _window_text(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def active_tab_title(focus_hwnd) -> str:
    """Windows 11 Explorer: every tab of a window shares the top-level HWND. The focused file list
    lives inside the active tab's ShellTabWindowClass, whose title is that tab's folder name."""
    u = ctypes.windll.user32
    u.GetParent.restype = wintypes.HWND
    h = focus_hwnd
    for _ in range(12):
        if not h:
            break
        if _class_name(h) == "ShellTabWindowClass":
            return _window_text(h)
        h = u.GetParent(h)
    return ""


def foreground_context():
    """(kind, top_hwnd, focus_class) for the foreground window; kind is explorer | desktop | other."""
    u = ctypes.windll.user32
    top = u.GetForegroundWindow()
    if not top:
        return "other", 0, ""
    cls = _class_name(top)
    kind = "explorer" if cls in EXPLORER_CLASSES else "desktop" if cls in DESKTOP_CLASSES else "other"
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    tid = u.GetWindowThreadProcessId(top, None)
    focus = ""
    if u.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
        focus = _class_name(info.hwndFocus)
        foreground_context.focus_hwnd = info.hwndFocus
    return kind, int(top), focus


def should_preview(kind: str, focus_class: str, since_typing: float) -> bool:
    """Pure decision used by the hook (and the tests)."""
    if kind not in ("explorer", "desktop"):
        return False
    if focus_class not in LIST_FOCUS_CLASSES:
        return False   # address bar, search box, rename edit, ribbon...
    return since_typing > TYPING_GRACE_S


def media_siblings(path: str) -> list[str]:
    """Media files next to `path`, name-sorted (for ← / → in the preview)."""
    folder = os.path.dirname(path)
    try:
        names = sorted(os.listdir(folder), key=lambda n: n.casefold())
    except OSError:
        return [path]
    out = [os.path.join(folder, n) for n in names if is_media_path(os.path.join(folder, n))]
    return out or [path]


_SHELL = None


def _shell_windows():
    """Explorer's ShellWindows, through one Shell.Application kept for the app's life: a fresh one per
    Space cost ~100 ms before anything could be read (23 ms with it reused). Made again if Explorer
    restarted and the old one stopped answering."""
    global _SHELL
    from PySide6.QtAxContainer import QAxObject   # needs a QApplication; imported lazily
    for _ in range(2):
        if _SHELL is None or _SHELL.isNull():
            _SHELL = QAxObject("Shell.Application")
        windows = _SHELL.querySubObject("Windows()") if not _SHELL.isNull() else None
        if windows is not None and not windows.isNull():
            return windows
        _SHELL = None
    return None


def warm_up():
    """Make the first Space as fast as the next ones (called once the app has settled)."""
    try:
        w = _shell_windows()
        if w is not None:
            w.property("Count")
    except Exception:
        pass


def selected_items(top_hwnd: int, kind: str, tab_title: str = "") -> list[str]:
    """Paths selected in the Explorer window `top_hwnd` (its active tab), or on the desktop."""
    windows = _shell_windows()
    if windows is None:
        return []
    doc = None
    if kind == "desktop":
        # ShellWindows.FindWindowSW(loc, root, SWC_DESKTOP=8, hwnd, SWFO_NEEDDISPATCH=1)
        desk = windows.querySubObject("FindWindowSW(QVariant&,QVariant&,int,int&,int)", 0, 0, 8, 0, 1)
        if desk is not None and not desk.isNull():
            doc = desk.querySubObject("Document")
    else:
        tabs = []
        for i in range(int(windows.property("Count") or 0)):
            w = windows.querySubObject("Item(QVariant)", i)
            if w is None or w.isNull():
                continue
            try:
                if int(w.property("HWND") or 0) == top_hwnd:
                    tabs.append(w)
            except (TypeError, ValueError):
                continue
        # several tabs share the window: take the one whose folder name is the active tab's
        pick = next((w for w in tabs if tab_title and str(w.property("LocationName") or "") == tab_title), None)
        if pick is None and len(tabs) == 1:
            pick = tabs[0]
        if pick is not None:
            doc = pick.querySubObject("Document")
    if doc is None or doc.isNull():
        return []
    items = doc.querySubObject("SelectedItems()")
    if items is None or items.isNull():
        return []
    out = []
    for i in range(min(int(items.property("Count") or 0), 50)):
        it = items.querySubObject("Item(QVariant)", i)
        if it is not None and not it.isNull():
            p = it.property("Path")
            if p:
                out.append(str(p))
    return out


class QuickLook(QObject):
    """System-wide Space → preview of the media file selected in Explorer."""
    previewRequested = Signal(str, list)
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._hook = None       # keyboard hook: only while Explorer / the desktop is in front
        self._watch = None      # foreground-window event hook: while the feature is on
        # keep references: Windows calls into them
        self._proc = HOOKPROC(self._on_key) if HOOKPROC else None
        self._fgproc = WINEVENTPROC(self._on_foreground) if WINEVENTPROC else None
        self._last_typing = 0.0
        self._busy = False
        self.sourceWindow = 0
        self.ownWindowActive = lambda: False   # set by the app: our own windows handle keys themselves
        if self.enabled:
            self.start()

    @Property(bool, notify=changed)
    def enabled(self):
        return bool(self._settings.value("quickLook", True)) and sys.platform == "win32"

    @enabled.setter
    def enabled(self, on):
        self._settings.setValue("quickLook", bool(on))
        self.start() if on else self.stop()
        self.changed.emit()

    @Property(bool, notify=changed)
    def active(self):
        """Watching for Explorer (the keyboard hook itself comes and goes with the foreground)."""
        return self._watch is not None

    def start(self):
        if self._watch or sys.platform != "win32" or os.environ.get("REFRATA_NO_HOOK"):
            return
        u = ctypes.windll.user32
        u.SetWinEventHook.restype = ctypes.c_void_p
        u.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WINEVENTPROC,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        self._watch = u.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, self._fgproc,
                                        0, 0, WINEVENT_OUTOFCONTEXT) or None
        self._sync_keys(u.GetForegroundWindow())
        self.changed.emit()

    def stop(self):
        if self._watch:
            ctypes.windll.user32.UnhookWinEvent(ctypes.c_void_p(self._watch))
            self._watch = None
        self._unhook_keys()
        self.changed.emit()

    def _on_foreground(self, hook, event, hwnd, obj, child, thread, when):
        try:
            self._sync_keys(hwnd)
        except Exception:
            pass

    def _sync_keys(self, hwnd):
        """Keyboard hook on while an Explorer window or the desktop is in front, off otherwise."""
        cls = _class_name(hwnd) if hwnd else ""
        if cls in EXPLORER_CLASSES or cls in DESKTOP_CLASSES:
            self._hook_keys()
        else:
            self._unhook_keys()

    def _hook_keys(self):
        if self._hook:
            return
        k32 = ctypes.windll.kernel32
        k32.GetModuleHandleW.restype = wintypes.HMODULE
        u = ctypes.windll.user32
        u.SetWindowsHookExW.restype = ctypes.c_void_p
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        self._hook = u.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, k32.GetModuleHandleW(None), 0) or None

    def _unhook_keys(self):
        if self._hook:
            ctypes.windll.user32.UnhookWindowsHookEx(ctypes.c_void_p(self._hook))
            self._hook = None

    # ---------------------------------------------------------------- hook (keep it tiny)
    def _on_key(self, code, wparam, lparam):
        try:
            if code == 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if k.vkCode == VK_SPACE:
                    if not self._busy and not self.ownWindowActive():
                        since = time.monotonic() - self._last_typing
                        kind, top, focus = foreground_context()
                        if should_preview(kind, focus, since):
                            self._busy = True
                            tab = active_tab_title(getattr(foreground_context, "focus_hwnd", 0)) if kind == "explorer" else ""
                            QTimer.singleShot(0, lambda: self._resolve(kind, top, tab))
                elif 0x30 <= k.vkCode <= 0x5A or 0xBA <= k.vkCode <= 0xE2:
                    self._last_typing = time.monotonic()   # letters, digits, punctuation
        except Exception:
            pass
        return ctypes.windll.user32.CallNextHookEx(None, code, wparam, ctypes.c_void_p(lparam))

    def _resolve(self, kind, top, tab=""):
        try:
            sel = selected_items(top, kind, tab)
        except Exception:
            sel = []
        finally:
            self._busy = False
        if len(sel) == 1 and is_media_path(sel[0]) and os.path.isfile(sel[0]):
            self.sourceWindow = int(top)
            self.previewRequested.emit(os.path.normpath(sel[0]), media_siblings(sel[0]))

    # ---------------------------------------------------------------- QML helpers
    @Slot(str, result=list)
    def siblings(self, path):
        return media_siblings(path)

    @Slot(str, result=str)
    def fileUrl(self, path):
        """Proper file URL (names with #, %, spaces) for QtMultimedia."""
        from PySide6.QtCore import QUrl
        return QUrl.fromLocalFile(path).toString()

    @Slot()
    def restoreForeground(self):
        """Give the focus back to the Explorer window the preview came from."""
        if self.sourceWindow and sys.platform == "win32":
            ctypes.windll.user32.SetForegroundWindow(self.sourceWindow)

    def shutdown(self):
        self.stop()
