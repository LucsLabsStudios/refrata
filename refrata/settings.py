"""Persistent JSON settings + watch history (stored in %LOCALAPPDATA%\\Refrata)."""
import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot

_LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home()))
DATA_DIR = Path(os.environ.get("REFRATA_DATA") or _LOCAL / "Refrata")
LEGACY_DIR = _LOCAL / "BestVLC"  # the app was called Best VLC before 1.0.0 shipped


def _migrate_legacy():
    """Carry settings, history and the library cache over from the Best VLC data folder, once."""
    if os.environ.get("REFRATA_DATA") or DATA_DIR.exists() or not LEGACY_DIR.is_dir():
        return
    import shutil
    try:
        shutil.copytree(LEGACY_DIR, DATA_DIR, ignore=shutil.ignore_patterns("*.log"))
    except OSError:
        shutil.rmtree(DATA_DIR, ignore_errors=True)  # start clean rather than half-copied


_migrate_legacy()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULTS = {
    "theme": "dark",          # dark | light
    "accent": "#FF7A2F",
    "resume": True,
    "autoplay": True,
    "hwdec": True,
    "volume": 80,
    "folders": [str(Path.home() / "Videos"), str(Path.home() / "Music")],
    "snapshotDir": str(Path.home() / "Pictures" / "Refrata"),
    "language": "pt-BR",
    "quickLook": True,        # Space preview in Explorer
    "background": False,      # close to the tray
    "language": "auto",       # auto (Windows language) or a code from refrata/i18n.py
}


class Settings(QObject):
    changed = Signal(str)
    historyChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = DATA_DIR / "settings.json"
        self._data = dict(DEFAULTS)
        self._history: list[dict] = []  # newest first: {path,title,time,pos,len}
        try:
            raw = json.loads(self._path.read_text(encoding="utf8"))
            self._data.update(raw.get("settings", {}))
            self._history = raw.get("history", [])
        except Exception:
            pass
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self.save)

    @Slot()
    def save(self):
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"settings": self._data, "history": self._history[:300]}, ensure_ascii=False, indent=1),
            encoding="utf8",
        )
        os.replace(tmp, self._path)

    def _dirty(self):
        self._save_timer.start()

    @Slot(str, result="QVariant")
    def value(self, key, default=None):
        return self._data.get(key, DEFAULTS.get(key, default))

    @Slot(str, "QVariant")
    def setValue(self, key, value):
        if self._data.get(key) != value:
            self._data[key] = value
            self._dirty()
            self.changed.emit(key)

    # convenient bindable properties for QML ------------------------------
    def _mk(key, typ, sig=changed):
        def g(self):
            return self.value(key)

        def s(self, v):
            self.setValue(key, v)

        return Property(typ, g, s, notify=sig)

    theme = _mk("theme", str)
    accent = _mk("accent", str)
    resume = _mk("resume", bool)
    autoplay = _mk("autoplay", bool)
    hwdec = _mk("hwdec", bool)
    folders = _mk("folders", list)
    del _mk

    @Slot(str)
    def addFolder(self, url):
        p = QUrl(url).toLocalFile() if url.startswith("file:") else url
        p = os.path.normpath(p)
        f = list(self.value("folders"))
        if p and p not in f:
            f.append(p)
            self.setValue("folders", f)

    @Slot(str)
    def removeFolder(self, p):
        f = [x for x in self.value("folders") if x != p]
        self.setValue("folders", f)

    # history / resume ------------------------------------------------------
    def _find(self, path):
        for h in self._history:
            if h["path"] == path:
                return h
        return None

    def add_history(self, path, title):
        h = self._find(path) or {"path": path, "title": title, "pos": 0, "len": 0}
        if h in self._history:
            self._history.remove(h)
        h["time"] = time.time()
        h["title"] = title
        self._history.insert(0, h)
        self._dirty()
        self.historyChanged.emit()

    def set_resume(self, path, pos, length):
        h = self._find(path)
        if not h:
            return
        # don't resume near the very start / end
        if pos < 10_000 or (length and pos > length - 15_000):
            pos = 0
        h["pos"], h["len"] = int(pos), int(length)
        self._dirty()
        self.historyChanged.emit()

    def resume_position(self, path) -> int:
        h = self._find(path)
        return int(h["pos"]) if h else 0

    def resume_length(self, path) -> int:
        h = self._find(path)
        return int(h.get("len", 0)) if h else 0

    @Property(list, notify=historyChanged)
    def history(self):
        return [h for h in self._history if "://" in h["path"] or os.path.exists(h["path"])][:40]

    @Slot(str)
    def removeHistory(self, path):
        self._history = [h for h in self._history if h["path"] != path]
        self._dirty()
        self.historyChanged.emit()

    @Slot()
    def clearHistory(self):
        self._history = []
        self._dirty()
        self.historyChanged.emit()
