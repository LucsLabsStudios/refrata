"""Single-instance support over a per-user QLocalServer.

The first instance listens; a later launch (e.g. double-clicking a file in Explorer)
sends its arguments as JSON and exits, and the first instance opens them.

claim_or_hand_over() decides which one a launch is, before anything heavy loads:
- a lock file (QLockFile, freed by the system if its owner dies) makes exactly one launch the
  primary, even when two start at the same moment;
- the primary listens at once, while its window is still being built; what arrives meanwhile
  waits in the server (hold) and is handed to the app when it is ready (release);
- a later launch keeps trying to reach the primary for as long as the primary is alive and starting:
  it never opens a second Refrata next to it (1.4.3 did, with two icons in the tray).
"""
from __future__ import annotations

import json
import time
import uuid

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


MAX_MESSAGE = 4 * 1024 * 1024


def server_name(tag: str = "") -> str:
    from .fastlaunch import server_name as name   # one definition: the fast path must find the same pipe
    return name(tag)


def send_to_running(args: list[str], name: str | None = None, timeout_ms: int = 800, ack_ms: int = 5000,
                    msg_id: str = "") -> bool:
    """True if a running instance accepted the arguments.

    Connecting is quick or nothing is listening (timeout_ms). Once connected, the running app may
    be busy for a moment (loading a library, a slow disk): its answer gets ack_ms. A short wait there
    made a second, full Refrata open while the first one was about to play the file too."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    from .fastlaunch import grant_foreground
    grant_foreground()   # the running one may bring its window to the front for this
    sock.write(json.dumps({"open": args, "id": msg_id or uuid.uuid4().hex}).encode("utf8") + b"\n")
    sock.flush()
    # on Unix sockets flush() usually writes everything at once, and waitForBytesWritten() then
    # answers False (nothing left to wait for): only wait while something is still queued
    ok = sock.bytesToWrite() == 0 or sock.waitForBytesWritten(ack_ms)
    # wait for the ack so the receiver has parsed the request before we exit
    ok = ok and sock.waitForReadyRead(ack_ms) and bytes(sock.readAll().data()).startswith(b"ok")
    sock.disconnectFromServer()
    return bool(ok)


def claim_or_hand_over(files: list[str], name: str | None = None, wait_s: float = 25.0, msg_id: str = ""):
    """The primary gets back an InstanceServer, already listening and holding requests until
    release(); a later launch hands `files` over and gets None (it should exit)."""
    from .settings import DATA_DIR
    lock = QLockFile(str(DATA_DIR / f"{name or server_name()}.lock"))
    lock.setStaleLockTime(0)   # stale only when its process is gone
    msg_id = msg_id or uuid.uuid4().hex   # the same request, if it has to be sent twice, counts once
    deadline = time.monotonic() + wait_s
    while True:
        if send_to_running(files, name, msg_id=msg_id):
            return None
        if lock.tryLock(0):
            srv = InstanceServer(name, hold=True)
            srv.lock = lock          # kept for the life of the process
            srv.listen()
            return srv
        if time.monotonic() > deadline:
            # the primary holds the lock but never answers (hung): take over rather than do nothing
            lock.removeStaleLockFile()
            if lock.tryLock(0):
                srv = InstanceServer(name, hold=True)
                srv.lock = lock
                srv.listen()
                return srv
            return None
        time.sleep(0.15)   # the primary is starting up: its server comes in a moment


class InstanceServer(QObject):
    received = Signal(list)  # list[str]
    activateRequested = Signal()

    def __init__(self, name: str | None = None, parent=None, hold: bool = False):
        super().__init__(parent)
        self.name = name or server_name()
        self.lock = None
        self._hold = hold           # queue requests until the app is ready (release)
        self._queued: list[list[str]] = []
        self._seen: set[str] = set()
        self._srv = QLocalServer(self)
        self._srv.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._srv.newConnection.connect(self._on_conn)
        self._buffers: dict[int, bytes] = {}

    def listen(self) -> bool:
        if self._srv.listen(self.name):
            return True
        QLocalServer.removeServer(self.name)  # stale pipe from a crashed instance
        return self._srv.listen(self.name)

    def release(self):
        """The app is ready: hand over what arrived while it was starting, and everything after."""
        self._hold = False
        for files in self._queued:
            if files:
                self.received.emit(files)
            else:
                self.activateRequested.emit()
        self._queued.clear()

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
            mid = str(msg.get("id", ""))
        except (ValueError, AttributeError, TypeError):
            s.write(b"bad\n")
            return
        s.write(b"ok\n")
        s.flush()
        if mid:
            if mid in self._seen:
                return   # a retry of a request already taken
            self._seen.add(mid)
        if self._hold:
            self._queued.append(files)
            return
        # a plain relaunch brings the window back; with files, the app decides where they go
        # (quick player or the open window) - activating here would pop the main window up with
        # the previous video behind the quick player
        if files:
            self.received.emit(files)
        else:
            self.activateRequested.emit()
