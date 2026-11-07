"""Minimal readers for M3U/M3U8, PLS and XSPF playlists.

libVLC's MediaPlayer does not expand playlist files by itself, so the app
flattens them into a list of local paths / URLs before queueing.
"""
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

PLAYLIST_EXT = {".m3u", ".m3u8", ".pls", ".xspf"}


def is_playlist(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in PLAYLIST_EXT


def _resolve(entry: str, base: str) -> str:
    entry = entry.strip()
    if not entry:
        return ""
    if entry.lower().startswith("file:"):
        p = urlparse(entry)
        path = unquote(p.path)
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        return os.path.normpath(path)
    if "://" in entry:
        return entry
    if not os.path.isabs(entry):
        entry = os.path.join(base, entry)
    return os.path.normpath(entry)


def _read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def read_playlist(path: str) -> list[str]:
    base = os.path.dirname(os.path.abspath(path))
    ext = os.path.splitext(path)[1].lower()
    try:
        text = _read_text(path)
    except OSError:
        return []
    items: list[str] = []
    if ext in (".m3u", ".m3u8"):
        # an HLS stream manifest is itself playable media, not a list of files
        if "#EXT-X-" in text:
            return [path]
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                items.append(_resolve(line, base))
    elif ext == ".pls":
        found = []
        for line in text.splitlines():
            m = re.match(r"\s*File(\d+)\s*=\s*(.+)", line, re.I)
            if m:
                found.append((int(m.group(1)), m.group(2)))
        items = [_resolve(v, base) for _, v in sorted(found)]
    elif ext == ".xspf":
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        for el in root.iter():
            if el.tag.endswith("location") and el.text:
                items.append(_resolve(el.text, base))
    return [i for i in items if i and ("://" in i or os.path.exists(i))]
