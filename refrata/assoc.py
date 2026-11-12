"""Refrata in Explorer's "Abrir com" list and in Windows' Apps padrão, per user (no admin).

The installer registers these keys (installer/Refrata.iss). The portable zip registered nothing,
so a copy run from a folder never showed up in "Abrir com", Windows kept opening videos in its
own player, and "Abrir arquivos no player rápido" never got a file. `register()` writes the
installer's keys under HKCU for the running exe. Since Windows 8 an app can't make itself the
default; `openDefaults()` opens the Windows page where the user picks it.

The test batteries (REFRATA_E2E_TMP) write under a throwaway HKCU\\Software\\RefrataTest key,
never the real ones, and skip the Explorer refresh and the Settings page.
"""
import os
import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from .i18n import tr
from .media import AUDIO_EXT, PLAYLIST_EXT, VIDEO_EXT

PROGID = "Refrata.Media"
APP_EXE = "Refrata.exe"
CAP_KEY = r"Software\LucsLabs Studios\Refrata\Capabilities"
TEST_ROOT = r"Software\RefrataTest"
_FILEEXTS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"


def _testing() -> bool:
    return bool(os.environ.get("REFRATA_E2E_TMP"))


def _key(sub: str) -> str:
    """Where a Software\\… key lives: the real place, or the test sandbox."""
    return TEST_ROOT + "\\" + sub if _testing() else sub


def exts() -> list[str]:
    return sorted(set(VIDEO_EXT) | set(AUDIO_EXT) | set(PLAYLIST_EXT))


# The types nearly every video and music file is. "Player padrão" counts as done when Windows opens
# all of these with Refrata; the rest are counted and shown, but do not hold the switch back.
MAIN_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".mp3", ".flac", ".m4a", ".wav")


def _effective_progid(ext: str) -> str:
    """The ProgId Windows opens `ext` with now, as Explorer resolves it.

    Not the registry's UserChoice: Windows 11 26H2 keeps the person's choice under UserChoiceLatest
    and left the old key behind (here .mkv read "Reprodutor Multimídia" there while it opened in
    Refrata). AssocQueryString answers with what a double-click really does."""
    if _testing():   # the battery's sandbox: what a choice in Settings would leave there
        base = _key(_FILEEXTS + "\\" + ext)
        import winreg
        return (_read(winreg.HKEY_CURRENT_USER, base + r"\UserChoiceLatest\ProgId", "ProgId")
                or _read(winreg.HKEY_CURRENT_USER, base + r"\UserChoice", "ProgId") or "")
    import ctypes
    from ctypes import wintypes as wt
    n = wt.DWORD(512)
    buf = ctypes.create_unicode_buffer(512)
    ASSOCSTR_PROGID = 20
    try:
        hr = ctypes.windll.shlwapi.AssocQueryStringW(0, ASSOCSTR_PROGID, ext, None, buf, ctypes.byref(n))
    except (AttributeError, OSError):
        return ""
    return buf.value if hr == 0 else ""


def _is_ours(progid: str) -> bool:
    return progid == PROGID or progid.lower() == ("applications\\" + APP_EXE).lower()


def mime_types() -> list[str]:
    """Linux: the MIME types for those extensions (from the shared MIME database Qt reads)."""
    from PySide6.QtCore import QMimeDatabase
    db = QMimeDatabase()
    out = []
    for e in exts():
        m = db.mimeTypeForFile("x" + e, QMimeDatabase.MatchMode.MatchExtension)
        if m.isValid() and m.name() not in ("application/octet-stream", "text/plain") and m.name() not in out:
            out.append(m.name())
    return out


def _linux_apps_dir() -> Path:
    base = os.environ.get("REFRATA_E2E_TMP") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "applications"


DESKTOP_ID = "refrata.desktop"


def _exe() -> str | None:
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.environ.get("REFRATA_ASSOC_EXE") if _testing() else None


def _read(hive, sub: str, name: str = ""):
    import winreg
    try:
        with winreg.OpenKey(hive, sub) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _command_exe(cmd) -> str:
    """The exe path in a `"C:\\…\\Refrata.exe" "%1"` command."""
    if not isinstance(cmd, str):
        return ""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        return cmd[1:].split('"', 1)[0]
    return cmd.split(" ", 1)[0]


