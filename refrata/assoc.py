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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._registered = False
        self._default = False
        self.refresh()

    # ------------------------------------------------------------------ state
    @Slot()
    def refresh(self):
        reg, dflt = self._registered, self._default
        self._registered, self._default = self._read_state()
        if (reg, dflt) != (self._registered, self._default):
            self.changed.emit()

    def _read_state(self) -> tuple[bool, bool]:
        if sys.platform != "win32":
            return False, False
        import winreg
        hives = [winreg.HKEY_CURRENT_USER] if _testing() else [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
        registered = False
        for hive in hives:
            cap = _read(hive, _key(r"Software\RegisteredApplications"), "Refrata")
            cmd = _read(hive, _key(rf"Software\Classes\{PROGID}\shell\open\command"))
            if cap and Path(_command_exe(cmd)).is_file():
                registered = True
                break
        # the user's pick for .mp4 in Explorer (Apps padrão writes UserChoice)
        choice = _read(winreg.HKEY_CURRENT_USER, _key(_FILEEXTS + r"\.mp4\UserChoice"), "ProgId") or ""
        default = registered and (choice == PROGID or choice.lower() == ("applications\\" + APP_EXE).lower())
        return registered, default

    @Property(bool, notify=changed)
    def available(self):
        """Only a real Refrata.exe can be registered (not python.exe running the source tree)."""
        return sys.platform == "win32" and _exe() is not None

    @Property(bool, notify=changed)
    def registered(self):
        return self._registered

    @Property(bool, notify=changed)
    def isDefault(self):
        return self._default

    # ---------------------------------------------------------------- actions
    @Slot(result=bool)
    def register(self) -> bool:
        exe = _exe()
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

    @Slot()
    def openDefaults(self):
        """Register if needed, then open Windows' Apps padrão on Refrata's page."""
        if not self._registered:
            self.register()
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
