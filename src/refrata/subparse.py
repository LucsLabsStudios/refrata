"""Text subtitles read by the app itself: SRT, WebVTT and ASS/SSA, into (start ms, end ms, text).

libVLC 3 shows one subtitle track at a time. The second subtitle (two languages at once) and the
subtitles made by speech recognition (aisubs.py) are drawn by the interface over the picture, so
their text is read here. Styling tags are dropped (ASS overrides, HTML-ish <i>/<b>/<font>); line
breaks are kept. Hostile files only give fewer cues: sizes and counts are bounded.
"""
from __future__ import annotations

import re

MAX_BYTES = 16 << 20
MAX_CUES = 200_000

_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TAGS = re.compile(r"<[^>]{0,200}>")
_ASS_TAGS = re.compile(r"\{[^}]{0,500}\}")


def _ms(m) -> int:
    h, mi, s, frac = m.group(1), m.group(2), m.group(3), m.group(4)
    return ((int(h or 0) * 60 + int(mi)) * 60 + int(s)) * 1000 + int((frac + "00")[:3])


def decode(data: bytes, fallback: str = "cp1252") -> str:
    """UTF-8 (with or without BOM), UTF-16 with BOM, else the fallback encoding."""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode(fallback if fallback and fallback.lower() not in ("auto", "") else "cp1252", "replace")


def _clean(t: str) -> str:
    t = _TAGS.sub("", t)
    t = "\n".join(ln.strip() for ln in t.replace("\r", "").split("\n"))
    return t.strip()


def parse_srt_vtt(text: str) -> list[tuple[int, int, str]]:
    out = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    for b in blocks:
        lines = b.strip("\n").split("\n")
        for i, ln in enumerate(lines[:3]):
            if "-->" in ln:
                a, _, c = ln.partition("-->")
                m1, m2 = _TIME.search(a), _TIME.search(c)
                if m1 and m2:
                    body = _clean("\n".join(lines[i + 1:]))
                    if body:
                        out.append((_ms(m1), _ms(m2), body))
                break
        if len(out) >= MAX_CUES:
            break
    return out


def parse_ass(text: str) -> list[tuple[int, int, str]]:
    out = []
    fmt = None
    in_events = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("["):
            in_events = s.lower() == "[events]"
            continue
        if not in_events:
            continue
        if s.lower().startswith("format:"):
            fmt = [f.strip().lower() for f in s[7:].split(",")]
        elif s.lower().startswith("dialogue:") and fmt:
            parts = s[9:].split(",", len(fmt) - 1)
            if len(parts) < len(fmt):
                continue
            d = dict(zip(fmt, parts))
            m1, m2 = _TIME.search(d.get("start", "")), _TIME.search(d.get("end", ""))
            if not (m1 and m2):
                continue
            body = _ASS_TAGS.sub("", d.get("text", "")).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
            body = _clean(body)
            if body:
                out.append((_ms(m1), _ms(m2), body))
            if len(out) >= MAX_CUES:
                break
    return out


def parse(data: bytes, name: str = "", encoding: str = "auto") -> list[tuple[int, int, str]]:
    """Cues sorted by start; [] for anything that is not a text subtitle."""
    if len(data) > MAX_BYTES:
        data = data[:MAX_BYTES]
    text = decode(data, encoding)
    low = name.lower()
    if low.endswith((".ass", ".ssa")) or "[events]" in text[:20000].lower():
        cues = parse_ass(text)
    else:
        cues = parse_srt_vtt(text)
    cues.sort(key=lambda c: (c[0], c[1]))
    return cues


def read(path: str, encoding: str = "auto") -> list[tuple[int, int, str]]:
    try:
        with open(path, "rb") as f:
            return parse(f.read(MAX_BYTES + 1), path, encoding)
    except OSError:
        return []


def to_srt(cues) -> str:
    def t(ms):
        ms = max(0, int(ms))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "".join(f"{i}\n{t(a)} --> {t(b)}\n{x}\n\n" for i, (a, b, x) in enumerate(cues, 1))
