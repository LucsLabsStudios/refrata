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


def server_name(tag: str = "") -> str:
    who = f"{getpass.getuser()}|{os.environ.get('REFRATA_DATA', '')}|{tag}"
    return "Refrata-" + hashlib.sha1(who.encode("utf8")).hexdigest()[:16]


def send_to_running(args: list[str], name: str | None = None, timeout_ms: int = 800) -> bool:
    """True if a running instance accepted the arguments."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(json.dumps({"open": args}).encode("utf8") + b"\n")
    sock.flush()
    ok = sock.waitForBytesWritten(timeout_ms)
    # wait for the ack so the receiver has parsed the request before we exit
    ok = ok and sock.waitForReadyRead(timeout_ms) and bytes(sock.readAll().data()).startswith(b"ok")
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
            s.disconnected.connect(s.deleteLater)

    def _read(self, s: QLocalSocket):
        key = id(s)
        self._buffers[key] = self._buffers.get(key, b"") + bytes(s.readAll().data())
        if b"\n" not in self._buffers[key]:
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
