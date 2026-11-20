"""HDR output: showing HDR video at its real brightness on an HDR screen (Windows "Use HDR" on).

How the window gets there:
  1. `wanted()` decides at start whether to ask for an HDR swapchain: the setting allows it
     ("hdrOutput" = auto) and the screen has Windows HDR on right now.
  2. Main.qml asks Qt Quick for a scRGB swapchain (16-bit float, linear light, BT.709 primaries,
     1.0 = 80 nits) through the window's `_qt_sg_hdr_format` property. scRGB rather than HDR10:
     the desktop compositor takes it in a window as is, colours outside BT.709 are negative values,
     and the brightness is absolute, so nothing depends on how a TV reads HDR10 metadata.
  3. Once a frame is out, `active` says whether Qt really made that swapchain.
  4. While active, the whole window renders into one 16-bit layer that Main.qml converts to light:
     the interface at the user's SDR brightness (the "SDR content brightness" slider), so it looks
     exactly as in any other app; the video's HDR frames at their own brightness, tone mapped only
     where they exceed the screen's peak (present.frag).

Following the screen while the app runs (Windows HDR switched on or off, the window moved to
another monitor, the setting changed):
  - Qt reads the format only when it creates the swapchain, and Qt's D3D11 backend accepts scRGB
    only if the window's output is in HDR at that moment (QD3D11SwapChain::isFormatSupported).
    So the swapchain is rebuilt: the native window is destroyed and shown again in the same state
    (QWindow.destroy + show). Qt keeps its QRhi (the GPU device) across that, so video textures,
    the shader pipelines and RTX Video stay; only the swapchain and the HWND are new.
  - HDR switched OFF in Windows: that rebuild gives an SDR swapchain (measured with real switches).
  - HDR switched ON after Refrata started: Qt 6.8 asks whether the screen is in HDR through the
    DXGI adapter of its device, and an adapter from before the switch keeps reporting the old
    state, so a rebuilt swapchain still comes out SDR. A new device would see it, but Qt drops a
    window's device only through hide() with persistence off, and repeating that while video plays
    hung the interface (the GUI waits without a timeout for the render thread). So one rebuild is
    tried (it does work when the device already knew the screen in HDR: the setting switched on,
    a window moved to a monitor already in HDR); if it stays SDR, `needsReopen` asks for a new
    process, which Main.qml offers.
  - A thread of its own watches the displays: a hidden top-level window gets WM_DISPLAYCHANGE,
    which Windows sends to every window when HDR is switched (and on any mode change), and reads
    the screen a few times while it settles; between notices it reads every POLL_MS as a safety
    net (the SDR brightness slider sends nothing). Reading takes ~3 ms of DXGI calls, kept off the
    interface thread that video frames wait for; the interface hears only of real changes.
"""
import sys
import threading

from PySide6.QtCore import Property, QObject, Signal, Slot

from . import hdrdisplay

POLL_MS = 2000
# after WM_DISPLAYCHANGE: DXGI reports the new colour space a moment after the notice
SETTLE_MS = (60, 400, 1200, 3000)
# a swapchain in HDR: tried this long after the screen says HDR (made sooner it can come out SDR)
HDR_DELAY_MS = (400,)
HDR_TRIES = len(HDR_DELAY_MS)


def wanted(settings) -> bool:
    """Ask for the HDR swapchain at start? Only on Windows, when allowed and some screen is in HDR."""
    if sys.platform != "win32" or settings.value("hdrOutput", "auto") == "off":
        return False
    try:
        return any(s.get("hdr") for s in hdrdisplay.screens().values())
    except Exception:
        return False


