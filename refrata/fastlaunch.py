"""Hand files to the running Refrata before anything heavy loads.

Opening a second video while Refrata runs used to start a whole second process (Python, Qt, libVLC:
half a second or more) only to pass a path along and quit. This talks to the running instance's
QLocalServer directly - a named pipe on Windows, a Unix socket elsewhere - with the standard library
alone, at the very top of main.py. When it gets the "ok", the launch ends right there. Anything else
(no Refrata running, or one still starting) falls through to the full path in refrata/single.py,
with the same request id, so a file sent twice is still opened once.

Keep this module free of Qt and of other refrata imports: its speed is the point.
"""
import getpass
import hashlib
import json
import os
import sys
import threading
import uuid

SKIP_FLAGS = ("--selftest", "--scenario", "--screenshot", "--new-window", "--background")


def server_name(tag: str = "") -> str:
    """Same name as refrata.single.server_name (QLocalServer's name for this user and data folder)."""
    who = f"{getpass.getuser()}|{os.environ.get('REFRATA_DATA', '')}|{tag}"
    return "Refrata-" + hashlib.sha1(who.encode("utf8")).hexdigest()[:16]


# main.py's options that take a value: the value is not a file to open
VALUE_FLAGS = ("--page", "--panel", "--delay", "--size", "--theme", "-k", "--report", "--log")


def _files(argv) -> list[str]:
    out = []
    skip = False
    for a in argv[1:]:
        if skip:
            skip = False
            continue
        if a.startswith("-"):
            skip = a in VALUE_FLAGS
            continue
        out.append(os.path.abspath(a) if os.path.exists(a) else a)
    return out


def grant_foreground():
    """Let the running Refrata bring its window to the front for this request.

    Windows lets only the process the person just started (this one, from Explorer) set the
    foreground window; the Refrata already running, sitting in the tray, is a background process and
    its window opened behind the others with the taskbar button flashing, whenever its own
    workaround could not attach to the window in front. This passes the right on, which is what
    Windows provides AllowSetForegroundWindow for. ASFW_ANY: the pid of the other one is not known."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_uint32(0xFFFFFFFF))
    except Exception:
        pass


def _roundtrip(payload: bytes, timeout: float) -> bytes:
    """Send one request line and read the reply, within `timeout` seconds (b"" on any failure)."""
    name = server_name()
    result = [b""]

    def work():
        try:
            if sys.platform == "win32":
                # QLocalServer's pipe; opening fails at once when nobody listens
                with open(rf"\\.\pipe\{name}", "r+b", buffering=0) as pipe:
                    pipe.write(payload)
                    result[0] = pipe.read(3)
            else:
                import socket
                import tempfile
                path = os.path.join(os.environ.get("TMPDIR") or tempfile.gettempdir(), name)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(timeout)
                    s.connect(path)
                    s.sendall(payload)
                    result[0] = s.recv(3)
        except OSError:
            pass
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    return result[0]


def try_hand_over(argv=None, timeout: float = 1.5) -> str:
    """Exit the process if the running Refrata took the request; else return the request id for
    the full path to reuse (or "" when this launch must not hand over at all)."""
    argv = list(sys.argv if argv is None else argv)
    if any(f in argv for f in SKIP_FLAGS):
        return ""
    msg_id = uuid.uuid4().hex
    payload = json.dumps({"open": _files(argv), "id": msg_id}).encode("utf8") + b"\n"
    grant_foreground()
    if _roundtrip(payload, timeout).startswith(b"ok"):
        os._exit(0)
    return msg_id
