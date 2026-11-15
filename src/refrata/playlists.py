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


def _unc_host(path: str) -> str:
    r"""The server of a network path (\\server\share\… or file://server/…), "" for local ones."""
    if path.lower().startswith(("file:", "smb:")):
        return (urlparse(path).hostname or "").lower()
    p = path.replace("/", "\\")
    if p.startswith("\\\\") and not p.startswith(("\\\\?\\", "\\\\.\\")):
        return p[2:].split("\\", 1)[0].lower()
    return ""


MAX_PLAYLIST = 16 * 1024 * 1024   # a list of paths is kilobytes; a video renamed .m3u is not read whole


def _read_text(path):
    with open(path, "rb") as f:
        raw = f.read(MAX_PLAYLIST + 1)
    if len(raw) > MAX_PLAYLIST:
        raise OSError("playlist too large")
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
        # a playlist needs no DTD: one that declares entities is the "billion laughs" trick (a few
        # lines that expand into gigabytes while being parsed)
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            return []
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        for el in root.iter():
            if el.tag.endswith("location") and el.text:
                items.append(_resolve(el.text, base))
    # A playlist from the internet can point at \\attacker\share\x.mp3: just checking that the file
    # exists makes Windows sign in to that server and hand it the user's NTLM hash. Network paths
    # are kept only when the playlist itself lives on that same server (a list on a NAS).
    own = _unc_host(os.path.abspath(path))
    safe = [i for i in items if i and (not _unc_host(i) or _unc_host(i) == own)]
    return [i for i in safe if "://" in i or os.path.exists(i)]
