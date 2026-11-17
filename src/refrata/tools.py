"""The player's extra tools, as the `tools` context property (PlayerView › Ferramentas, Faixas):

  - bookmarks: points marked in a file, kept per file (settings "bookmarks"), shown on the timeline
  - skip intro / outro: per folder (a season of a series shares its opening length): the intro is
    skipped when an episode starts, the outro jumps to the next episode (settings "skipMarks")
  - second subtitle: a text subtitle file drawn by the interface over the picture, together with
    libVLC's own subtitle (two languages at once); also the subtitles made by speech recognition
    (aisubs.py) are shown through it
  - save a clip (MP4, streams copied) or a GIF of the A-B stretch, or of the next seconds

Everything that runs per frame stays in QML: the cues are handed over once, and the overlay looks
them up against its own smooth clock.
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from . import clips, colorinfo, subparse
from .i18n import tr

MAX_BOOKMARKS = 200
CLIP_DEFAULT_MS = 6000


def _folder(path: str) -> str:
    return os.path.normcase(os.path.dirname(os.path.abspath(path))) if path and "://" not in path else ""


class PlayerTools(QObject):
    changed = Signal()            # bookmarks / skip marks of the current file
    cuesChanged = Signal()
    exportChanged = Signal()
    message = Signal(str)
    _exportDone = Signal(bool, str, str)

    def __init__(self, engine, settings, parent=None):
        super().__init__(parent)
        self._e = engine
        self._s = settings
        self._path = ""
        self._cues: list = []
        self._cue_name = ""
        self._export = ""          # "", "mp4", "gif": one export at a time
        self._outro_done = False
        self._intro_done = False
        engine.currentIndexChanged.connect(self._on_item)
        engine.mediaChanged.connect(self._on_item)
        engine.timeChanged.connect(self._on_time)
        self._exportDone.connect(self._on_export_done)
        self._on_item()

    # ------------------------------------------------------------ current item
    def _cur(self) -> str:
        p = self._e.currentPath
        return p if p and "://" not in p else ""

    def _on_item(self):
        p = self._cur()
        if p != self._path:
            self._path = p
            self._intro_done = self._outro_done = False
            if self._cues and self._cue_name != "ai":
                self._cues, self._cue_name = [], ""
                self.cuesChanged.emit()
            self.changed.emit()

    # ------------------------------------------------------------ bookmarks
    def _all_bookmarks(self) -> dict:
        b = self._s.value("bookmarks")
        return b if isinstance(b, dict) else {}

    @Property(list, notify=changed)
    def bookmarks(self):
        """Marked points of the file on screen, ms, in order."""
        return sorted(int(x) for x in self._all_bookmarks().get(self._path, []) if isinstance(x, (int, float)))

    @Slot()
    def addBookmark(self):
        if not self._path or self._e.length <= 0:
            return
        t = int(self._e.time)
        all_ = dict(self._all_bookmarks())
        cur = [int(x) for x in all_.get(self._path, [])]
        if any(abs(x - t) < 1000 for x in cur):
            self.message.emit(tr("Já há um favorito aqui"))
            return
        cur = sorted(cur + [t])[:MAX_BOOKMARKS]
        all_[self._path] = cur
        self._s.setValue("bookmarks", all_)
        self.changed.emit()
        self.message.emit(tr("Favorito em %1").replace("%1", _fmt(t)))

    @Slot(int)
    def removeBookmark(self, ms):
        all_ = dict(self._all_bookmarks())
        cur = [int(x) for x in all_.get(self._path, []) if int(x) != int(ms)]
        if cur:
            all_[self._path] = cur
        else:
            all_.pop(self._path, None)
        self._s.setValue("bookmarks", all_)
        self.changed.emit()

    @Slot(int)
    def jumpBookmark(self, ms):
        self._e.seek(int(ms))

    @Slot(int)
    def nextBookmark(self, direction):
        """The next (1) or previous (-1) bookmark from where the video is."""
        t = self._e.time
        marks = self.bookmarks
        if direction >= 0:
            after = [m for m in marks if m > t + 500]
            if after:
                self._e.seek(after[0])
        else:
            before = [m for m in marks if m < t - 1500]
            if before:
                self._e.seek(before[-1])

    # ------------------------------------------------------------ skip intro / outro
    def _marks(self) -> dict:
        m = self._s.value("skipMarks")
        return (m if isinstance(m, dict) else {}).get(_folder(self._path), {}) if self._path else {}

    @Property(int, notify=changed)
    def introEnd(self):
        return int(self._marks().get("intro", 0) or 0)

    @Property(int, notify=changed)
    def outroLength(self):
        """How much of the end is outro, ms (0 = none)."""
        return int(self._marks().get("outro", 0) or 0)

    def _set_mark(self, key, value):
        if not self._path:
            return
        all_ = dict(self._s.value("skipMarks") or {})
        f = _folder(self._path)
        cur = dict(all_.get(f, {}))
        if value > 0:
            cur[key] = int(value)
        else:
            cur.pop(key, None)
        if cur:
            all_[f] = cur
        else:
            all_.pop(f, None)
        self._s.setValue("skipMarks", all_)
        self.changed.emit()

    @Slot()
    def markIntroEnd(self):
        """The opening ends here: every file of this folder will start from this point."""
        t = int(self._e.time)
        if 0 < t < 15 * 60_000:
            self._set_mark("intro", t)
            self._intro_done = True
            self.message.emit(tr("Abertura de %1: será pulada nos vídeos desta pasta").replace("%1", _fmt(t)))

    @Slot()
    def markOutroStart(self):
        """The ending starts here: from this far before the end, files of this folder go to the next."""
        ln, t = self._e.length, self._e.time
        if ln > 0 and 0 < ln - t < 15 * 60_000:
            self._set_mark("outro", ln - t)
            self._outro_done = True
            self.message.emit(tr("Encerramento de %1: será pulado nos vídeos desta pasta").replace("%1", _fmt(ln - t)))

    @Slot()
    def clearSkip(self):
        self._set_mark("intro", 0)
        self._set_mark("outro", 0)

    def _on_time(self):
        if not self._path or self._e.state != "playing":
            return
        t, ln = self._e.time, self._e.length
        intro, outro = self.introEnd, self.outroLength
        if intro and not self._intro_done and 0 <= t < intro - 1000 and t < 30_000:
            self._intro_done = True
            self._e.seek(intro)
            self.message.emit(tr("Abertura pulada"))
        if outro and not self._outro_done and ln > outro and t >= ln - outro and ln - t > 1500:
            self._outro_done = True
            if self._e.currentIndex + 1 < self._e.queueCount or self._e.repeat == 1:
                self.message.emit(tr("Encerramento pulado"))
                self._e.next()

    # ------------------------------------------------------------ second subtitle
    @Property(list, notify=cuesChanged)
    def cues(self):
        """[start ms, end ms, text] of the overlay subtitle, sorted by start."""
        return [[a, b, t] for a, b, t in self._cues]

    @Property(str, notify=cuesChanged)
    def cuesJson(self):
        """The same cues as JSON, for the overlay: a plain JS array there. (A list property reaches
        QML as a sequence tied to this object, read again from Python on every access: the
        overlay's per-frame lookup did that 150 times a second.)"""
        import json
        return json.dumps([[a, b, t] for a, b, t in self._cues], ensure_ascii=False)

    @Property(str, notify=cuesChanged)
    def cuesName(self):
        """The file name of the second subtitle; "ai" for the subtitles made by speech recognition."""
        return self._cue_name

    @Slot(str, result=bool)
    def loadSecondSubtitle(self, url):
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url
        cues = subparse.read(path, self._s.value("subEncoding", "auto") if hasattr(self._s, "value") else "auto")
        if not cues:
            self.message.emit(tr("Não foi possível ler esta legenda"))
            return False
        self._cues, self._cue_name = cues, os.path.basename(path)
        self.cuesChanged.emit()
        return True

    def setCues(self, cues, name):
        """From aisubs.py: replace the overlay's cues."""
        self._cues, self._cue_name = list(cues), name
        self.cuesChanged.emit()

    @Slot()
    def clearSecondSubtitle(self):
        if self._cues:
            self._cues, self._cue_name = [], ""
            self.cuesChanged.emit()

    # ------------------------------------------------------------ clips
    @Property(str, notify=exportChanged)
    def exporting(self):
        return self._export

    def _range(self, max_ms):
        a, b = self._e.loopA, self._e.loopB
        if a >= 0 and b > a:
            return a, min(b, a + max_ms)
        t = max(0, self._e.time)
        ln = self._e.length
        return t, min(ln, t + CLIP_DEFAULT_MS) if ln > 0 else t + CLIP_DEFAULT_MS

    def _out_path(self, ext):
        folder = self._s.value("snapshotDir", "") or os.path.join(os.path.expanduser("~"), "Pictures")
        os.makedirs(folder, exist_ok=True)
        stem = os.path.splitext(os.path.basename(self._path))[0][:80] or "Refrata"
        return os.path.join(folder, f"{stem} {time.strftime('%Y-%m-%d %H-%M-%S')}.{ext}")

    @Slot()
    def saveClip(self):
        self._start_export("mp4")

    @Slot()
    def saveGif(self):
        self._start_export("gif")

    def _start_export(self, kind):
        if self._export or not self._path or self._e.length <= 0:
            return
        start, end = self._range(clips.GIF_MAX_MS if kind == "gif" else 30 * 60_000)
        if end - start < 300:
            return
        dst = self._out_path(kind)
        self._export = kind
        self.exportChanged.emit()
        self.message.emit(tr("Salvando o trecho…") if kind == "mp4" else tr("Criando o GIF…"))
        src = self._path
        if kind == "mp4":
            args = (src, start, end, dst)
            fn = clips.export_mp4
        else:
            fn = clips.export_gif
            args = (src, start, end, dst, self._e.videoFps or 30.0, colorinfo.hdr_kind(self._e._color))
        clips.run_async(fn, args, lambda ok: self._exportDone.emit(ok, kind, dst))

    def _on_export_done(self, ok, kind, dst):
        self._export = ""
        self.exportChanged.emit()
        self.lastExport = dst if ok else ""
        if ok:
            self.message.emit((tr("Trecho salvo: %1") if kind == "mp4" else tr("GIF salvo: %1")).replace("%1", os.path.basename(dst)))
        else:
            self.message.emit(tr("Não foi possível salvar o trecho"))

    lastExport = ""


def _fmt(ms):
    s = int(ms) // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
