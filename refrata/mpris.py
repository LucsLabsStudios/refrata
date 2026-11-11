"""Linux media controls: MPRIS2 over D-Bus.

GNOME's and KDE's media widgets, the keyboard's play/pause/next keys, the lock screen and tools
like playerctl talk to players through MPRIS2 (org.mpris.MediaPlayer2.*). This publishes Refrata
there: what is playing (title, artist, album, cover, length, position) and the commands
(play, pause, next, previous, seek, volume). Windows has refrata/mediakeys.py for the same job.

Without a session bus (a bare server, some containers) it does nothing.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import ClassInfo, Property, QObject, QTimer, QUrl, Signal, Slot

SERVICE = "org.mpris.MediaPlayer2.refrata"
PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"

try:
    from PySide6.QtDBus import (QDBusAbstractAdaptor, QDBusConnection, QDBusMessage, QDBusObjectPath,
                                QDBusVariant)
    AVAILABLE = sys.platform.startswith("linux")
except ImportError:   # Qt built without D-Bus
    AVAILABLE = False


if AVAILABLE:
    @ClassInfo({"D-Bus Interface": ROOT_IFACE})
    class _Root(QDBusAbstractAdaptor):
        def __init__(self, owner):
            super().__init__(owner)
            self._o = owner

        @Property(str)
        def Identity(self):
            return "Refrata"

        @Property(str)
        def DesktopEntry(self):
            return "refrata"

        @Property(bool)
        def CanQuit(self):
            return True

        @Property(bool)
        def CanRaise(self):
            return True

        @Property(bool)
        def HasTrackList(self):
            return False

        @Property("QStringList")
        def SupportedUriSchemes(self):
            return ["file", "http", "https", "rtsp", "rtmp", "mms", "smb", "ftp"]

        @Property("QStringList")
        def SupportedMimeTypes(self):
            from .assoc import mime_types
            return mime_types()

        @Slot()
        def Raise(self):
            self._o.raiseRequested.emit()

        @Slot()
        def Quit(self):
            from PySide6.QtWidgets import QApplication
            QApplication.instance().quit()

    @ClassInfo({"D-Bus Interface": PLAYER_IFACE})
    class _Player(QDBusAbstractAdaptor):
        Seeked = Signal("qlonglong")

        def __init__(self, owner):
            super().__init__(owner)
            self._o = owner

        # -------------------------------------------------------------- properties
        @Property(str)
        def PlaybackStatus(self):
            return self._o.status()

        @Property(str)
        def LoopStatus(self):
            return {1: "Playlist", 2: "Track"}.get(int(self._o.engine.repeat), "None")

        @Property(float)
        def Rate(self):
            return float(self._o.engine.rate)

        @Property(bool)
        def Shuffle(self):
            return bool(self._o.engine.shuffle)

        @Property("QVariantMap")
        def Metadata(self):
            return self._o.metadata()

        @Property(float)
        def Volume(self):
            return 0.0 if self._o.engine.muted else self._o.engine.volume / 100.0

        @Volume.setter
        def Volume(self, v):
            self._o.engine.volume = int(round(max(0.0, min(1.5, float(v))) * 100))

        @Property("qlonglong")
        def Position(self):
            return max(0, int(self._o.engine.time)) * 1000

        @Property(float)
        def MinimumRate(self):
            return 0.25

        @Property(float)
        def MaximumRate(self):
            return 4.0

        @Property(bool)
        def CanGoNext(self):
            return self._o.engine.hasMedia

        @Property(bool)
        def CanGoPrevious(self):
            return self._o.engine.hasMedia

        @Property(bool)
        def CanPlay(self):
            return self._o.engine.hasMedia

        @Property(bool)
        def CanPause(self):
            return self._o.engine.hasMedia

        @Property(bool)
        def CanSeek(self):
            return self._o.engine.length > 0

        @Property(bool)
        def CanControl(self):
            return True

        # -------------------------------------------------------------- methods
        @Slot()
        def Play(self):
            self._o.engine.play()

        @Slot()
        def Pause(self):
            self._o.engine.pause()

        @Slot()
        def PlayPause(self):
            self._o.engine.togglePlay()

        @Slot()
        def Stop(self):
            self._o.engine.pause()

        @Slot()
        def Next(self):
            self._o.engine.next()

        @Slot()
        def Previous(self):
            self._o.engine.previous()

        @Slot("qlonglong")
        def Seek(self, offset_us):
            e = self._o.engine
            e.seek(max(0, e.time + int(offset_us) // 1000))
            self._o.seeked()

        @Slot(QDBusObjectPath, "qlonglong")
        def SetPosition(self, track, position_us):
            if track.path() == self._o.track_id():
                self._o.engine.seek(int(position_us) // 1000)
                self._o.seeked()

        @Slot(str)
        def OpenUri(self, uri):
            self._o.openRequested.emit([uri])


class Mpris(QObject):
    """Owner of the two D-Bus adaptors; mirrors the engine and sends PropertiesChanged."""
    raiseRequested = Signal()
    openRequested = Signal(list)

    def __init__(self, engine, library=None, parent=None):
        super().__init__(parent)
        self.engine, self._library = engine, library
        self.available = False
        self._last: dict = {}
        self._track = 0
        if not AVAILABLE or os.environ.get("REFRATA_NO_MEDIAKEYS") == "1":
            return
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return
        self._root, self._player = _Root(self), _Player(self)
        if not bus.registerObject(PATH, self):
            print("mpris: object not registered")
            return
        self.service = SERVICE
        if not bus.registerService(SERVICE):
            # a second copy (or a stale name): keep ours unique per process
            self.service = f"{SERVICE}.instance{os.getpid()}"
            if not bus.registerService(self.service):
                print("mpris: service name not registered")
                return
        self._bus = bus
        self.available = True
        engine.stateChanged.connect(self._changed)
        engine.mediaChanged.connect(self._new_track)
        engine.volumeChanged.connect(self._changed)
        engine.currentIndexChanged.connect(self._changed)

    # ------------------------------------------------------------------ mirror
    def status(self) -> str:
        st = self.engine.state
        return "Playing" if st in ("playing", "loading") and self.engine.hasMedia else \
            "Paused" if st == "paused" else "Stopped"

    def track_id(self) -> str:
        return f"/org/mpris/MediaPlayer2/Track/{self._track}" if self.engine.hasMedia else NO_TRACK

    def metadata(self) -> dict:
        e = self.engine
        if not e.hasMedia:
            return {"mpris:trackid": QDBusObjectPath(NO_TRACK)}
        md = {"mpris:trackid": QDBusObjectPath(self.track_id()), "xesam:title": e.title or ""}
        if e.length > 0:
            md["mpris:length"] = QDBusVariant(int(e.length) * 1000).variant()
        if e.artist:
            md["xesam:artist"] = [e.artist]
        if e.album:
            md["xesam:album"] = e.album
        art = e.artwork or (self._library.thumbFor(e.currentPath) if self._library else "")
        if art:
            md["mpris:artUrl"] = art if "://" in art else QUrl.fromLocalFile(art).toString()
        path = e.currentPath or ""
        if path:
            md["xesam:url"] = path if "://" in path else QUrl.fromLocalFile(path).toString()
        return md

    def _new_track(self):
        self._track += 1
        self._changed()

    def _changed(self):
        now = {"PlaybackStatus": self.status(), "Metadata": self.metadata(),
               "Volume": 0.0 if self.engine.muted else self.engine.volume / 100.0,
               "CanGoNext": self.engine.hasMedia, "CanGoPrevious": self.engine.hasMedia,
               "CanPlay": self.engine.hasMedia, "CanPause": self.engine.hasMedia,
               "CanSeek": self.engine.length > 0}
        diff = {k: v for k, v in now.items() if _plain(self._last.get(k)) != _plain(v)}
        self._last = now
        if diff:
            QTimer.singleShot(0, lambda d=diff: self._emit_changed(d))

    def _emit_changed(self, changed: dict):
        msg = QDBusMessage.createSignal(PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged")
        msg.setArguments([PLAYER_IFACE, changed, []])
        self._bus.send(msg)

    def seeked(self):
        if self.available:
            self._player.Seeked.emit(max(0, int(self.engine.time)) * 1000)

    def shutdown(self):
        if self.available:
            self._bus.unregisterObject(PATH)
            self._bus.unregisterService(self.service)
            self.available = False


def _plain(v):
    """Comparable form of a property value (QDBusObjectPath has no ==)."""
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if AVAILABLE and isinstance(v, QDBusObjectPath):
        return v.path()
    return v
