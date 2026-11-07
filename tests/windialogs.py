"""Drive the real Windows common file dialogs (IFileOpenDialog) from a test.

Qt shows native dialogs, which run their own modal loop, so the driver works from a
background thread with plain Win32 calls (ctypes releases the GIL during each call):

    drv = DialogDriver("Abrir mídia", folder=r"C:\\x", names=["a.mkv", "b.mkv"]).start()
    click(...)                      # opens the dialog on the GUI thread
    wait(lambda: drv.done)          # pumps Qt events while the driver types + presses Open

The driver records what it saw (dialog title, filter text) for assertions.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
BM_CLICK = 0x00F5
CB_GETCURSEL = 0x0147
CB_GETLBTEXT = 0x0148
CB_GETLBTEXTLEN = 0x0149
IDOK, IDCANCEL = 1, 2
ID_FILENAME_COMBOEX = 0x047C  # 1148: "Nome:" / "Pasta:" combo
ID_FILETYPE_COMBO = 0x0470    # 1136: file-type filter combo

EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, EnumProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetDlgItem.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = wintypes.LPARAM
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]


def _cls(h) -> str:
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def _title(h) -> str:
    b = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(h, b, 512)
    return b.value


def find_dialog(title: str, pid: int | None = None) -> int | None:
    pid = pid or os.getpid()
    found = []

    def cb(h, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(h) and _cls(h) == "#32770" and _title(h) == title:
            found.append(h)
            return False
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found[0] if found else None


def any_dialog(pid: int | None = None) -> list[str]:
    """Titles of every visible common dialog of the process (for diagnostics)."""
    pid = pid or os.getpid()
    out = []

    def cb(h, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(h) and _cls(h) == "#32770":
            out.append(_title(h))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def _filename_edit(dlg) -> int | None:
    combo = user32.GetDlgItem(dlg, ID_FILENAME_COMBOEX)
    if combo:
        # ComboBoxEx32 > ComboBox > Edit
        inner = user32.FindWindowExW(combo, None, "ComboBox", None)
        edit = user32.FindWindowExW(inner or combo, None, "Edit", None)
        if edit:
            return edit
    # folder picker / fallback: first visible Edit anywhere under the dialog
    edits = []

    def cb(h, _):
        if _cls(h) == "Edit" and user32.IsWindowVisible(h):
            edits.append(h)
        return True

    user32.EnumChildWindows(dlg, EnumProc(cb), 0)
    return edits[0] if edits else None


def _get_text(h) -> str:
    n = user32.SendMessageW(h, WM_GETTEXTLENGTH, 0, 0)
    b = ctypes.create_unicode_buffer(n + 1)
    user32.SendMessageW(h, WM_GETTEXT, n + 1, ctypes.cast(b, ctypes.c_void_p).value)
    return b.value


def _set_text(h, text: str):
    buf = ctypes.create_unicode_buffer(text)
    user32.SendMessageW(h, WM_SETTEXT, 0, ctypes.cast(buf, ctypes.c_void_p).value)


def _filter_text(dlg) -> str:
    combo = user32.GetDlgItem(dlg, ID_FILETYPE_COMBO)
    if not combo:
        return ""
    i = user32.SendMessageW(combo, CB_GETCURSEL, 0, 0)
    if i < 0:
        return ""
    n = user32.SendMessageW(combo, CB_GETLBTEXTLEN, i, 0)
    b = ctypes.create_unicode_buffer(max(n, 0) + 1)
    user32.SendMessageW(combo, CB_GETLBTEXT, i, ctypes.cast(b, ctypes.c_void_p).value)
    return b.value


def _press(dlg, cmd):
    btn = user32.GetDlgItem(dlg, cmd)
    if btn:
        user32.PostMessageW(btn, BM_CLICK, 0, 0)
    else:
        user32.PostMessageW(dlg, WM_COMMAND, cmd, 0)


class DialogDriver(threading.Thread):
    """Wait for a dialog by title, then type into it and press Open/OK (or Cancel).

    folder: navigate here first (typed into the name box + Open, like a user would)
    names:  file names inside `folder` to select (several → quoted list = multi-select)
    path:   a full path typed as-is (folder pickers: the folder to choose)
    cancel: press Cancel instead
    """

    def __init__(self, title: str, *, folder: str | None = None, names: list[str] | None = None,
                 path: str | None = None, cancel: bool = False, timeout: float = 15.0):
        super().__init__(daemon=True)
        self.title, self.folder, self.names, self.path = title, folder, names or [], path
        self.cancel, self.timeout = cancel, timeout
        self.done = False
        self.error = ""
        self.seen_filter = ""
        self.steps: list[str] = []

    def start(self):  # noqa: D401  (chainable)
        super().start()
        return self

    def _wait_dialog(self, until):
        while time.time() < until:
            h = find_dialog(self.title)
            if h:
                return h
            time.sleep(0.05)
        return None

    def _edit(self, dlg):
        for _ in range(60):  # the name box appears a moment after the window
            edit = _filename_edit(dlg)
            if edit:
                return edit
            time.sleep(0.05)
        raise RuntimeError("file-name box not found")

    def _type_and_ok(self, dlg, text, settle=0.35, navigate=False):
        edit = self._edit(dlg)
        _set_text(edit, text)
        time.sleep(0.1)
        self.steps.append(f"typed {_get_text(edit)!r}")
        _press(dlg, IDOK)
        if navigate:
            # the dialog clears the name box once it has moved into the folder
            end = time.time() + 6
            while time.time() < end and _get_text(edit) == text:
                time.sleep(0.05)
            self.steps.append("navigated" if _get_text(edit) != text else "navigation not confirmed")
        time.sleep(settle)
        self._check_error_box(dlg)

    def _check_error_box(self, dlg):
        """The shell reports bad input in a message box captioned like the dialog itself."""
        pid = os.getpid()
        boxes = []

        def cb(h, _):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if h != dlg and p.value == pid and user32.IsWindowVisible(h) and _cls(h) == "#32770" and _title(h) == self.title:
                boxes.append(h)
            return True

        user32.EnumWindows(EnumProc(cb), 0)
        for box in boxes:
            texts = []

            def tcb(h, _):
                if _cls(h) in ("Static", "DirectUIHWND") and _title(h):
                    texts.append(_title(h))
                return True

            user32.EnumChildWindows(box, EnumProc(tcb), 0)
            _press(box, IDOK)
            raise RuntimeError("the dialog showed an error: " + " | ".join(texts or ["(no text)"]))

    def run(self):
        try:
            until = time.time() + self.timeout
            dlg = self._wait_dialog(until)
            if not dlg:
                raise RuntimeError(f"dialog '{self.title}' never appeared (open: {any_dialog()})")
            time.sleep(0.4)  # let the shell view finish populating
            self.seen_filter = _filter_text(dlg)
            if self.cancel:
                _press(dlg, IDCANCEL)
            else:
                if self.folder and len(self.names) == 1:
                    self._type_and_ok(dlg, os.path.join(self.folder, self.names[0]))
                elif self.names:
                    if self.folder:
                        self._type_and_ok(dlg, self.folder, settle=0.3, navigate=True)  # dialog stays open
                    self._type_and_ok(dlg, " ".join(f'"{n}"' for n in self.names))
                elif self.path:
                    self._type_and_ok(dlg, self.path, settle=0.6)
                    # a folder picker may navigate into the typed folder instead of choosing it
                    if user32.IsWindow(dlg) and user32.IsWindowVisible(dlg):
                        self.steps.append("second OK (select current folder)")
                        _press(dlg, IDOK)
            # wait until the dialog is gone
            while time.time() < until and user32.IsWindow(dlg) and user32.IsWindowVisible(dlg):
                time.sleep(0.05)
            if user32.IsWindow(dlg) and user32.IsWindowVisible(dlg):
                edit = _filename_edit(dlg)
                state = f"name box={_get_text(edit)!r}" if edit else "no name box"
                user32.PostMessageW(dlg, WM_CLOSE, 0, 0)
                raise RuntimeError(f"dialog did not close ({state}; dialogs: {any_dialog()}): " + "; ".join(self.steps))
        except Exception as exc:  # reported to the test
            self.error = str(exc)
        finally:
            self.done = True
