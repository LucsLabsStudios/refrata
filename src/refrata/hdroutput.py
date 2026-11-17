"""HDR output: showing HDR video at its real brightness on an HDR screen (Windows "Use HDR" on).

How the window gets there:
  1. Before the main window is created, `wanted()` decides whether to ask for an HDR swapchain:
     the setting allows it ("hdrOutput" = auto) and some screen has Windows HDR on right now.
  2. Main.qml asks Qt Quick for a scRGB swapchain (16-bit float, linear light, BT.709 primaries,
     1.0 = 80 nits) through the window's `_qt_sg_hdr_format` property. scRGB rather than HDR10:
     the desktop compositor takes it in a window as is, colours outside BT.709 are negative values,
     and the brightness is absolute, so nothing depends on how a TV reads HDR10 metadata.
  3. Once a frame is out, `active` says whether Qt really made that swapchain (it falls back to SDR
     when the screen cannot).
  4. While active, the whole window renders into one 16-bit layer that Main.qml converts to light:
     the interface at the user's SDR brightness (the "SDR content brightness" slider), so it looks
     exactly as in any other app; the video's HDR frames at their own brightness, tone mapped only
     where they exceed the screen's peak (present.frag).

The screen's state is read again every few seconds and when the window changes screen (turning
HDR off in Windows, moving the window to an SDR monitor): on an SDR screen the same swapchain shows
the picture tone mapped to SDR, so the window never needs to be rebuilt.
"""
import sys

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from . import hdrdisplay

POLL_MS = 3000


def wanted(settings) -> bool:
    """Ask for the HDR swapchain at all? Only on Windows, when allowed and some screen is in HDR."""
    if sys.platform != "win32" or settings.value("hdrOutput", "auto") == "off":
        return False
    try:
        return any(s.get("hdr") for s in hdrdisplay.screens().values())
    except Exception:
        return False


class HdrOutput(QObject):
    changed = Signal()

    def __init__(self, requested: bool, parent=None):
        super().__init__(parent)
        self._requested = bool(requested)
        self._active = False
        self._screen = hdrdisplay._empty()
        self._win = None
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)

    def attach(self, win):
        """Follow a QQuickWindow: its swapchain once the first frame is out, its screen."""
        self._win = win
        # what the screen can do is shown in Settings either way; it is followed only when used
        win.screenChanged.connect(lambda *_: self.refresh())
        self.refresh()
        if not self._requested:
            return
        win.frameSwapped.connect(self._check_swapchain)
        self._timer.start()

    def _check_swapchain(self):
        win = self._win
        if win is None:
            return
        try:
            win.frameSwapped.disconnect(self._check_swapchain)
        except (RuntimeError, TypeError):
            pass
        active = False
        try:
            from PySide6.QtGui import QRhiSwapChain
            sc = win.swapChain()
            active = sc is not None and sc.format() == QRhiSwapChain.Format.HDRExtendedSrgbLinear
        except Exception:
            active = False
        if active != self._active:
            self._active = active
            self.changed.emit()

    @Slot()
    def refresh(self):
        win = self._win
        try:
            # by the window's own monitor (Qt's screen name is the model name, not \\.\DISPLAYn)
            info = hdrdisplay.screen_of_window(int(win.winId())) if win is not None else hdrdisplay._empty()
        except Exception:
            info = hdrdisplay._empty()
        if info != self._screen:
            self._screen = info
            self.changed.emit()

    # ------------------------------------------------------------ for QML
    @Property(bool, constant=True)
    def requested(self):
        """Main.qml asked Qt for the scRGB swapchain."""
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
