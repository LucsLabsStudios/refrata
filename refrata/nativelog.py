"""Route native (libVLC / ffmpeg) stderr chatter into a log file.

VLC 3 for Windows is built against msvcrt.dll, whose stderr is separate from the
UCRT stderr used by Python. Redirecting msvcrt's fd 2 keeps ffmpeg notices such as
"[swscaler] deprecated pixel format used" out of the console while Python
tracebacks and Qt warnings still appear normally.
"""
import ctypes
import sys
from pathlib import Path

_O_WRONLY, _O_CREAT, _O_TRUNC, _O_BINARY = 0x0001, 0x0100, 0x0200, 0x8000
_S_IREAD, _S_IWRITE = 0x0100, 0x0080


def redirect_native_stderr(log_path: Path) -> bool:
    if sys.platform != "win32":
        return False
    try:
        crt = ctypes.cdll.msvcrt
        crt._open.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_int]
        crt._wopen.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_int]
        fd = crt._wopen(str(log_path), _O_WRONLY | _O_CREAT | _O_TRUNC | _O_BINARY, _S_IREAD | _S_IWRITE)
        if fd < 0:
            return False
        ok = crt._dup2(fd, 2) == 0
        crt._close(fd)
        return ok
    except Exception:
        return False
