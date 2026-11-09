"""Single-instance support over a per-user QLocalServer.

The first instance listens; a later launch (e.g. double-clicking a file in Explorer)
sends its arguments as JSON and exits, and the first instance opens them.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


MAX_MESSAGE = 4 * 1024 * 1024


def server_name(tag: str = "") -> str:
    who = f"{getpass.getuser()}|{os.environ.get('REFRATA_DATA', '')}|{tag}"
    return "Refrata-" + hashlib.sha1(who.encode("utf8")).hexdigest()[:16]


def send_to_running(args: list[str], name: str | None = None, timeout_ms: int = 800, ack_ms: int = 5000) -> bool:
    """True if a running instance accepted the arguments.

    Connecting is quick or nothing is listening (timeout_ms). Once connected, the running app may
    be busy for a moment (loading a library, a slow disk): its answer gets ack_ms. A short wait there
    made a second, full Refrata open while the first one was about to play the file too."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(json.dumps({"open": args}).encode("utf8") + b"\n")
    sock.flush()
    # on Unix sockets flush() usually writes everything at once, and waitForBytesWritten() then
    # answers False (nothing left to wait for): only wait while something is still queued
    ok = sock.bytesToWrite() == 0 or sock.waitForBytesWritten(ack_ms)
    # wait for the ack so the receiver has parsed the request before we exit
    ok = ok and sock.waitForReadyRead(ack_ms) and bytes(sock.readAll().data()).startswith(b"ok")
    sock.disconnectFromServer()
    return bool(ok)


class InstanceServer(QObject):
    received = Signal(list)  # list[str]
    activateRequested = Signal()

    def __init__(self, name: str | None = None, parent=None):
        super().__init__(parent)
        self.name = name or server_name()
        self._srv = QLocalServer(self)
        self._srv.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._srv.newConnection.connect(self._on_conn)
        self._buffers: dict[int, bytes] = {}

    def listen(self) -> bool:
        if self._srv.listen(self.name):
            return True
        QLocalServer.removeServer(self.name)  # stale pipe from a crashed instance
        return self._srv.listen(self.name)

    def close(self):
        self._srv.close()

    def _on_conn(self):
        while self._srv.hasPendingConnections():
            s = self._srv.nextPendingConnection()
            s.readyRead.connect(lambda s=s: self._read(s))
            s.disconnected.connect(lambda s=s: self._buffers.pop(id(s), None))   # no leftovers per socket
            s.disconnected.connect(s.deleteLater)

    def _read(self, s: QLocalSocket):
        key = id(s)
        self._buffers[key] = self._buffers.get(key, b"") + bytes(s.readAll().data())
        if b"\n" not in self._buffers[key]:
            if len(self._buffers[key]) > MAX_MESSAGE:   # file lists are kilobytes: drop junk
                self._buffers.pop(key, None)
                s.abort()
            return
        line = self._buffers.pop(key).split(b"\n", 1)[0]
        try:
            msg = json.loads(line.decode("utf8"))
            files = [str(x) for x in msg.get("open", [])]
        except (ValueError, AttributeError):
            s.write(b"bad\n")
            return
        s.write(b"ok\n")
        s.flush()
        # a plain relaunch brings the window back; with files, the app decides where they go
        # (quick player or the open window) - activating here would pop the main window up with
        # the previous video behind the quick player
        if files:
            self.received.emit(files)
        else:
            self.activateRequested.emit()
