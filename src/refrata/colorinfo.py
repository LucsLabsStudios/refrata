"""How a video file says its colours are encoded: primaries, transfer curve (SDR, PQ, HLG), matrix,
range, bit depth, and HDR brightness metadata.

libVLC 3 decodes HDR video but does not tell its callers about it (libvlc_video_track_t has no colour
fields), and frames reach us as plain YUV. Without this, HDR10 shows washed out and grey: PQ code
values read as if they were SDR. So the file is read here, a few kilobytes of headers:

- MP4 / MOV / M4V: the video sample entry's `colr` (nclx / nclc), `mdcv`, `clli`, and the SPS and
  SEI inside `hvcC`, or `vpcC`.
- Matroska / WebM: the track's `Colour` element (with `MasteringMetadata`), and an `hvcC` in
  CodecPrivate.
- MPEG-TS / M2TS and raw HEVC: the first megabytes, depacketised, searched for the HEVC sequence
  parameter set (its VUI has the colour description) and the mastering-display / light-level SEI.

Codes follow ITU-T H.273. Everything is bounded (bytes read, boxes, elements) and a broken or
hostile file only yields "unknown"; nothing here raises.
"""
from __future__ import annotations

import os
import struct
import threading

# ITU-T H.273
PRIMARIES = {1: "bt709", 5: "bt601", 6: "bt601", 9: "bt2020", 11: "p3", 12: "p3"}
TRANSFERS = {16: "pq", 18: "hlg"}
MATRICES = {1: "bt709", 5: "bt601", 6: "bt601", 9: "bt2020", 10: "bt2020"}

MAX_TS_BYTES = 6 << 20
MAX_BOXES = 4000


def _empty() -> dict:
    return {"primaries": "", "transfer": "", "matrix": "", "full_range": None, "bit_depth": 0,
            "max_cll": 0, "max_fall": 0, "master_max": 0.0, "master_min": 0.0, "dolby_vision": False}


def _apply_codes(info: dict, prim: int, trc: int, mat: int, full=None):
    if prim in PRIMARIES and not info["primaries"]:
        info["primaries"] = PRIMARIES[prim]
    if trc and not info["transfer"]:
        info["transfer"] = TRANSFERS.get(trc, "sdr" if trc in (1, 4, 5, 6, 7, 8, 13, 14, 15) else "")
    if mat in MATRICES and not info["matrix"]:
        info["matrix"] = MATRICES[mat]
    if full is not None and info["full_range"] is None:
        info["full_range"] = bool(full)


def hdr_kind(info: dict | None) -> str:
    """'pq', 'hlg' or '' (SDR / unknown)."""
    return (info or {}).get("transfer") if (info or {}).get("transfer") in ("pq", "hlg") else ""


def peak_nits(info: dict | None) -> float:
    """The brightest the content gets: MaxCLL when the file states it, else the mastering
    display's peak, else 1000 nits (what most HDR10 is graded for)."""
    info = info or {}
    cll = info.get("max_cll") or 0
    if 100 <= cll <= 10000:
        return float(cll)
    mm = info.get("master_max") or 0
    if 100 <= mm <= 10000:
        return float(mm)
    return 1000.0


