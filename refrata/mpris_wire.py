"""MPRIS2 over D-Bus with every value's type spelled out (Linux), through jeepney.

refrata/mpris.py publishes the same service through QtDBus. PySide 6.8 cannot give a value its D-Bus
type: a Python int below 2**31 always goes out as int32 ("i"), and MPRIS says the track's length
(mpris:length) is int64 ("x"). Clients that check the type then lose it: playerctl answered
`{{duration(mpris:length)}}` with "function duration can only be called on track position values".

jeepney writes the D-Bus wire format itself (pure Python), so here each value carries its signature.
A thread reads the bus; each call is answered on the Qt main thread (where the engine lives) through
a queued signal. Used when jeepney is installed (the Linux package bundles it); otherwise main.py
falls back to refrata/mpris.py.
"""
from __future__ import annotations

import os
import sys
import threading

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

SERVICE = "org.mpris.MediaPlayer2.refrata"
PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
URI_SCHEMES = ["file", "http", "https", "rtsp", "rtmp", "mms", "smb", "ftp"]


def available() -> bool:
    if not sys.platform.startswith("linux") or os.environ.get("REFRATA_NO_MEDIAKEYS") == "1":
        return False
    try:
        import jeepney  # noqa: F401
        import jeepney.io.threading  # noqa: F401
    except Exception:
        return False
    return bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))


_INTROSPECT = """<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.mpris.MediaPlayer2">
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
    <method name="Raise"/>
    <method name="Quit"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="read"/>
    <property name="Rate" type="d" access="read"/>
    <property name="Shuffle" type="b" access="read"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
    <signal name="Seeked"><arg type="x"/></signal>
    <method name="Play"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Seek"><arg type="x" direction="in"/></method>
    <method name="SetPosition"><arg type="o" direction="in"/><arg type="x" direction="in"/></method>
    <method name="OpenUri"><arg type="s" direction="in"/></method>
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
    <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
    <method name="Set"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/></method>
    <signal name="PropertiesChanged"><arg type="s"/><arg type="a{sv}"/><arg type="as"/></signal>
  </interface>
  <interface name="org.freedesktop.DBus.Introspectable">
    <method name="Introspect"><arg type="s" direction="out"/></method>
  </interface>
  <interface name="org.freedesktop.DBus.Peer">
    <method name="Ping"/>
    <method name="GetMachineId"><arg type="s" direction="out"/></method>
  </interface>
</node>
"""