class _Watcher(threading.Thread):
    """Reads the window's screen off the interface thread; calls `report(info)` (from this thread)
    whenever what it reads differs from the last report."""

    def __init__(self, report):
        super().__init__(daemon=True, name="hdr-watch")
        self.hwnd = 0                # the window followed (its monitor), set by the interface
        self._report = report
        self._last = None
        self._tid = 0
        self._wake = threading.Event()
        self._settle = []            # pending re-reads after a display change (monotonic times)
        self.own = 0                 # its hidden window (tests post WM_DISPLAYCHANGE to it)
        self.notices = 0             # WM_DISPLAYCHANGE received
        self.fresh_reads = 0

    def poke(self, fresh=True):
        """Read again now (the window moved, was rebuilt, the setting changed)."""
        self._kick(fresh)

    def stop(self):
        if self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)     # WM_QUIT

    def _kick(self, fresh):
        import time
        now = time.monotonic()
        self._settle = sorted(set(self._settle) | {now + ms / 1000 for ms in SETTLE_MS}) if fresh else self._settle
        self._wake.set()
        if self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0400, 0, 0)     # WM_USER: wake

    def _read(self, fresh):
        self.fresh_reads += bool(fresh)
        hwnd = self.hwnd
        if not hwnd:                    # no window, or being rebuilt: nothing to say
            return
        try:
            dev = hdrdisplay.device_of_window(hwnd)
            if not dev:                 # a window just destroyed has no monitor: not "an SDR screen"
                return
            info = hdrdisplay.screen(dev, fresh)
        except Exception:
            return
        if hwnd != self.hwnd:           # rebuilt while reading
            return
        if info != self._last:
            self._last = info
            self._report(dict(info))

    def run(self):
        import ctypes
        import time
        from ctypes import wintypes as wt
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        self._tid = k.GetCurrentThreadId()
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.DefWindowProcW.restype = LRESULT

        def proc(hwnd, msg, wp, lp):
            if msg == 0x007E:                      # WM_DISPLAYCHANGE: HDR switched, mode changed
                self.notices += 1
                self._kick(True)
            return u.DefWindowProcW(hwnd, msg, wp, lp)
        self._proc = WNDPROC(proc)                 # kept alive with the thread

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                        ("lpszClassName", wt.LPCWSTR)]
        # every handle declared: an undeclared return type is a 32-bit int (a truncated HMODULE)
        k.GetModuleHandleW.restype = wt.HMODULE
        k.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        u.RegisterClassW.restype = wt.ATOM
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        u.MsgWaitForMultipleObjects.argtypes = [wt.DWORD, wt.LPVOID, wt.BOOL, wt.DWORD, wt.DWORD]
        u.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
        u.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        u.DispatchMessageW.restype = LRESULT
        hinst = k.GetModuleHandleW(None)
        self._wc = wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName="RefrataHdrWatch")
        u.RegisterClassW(ctypes.byref(wc))
        u.CreateWindowExW.restype = wt.HWND
        u.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        # a real top-level window (never shown): message-only windows get no broadcasts
        self.own = u.CreateWindowExW(0x00000080, "RefrataHdrWatch", "", 0x80000000, 0, 0, 0, 0,   # TOOLWINDOW, POPUP
                                     None, None, hinst, None) or 0
        msg = wt.MSG()
        next_poll = time.monotonic()
        while True:
            now = time.monotonic()
            due = [t for t in self._settle if t <= now]
            if due:
                self._settle = [t for t in self._settle if t > now]
                self._read(True)
                next_poll = now + POLL_MS / 1000
            elif now >= next_poll or self._wake.is_set():
                self._read(False)
                next_poll = now + POLL_MS / 1000
            self._wake.clear()
            wake_at = min([next_poll] + self._settle)
            timeout = max(0, int((wake_at - time.monotonic()) * 1000))
            u.MsgWaitForMultipleObjects(0, None, False, timeout, 0x04FF)              # QS_ALLINPUT
            while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):                 # PM_REMOVE
                if msg.message == 0x0012:                                             # WM_QUIT
                    return
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))


