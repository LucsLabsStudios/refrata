"""The launch splash of the packaged app on Windows: only when this launch really starts Refrata.

It used to be PyInstaller's bootloader splash, shown before Python runs, so it showed on *every*
launch, including the ones that only hand a file to the Refrata already running (fastlaunch.py)
and quit a moment later: a splash flashed before every video opened from Explorer. Here it is
shown after that hand-over failed, i.e. on a cold start only, and never for a start that stays
hidden (tray, the quick player) or a test run.

A plain Win32 layered window with the PNG decoded by GDI+, both through ctypes: no Tcl/Tk (which the
bootloader splash unpacked and loaded on every start) and no Qt (whose loading is what the splash
covers). It lives on its own thread, with its own message loop, so Windows never sees it as not
responding while the main thread imports Qt and libVLC. Its window procedure is DefWindowProc
itself: nothing calls back into Python. Keep this module free of Qt and of other refrata imports.
"""
import json
import os
import sys
import threading

_state = {"hwnd": 0, "thread": None, "tid": 0}

# flags of launches that never show a window of ours at start (fastlaunch.SKIP_FLAGS and more)
_NO_SPLASH = ("--selftest", "--scenario", "--screenshot", "--background")


def _quick_open_hidden(argv) -> bool:
    """One media file with "open files in the quick player" on: the main window stays hidden."""
    files = [a for a in argv[1:] if not a.startswith("-")]
    if len(files) != 1:
        return False
    try:
        base = os.environ.get("REFRATA_DATA") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Refrata")
        with open(os.path.join(base, "settings.json"), encoding="utf8") as f:
            return bool(json.load(f).get("settings", {}).get("quickOpen", False))
    except (OSError, ValueError, AttributeError):
        return False


def wanted(argv) -> bool:
    return sys.platform == "win32" and not any(f in argv for f in _NO_SPLASH) and not _quick_open_hidden(argv)


def show(png: str):
    """Show the splash centred on the primary screen's work area (no-op if it cannot)."""
    if _state["thread"] is not None or not os.path.exists(png):
        return
    ready = threading.Event()
    t = threading.Thread(target=_run, args=(png, ready), name="launchsplash", daemon=True)
    _state["thread"] = t
    t.start()
    ready.wait(0.5)       # the window is up (or failed) before the heavy imports start


def close():
    """Take it down (from any thread); safe to call any number of times."""
    hwnd, tid = _state["hwnd"], _state["tid"]
    _state["hwnd"] = 0
    if not tid:
        return
    try:
        import ctypes
        u = ctypes.windll.user32
        if hwnd:
            u.ShowWindow(ctypes.c_void_p(hwnd), 0)
        u.PostThreadMessageW(tid, 0x0012, 0, 0)    # WM_QUIT: ends its message loop
    except Exception:
        pass
    _state["tid"] = 0


def _run(png, ready):
    import ctypes
    from ctypes import wintypes as wt
    try:
        u, g, k = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
        gp = ctypes.windll.gdiplus
        _state["tid"] = k.GetCurrentThreadId()

        class GdiplusStartupInput(ctypes.Structure):
            _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                        ("SuppressBackgroundThread", wt.BOOL), ("SuppressExternalCodecs", wt.BOOL)]
        token = ctypes.c_size_t()
        si = GdiplusStartupInput(1, None, False, False)
        if gp.GdiplusStartup(ctypes.byref(token), ctypes.byref(si), None):
            return
        bmp = ctypes.c_void_p()
        if gp.GdipCreateBitmapFromFile(ctypes.c_wchar_p(png), ctypes.byref(bmp)):
            return
        w, h = ctypes.c_uint(), ctypes.c_uint()
        gp.GdipGetImageWidth(bmp, ctypes.byref(w))
        gp.GdipGetImageHeight(bmp, ctypes.byref(h))
        hbm = ctypes.c_void_p()
        gp.GdipCreateHBITMAPFromBitmap(bmp, ctypes.byref(hbm), ctypes.c_uint32(0xFF000000))
        gp.GdipDisposeImage(bmp)
        if not hbm:
            return

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE),
                        ("hIcon", wt.HICON), ("hCursor", ctypes.c_void_p), ("hbrBackground", wt.HBRUSH),
                        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]
        hinst = k.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        # DefWindowProcW's own address: the window never calls into Python
        wc.lpfnWndProc = ctypes.cast(u.DefWindowProcW, ctypes.c_void_p).value
        wc.hInstance = hinst
        wc.hCursor = u.LoadCursorW(None, ctypes.c_void_p(32514))     # IDC_APPSTARTING
        wc.lpszClassName = "RefrataLaunchSplash"
        u.RegisterClassExW(ctypes.byref(wc))
        area = wt.RECT()
        u.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0)    # SPI_GETWORKAREA
        x = area.left + (area.right - area.left - w.value) // 2
        y = area.top + (area.bottom - area.top - h.value) // 2
        u.CreateWindowExW.restype = wt.HWND
        hwnd = u.CreateWindowExW(0x00080000 | 0x00000080,            # WS_EX_LAYERED | WS_EX_TOOLWINDOW
                                 wc.lpszClassName, "Refrata", 0x80000000,  # WS_POPUP
                                 x, y, w.value, h.value, None, None, hinst, None)
        if not hwnd:
            return
        screen = u.GetDC(None)
        mem = g.CreateCompatibleDC(screen)
        old = g.SelectObject(mem, hbm)

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_byte)]
        pos, size, src = wt.POINT(x, y), wt.SIZE(w.value, h.value), wt.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 0)                          # opaque picture
        u.UpdateLayeredWindow(hwnd, screen, ctypes.byref(pos), ctypes.byref(size), mem, ctypes.byref(src),
                              0, ctypes.byref(blend), 2)             # ULW_ALPHA
        g.SelectObject(mem, old)
        g.DeleteDC(mem)
        u.ReleaseDC(None, screen)
        u.ShowWindow(hwnd, 4)                                        # SW_SHOWNOACTIVATE
        _state["hwnd"] = hwnd
        ready.set()
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        u.DestroyWindow(hwnd)
        g.DeleteObject(hbm)
        gp.GdiplusShutdown(token)
    except Exception:
        pass
    finally:
        ready.set()
