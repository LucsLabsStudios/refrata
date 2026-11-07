"""Media library: folder scanning, metadata/duration cache and thumbnail generation."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from pathlib import Path

import vlc
from PySide6.QtCore import (
    Property, QAbstractListModel, QByteArray, QModelIndex, QObject, QSortFilterProxyModel,
    Qt, QTimer, QUrl, Signal, Slot,
)

from .engine import FrameRenderer
from .media import AUDIO_EXT, VIDEO_EXT, nice_title
from .settings import DATA_DIR

THUMB_DIR = DATA_DIR / "thumbs"
THUMB_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = DATA_DIR / "library.json"
MAX_FILES = 4000
MAX_DEPTH = 5

ROLES = ["path", "url", "title", "kind", "folder", "duration", "thumb", "mtime", "size", "artist", "album", "ext"]


def _thumb_path(path: str, mtime: float) -> Path:
    h = hashlib.sha1(f"{path}|{int(mtime)}|960".encode("utf8", "ignore")).hexdigest()[:20]
    return THUMB_DIR / f"{h}.jpg"


class LibraryModel(QAbstractListModel):
    countChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []
        self._by_path: dict[str, int] = {}
        self._roles = {Qt.ItemDataRole.UserRole + i + 1: QByteArray(r.encode()) for i, r in enumerate(ROLES)}
        self._role_key = {Qt.ItemDataRole.UserRole + i + 1: r for i, r in enumerate(ROLES)}

    def roleNames(self):
        return self._roles

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        key = self._role_key.get(role)
        if key is None:
            return item["title"] if role == Qt.ItemDataRole.DisplayRole else None
        return item.get(key)

    def reset(self, items):
        self.beginResetModel()
        self._items = items
        self._by_path = {it["path"]: i for i, it in enumerate(items)}
        self.endResetModel()
        self.countChanged.emit()

    def update(self, path, fields: dict):
        row = self._by_path.get(path)
        if row is None:
            return
        self._items[row].update(fields)
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx)

    def item(self, path):
        row = self._by_path.get(path)
        return self._items[row] if row is not None else None

    def items(self):
        return self._items


class MediaFilter(QSortFilterProxyModel):
    """Filter by kind/search text and sort; usable directly from QML."""

    changed = Signal()

    def __init__(self, source, kind="", parent=None):
        super().__init__(parent)
        self._kind = kind
        self._search = ""
        self._sort = "recent"
        self._folder = ""
        self.setSourceModel(source)
        self.setDynamicSortFilter(True)
        self._apply_sort()
        source.modelReset.connect(self.changed)
        self.rowsInserted.connect(self.changed)
        self.rowsRemoved.connect(self.changed)
        self.modelReset.connect(self.changed)
        self.layoutChanged.connect(self.changed)

    def _role(self, name):
        return Qt.ItemDataRole.UserRole + ROLES.index(name) + 1

    def filterAcceptsRow(self, row, parent):
        it = self.sourceModel().items()[row]
        if self._kind and it["kind"] != self._kind:
            return False
        if self._folder and not it["path"].startswith(self._folder):
            return False
        if self._search:
            hay = f"{it['title']} {it.get('artist', '')} {it.get('album', '')} {os.path.basename(it['path'])}".lower()
            return all(w in hay for w in self._search.lower().split())
        return True

    def lessThan(self, left, right):
        a = self.sourceModel().items()[left.row()]
        b = self.sourceModel().items()[right.row()]
        if self._sort == "name":
            return a["title"].lower() < b["title"].lower()
        if self._sort == "duration":
            return (a["duration"] or 0) > (b["duration"] or 0)
        if self._sort == "size":
            return a["size"] > b["size"]
        return a["mtime"] > b["mtime"]

    def _apply_sort(self):
        self.invalidate()
        self.sort(0)

    @Property(str, notify=changed)
    def search(self):
        return self._search

    @search.setter
    def search(self, s):
        if s != self._search:
            self._search = s
            self.invalidateFilter()
            self.changed.emit()

    @Property(str, notify=changed)
    def sortMode(self):
        return self._sort

    @sortMode.setter
    def sortMode(self, s):
        if s != self._sort:
            self._sort = s
            self._apply_sort()
            self.changed.emit()

    @Property(str, notify=changed)
    def folder(self):
        return self._folder

    @folder.setter
    def folder(self, f):
        if f != self._folder:
            self._folder = f
            self.invalidateFilter()
            self.changed.emit()

    @Property(int, notify=changed)
    def count(self):
        return self.rowCount()

    @Slot(int, result="QVariantMap")
    def get(self, row):
        if 0 <= row < self.rowCount():
            src = self.mapToSource(self.index(row, 0)).row()
            return dict(self.sourceModel().items()[src])
        return {}

    @Slot(result=list)
    def paths(self):
        return [self.get(i)["path"] for i in range(self.rowCount())]


class Thumbnailer(threading.Thread):
    """Extracts duration/tags and a still frame for each file using its own libVLC."""

    def __init__(self, on_result):
        super().__init__(daemon=True, name="thumbnailer")
        self._q: queue.Queue = queue.Queue()
        self._on_result = on_result
        self._vlc = vlc.Instance(["--quiet", "--verbose=-1", "--no-audio", "--no-spu", "--no-sub-autodetect-file",
                                  "--avcodec-hw=none", "--no-video-title-show", "--no-osd"])
        self._stopping = False
        self._halt = threading.Event()

    def push(self, item):
        self._q.put(item)

    def stop(self, timeout=3.0):
        self._stopping = True
        self._halt.set()
        self._q.put(None)
        self.join(timeout)

    def run(self):
        while not self._stopping:
            it = self._q.get()
            if it is None or self._stopping:
                break
            try:
                self._process(it)
            except Exception as exc:
                print("thumb error", it.get("path"), exc)

    def _parse(self, path):
        media = self._vlc.media_new_path(path)
        # local (0x0) | fetch_local (0x2): read tags and local artwork, no network
        media.parse_with_options(vlc.MediaParseFlag.fetch_local, 4000)
        t0 = time.time()
        while time.time() - t0 < 4.5:
            st = media.get_parsed_status()
            if getattr(st, "value", st) >= 1:  # skipped / failed / timeout / done
                break
            time.sleep(0.05)
        return media

    def _process(self, it):
        path = it["path"]
        media = self._parse(path)
        res = {"duration": max(0, media.get_duration())}
        if it["kind"] == "audio":
            for key, meta in (("tagTitle", vlc.Meta.Title), ("artist", vlc.Meta.Artist), ("album", vlc.Meta.Album)):
                v = media.get_meta(meta)
                if v:
                    res[key] = v
            art = media.get_meta(vlc.Meta.ArtworkURL) or ""
            if art.startswith("file:"):
                res["thumb"] = art
            self._on_result(path, res)
            return
        target = _thumb_path(path, it["mtime"])
        if not target.exists():
            self._grab(media, res["duration"], target)
        if target.exists():
            res["thumb"] = QUrl.fromLocalFile(str(target)).toString()
        self._on_result(path, res)

    def _grab(self, media, duration, target: Path):
        mp = self._vlc.media_player_new()
        got = threading.Event()
        frames = [0]

        def on_frame(r):
            frames[0] += 1
            if frames[0] >= 2 and not got.is_set():
                img = r.to_image()
                if img is not None and not img.isNull():
                    img.save(str(target), "JPG", 82)
                got.set()

        FrameRenderer(mp, 960, on_frame)
        start = duration * 0.18 / 1000 if duration > 4000 else 0
        media.add_option(f":start-time={start:.1f}")
        mp.set_media(media)
        mp.play()
        t0 = time.time()
        while not got.is_set() and not self._halt.is_set() and time.time() - t0 < 7:
            got.wait(0.1)
        mp.stop()
        mp.release()


class Library(QObject):
    scanningChanged = Signal()
    statsChanged = Signal()
    _scanned = Signal(list)
    _result = Signal(str, dict)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._model = LibraryModel(self)
        self.videos = MediaFilter(self._model, "video", self)
        self.music = MediaFilter(self._model, "audio", self)
        self.recent = MediaFilter(self._model, "video", self)
        self.search = MediaFilter(self._model, "", self)
        self._scanning = False
        self._cache = self._load_cache()
        self._cache_dirty = False
        self._scanned.connect(self._on_scanned, Qt.ConnectionType.QueuedConnection)
        self._result.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        self._thumbs = Thumbnailer(lambda p, r: self._result.emit(p, r))
        self._thumbs.start()
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(3000)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_cache)
        self._revision = 0
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(250)
        self._stats_timer.setSingleShot(True)
        self._stats_timer.timeout.connect(self._emit_stats)
        settings.changed.connect(lambda k: k == "folders" and self.rescan())

    # ---------------------------------------------------------------- cache
    def _load_cache(self):
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf8"))
        except Exception:
            return {}

    def _save_cache(self):
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf8")
        os.replace(tmp, CACHE_FILE)

    # ----------------------------------------------------------------- scan
    @Slot()
    def rescan(self):
        if self._scanning:
            return
        self._scanning = True
        self.scanningChanged.emit()
        folders = list(self._settings.value("folders"))
        threading.Thread(target=self._scan, args=(folders,), daemon=True).start()

    def _scan(self, folders):
        found = []
        seen = set()
        for root in folders:
            root = os.path.normpath(root)
            if not os.path.isdir(root):
                continue
            stack = [(root, 0)]
            while stack and len(found) < MAX_FILES:
                d, depth = stack.pop()
                try:
                    entries = list(os.scandir(d))
                except OSError:
                    continue
                for e in entries:
                    name = e.name
                    if name.startswith((".", "$")):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < MAX_DEPTH:
                                stack.append((e.path, depth + 1))
                            continue
                        ext = os.path.splitext(name)[1].lower()
                        kind = "video" if ext in VIDEO_EXT else "audio" if ext in AUDIO_EXT else None
                        if not kind or e.path in seen:
                            continue
                        st = e.stat()
                        if st.st_size < 32 * 1024:
                            continue
                        seen.add(e.path)
                        found.append({
                            "path": e.path,
                            "url": QUrl.fromLocalFile(e.path).toString(),
                            "title": nice_title(e.path),
                            "kind": kind,
                            "folder": os.path.basename(os.path.dirname(e.path)),
                            "duration": 0,
                            "thumb": "",
                            "mtime": st.st_mtime,
                            "size": st.st_size,
                            "artist": "",
                            "album": "",
                            "ext": ext[1:].upper(),
                        })
                    except OSError:
                        continue
        self._scanned.emit(found)

    def _on_scanned(self, items):
        pending = []
        for it in items:
            c = self._cache.get(it["path"])
            if c and abs(c.get("mtime", 0) - it["mtime"]) < 2:
                it["duration"] = c.get("duration", 0)
                it["artist"] = c.get("artist", "")
                it["album"] = c.get("album", "")
                if c.get("tagTitle"):
                    it["title"] = c["tagTitle"]
                th = c.get("thumb", "")
                if th and (not th.startswith("file:") or os.path.exists(QUrl(th).toLocalFile())):
                    it["thumb"] = th
                if c.get("done") and (it["thumb"] or it["kind"] == "audio"):
                    continue
            pending.append(it)
        self._model.reset(items)
        # newest first so the home screen fills in quickly
        for it in sorted(pending, key=lambda x: -x["mtime"]):
            self._thumbs.push(dict(it))
        self._scanning = False
        self.scanningChanged.emit()
        self._emit_stats()

    def _on_result(self, path, res):
        it = self._model.item(path)
        if not it:
            return
        fields = {k: v for k, v in res.items() if k in ROLES}
        if res.get("tagTitle"):
            fields["title"] = res["tagTitle"]
        self._model.update(path, fields)
        entry = {"mtime": it["mtime"], "done": True, **res}
        self._cache[path] = entry
        if not self._save_timer.isActive():
            self._save_timer.start()
        if not self._stats_timer.isActive():
            self._stats_timer.start()

    def _emit_stats(self):
        self._revision += 1
        self.statsChanged.emit()

    # ------------------------------------------------------------- QML API
    @Property(int, notify=statsChanged)
    def revision(self):
        """Bumps whenever item metadata changes; QML bindings depend on it."""
        return self._revision

    @Property(bool, notify=scanningChanged)
    def scanning(self):
        return self._scanning

    @Property(QObject, constant=True)
    def videoModel(self):
        return self.videos

    @Property(QObject, constant=True)
    def musicModel(self):
        return self.music

    @Property(QObject, constant=True)
    def searchModel(self):
        return self.search

    @Property(int, notify=statsChanged)
    def videoCount(self):
        return sum(1 for i in self._model.items() if i["kind"] == "video")

    @Property(int, notify=statsChanged)
    def audioCount(self):
        return sum(1 for i in self._model.items() if i["kind"] == "audio")

    @Property(float, notify=statsChanged)
    def totalHours(self):
        return sum((i["duration"] or 0) for i in self._model.items()) / 3_600_000

    @Property(float, notify=statsChanged)
    def totalGB(self):
        return sum(i["size"] for i in self._model.items()) / 1024 ** 3

    @Slot(str, result="QVariantMap")
    def info(self, path):
        it = self._model.item(path)
        return dict(it) if it else {}

    @Slot(str, result=str)
    def thumbFor(self, path):
        it = self._model.item(path)
        return it["thumb"] if it else ""

    def shutdown(self):
        self._save_timer.stop()
        self._save_cache()
        self._thumbs.stop()
