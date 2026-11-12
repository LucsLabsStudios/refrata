"""Persistent JSON settings + watch history (stored in %LOCALAPPDATA%\\Refrata)."""
import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Property, QAbstractListModel, QModelIndex, QObject, Qt, QTimer, QUrl, Signal, Slot

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
    "quickOpen": False,       # files from Explorer start in the quick player (off: full app)
    "quickLook": True,        # Space preview in Explorer
    "background": False,      # close to the tray
    "language": "auto",       # auto (Windows language) or a code from refrata/i18n.py
    "shortcuts": {},          # action -> key, only the ones changed in Ajustes (refrata/shortcuts.py)
}


def _valid(key, v) -> bool:
    """A stored value is used only if it has the shape the app expects (settings.json can be
    hand-edited, half-written by a crash or synced from somewhere else)."""
    d = DEFAULTS[key]
    if key == "accent":
        return isinstance(v, str) and len(v) in (7, 9) and v.startswith("#") and \
            all(c in "0123456789abcdefABCDEF" for c in v[1:])
    if key == "theme":
        return v in ("dark", "light")
    if key == "volume":
        return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 150
    if key == "folders":
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    if key == "shortcuts":
        return isinstance(v, dict) and all(isinstance(k, str) and isinstance(x, str) for k, x in v.items())
    return type(v) is type(d)


def _clean_settings(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and (k not in DEFAULTS or _valid(k, v))}


def _clean_history(raw) -> list[dict]:
    out = []
    for h in raw if isinstance(raw, list) else []:
        if not (isinstance(h, dict) and isinstance(h.get("path"), str) and h["path"]):
            continue
        num = lambda k: h.get(k) if isinstance(h.get(k), (int, float)) and not isinstance(h.get(k), bool) else 0
        out.append({"path": h["path"], "title": h["title"] if isinstance(h.get("title"), str) else "",
                    "time": num("time"), "pos": int(num("pos")), "len": int(num("len"))})
    return out


class HistoryModel(QAbstractListModel):
    """The recently-played shelf. Playing a file moves its card to the front and saving the
    position updates one card: the shelf's cards are not torn down and rebuilt (with their
    pictures and effects) on every track change. One role, so delegates use `modelData.*`."""

    ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []

    def roleNames(self):
        return {self.ROLE: b"modelData"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == self.ROLE and 0 <= index.row() < len(self.rows):
            return self.rows[index.row()]
        return None

    def sync(self, new: list[dict]):
        old = self.rows
        op, np_ = [r["path"] for r in old], [r["path"] for r in new]
        if op == np_:
            for i, (a, b) in enumerate(zip(old, new)):
                if a != b:
                    self.rows[i] = b
                    ix = self.index(i)
                    self.dataChanged.emit(ix, ix, [self.ROLE])
            return
        if np_ and np_[1:] == [p for p in op if p != np_[0]][: len(np_) - 1]:
            # the usual case: one file went (or came in) at the front
            p = np_[0]
            if p in op:
                i = op.index(p)
                self.beginMoveRows(QModelIndex(), i, i, QModelIndex(), 0)
                self.rows.insert(0, self.rows.pop(i))
                self.endMoveRows()
            else:
                self.beginInsertRows(QModelIndex(), 0, 0)
                self.rows.insert(0, new[0])
                self.endInsertRows()
            while len(self.rows) > len(new):
                n = len(self.rows) - 1
                self.beginRemoveRows(QModelIndex(), n, n)
                self.rows.pop()
                self.endRemoveRows()
            self.sync(new)   # now same order: refresh changed fields
            return
        self.beginResetModel()
        self.rows = list(new)
        self.endResetModel()


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
            if isinstance(raw, dict):
                self._data.update(_clean_settings(raw.get("settings")))
                self._history = _clean_history(raw.get("history"))
        except Exception:
            pass   # unreadable or not JSON: start from the defaults
        # Up to 1.3.0 "quickOpen" defaulted to on, and every default gets written to settings.json, so
        # old installs carry quickOpen=true without anyone choosing it. Reset it once to the new
        # default (off: a double-click opens the full app); a later choice in Ajustes sticks.
        if not self._data.get("quickOpenReset"):
            self._data["quickOpen"] = False
            self._data["quickOpenReset"] = True
        self._hmodel = HistoryModel(self)
        self._hmodel.sync(self._visible_history())
        self.historyChanged.connect(lambda: self._hmodel.sync(self._visible_history()))
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self.save)

    @Slot()
    def save(self):
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps({"settings": self._data, "history": self._history[:300]}, ensure_ascii=False, indent=1),
                encoding="utf8",
            )
            os.replace(tmp, self._path)
        except OSError as exc:
            # read-only file, full disk, antivirus lock: keep running with what is in memory
            print("settings not saved:", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

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
    quickOpen = _mk("quickOpen", bool)
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
        self._history_changed()

    def set_resume(self, path, pos, length):
        h = self._find(path)
        if not h:
            return
        # don't resume near the very start / end
        if pos < 10_000 or (length and pos > length - 15_000):
            pos = 0
        h["pos"], h["len"] = int(pos), int(length)
        self._history_changed()

    def resume_position(self, path) -> int:
        h = self._find(path)
        return int(h["pos"]) if h else 0

    def resume_length(self, path) -> int:
        h = self._find(path)
        return int(h.get("len", 0)) if h else 0

    VISIBLE = 40          # entries the home page and the network page show
    EXISTS_TTL = 120.0    # seconds a "the file is there" answer is reused

    def _exists(self, path: str) -> bool:
        """os.path.exists, remembered for EXISTS_TTL: a history on a cloud or network drive (Google
        Drive's G:) answers slowly, and was asked again for every entry each time it was read."""
        cache = self.__dict__.setdefault("_exists_cache", {})
        now = time.monotonic()
        hit = cache.get(path)
        if hit is None or now - hit[1] > self.EXISTS_TTL:
            hit = (os.path.exists(path), now)
            cache[path] = hit
        return hit[0]

    def _visible_history(self) -> list[dict]:
        """The entries still worth showing (links, and files that are still there), newest first.

        Computed once per change of the history. The pages read `history` hundreds of times per opened
        file (every binding on it, on every change notice): each read filtered all 300 entries against
        the disk, 22,781 checks and 4.1 s of a frozen interface for one double-click in Explorer."""
        rev = (len(self._history), id(self._history), self.__dict__.get("_hrev", 0))
        cached = self.__dict__.get("_visible")
        if cached is None or cached[0] != rev:
            out = []
            for h in self._history:
                if "://" in h["path"] or self._exists(h["path"]):
                    out.append(dict(h))
                    if len(out) >= self.VISIBLE:
                        break
            cached = (rev, out)
            self._visible = cached
        return [dict(h) for h in cached[1]]

    def _history_changed(self):
        self._hrev = self.__dict__.get("_hrev", 0) + 1
        self._dirty()
        self.historyChanged.emit()

    @Property(list, notify=historyChanged)
    def history(self):
        return self._visible_history()

    @Property(QObject, constant=True)
    def historyModel(self):
        return self._hmodel

    @Slot(str)
    def removeHistory(self, path):
        self._history = [h for h in self._history if h["path"] != path]
        self._history_changed()

    @Slot()
    def clearHistory(self):
        self._history = []
        self._history_changed()