class WireMpris(QObject):
    """Same face as refrata.mpris.Mpris (available, service, raiseRequested, openRequested,
    status(), metadata(), seeked(), shutdown())."""
    raiseRequested = Signal()
    openRequested = Signal(list)
    _call = Signal(object)        # a method call, from the reading thread to the main thread

    def __init__(self, engine, library=None, parent=None):
        super().__init__(parent)
        self.engine, self._library = engine, library
        self.available = False
        self.service = SERVICE
        self._last: dict = {}
        self._track = 0
        self._conn = None
        self._stop = False
        if not available():
            return
        from jeepney.bus_messages import message_bus
        from jeepney.io.threading import open_dbus_connection
        try:
            conn = open_dbus_connection(bus="SESSION")
        except Exception as exc:
            print("mpris: no session bus:", exc)
            return
        # 4 = DBUS_NAME_FLAG_DO_NOT_QUEUE; 1 = primary owner. A second copy takes a name of its own.
        for name in (SERVICE, f"{SERVICE}.instance{os.getpid()}"):
            if self._request(conn, message_bus.RequestName(name, 4)) == 1:
                self.service = name
                break
        else:
            print("mpris: service name not registered")
            conn.close()
            return
        self._conn = conn
        self.available = True
        self._call.connect(self._answer)
        self._reader = threading.Thread(target=self._read, daemon=True, name="mpris-bus")
        self._reader.start()
        engine.stateChanged.connect(self._changed)
        engine.mediaChanged.connect(self._new_track)
        engine.volumeChanged.connect(self._changed)
        engine.currentIndexChanged.connect(self._changed)

    @staticmethod
    def _request(conn, msg):
        """Send and wait for the reply before the reading thread starts (nothing else to read yet)."""
        from jeepney import HeaderFields, MessageType
        # (the threading connection's send() returns nothing: the serial is given here, from a range
        # its own counter, which starts at 1, does not reach in a session)
        WireMpris._serial = getattr(WireMpris, "_serial", 0x70000000) + 1
        serial = WireMpris._serial
        conn.send(msg, serial=serial)
        for _ in range(50):
            reply = conn.receive(timeout=2)
            if reply.header.fields.get(HeaderFields.reply_serial) == serial:
                if reply.header.message_type == MessageType.error:
                    return 0
                return reply.body[0]
        return 0

    # ------------------------------------------------------------------ bus side
    def _read(self):
        from jeepney import MessageType
        while not self._stop:
            try:
                msg = self._conn.receive()
            except Exception:
                return          # the connection closed (shutdown) or broke
            if msg.header.message_type == MessageType.method_call:
                self._call.emit(msg)

    def _send(self, msg):
        try:
            self._conn.send(msg)
        except Exception as exc:
            print("mpris send:", exc)

    def _answer(self, msg):
        """On the main thread: one method call, one reply (unless the caller asked for none)."""
        from jeepney import HeaderFields, new_error, new_method_return
        f = msg.header.fields
        iface, member, path = f.get(HeaderFields.interface, ""), f.get(HeaderFields.member, ""), f.get(HeaderFields.path, "")
        no_reply = bool(msg.header.flags & 1)

        def ok(signature=None, body=()):
            if not no_reply:
                self._send(new_method_return(msg, signature, body))

        def fail(name, text):
            if not no_reply:
                self._send(new_error(msg, name, "s", (text,)))
        if path != PATH:
            return fail("org.freedesktop.DBus.Error.UnknownObject", f"no object at {path}")
        try:
            if member == "Introspect":
                return ok("s", (_INTROSPECT,))
            if member == "Ping":
                return ok()
            if member == "GetMachineId":
                return ok("s", (_machine_id(),))
            if iface == PROPS_IFACE or member in ("Get", "GetAll", "Set") and not iface:
                return self._properties(msg, member, ok, fail)
            e = self.engine
            if member == "Raise":
                self.raiseRequested.emit()
            elif member == "Quit":
                from PySide6.QtWidgets import QApplication
                QTimer.singleShot(0, QApplication.instance().quit)
            elif member == "Play":
                e.play()
            elif member == "Pause":
                e.pause()
            elif member == "PlayPause":
                e.togglePlay()
            elif member == "Stop":
                e.pause()
            elif member == "Next":
                e.next()
            elif member == "Previous":
                e.previous()
            elif member == "Seek":
                e.seek(max(0, e.time + int(msg.body[0]) // 1000))
                ok()
                return self.seeked()
            elif member == "SetPosition":
                if msg.body[0] == self.track_id():
                    e.seek(int(msg.body[1]) // 1000)
                    ok()
                    return self.seeked()
            elif member == "OpenUri":
                self.openRequested.emit([str(msg.body[0])])
            else:
                return fail("org.freedesktop.DBus.Error.UnknownMethod", f"no method {member} on {iface or '(any)'}")
            ok()
        except Exception as exc:   # a broken request must not take the bus thread's partner down
            fail("org.freedesktop.DBus.Error.Failed", str(exc))

    def _properties(self, msg, member, ok, fail):
        if member == "GetAll":
            props = self._props(msg.body[0])
            return ok("a{sv}", (props,))
        if member == "Get":
            iface, name = msg.body
            props = self._props(iface)
            if name not in props:
                return fail("org.freedesktop.DBus.Error.InvalidArgs", f"no property {name} on {iface}")
            return ok("v", (props[name],))
        if member == "Set":
            iface, name, (sig, value) = msg.body
            if iface == PLAYER_IFACE and name == "Volume" and sig == "d":
                self.engine.volume = int(round(max(0.0, min(1.5, float(value))) * 100))
                return ok()
            return fail("org.freedesktop.DBus.Error.PropertyReadOnly", f"{name} cannot be set")
        return fail("org.freedesktop.DBus.Error.UnknownMethod", member)

    # ------------------------------------------------------------------ the values
    def _props(self, iface) -> dict:
        e = self.engine
        if iface == ROOT_IFACE:
            from .assoc import mime_types
            return {"Identity": ("s", "Refrata"), "DesktopEntry": ("s", "refrata"), "CanQuit": ("b", True),
                    "CanRaise": ("b", True), "HasTrackList": ("b", False),
                    "SupportedUriSchemes": ("as", URI_SCHEMES), "SupportedMimeTypes": ("as", mime_types())}
        if iface == PLAYER_IFACE:
            has = bool(e.hasMedia)
            return {"PlaybackStatus": ("s", self.status()),
                    "LoopStatus": ("s", {1: "Playlist", 2: "Track"}.get(int(e.repeat), "None")),
                    "Rate": ("d", float(e.rate)), "Shuffle": ("b", bool(e.shuffle)),
                    "Metadata": ("a{sv}", self.metadata()), "Volume": ("d", self._volume()),
                    "Position": ("x", max(0, int(e.time)) * 1000),
                    "MinimumRate": ("d", 0.25), "MaximumRate": ("d", 4.0),
                    "CanGoNext": ("b", has), "CanGoPrevious": ("b", has), "CanPlay": ("b", has),
                    "CanPause": ("b", has), "CanSeek": ("b", e.length > 0), "CanControl": ("b", True)}
        return {}

    def _volume(self) -> float:
        return 0.0 if self.engine.muted else self.engine.volume / 100.0

    def status(self) -> str:
        st = self.engine.state
        return "Playing" if st in ("playing", "loading") and self.engine.hasMedia else \
            "Paused" if st == "paused" else "Stopped"

    def track_id(self) -> str:
        return f"/org/mpris/MediaPlayer2/Track/{self._track}" if self.engine.hasMedia else NO_TRACK

    def metadata(self) -> dict:
        """a{sv}: each value as (signature, value)."""
        e = self.engine
        if not e.hasMedia:
            return {"mpris:trackid": ("o", NO_TRACK)}
        md = {"mpris:trackid": ("o", self.track_id()), "xesam:title": ("s", e.title or "")}
        if e.length > 0:
            md["mpris:length"] = ("x", int(e.length) * 1000)   # int64, as MPRIS says
        if e.artist:
            md["xesam:artist"] = ("as", [e.artist])
        if e.album:
            md["xesam:album"] = ("s", e.album)
        art = e.artwork or (self._library.thumbFor(e.currentPath) if self._library else "")
        if art:
            md["mpris:artUrl"] = ("s", art if "://" in art else QUrl.fromLocalFile(art).toString())
        path = e.currentPath or ""
        if path:
            md["xesam:url"] = ("s", path if "://" in path else QUrl.fromLocalFile(path).toString())
        return md

    # ------------------------------------------------------------------ signals
    def _new_track(self):
        self._track += 1
        self._changed()

    def _changed(self):
        props = self._props(PLAYER_IFACE)
        keys = ("PlaybackStatus", "Metadata", "Volume", "CanGoNext", "CanGoPrevious", "CanPlay", "CanPause", "CanSeek")
        now = {k: props[k] for k in keys}
        diff = {k: v for k, v in now.items() if self._last.get(k) != v}
        self._last = now
        if diff:
            QTimer.singleShot(0, lambda d=diff: self._emit_changed(d))

    def _emit_changed(self, changed: dict):
        if not self.available:
            return
        from jeepney import DBusAddress, new_signal
        self._send(new_signal(DBusAddress(PATH, interface=PROPS_IFACE), "PropertiesChanged", "sa{sv}as",
                              (PLAYER_IFACE, changed, [])))

    def seeked(self):
        if self.available:
            from jeepney import DBusAddress, new_signal
            self._send(new_signal(DBusAddress(PATH, interface=PLAYER_IFACE), "Seeked", "x",
                                  (max(0, int(self.engine.time)) * 1000,)))

    def shutdown(self):
        if not self.available:
            return
        self.available = False
        self._stop = True
        try:
            from jeepney.bus_messages import message_bus
            self._conn.send(message_bus.ReleaseName(self.service))
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass


def _machine_id() -> str:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            return open(p).read().strip()
        except OSError:
            continue
    return ""


def make(engine, library=None, parent=None):
    """The MPRIS service: typed (jeepney) when it can be, else QtDBus (refrata/mpris.py)."""
    if available():
        m = WireMpris(engine, library, parent)
        if m.available:
            return m
    from .mpris import Mpris
    return Mpris(engine, library, parent)
