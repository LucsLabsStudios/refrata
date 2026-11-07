"""Background mode: tray icon, close-to-tray and start with Windows (per user, no admin).

With background mode on, closing the window keeps Refrata running in the tray, so the Space
preview (refrata/quicklook.py) and files opened from Explorer appear instantly.
"""
import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Refrata"


def _exe() -> str | None:
    return sys.executable if getattr(sys, "frozen", False) else None


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
        a_open = QAction("Abrir o Refrata", menu)
        a_open.triggered.connect(self.showRequested.emit)
        self._a_preview = QAction("Prévia com Espaço no Explorer", menu, checkable=True)
        self._a_preview.toggled.connect(lambda on: setattr(self._ql, "enabled", on))
        self._a_auto = QAction("Iniciar com o Windows", menu, checkable=True)
        self._a_auto.toggled.connect(lambda on: self._set_autostart(on))
        a_quit = QAction("Sair", menu)
        a_quit.triggered.connect(QApplication.instance().quit)
        for a in (a_open, None, self._a_preview, self._a_auto, None, a_quit):
            menu.addSeparator() if a is None else menu.addAction(a)
        self._menu = menu
        self._icon.setContextMenu(menu)
        self._apply()

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
        return _exe() is not None and sys.platform == "win32"

    def _read_run(self):
        if sys.platform != "win32" or _testing():
            return None
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                return winreg.QueryValueEx(k, RUN_NAME)[0]
        except OSError:
            return None

    def _set_autostart(self, on):
        exe = _exe()
        if sys.platform == "win32" and exe and not _testing():
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
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