class Associations(QObject):
    changed = Signal()
    _linuxRead = Signal(object)   # (registered, {ext: ours}) from the worker thread

    def __init__(self, parent=None):
        super().__init__(parent)
        self._registered = False
        self._ours: dict[str, bool] = {}   # each file type: does a double-click open Refrata?
        self._linux_busy = False
        self._platform = sys.platform   # (an attribute: the Linux path is tested on any system)
        self._linuxRead.connect(self._apply_linux)
        self.refresh()

    # ------------------------------------------------------------------ state
    @Slot()
    def refresh(self):
        if self._platform == "linux":
            self._refresh_linux()
            return
        self._apply(*self._read_state())

    def _apply(self, registered: bool, ours: dict):
        if (registered, ours) != (self._registered, self._ours):
            self._registered, self._ours = registered, ours
            self.changed.emit()

    def _read_state(self) -> tuple[bool, dict]:
        if sys.platform != "win32":
            return False, {}
        import winreg
        hives = [winreg.HKEY_CURRENT_USER] if _testing() else [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
        registered = False
        for hive in hives:
            cap = _read(hive, _key(r"Software\RegisteredApplications"), "Refrata")
            cmd = _read(hive, _key(rf"Software\Classes\{PROGID}\shell\open\command"))
            if cap and Path(_command_exe(cmd)).is_file():
                registered = True
                break
        ours = {e: registered and _is_ours(_effective_progid(e)) for e in exts()}
        return registered, ours

    def _refresh_linux(self):
        """xdg-mime answers one type per call (a shell script, tens of milliseconds each): the
        types are asked on a worker thread, and the answer arrives by signal."""
        if self._linux_busy:
            return
        self._linux_busy = True
        from PySide6.QtCore import QMimeDatabase
        db = QMimeDatabase()
        by_ext = {}
        for e in exts():
            m = db.mimeTypeForFile("x" + e, QMimeDatabase.MatchMode.MatchExtension)
            if m.isValid() and m.name() not in ("application/octet-stream", "text/plain"):
                by_ext[e] = m.name()
        entry = _linux_apps_dir() / DESKTOP_ID

        def work():
            try:
                registered = entry.exists()
                answers = {}
                if registered:
                    for mime in set(by_ext.values()):
                        answers[mime] = _xdg_default(mime) == DESKTOP_ID
                self._linuxRead.emit((registered, {e: answers.get(m, False) for e, m in by_ext.items()}))
            except Exception as exc:   # a broken xdg-utils must not end the thread silently
                print("default apps:", exc)
                self._linuxRead.emit((entry.exists(), {}))
        import threading
        threading.Thread(target=work, daemon=True, name="default-apps").start()

    def _apply_linux(self, result):
        self._linux_busy = False
        self._apply(*result)

    @Property(int, notify=changed)
    def typeCount(self):
        """File types Refrata offers to open."""
        return len(self._ours)

    @Property(int, notify=changed)
    def defaultCount(self):
        """Of those, the ones a double-click opens in Refrata now."""
        return sum(1 for v in self._ours.values() if v)

    @Property("QStringList", notify=changed)
    def missingMain(self):
        """The common types (MAIN_EXTS) that still open in another app, for the person to pick."""
        return [e for e in MAIN_EXTS if e in self._ours and not self._ours[e]]

    @Property(bool, notify=changed)
    def available(self):
        """Only a real Refrata.exe can be registered (not python.exe running the source tree)."""
        return sys.platform in ("win32", "linux") and _exe() is not None

    @Property(bool, notify=changed)
    def registered(self):
        return self._registered

    @Property(bool, notify=changed)
    def isDefault(self):
        """Every common type opens in Refrata (one .mp4 was the whole test before)."""
        main = [e for e in MAIN_EXTS if e in self._ours]
        return self._registered and bool(main) and all(self._ours[e] for e in main)

    # ---------------------------------------------------------------- actions
    @Slot(result=bool)
    def register(self) -> bool:
        exe = _exe()
        if sys.platform == "linux" and exe:
            return self._register_linux(exe)
        if sys.platform != "win32" or not exe:
            return False
        import winreg
        icon, cmd = f'"{exe}",0', f'"{exe}" "%1"'
        values = [
            (rf"Software\Classes\{PROGID}", "", tr("Mídia do Refrata")),
            (rf"Software\Classes\{PROGID}\DefaultIcon", "", icon),
            (rf"Software\Classes\{PROGID}\shell\open", "FriendlyAppName", "Refrata"),
            (rf"Software\Classes\{PROGID}\shell\open\command", "", cmd),
            (rf"Software\Classes\Applications\{APP_EXE}", "FriendlyAppName", "Refrata"),
            (rf"Software\Classes\Applications\{APP_EXE}\shell\open\command", "", cmd),
            (CAP_KEY, "ApplicationName", "Refrata"),
            (CAP_KEY, "ApplicationDescription", tr("Player de vídeo e música sobre o motor do VLC")),
            (CAP_KEY, "ApplicationIcon", icon),
            (r"Software\RegisteredApplications", "Refrata", CAP_KEY),
        ]
        for e in exts():
            values += [(rf"Software\Classes\{e}\OpenWithProgids", PROGID, ""),
                       (rf"Software\Classes\Applications\{APP_EXE}\SupportedTypes", e, ""),
                       (CAP_KEY + r"\FileAssociations", e, PROGID)]
        try:
            for sub, name, data in values:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _key(sub)) as k:
                    winreg.SetValueEx(k, name, 0, winreg.REG_SZ, data)
        except OSError as exc:
            print("file types not registered:", exc)
            return False
        if not _testing():
            try:   # Explorer re-reads the associations without a sign-out
                import ctypes
                ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
            except (AttributeError, OSError):
                pass
        self.refresh()
        self.changed.emit()
        return True

    def _register_linux(self, exe: str) -> bool:
        """~/.local/share/applications/refrata.desktop with the MIME types; "Abrir com" in every
        file manager reads it. Making it the default (xdg-mime) is the button's second step."""
        from .tray import _desktop_entry
        from .vlcpath import app_root
        d = _linux_apps_dir()
        icons = d.parent / "icons" / "hicolor" / "256x256" / "apps"
        try:
            d.mkdir(parents=True, exist_ok=True)
            icons.mkdir(parents=True, exist_ok=True)
            (icons / "refrata.png").write_bytes((app_root() / "assets" / "brand" / "icon-256.png").read_bytes())
            (d / DESKTOP_ID).write_text(_desktop_entry(exe), encoding="utf8")
        except OSError as exc:
            print("desktop entry not written:", exc)
            return False
        if not _testing():
            import subprocess
            subprocess.run(["update-desktop-database", str(d)], capture_output=True, check=False)
        self.refresh()
        self.changed.emit()
        return True

    @Slot()
    def openDefaults(self):
        """Register if needed, then let the user choose: Windows' Apps padrão page; on Linux,
        xdg-mime makes Refrata the default for its video and audio types right away."""
        if not self._registered:
            self.register()
        if sys.platform == "linux":
            if not _testing():
                import subprocess
                subprocess.run(["xdg-mime", "default", DESKTOP_ID, *mime_types()], capture_output=True, check=False)
            self.refresh()
            return
        if not _testing():
            QDesktopServices.openUrl(QUrl("ms-settings:defaultapps?registeredAppUser=Refrata"))

    def repair(self):
        """A portable folder that moved leaves "Abrir com" pointing at an exe that is gone: point
        the per-user keys at this copy again. Keys of a copy that still exists are left alone."""
        exe = _exe()
        if sys.platform != "win32" or not exe:
            return
        import winreg
        cmd = _read(winreg.HKEY_CURRENT_USER, _key(rf"Software\Classes\{PROGID}\shell\open\command"))
        old = _command_exe(cmd)
        if old and os.path.normcase(old) != os.path.normcase(exe) and not Path(old).is_file():
            self.register()


def _xdg_default(mime: str) -> str:
    import shutil
    import subprocess
    if not shutil.which("xdg-mime"):
        return ""
    r = subprocess.run(["xdg-mime", "query", "default", mime], capture_output=True, text=True, check=False)
    return r.stdout.strip()


def clear_test_keys():
    """Remove the test sandbox (tests only)."""
    if sys.platform != "win32":
        return
    import winreg

    def wipe(sub):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as k:
                children = []
                i = 0
                while True:
                    try:
                        children.append(winreg.EnumKey(k, i))
                    except OSError:
                        break
                    i += 1
            for c in children:
                wipe(sub + "\\" + c)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            pass
    wipe(TEST_ROOT)
