"""Subtitle search on OpenSubtitles.com (REST API v1), like VLC's VLSub.

Needs an API key (free, from an opensubtitles.com account). It is read from the environment
variable REFRATA_OPENSUBTITLES_KEY, or from refrata/_keys.py, which tools/build.py writes from
that same variable (the file is git-ignored: the key never goes into the public repository).
Without a key the feature stays hidden.

Search is by the file's OpenSubtitles hash first (exact match for that release), then by name.
"""
from __future__ import annotations

import json
import os
import re
import struct
import urllib.parse
import urllib.request
from pathlib import Path

from PySide6.QtCore import Property, QObject, QThread, Signal, Slot

from . import __version__
from .i18n import tr

def _api() -> str:
    return os.environ.get("REFRATA_OPENSUBTITLES_URL", "https://api.opensubtitles.com/api/v1")


UA = f"Refrata v{__version__}"


def api_key() -> str:
    k = os.environ.get("REFRATA_OPENSUBTITLES_KEY", "")
    if k:
        return k
    try:
        from ._keys import OPENSUBTITLES  # written by the build, not in git
        return OPENSUBTITLES
    except Exception:
        return ""


def movie_hash(path: str) -> str:
    """OpenSubtitles hash: file size + 64-bit sums of the first and last 64 KiB."""
    size = os.path.getsize(path)
    if size < 131072:
        return ""
    h = size
    with open(path, "rb") as f:
        for off in (0, size - 65536):
            f.seek(off)
            buf = f.read(65536)
            for (v,) in struct.iter_unpack("<Q", buf):
                h = (h + v) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def guess_query(path: str) -> str:
    """'The.Movie.2019.1080p.x264.mkv' → 'The Movie 2019'."""
    name = Path(path).stem
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\b(2160p|1080p|720p|480p|x26[45]|h ?26[45]|hevc|web[- ]?dl|webrip|bluray|brrip|hdrip|"
                  r"dvdrip|aac|ac3|dts|10bit|hdr).*$", "", name, flags=re.I)
    return name.strip()


def _call(method: str, path: str, key: str, params=None, body=None, timeout=15):
    url = _api() + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Api-Key": key, "User-Agent": UA, "Accept": "application/json",
        **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf8"))


def search(path: str, languages: str) -> list[dict]:
    key = api_key()
    out, seen = [], set()
    queries = []
    if path and "://" not in path and os.path.isfile(path):
        mh = movie_hash(path)
        if mh:
            queries.append({"moviehash": mh, "languages": languages})
    q = guess_query(path) if path else ""
    if q:
        queries.append({"query": q, "languages": languages})
    for params in queries:
        res = _call("GET", "/subtitles", key, params)
        for item in res.get("data", []):
            a = item.get("attributes", {})
            files = a.get("files") or []
            if not files:
                continue
            fid = files[0].get("file_id")
            if fid in seen:
                continue
            seen.add(fid)
            out.append({"fileId": fid, "language": a.get("language", ""), "name": a.get("release") or files[0].get("file_name", ""),
                        "downloads": a.get("download_count", 0), "exact": bool(a.get("moviehash_match")),
                        "hearingImpaired": bool(a.get("hearing_impaired"))})
        if out and "moviehash" in params:
            break   # exact matches for this very file: no need to search by name
    out.sort(key=lambda r: (not r["exact"], -r["downloads"]))
    return out[:40]


def download(file_id: int, dest_dir: Path, stem: str, language: str) -> str:
    res = _call("POST", "/download", api_key(), body={"file_id": file_id})
    link = res.get("link")
    if not link:
        raise RuntimeError(res.get("message") or "no download link")
    req = urllib.request.Request(link, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[\\/:*?"<>|]+', "_", f"{stem}.{language or 'sub'}.srt")
    p = dest_dir / name
    p.write_bytes(data)
    return str(p)


class _Job(QThread):
    done = Signal(object, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn(), "")
        except Exception as exc:
            self.done.emit(None, str(exc))


class SubtitleSearch(QObject):
    """QML-facing: search for the current file, then download one and load it."""
    changed = Signal()
    loaded = Signal(str)

    def __init__(self, engine, data_dir: Path, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._dir = Path(data_dir) / "subtitles"
        self._results: list[dict] = []
        self._busy = False
        self._status = ""
        self._job = None

    @Property(bool, constant=True)
    def available(self):
        return bool(api_key())

    @Property(list, notify=changed)
    def results(self):
        return self._results

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def status(self):
        return self._status

    def _run(self, fn, on_done):
        if self._job is not None:
            return   # one request at a time
        self._busy = True
        self.changed.emit()
        job = _Job(fn, self)
        job.done.connect(on_done)
        job.finished.connect(job.deleteLater)
        self._job = job
        job.start()

    @Slot(str)
    def find(self, languages):
        """languages: OpenSubtitles codes, e.g. "pt-br,en"."""
        path = self._engine.currentPath
        self._results = []
        self._status = tr("Procurando legendas…")
        self._run(lambda: search(path, languages), self._found)

    def _found(self, res, err):
        self._job = None
        self._busy = False
        if err:
            print("subtitle search:", err)
            self._status = tr("Não foi possível buscar legendas agora")
        else:
            self._results = res
            self._status = "" if res else tr("Nenhuma legenda encontrada para este arquivo")
        self.changed.emit()

    @Slot(int)
    def fetch(self, i):
        if not (0 <= i < len(self._results)):
            return
        r = self._results[i]
        path = self._engine.currentPath
        stem = Path(path).stem if path and "://" not in path else "legenda"
        self._status = tr("Baixando legenda…")
        self._run(lambda: download(r["fileId"], self._dir, stem, r["language"]), self._fetched)

    def _fetched(self, path, err):
        self._job = None
        self._busy = False
        if err or not path:
            print("subtitle download:", err)
            self._status = tr("Não foi possível baixar a legenda")
        else:
            self._status = ""
            self._engine.addSubtitle(path)
            self.loaded.emit(path)
        self.changed.emit()

    def shutdown(self):
        if self._job is not None:
            self._job.wait(3000)