# ---------------------------------------------------------------------------- bit reader
class _Bits:
    def __init__(self, data: bytes):
        self.d = data
        self.n = len(data) * 8
        self.p = 0

    def u(self, k: int) -> int:
        if self.p + k > self.n:
            raise EOFError
        v = 0
        for _ in range(k):
            v = (v << 1) | ((self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def skip(self, k: int):
        if self.p + k > self.n:
            raise EOFError
        self.p += k

    def ue(self) -> int:
        z = 0
        while self.u(1) == 0:
            z += 1
            if z > 31:
                raise ValueError("bad exp-golomb")
        return (1 << z) - 1 + self.u(z)

    def se(self) -> int:
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


def _unescape(nal: bytes) -> bytes:
    """Drop the emulation-prevention bytes (00 00 03 -> 00 00)."""
    if b"\x00\x00\x03" not in nal:
        return nal
    out = bytearray()
    zeros = 0
    for b in nal:
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


# ---------------------------------------------------------------------------- HEVC
def _hevc_ptl(r: _Bits, max_sub: int):
    r.skip(88)      # general profile space, tier, idc, compatibility flags, constraint flags
    r.skip(8)       # general_level_idc
    prof, lvl = [], []
    for _ in range(max_sub):
        prof.append(r.u(1))
        lvl.append(r.u(1))
    if max_sub > 0:
        r.skip(2 * (8 - max_sub))
    for i in range(max_sub):
        if prof[i]:
            r.skip(88)
        if lvl[i]:
            r.skip(8)


def _hevc_scaling_list(r: _Bits):
    for size in range(4):
        for _matrix in range(0, 6, 3 if size == 3 else 1):
            if not r.u(1):
                r.ue()
            else:
                n = min(64, 1 << (4 + (size << 1)))
                if size > 1:
                    r.se()
                for _ in range(n):
                    r.se()


def _hevc_st_rps(r: _Bits, idx: int, num_delta: list):
    inter = r.u(1) if idx != 0 else 0
    if inter:
        r.u(1)
        r.ue()
        ref = num_delta[idx - 1]
        count = 0
        for _ in range(ref + 1):
            used = r.u(1)
            use_delta = 1 if used else r.u(1)
            if used or use_delta:
                count += 1
        num_delta.append(count)
    else:
        neg, pos = r.ue(), r.ue()
        if neg > 16 or pos > 16:
            raise ValueError("bad rps")
        for _ in range(neg + pos):
            r.ue()
            r.u(1)
        num_delta.append(neg + pos)


def parse_hevc_sps(nal: bytes, info: dict) -> bool:
    """A HEVC SPS NAL (with its 2-byte header): bit depth and the VUI's colour description."""
    r = _Bits(_unescape(nal)[2:])
    try:
        r.u(4)
        max_sub = r.u(3)
        r.u(1)
        _hevc_ptl(r, max_sub)
        r.ue()
        chroma = r.ue()
        if chroma == 3:
            r.u(1)
        r.ue()
        r.ue()
        if r.u(1):
            for _ in range(4):
                r.ue()
        depth = r.ue() + 8
        r.ue()
        if 8 <= depth <= 16 and not info["bit_depth"]:
            info["bit_depth"] = depth
        log2_poc = r.ue() + 4
        ordering = r.u(1)
        for _ in range(0 if ordering else max_sub, max_sub + 1):
            r.ue(); r.ue(); r.ue()
        for _ in range(6):
            r.ue()
        if r.u(1) and r.u(1):
            _hevc_scaling_list(r)
        r.u(1)
        r.u(1)
        if r.u(1):
            r.skip(8)
            r.ue(); r.ue()
            r.u(1)
        n_rps = r.ue()
        if n_rps > 64:
            return False
        num_delta: list = []
        for i in range(n_rps):
            _hevc_st_rps(r, i, num_delta)
        if r.u(1):
            n_lt = r.ue()
            if n_lt > 32:
                return False
            for _ in range(n_lt):
                r.skip(log2_poc)
                r.u(1)
        r.u(1)
        r.u(1)
        if not r.u(1):       # no VUI
            return True
        if r.u(1):
            if r.u(8) == 255:
                r.skip(32)
        if r.u(1):
            r.u(1)
        if r.u(1):
            r.u(3)
            full = r.u(1)
            if r.u(1):
                _apply_codes(info, r.u(8), r.u(8), r.u(8), full)
            elif info["full_range"] is None:
                info["full_range"] = bool(full)
        return True
    except (EOFError, ValueError, IndexError):
        return False


def parse_hevc_sei(nal: bytes, info: dict):
    """Prefix SEI: mastering display colour volume (137) and content light level (144)."""
    d = _unescape(nal)[2:]
    i = 0
    try:
        while i < len(d) - 2:
            t = 0
            while d[i] == 0xFF:
                t += 255
                i += 1
            t += d[i]
            i += 1
            size = 0
            while d[i] == 0xFF:
                size += 255
                i += 1
            size += d[i]
            i += 1
            body = d[i:i + size]
            if t == 137 and len(body) >= 24 and not info["master_max"]:
                mx, mn = struct.unpack(">II", body[16:24])
                info["master_max"], info["master_min"] = mx / 10000.0, mn / 10000.0
            elif t == 144 and len(body) >= 4 and not info["max_cll"]:
                info["max_cll"], info["max_fall"] = struct.unpack(">HH", body[:4])
            i += size
            if i < len(d) and d[i] == 0x80:   # rbsp trailing bits
                break
    except IndexError:
        pass


def parse_hvcc(data: bytes, info: dict):
    if len(data) < 23:
        return
    if not info["bit_depth"]:
        info["bit_depth"] = (data[17] & 7) + 8      # bitDepthLumaMinus8
    i, n = 23, data[22]
    for _ in range(n):
        if i + 3 > len(data):
            return
        kind = data[i] & 0x3F
        count = struct.unpack(">H", data[i + 1:i + 3])[0]
        i += 3
        for _ in range(count):
            if i + 2 > len(data):
                return
            ln = struct.unpack(">H", data[i:i + 2])[0]
            nal = data[i + 2:i + 2 + ln]
            i += 2 + ln
            if kind == 33:
                parse_hevc_sps(nal, info)
            elif kind in (39, 40):
                parse_hevc_sei(nal, info)


def scan_annexb(data: bytes, info: dict):
    """Start-code delimited HEVC: the SPS and the HDR SEI messages."""
    seen_sps = False
    i = data.find(b"\x00\x00\x01")
    while i >= 0:
        j = data.find(b"\x00\x00\x01", i + 3)
        nal = data[i + 3:j if j >= 0 else len(data)].rstrip(b"\x00")
        if len(nal) > 2:
            kind = (nal[0] >> 1) & 0x3F
            if kind == 33 and not seen_sps and (nal[0] & 0x81) == 0:
                seen_sps = parse_hevc_sps(nal, info)
            elif kind == 39:
                parse_hevc_sei(nal, info)
        if seen_sps and info["max_cll"] and info["master_max"]:
            return
        i = j


# ---------------------------------------------------------------------------- MP4
def _boxes(f, start: int, end: int, budget: list):
    pos = start
    while pos + 8 <= end and budget[0] > 0:
        budget[0] -= 1
        f.seek(pos)
        h = f.read(8)
        if len(h) < 8:
            return
        size, kind = struct.unpack(">I4s", h)
        hdr = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end + 8:
            return
        yield kind, pos + hdr, min(pos + size, end)
        pos += size


def _read(f, a: int, b: int, cap=1 << 20) -> bytes:
    f.seek(a)
    return f.read(max(0, min(b - a, cap)))


def probe_mp4(f, size: int, info: dict) -> bool:
    budget = [MAX_BOXES]
    for kind, a, b in _boxes(f, 0, size, budget):
        if kind != b"moov":
            continue
        for tk, ta, tb in _boxes(f, a, b, budget):
            if tk != b"trak":
                continue
            stsd = None
            video = False
            for mk, ma, mb in _boxes(f, ta, tb, budget):
                if mk != b"mdia":
                    continue
                for k2, a2, b2 in _boxes(f, ma, mb, budget):
                    if k2 == b"hdlr" and _read(f, a2 + 8, a2 + 12) == b"vide":
                        video = True
                    elif k2 == b"minf":
                        for k3, a3, b3 in _boxes(f, a2, b2, budget):
                            if k3 == b"stbl":
                                for k4, a4, b4 in _boxes(f, a3, b3, budget):
                                    if k4 == b"stsd":
                                        stsd = (a4, b4)
            if not video or stsd is None:
                continue
            for entry, ea, eb in _boxes(f, stsd[0] + 8, stsd[1], budget):
                if entry in (b"dvh1", b"dvhe", b"dvav", b"dva1"):
                    info["dolby_vision"] = True
                for ck, ca, cb in _boxes(f, ea + 78, eb, budget):
                    body = _read(f, ca, cb)
                    if ck == b"colr" and len(body) >= 10 and body[:4] in (b"nclx", b"nclc"):
                        p, t, m = struct.unpack(">HHH", body[4:10])
                        full = (body[10] >> 7) & 1 if body[:4] == b"nclx" and len(body) > 10 else None
                        _apply_codes(info, p, t, m, full)
                    elif ck == b"mdcv" and len(body) >= 24:
                        mx, mn = struct.unpack(">II", body[16:24])
                        info["master_max"], info["master_min"] = mx / 10000.0, mn / 10000.0
                    elif ck == b"clli" and len(body) >= 4:
                        info["max_cll"], info["max_fall"] = struct.unpack(">HH", body[:4])
                    elif ck == b"hvcC":
                        parse_hvcc(body, info)
                    elif ck == b"vpcC" and len(body) >= 10:
                        info["bit_depth"] = info["bit_depth"] or body[6] >> 4
                        _apply_codes(info, body[7], body[8], body[9], body[6] & 1)
                    elif ck in (b"dvcC", b"dvvC"):
                        info["dolby_vision"] = True
                return True
    return False


# ---------------------------------------------------------------------------- Matroska
def _ebml_vint(f, keep_marker: bool):
    b = f.read(1)
    if not b:
        raise EOFError
    first = b[0]
    n = 1
    mask = 0x80
    while n <= 8 and not first & mask:
        mask >>= 1
        n += 1
    if n > 8:
        raise ValueError("bad vint")
    rest = f.read(n - 1)
    if len(rest) < n - 1:
        raise EOFError
    v = first if keep_marker else first & (mask - 1)
    for x in rest:
        v = (v << 8) | x
    unknown = not keep_marker and v == (1 << (7 * n)) - 1
    return v, unknown


def _ebml_children(f, start: int, end: int, budget: list):
    pos = start
    while pos < end and budget[0] > 0:
        budget[0] -= 1
        f.seek(pos)
        try:
            eid, _ = _ebml_vint(f, True)
            size, unknown = _ebml_vint(f, False)
        except (EOFError, ValueError):
            return
        body = f.tell()
        if unknown:
            size = end - body
        yield eid, body, min(body + size, end)
        pos = body + size


def _uint(b: bytes) -> int:
    return int.from_bytes(b, "big") if b else 0


def _float(b: bytes) -> float:
    if len(b) == 4:
        return struct.unpack(">f", b)[0]
    if len(b) == 8:
        return struct.unpack(">d", b)[0]
    return 0.0


def probe_mkv(f, size: int, info: dict) -> bool:
    budget = [MAX_BOXES]
    for eid, a, b in _ebml_children(f, 0, size, budget):
        if eid != 0x18538067:          # Segment
            continue
        for sid, sa, sb in _ebml_children(f, a, b, budget):
            if sid == 0x1F43B675:      # Cluster: the tracks come before the first one
                return False
            if sid != 0x1654AE6B:      # Tracks
                continue
            for tid, ta, tb in _ebml_children(f, sa, sb, budget):
                if tid != 0xAE:
                    continue
                kids = {}
                video = None
                for kid, ka, kb in _ebml_children(f, ta, tb, budget):
                    if kid == 0xE0:
                        video = (ka, kb)
                    elif kid in (0x83, 0x86, 0x63A2):
                        kids[kid] = _read(f, ka, kb)
                if _uint(kids.get(0x83, b"")) != 1 or video is None:
                    continue
                codec = kids.get(0x86, b"").decode("ascii", "replace")
                if codec.startswith("V_MPEGH/ISO/HEVC") and kids.get(0x63A2):
                    parse_hvcc(kids[0x63A2], info)
                for vid, va, vb in _ebml_children(f, video[0], video[1], budget):
                    if vid != 0x55B0:  # Colour
                        continue
                    c = {}
                    for cid, ca, cb in _ebml_children(f, va, vb, budget):
                        if cid == 0x55D0:   # MasteringMetadata
                            for mid, ma, mb in _ebml_children(f, ca, cb, budget):
                                if mid == 0x55D9:
                                    info["master_max"] = _float(_read(f, ma, mb))
                                elif mid == 0x55DA:
                                    info["master_min"] = _float(_read(f, ma, mb))
                        else:
                            c[cid] = _uint(_read(f, ca, cb, 8))
                    info["max_cll"] = c.get(0x55BC, info["max_cll"])
                    info["max_fall"] = c.get(0x55BD, info["max_fall"])
                    if c.get(0x55B2):
                        info["bit_depth"] = c[0x55B2]
                    rng = c.get(0x55B9)
                    # Colour wins over the bitstream's VUI: muxers write what the stream says
                    vui = dict(info)
                    info.update(primaries="", transfer="", matrix="", full_range=None)
                    _apply_codes(info, c.get(0x55BB, 0), c.get(0x55BA, 0), c.get(0x55B1, 0),
                                 None if rng in (None, 0) else rng == 2)
                    for k in ("primaries", "transfer", "matrix", "full_range"):
                        if info[k] in ("", None):
                            info[k] = vui[k]
                return True
    return False


# ---------------------------------------------------------------------------- MPEG-TS
def probe_ts(f, info: dict) -> bool:
    data = f.read(MAX_TS_BYTES)
    stride = 0
    for s in (188, 192):
        off = 4 if s == 192 else 0
        if len(data) > s * 4 and all(data[off + k * s] == 0x47 for k in range(4)):
            stride = s
            break
    if not stride:
        return False
    off = 4 if stride == 192 else 0
    streams: dict[int, bytearray] = {}
    for p in range(off, len(data) - 187, stride):
        pkt = data[p:p + 188]
        if pkt[0] != 0x47 or not pkt[3] & 0x10:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid in (0, 0x1FFF):
            continue
        start = 4
        if pkt[3] & 0x20:
            start = 5 + pkt[4]
        if start >= 188:
            continue
        payload = pkt[start:]
        if pkt[1] & 0x40 and payload[:3] == b"\x00\x00\x01" and len(payload) > 9:
            payload = payload[9 + payload[8]:]     # PES header
        streams.setdefault(pid, bytearray()).extend(payload)
    for buf in streams.values():
        if b"\x00\x00\x01\x42" in buf or b"\x00\x00\x01\x40" in buf:   # HEVC SPS / VPS
            scan_annexb(bytes(buf), info)
            return True
    return False


# ---------------------------------------------------------------------------- entry
_cache: dict = {}
_lock = threading.Lock()


def probe(path: str) -> dict | None:
    """The colour description of a local video file, or None when it cannot be read. Cached by
    (path, size, mtime)."""
    if not path or "://" in path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_size, st.st_mtime_ns)
    with _lock:
        if key in _cache:
            return _cache[key]
    info = _empty()
    found = False
    try:
        with open(path, "rb") as f:
            head = f.read(16)
            f.seek(0)
            if head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip"):
                found = probe_mp4(f, st.st_size, info)
            elif head[:4] == b"\x1a\x45\xdf\xa3":
                found = probe_mkv(f, st.st_size, info)
            elif head[:1] == b"\x47" or head[4:5] == b"\x47":
                found = probe_ts(f, info)
            elif head[:4] in (b"\x00\x00\x00\x01", b"\x00\x00\x01\x40") or head[:3] == b"\x00\x00\x01":
                scan_annexb(f.read(2 << 20), info)
                found = True
    except (OSError, EOFError, ValueError, struct.error, IndexError, OverflowError, MemoryError):
        found = False
    result = info if found else None
    with _lock:
        if len(_cache) > 256:
            _cache.clear()
        _cache[key] = result
    return result