class HdrOutput(QObject):
    changed = Signal()
    rebuilt = Signal()             # the window has a new swapchain (tests, QML: restyle)
    _seen = Signal(object)         # from the watcher thread, delivered on the interface thread

    def __init__(self, requested: bool, parent=None, settings=None):
        super().__init__(parent)
        self._requested = bool(requested)
        self._settings = settings
        self._active = False
        self._known = False        # the swapchain's format has been read (after its first frame)
        self._screen = hdrdisplay._empty()
        self._win = None
        self._tried = None         # (screen info, wanted) of the last rebuild: no retry loop
        self._rebuilds = 0
        self._big = False
        self._reopen = False       # HDR wanted, and only a new process can have it (see notes)
        self._t0 = 0.0             # when the last rebuild started; its first new frame ends the gap
        self._gap_ms = 0.0
        self._watch = None
        self._dxgi = None          # refrata_native with the DXGI answer corrected (dxgihook.cpp)
        self._seen.connect(self._on_screen)
        from PySide6.QtCore import QTimer
        self._later = QTimer(self)
        self._later.setSingleShot(True)
        self._later.timeout.connect(self._later_fired)
        if settings is not None:
            settings.changed.connect(self._setting_changed)

    def attach(self, win):
        """Follow a QQuickWindow: its swapchain once a frame is out, its screen."""
        self._win = win
        win.frameSwapped.connect(self._check_swapchain)
        win.screenChanged.connect(lambda *_: self._poke())
        if sys.platform == "win32":
            # Qt's "is this screen in HDR?" answered with the screen as it is now (dxgihook.cpp):
            # without it, HDR switched on after start needed a reopen
            try:
                import refrata_native
                self._dxgi = refrata_native if refrata_native.dxgi_hook_install() else None
            except Exception:
                self._dxgi = None
            self._watch = _Watcher(self._seen.emit)
            self._watch.hwnd = int(win.winId())
            self._watch.start()
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.instance().aboutToQuit.connect(self._watch.stop)
        self.refresh()

    def _poke(self):
        if self._watch is not None:
            self._watch.poke()

    def _allowed(self):
        if sys.platform != "win32":
            return False
        s = self._settings
        return s is None or s.value("hdrOutput", "auto") != "off"

    def _setting_changed(self, key):
        if key == "hdrOutput":
            self._tried = None
            self._apply()

    def _check_swapchain(self):
        """After each new swapchain: did Qt really make it scRGB? (read once per swapchain)"""
        win = self._win
        if win is None:
            return
        try:
            win.frameSwapped.disconnect(self._check_swapchain)
        except (RuntimeError, TypeError):
            pass
        try:
            from PySide6.QtGui import QRhiSwapChain
            sc = win.swapChain()
            active = sc is not None and sc.format() == QRhiSwapChain.Format.HDRExtendedSrgbLinear
        except Exception:
            active = False
        self._known = True
        if self._t0:
            import time
            self._gap_ms = (time.perf_counter() - self._t0) * 1000
            self._t0 = 0.0
        if active != self._active:
            self._active = active
            self.changed.emit()
        self._apply()

    @Slot(object)
    def _on_screen(self, info):
        if self._dxgi is not None and info.get("name"):
            # before any rebuild below: the new swapchain is made with this answer
            self._dxgi.dxgi_override(info["name"], 1 if info.get("hdr") else 0)
        if info != self._screen:
            self._screen = info
            self._tried = None          # a new situation: a rebuild may be worth trying again
            self.changed.emit()
        self._apply()

    @Slot()
    def refresh(self):
        """Read the screen now, on this thread (tests; the watcher does it otherwise)."""
        win = self._win
        try:
            info = hdrdisplay.screen_of_window(int(win.winId()), True) if win is not None else hdrdisplay._empty()
        except Exception:
            info = hdrdisplay._empty()
        self._on_screen(info)

    def _apply(self):
        """Ask for the swapchain this screen and the setting call for; rebuild when it differs."""
        want = self._allowed() and bool(self._screen.get("hdr"))
        if want != self._requested:
            self._requested = want
            self.changed.emit()          # Main.qml's _qt_sg_hdr_format follows (a binding)
        win = self._win
        if win is None or not self._known:
            return
        key = (tuple(sorted(self._screen.items())), want)
        tried = self._tried[1] if self._tried and self._tried[0] == key else 0
        # HDR wanted, the swapchain tried in this process came out SDR: Qt's device was made before
        # Windows switched HDR on and keeps seeing the screen as it was (module notes); only a
        # new process sees it in HDR. Main.qml offers to reopen in place.
        reopen = want and not self._active and tried >= HDR_TRIES
        if reopen != self._reopen:
            self._reopen = reopen
            self.changed.emit()
        if want == self._active:
            self._later.stop()
            return
        if tried >= (HDR_TRIES if want else 1):
            return                      # Qt refused it for this screen: no loop
        if self._later.isActive():
            return
        if not want:
            self._tried = (key, 1)
            self.rebuild()              # to SDR: any moment will do
            return
        # to HDR: Windows says HDR a moment before a swapchain can be made in it (made too soon,
        # in full screen, it came out SDR): a little later, and again if Qt still says no
        self._later.start(HDR_DELAY_MS[tried])

    def _later_fired(self):
        want = self._allowed() and bool(self._screen.get("hdr"))
        if self._win is None or not self._known or want == self._active:
            return
        key = (tuple(sorted(self._screen.items())), want)
        tried = self._tried[1] if self._tried and self._tried[0] == key else 0
        self._tried = (key, tried + 1)
        self.rebuild()

    @Slot()
    def rebuild(self):
        """New native window, new swapchain, same state on screen (see the module notes)."""
        from PySide6.QtGui import QWindow
        win = self._win
        if win is None:
            return
        win.setProperty("_qt_sg_hdr_format", "scrgb" if self._requested else "")
        vis = win.visibility()
        geo = win.geometry()           # a new window goes where Windows places new windows
        shown = win.isVisible()
        focused = win.isActive()
        win.frameSwapped.connect(self._check_swapchain)
        self._active = False
        self._known = False
        self.changed.emit()
        # (a frame has been out, _known: the native window exists; hidden in the tray it stays
        # hidden and gets the new swapchain when shown)
        big = shown and vis in (QWindow.Visibility.FullScreen, QWindow.Visibility.Maximized)
        if big and win.property("rebuiltFromBig") is not None:
            # before the new window exists: on its way to full screen it is briefly "windowed" at
            # the big size, which Main.qml must not take for its windowed size
            win.setProperty("rebuiltFromBig", True)
        import time
        self._t0 = time.perf_counter()
        if self._watch is not None:
            self._watch.hwnd = 0        # the old handle dies now: no reading through it
        win.destroy()
        if shown:
            V = QWindow.Visibility
            if vis == V.FullScreen:
                win.showFullScreen()
            elif vis == V.Maximized:
                win.showMaximized()
            elif vis == V.Minimized:
                win.showMinimized()
            else:
                win.setGeometry(geo)
                win.show()
                win.setGeometry(geo)
            if focused:
                win.requestActivate()
        if self._watch is not None:
            self._watch.hwnd = int(win.winId())
        self._rebuilds += 1
        self._big = big
        self.rebuilt.emit()

    # ------------------------------------------------------------ for QML
    @Property(bool, notify=changed)
    def requested(self):
        """The window asks Qt for the scRGB swapchain (the screen is in HDR and it is allowed)."""
        return self._requested

    @Property(bool, notify=changed)
    def active(self):
        """The window really renders to scRGB: the interface layer converts to light."""
        return self._active

    @Property(bool, notify=changed)
    def screenHdr(self):
        """...and the screen it is on is in HDR now: video may go above SDR white."""
        return self._active and bool(self._screen.get("hdr"))

    @Property(float, notify=changed)
    def sdrWhite(self):
        """Nits of the desktop's SDR white on this screen (80 on an SDR screen)."""
        return float(self._screen.get("sdr_white") or 80.0) if self._screen.get("hdr") else 80.0

    @Property(float, notify=changed)
    def peak(self):
        """The screen's peak luminance, nits (0 = SDR / unknown)."""
        return float(self._screen.get("max_nits") or 0.0) if self._screen.get("hdr") else 0.0

    @Property("QVariantMap", notify=changed)
    def screenInfo(self):
        return dict(self._screen)

    @Property(bool, notify=rebuilt)
    def rebuiltBig(self):
        """The last rebuild was of a maximised or full-screen window (Main.qml gives it back its
        windowed size when it leaves that state: the new HWND never had one)."""
        return self._big

    @Property(float, notify=changed)
    def rebuildGapMs(self):
        """From the old window's end to the new one's first frame, the last rebuild (tests)."""
        return self._gap_ms

    def reopen_with(self, fn):
        """What reopen() runs (refrata/reopen.py relaunch, set by the app)."""
        self._reopen_fn = fn

    @Slot(result=bool)
    def reopen(self):
        """The "Reabrir em HDR" button: False if the new process could not be started."""
        fn = getattr(self, "_reopen_fn", None)
        return bool(fn()) if fn else False

    @Property(bool, notify=changed)
    def needsReopen(self):
        """Windows HDR came on after Refrata started: HDR video can show in HDR only after a reopen."""
        return self._reopen

    @Property(int, notify=rebuilt)
    def rebuilds(self):
        """How many times the swapchain was rebuilt for a change of screen (tests)."""
        return self._rebuilds
