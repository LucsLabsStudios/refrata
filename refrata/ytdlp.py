"""yt-dlp kept current without waiting for a new Refrata release.

Sites like YouTube change often and an old yt-dlp stops opening their links within weeks. So the
app can fetch the newest yt-dlp from PyPI (its official home) on its own:

- only when a web link is opened, at most once a day, and right away when a link fails;
- only with Ajustes › atualizações automáticas on (the same switch as app updates);
- the wheel's SHA-256 must match the one PyPI publishes, and only the `yt_dlp/` package is
  extracted, into <data>/yt-dlp/<version>/;
- a downloaded version is used only when it is newer than the one bundled with the app.

The bundled copy lives inside the PyInstaller archive, whose importer comes first in
sys.meta_path; a finder placed ahead of it serves `yt_dlp` and its submodules from the folder.
"""
from __future__ import annotations

import hashlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import __version__
from .settings import DATA_DIR

ROOT = DATA_DIR / "yt-dlp"
CHECK_EVERY = 24 * 3600        # a routine look, when links are used
RETRY_EVERY = 3600             # after a failed link (private videos fail too: don't ask PyPI every time)
MAX_WHEEL = 40 * 1024 * 1024
UA = f"Refrata/{__version__} (+https://github.com/LucsLabsStudios/refrata)"

allowed = lambda: True         # noqa: E731  — main.py ties it to the auto-update setting
_lock = threading.RLock()
_finder = None
_active = ""                   # version served from ROOT, "" = the bundled one
_last_check = 0.0
_last_forced = 0.0


def _pypi_url() -> str:
    return os.environ.get("REFRATA_PYPI_URL", "https://pypi.org/pypi/yt-dlp/json")


def _vkey(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def bundled_version() -> str:
    try:
        return importlib.metadata.version("yt-dlp")
    except Exception:
        return ""


def current_version() -> str:
    """The newest yt-dlp the app has: downloaded (in use or waiting) or bundled."""
    found = _installed()
    best = found[-1][0] if found else ""
    return best if _vkey(best) > _vkey(bundled_version()) else bundled_version()


class _Finder(importlib.abc.MetaPathFinder):
    def __init__(self, base: Path):
        self.base = str(base)

    def find_spec(self, name, path, target=None):
        if name != "yt_dlp" and not name.startswith("yt_dlp."):
            return None
        return importlib.machinery.PathFinder.find_spec(name, [self.base] if name == "yt_dlp" else path)


def _installed() -> list[tuple[str, Path]]:
    out = []
    if ROOT.is_dir():
        for d in ROOT.iterdir():
            if d.is_dir() and (d / "ok").exists() and (d / "yt_dlp" / "__init__.py").exists():
                out.append((d.name, d))
    return sorted(out, key=lambda t: _vkey(t[0]))


def _purge():
    for name in [n for n in sys.modules if n == "yt_dlp" or n.startswith("yt_dlp.")]:
        del sys.modules[name]


def activate() -> str:
    """Serve the newest downloaded yt-dlp when it beats the bundled one. Returns its version."""
    global _finder, _active
    with _lock:
        found = _installed()
        best = found[-1] if found else None
        if not best or _vkey(best[0]) <= _vkey(bundled_version()):
            return ""
        if _active == best[0]:
            return _active
        if _finder in sys.meta_path:
            sys.meta_path.remove(_finder)
        _finder = _Finder(best[1])
        sys.meta_path.insert(0, _finder)
        _purge()                       # the next `import yt_dlp` comes from the new folder
        _active = best[0]
        return _active


def active() -> str:
    return _active


def reject():
    """The downloaded copy does not load here: drop it and go back to the bundled one."""
    global _finder, _active
    with _lock:
        if _finder in sys.meta_path:
            sys.meta_path.remove(_finder)
        bad, _finder, _active = _active, None, ""
        _purge()
        if bad:
            shutil.rmtree(ROOT / bad, ignore_errors=True)
            print("yt-dlp update: dropped", bad, "(does not load)")


def available() -> bool:
    """Some yt-dlp can be imported (without importing it, nor switching versions)."""
    return bool(_installed()) or "yt_dlp" in sys.modules or importlib.util.find_spec("yt_dlp") is not None


PYPI_HOSTS = ("pypi.org", "files.pythonhosted.org")


def _get(url: str, limit: int, timeout=30) -> bytes:
    # the wheel's address comes from PyPI's answer: only HTTPS, only PyPI's own hosts (the tests
    # point REFRATA_PYPI_URL at a local stand-in)
    parts = urllib.parse.urlsplit(url)
    if not os.environ.get("REFRATA_PYPI_URL") and (parts.scheme != "https" or parts.hostname not in PYPI_HOSTS):
        raise RuntimeError("refused: not an HTTPS address at PyPI")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("download too large")
    return data


def check(force: bool = False) -> bool:
    """Look for a newer yt-dlp on PyPI and install it. True when a newer one is now active."""
    global _last_check, _last_forced
    if not allowed():
        return False
    with _lock:
        now = time.time()
        if force:
            if now - _last_forced < RETRY_EVERY:
                return False
            _last_forced = now
        elif now - _last_check < CHECK_EVERY:
            return False
        _last_check = now
        try:
            meta = json.loads(_get(_pypi_url(), 2 * 1024 * 1024))
            latest = str(meta["info"]["version"])
            if _vkey(latest) <= _vkey(current_version()):
                return False
            wheel = next(f for f in meta["urls"] if f.get("packagetype") == "bdist_wheel"
                         and f.get("filename", "").endswith("-py3-none-any.whl"))
            expected = str(wheel["digests"]["sha256"]).lower()
            data = _get(wheel["url"], MAX_WHEEL, timeout=120)
            if hashlib.sha256(data).hexdigest() != expected:
                raise RuntimeError("SHA-256 mismatch")
            _install(latest, data)
        except Exception as exc:
            print("yt-dlp update:", exc)
            return False
        print("yt-dlp update: installed", latest)
        return True   # websites switches to it before its next lookup (activate)


def check_soon():
    """The routine daily look, off the caller's thread."""
    if allowed() and time.time() - _last_check >= CHECK_EVERY:
        threading.Thread(target=check, daemon=True, name="yt-dlp-update").start()


def _install(version: str, wheel: bytes):
    import io
    if not re.fullmatch(r"[0-9][0-9.]*", version):
        raise RuntimeError("odd version")
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = ROOT / f"{version}.part"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    base = tmp.resolve()
    try:
        with zipfile.ZipFile(io.BytesIO(wheel)) as z:
            members = [i for i in z.infolist() if i.filename.startswith("yt_dlp/") and not i.is_dir()]
            for info in members:   # check every path before writing anything
                if base not in (tmp / info.filename).resolve().parents:
                    raise RuntimeError("bad path in wheel")
            for info in members:
                dest = (tmp / info.filename).resolve()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(info))
        if not (tmp / "yt_dlp" / "__init__.py").exists():
            raise RuntimeError("no yt_dlp package in wheel")
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    (tmp / "ok").write_text(version, encoding="utf8")
    final = ROOT / version
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)
    # keep this one and the one in use; older copies go
    for name, d in _installed():
        if name not in (version, _active):
            shutil.rmtree(d, ignore_errors=True)
