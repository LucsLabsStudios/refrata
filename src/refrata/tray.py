"""Background mode: tray icon, close-to-tray and start with Windows (per user, no admin).

With background mode on, closing the window keeps Refrata running in the tray, so the Space
preview (refrata/quicklook.py) and files opened from Explorer appear instantly.
"""
import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from .i18n import tr

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Refrata"


def _exe() -> str | None:
    # (the tests exercise the same code from the source tree, on their own registry key)
    return sys.executable if getattr(sys, "frozen", False) or _testing() else None


def _run_key() -> str:
    """Where "start with Windows" is written. The test batteries get a throwaway key of their own
    (per process: they run side by side), never the user's real Run entry."""
    import os
    return rf"Software\RefrataTest\Run-{os.getpid()}" if _testing() else RUN_KEY


def _autostart_file() -> Path:
    """Linux (XDG): ~/.config/autostart/refrata.desktop; the tests use their own config folder."""
    import os
    base = Path(os.environ.get("REFRATA_E2E_TMP") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "refrata.desktop"


def _desktop_entry(exe: str, args: str = "%F", autostart: bool = False) -> str:
    """A freedesktop .desktop entry for this copy of Refrata."""
    from .assoc import mime_types
    lines = ["[Desktop Entry]", "Type=Application", "Name=Refrata",
             "Comment=Video and music player built on the VLC engine",
             f'Exec="{exe}" {args}', "Icon=refrata", "Terminal=false", "Categories=AudioVideo;Player;Video;Audio;"]
    if autostart:
        lines.append("X-GNOME-Autostart-enabled=true")
    else:
        lines.append("MimeType=" + ";".join(mime_types()) + ";")
    return "\n".join(lines) + "\n"


def _testing() -> bool:
    """The E2E battery (also --selftest inside an installed copy) must never touch the user's
    real "start with Windows" entry."""
    import os
    return bool(os.environ.get("REFRATA_E2E_TMP"))


class Tray(QObject):
    changed = Signal()
    showRequested = Signal()

    def __init__(self, settings, quicklook, icon_path: Path, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._ql = quicklook
        self._icon = QSystemTrayIcon(QIcon(str(icon_path)), self)
        self._icon.setToolTip("Refrata")
        self._icon.activated.connect(self._activated)
        menu = QMenu()
        a_open = self._a_open = QAction(menu)
        a_open.triggered.connect(self.showRequested.emit)
        self._a_preview = QAction(menu, checkable=True)
        self._a_preview.toggled.connect(lambda on: setattr(self._ql, "enabled", on))
        self._a_auto = QAction(menu, checkable=True)
        self._a_auto.toggled.connect(lambda on: self._set_autostart(on))
        a_quit = self._a_quit = QAction(menu)
        a_quit.triggered.connect(QApplication.instance().quit)
        # the Space preview is an Explorer feature
        items = (a_open, None, self._a_preview, self._a_auto, None, a_quit) if sys.platform == "win32"             else (a_open, None, self._a_auto, None, a_quit)
        for a in items:
            menu.addSeparator() if a is None else menu.addAction(a)
        self._menu = menu
        self.retranslate()
        self._icon.setContextMenu(menu)
        self._apply()

    def retranslate(self):
        """Menu text in the current language (called again when the language changes)."""
        self._a_open.setText(tr("Abrir o Refrata"))
        self._a_preview.setText(tr("Prévia com Espaço no Explorer"))
        self._a_auto.setText(tr("Iniciar com o Windows") if sys.platform == "win32" else tr("Iniciar com o sistema"))
        self._a_quit.setText(tr("Sair"))

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.showRequested.emit()

    def _apply(self):
        on = self.background
        QApplication.instance().setQuitOnLastWindowClosed(not on)
        self._icon.setVisible(on and QSystemTrayIcon.isSystemTrayAvailable())
        for a, v in ((self._a_preview, self._ql.enabled), (self._a_auto, self.autostart)):
            a.blockSignals(True)
            a.setChecked(v)
            a.blockSignals(False)

    # ------------------------------------------------------------------ properties
    @Property(bool, notify=changed)
    def background(self):
        return bool(self._settings.value("background", False))

    @background.setter
    def background(self, on):
        self._settings.setValue("background", bool(on))
        if not on and self.autostart:
            self._set_autostart(False)
        self._apply()
        self.changed.emit()

    @Property(bool, notify=changed)
    def autostart(self):
        return self._read_run() is not None

    @autostart.setter
    def autostart(self, on):
        self._set_autostart(on)

    @Property(bool, constant=True)
    def canAutostart(self):
        return _exe() is not None and sys.platform in ("win32", "linux")

    def _read_run(self):
        if sys.platform == "linux":
            f = _autostart_file()
            return str(f) if f.exists() else None
        if sys.platform != "win32":
            return None
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _run_key()) as k:
                return winreg.QueryValueEx(k, RUN_NAME)[0]
        except OSError:
            return None

    def _set_autostart(self, on):
        exe = _exe()
        if sys.platform == "linux" and exe:
            f = _autostart_file()
            if on:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(_desktop_entry(exe, "--background", autostart=True), encoding="utf8")
            else:
                f.unlink(missing_ok=True)
            if on and not self.background:
                self._settings.setValue("background", True)
        elif sys.platform == "win32" and exe:
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _run_key()) as k:
                if on:
                    winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{exe}" --background')
                else:
                    try:
                        winreg.DeleteValue(k, RUN_NAME)
                    except OSError:
                        pass
            if on and not self.background:
                self._settings.setValue("background", True)
        self._apply()
        self.changed.emit()

    @Slot(str, str)
    def notify(self, title, text):
        if self._icon.isVisible():
            self._icon.showMessage(title, text, QSystemTrayIcon.MessageIcon.NoIcon, 3500)

    def shutdown(self):
        self._icon.hide()
