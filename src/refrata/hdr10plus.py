"""HDR10+ (SMPTE ST 2094-40) dynamic metadata: how bright each scene really gets.

HDR10 carries one brightness for the whole film (MaxCLL, or the mastering display's peak), so a
dark scene is tone mapped as if a 1000-nit highlight could appear in it any moment, and comes out
darker and flatter than graded. HDR10+ adds, per frame, the scene's measured maximum (maxscl), its
average and a brightness histogram, plus a tone curve (knee point and Bézier anchors) the colourist
approved for a given target display. libVLC 3 drops all of it, so it is read here from the file:

  - MP4 / MOV: every HEVC sample's prefix SEI (user_data_registered_itu_t_t35, payload type 4)
  - Matroska / WebM: the same SEI inside each block, or the T.35 bytes in BlockAdditions
    (BlockAddID 4, as mkvmerge and FFmpeg write HDR10+)

Only the first ~1.5 KB of each sample is read (the SEI comes before the slices), so a 2-hour film is
a few hundred MB of small reads, done on a background thread; `timeline()` returns what is known so
far. Frames whose metadata equals the previous frame's are folded, so the result is one entry per
scene, in milliseconds of presentation time. Bounded and defensive like colorinfo: a broken file
only gives an empty timeline.
"""
from __future__ import annotations

import bisect
import os
import struct
import threading

from .colorinfo import _Bits, _boxes, _ebml_children, _ebml_vint, _read, _uint, _unescape, MAX_BOXES

# how far into a file to look for the first HDR10+ message before giving up
PROBE_SAMPLES = 48
SAMPLE_HEAD = 1536
MAX_SAMPLES = 2_000_000


# ---------------------------------------------------------------------------- ST 2094-40
def parse_t35(p: bytes) -> dict | None:
    """ITU-T T.35 bytes (country code first) of an HDR10+ message -> the values that matter for
    tone mapping, or None when it is not HDR10+ (or is malformed)."""
    if len(p) < 7 or p[0] != 0xB5:
        return None
    provider, oriented = struct.unpack(">HH", p[1:5])
    if provider != 0x003C or oriented != 0x0001 or p[5] != 4 or p[6] > 1:
        return None
    r = _Bits(p[7:])
    try:
        windows = r.u(2)
        if windows < 1:
            return None
        for _ in range(windows - 1):
            r.skip(16 * 6 + 8 + 16 * 3 + 1)
        target = r.u(27)
        if r.u(1):
            rows, cols = r.u(5), r.u(5)
            r.skip(4 * rows * cols)
        maxscl, avg, pct = None, 0, []
        for w in range(windows):
            m = [r.u(17) for _ in range(3)]
            a = r.u(17)
            n = r.u(4)
            ps = [(r.u(7), r.u(17)) for _ in range(n)]
            r.u(10)       # fraction_bright_pixels
            if w == 0:
                maxscl, avg, pct = m, a, ps
        if r.u(1):
            rows, cols = r.u(5), r.u(5)
            r.skip(4 * rows * cols)
        knee, anchors = None, []
        for w in range(windows):
            if r.u(1):
                kx, ky, n = r.u(12), r.u(12), r.u(4)
                an = [r.u(10) for _ in range(n)]
                if w == 0:
                    knee, anchors = (kx / 4095.0, ky / 4095.0), [a / 1023.0 for a in an]
            if r.u(1):
                r.u(6)
    except (EOFError, ValueError):
        return None
    # maxscl, average and percentiles are linear light where 1.0 = 10000 cd/m², in units of
    # 0.00001: /10 gives nits
    peak = max(maxscl) / 10.0
    # the brightest histogram percentile (99.98% when present) ignores a few hot pixels
    top = max((pc for pc in pct if pc[0] <= 100), default=None)
    out = {
        "peak": peak,
        "p99": (top[1] / 10.0) if top and top[0] >= 98 else peak,
        "avg": avg / 10.0,
        "target": float(target),       # cd/m² the curve below was made for (0 = none)
        "knee": knee,
        "anchors": anchors,
    }
    return out


def _sei_t35(nal: bytes) -> list:
    """All T.35 payloads (type 4) of one prefix SEI NAL (with its 2-byte header)."""
    d = _unescape(nal)[2:]
    out, i = [], 0
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
            if t == 4:
                out.append(bytes(d[i:i + size]))
            i += size
            if i < len(d) and d[i] == 0x80:
                break
    except IndexError:
        pass
    return out


def _from_sample(head: bytes, nal_len: int) -> dict | None:
    """HDR10+ from the first bytes of one length-prefixed HEVC access unit."""
    i = 0
    while i + nal_len < len(head):
        n = int.from_bytes(head[i:i + nal_len], "big")
        i += nal_len
        if n < 2:
            return None
        kind = (head[i] >> 1) & 0x3F
        if kind < 32:          # a slice: the prefix SEI would have come before it
            return None
        if kind == 39:
            for p in _sei_t35(head[i:i + n]):
                m = parse_t35(p)
                if m:
                    return m
        i += n
    return None


# ---------------------------------------------------------------------------- MP4
def _mp4_samples(f, size: int):
    """(composition time ms, offset, size, nal length size) of every sample of the first HEVC
    video track, in decode order; [] when there is none."""
    budget = [MAX_BOXES]
    for kind, a, b in _boxes(f, 0, size, budget):
        if kind != b"moov":
            continue
        for tk, ta, tb in _boxes(f, a, b, budget):
            if tk != b"trak":
                continue
            tables, timescale, video, nal_len = {}, 0, False, 0
            for mk, ma, mb in _boxes(f, ta, tb, budget):
                if mk != b"mdia":
                    continue
                for k2, a2, b2 in _boxes(f, ma, mb, budget):
                    if k2 == b"mdhd":
                        h = _read(f, a2, a2 + 32)
                        timescale = struct.unpack(">I", h[20:24] if h[0] == 1 else h[12:16])[0]
                    elif k2 == b"hdlr" and _read(f, a2 + 8, a2 + 12) == b"vide":
                        video = True
                    elif k2 == b"minf":
                        for k3, a3, b3 in _boxes(f, a2, b2, budget):
                            if k3 != b"stbl":
                                continue
                            for k4, a4, b4 in _boxes(f, a3, b3, budget):
                                if k4 == b"stsd":
                                    for ek, ea, eb in _boxes(f, a4 + 8, b4, budget):
                                        if ek not in (b"hvc1", b"hev1", b"dvh1", b"dvhe"):
                                            continue
                                        for ck, ca, cb in _boxes(f, ea + 78, eb, budget):
                                            if ck == b"hvcC":
                                                body = _read(f, ca, cb, 64)
                                                if len(body) > 21:
                                                    nal_len = (body[21] & 3) + 1
                                elif k4 in (b"stsz", b"stco", b"co64", b"stsc", b"stts", b"ctts"):
                                    tables[k4] = _read(f, a4, b4, 64 << 20)
            if not video:
                continue
            if not nal_len or not timescale or b"stsz" not in tables:
                return []
            return _mp4_table(tables, timescale, nal_len)
    return []


def _mp4_table(t: dict, timescale: int, nal_len: int) -> list:
    sz = t[b"stsz"]
    fixed, count = struct.unpack(">II", sz[4:12])
    count = min(count, MAX_SAMPLES)
    sizes = [fixed] * count if fixed else list(struct.unpack(f">{count}I", sz[12:12 + 4 * count]))
    if b"co64" in t:
        n = struct.unpack(">I", t[b"co64"][4:8])[0]
        chunks = list(struct.unpack(f">{n}Q", t[b"co64"][8:8 + 8 * n]))
    else:
        n = struct.unpack(">I", t[b"stco"][4:8])[0]
        chunks = list(struct.unpack(f">{n}I", t[b"stco"][8:8 + 4 * n]))
    stsc = t[b"stsc"]
    ns = struct.unpack(">I", stsc[4:8])[0]
    runs = [struct.unpack(">III", stsc[8 + 12 * k:20 + 12 * k]) for k in range(ns)]
    offsets = []
    for r, (first, per, _desc) in enumerate(runs):
        last = runs[r + 1][0] - 1 if r + 1 < len(runs) else len(chunks)
        for c in range(first - 1, min(last, len(chunks))):
            pos = chunks[c]
            for _ in range(per):
                k = len(offsets)
                if k >= count:
                    break
                offsets.append(pos)
                pos += sizes[k]
    dts, times = 0, []
    stts = t.get(b"stts", b"")
    for k in range(struct.unpack(">I", stts[4:8])[0] if len(stts) >= 8 else 0):
        n, delta = struct.unpack(">II", stts[8 + 8 * k:16 + 8 * k])
        for _ in range(n):
            times.append(dts)
            dts += delta
    ctts = t.get(b"ctts", b"")
    if len(ctts) >= 8:
        signed = ctts[0] == 1
        j = 0
        for k in range(struct.unpack(">I", ctts[4:8])[0]):
            n, off = struct.unpack(">Ii" if signed else ">II", ctts[8 + 8 * k:16 + 8 * k])
            for _ in range(n):
                if j < len(times):
                    times[j] += off
                j += 1
    n = min(len(offsets), len(times), count)
    t0 = min(times[:n]) if n else 0
    return [((times[k] - t0) * 1000.0 / timescale, offsets[k], sizes[k], nal_len) for k in range(n)]


def _scan_mp4(f, size, stop, out):
    samples = _mp4_samples(f, size)
    for k, (ms, off, n, nal_len) in enumerate(samples):
        if stop.is_set():
            return
        f.seek(off)
        m = _from_sample(f.read(min(n, SAMPLE_HEAD)), nal_len)
        if m:
            out.add(ms, m)
        elif k >= PROBE_SAMPLES and not out.any():
            return


# ---------------------------------------------------------------------------- Matroska
def _scan_mkv(f, size, stop, out):
    budget = [MAX_BOXES * 50]
    for eid, a, b in _ebml_children(f, 0, size, budget):
        if eid != 0x18538067:
            continue
        scale, track, nal_len, blocks = 1_000_000, 0, 4, 0
        for sid, sa, sb in _ebml_children(f, a, b, budget):
            if stop.is_set():
                return
            if sid == 0x1549A966:      # Info: TimecodeScale
                for iid, ia, ib in _ebml_children(f, sa, sb, budget):
                    if iid == 0x2AD7B1:
                        scale = _uint(_read(f, ia, ib, 8)) or scale
            elif sid == 0x1654AE6B:    # Tracks: the HEVC video track
                for tid, ta, tb in _ebml_children(f, sa, sb, budget):
                    if tid != 0xAE:
                        continue
                    kids = {}
                    for kid, ka, kb in _ebml_children(f, ta, tb, budget):
                        if kid in (0xD7, 0x83, 0x86, 0x63A2):
                            kids[kid] = _read(f, ka, kb, 1 << 16)
                    codec = kids.get(0x86, b"").decode("ascii", "replace")
                    if _uint(kids.get(0x83, b"")) == 1 and codec.startswith("V_MPEGH/ISO/HEVC") and not track:
                        track = _uint(kids.get(0xD7, b""))
                        cp = kids.get(0x63A2, b"")
                        if len(cp) > 21:
                            nal_len = (cp[21] & 3) + 1
            elif sid == 0x1F43B675 and track:   # Cluster
                ctime = 0
                for cid, ca, cb in _ebml_children(f, sa, sb, budget):
                    if stop.is_set():
                        return
                    if cid == 0xE7:
                        ctime = _uint(_read(f, ca, cb, 8))
                    elif cid in (0xA3, 0xA0):
                        m, rel = _mkv_block(f, cid, ca, cb, track, nal_len, budget)
                        if rel is None:
                            continue
                        blocks += 1
                        if m:
                            out.add((ctime + rel) * scale / 1e6, m)
                        elif blocks >= PROBE_SAMPLES and not out.any():
                            return
                budget[0] = max(budget[0], 4000)
        return


def _mkv_block(f, cid, a, b, track, nal_len, budget):
    """(HDR10+ or None, relative timecode or None when not our track) of a SimpleBlock or
    BlockGroup."""
    extra = None
    if cid == 0xA0:
        blk = None
        for gid, ga, gb in _ebml_children(f, a, b, budget):
            if gid == 0xA1:
                blk = (ga, gb)
            elif gid == 0x75A1:                  # BlockAdditions > BlockMore
                for mid, ma, mb in _ebml_children(f, ga, gb, budget):
                    if mid != 0xA6:
                        continue
                    add_id, data = 1, b""
                    for xid, xa, xb in _ebml_children(f, ma, mb, budget):
                        if xid == 0xEE:
                            add_id = _uint(_read(f, xa, xb, 8))
                        elif xid == 0xA5:
                            data = _read(f, xa, xb, 4096)
                    if add_id == 4 and data:
                        extra = parse_t35(data)
        if blk is None:
            return None, None
        a, b = blk
    f.seek(a)
    try:
        num, _ = _ebml_vint(f, False)
    except (EOFError, ValueError):
        return None, None
    if num != track:
        return None, None
    hdr = f.read(3)
    if len(hdr) < 3:
        return None, None
    rel = struct.unpack(">h", hdr[:2])[0]
    if extra:
        return extra, rel
    if hdr[2] & 0x06:            # laced: not used for video in practice
        return None, rel
    head = f.read(min(SAMPLE_HEAD, max(0, b - f.tell())))
    return _from_sample(head, nal_len), rel


# ---------------------------------------------------------------------------- timeline
class _Timeline:
    """Scenes in presentation order: a new entry only when the metadata changes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.times: list = []
        self.items: list = []
        self.done = False

    def any(self):
        return bool(self.items)

    def add(self, ms, m):
        with self.lock:
            k = bisect.bisect_right(self.times, ms)
            if k > 0 and self.items[k - 1] == m:
                return
            if k < len(self.items) and self.items[k] == m:
                # frames arrive in decode order: a reordered one starts its scene a bit earlier
                self.times[k] = ms
                return
            self.times.insert(k, ms)
            self.items.insert(k, m)

    def at(self, ms):
        with self.lock:
            if not self.items:
                return None
            k = bisect.bisect_right(self.times, ms) - 1
            # before the first stamped frame (a B-frame reorder, a rounding): the first scene
            return self.items[max(k, 0)]


def _native_reader():
    try:
        from refrata_native import Hdr10PlusReader
        return Hdr10PlusReader
    except Exception:
        return None


class _NativeReader:
    """The same reading in Rust (native/src/hdr10plus.rs): no GIL, so a 2-hour film read in the
    background costs the frames on screen nothing. One dict per scene, the same object every time
    (callers tell scenes apart by identity)."""

    def __init__(self, cls, path):
        self.path = path
        self._r = cls(path)
        self._cache: dict = {}

    @property
    def done(self) -> bool:
        return self._r.done

    def found(self) -> bool:
        return self._r.found()

    def scenes(self) -> int:
        return self._r.scenes()

    def at(self, ms: float):
        i = self._r.index_at(float(ms))
        if i < 0:
            return None
        m = self._r.scene(i)
        if m is None:
            return None
        # indices move while the file is still being read: one object per distinct metadata
        key = (m["peak"], m["p99"], m["avg"], m["target"], m["knee"], tuple(m["anchors"]))
        return self._cache.setdefault(key, m)

    def stop(self):
        self._r.stop()

    def join(self, timeout=None):
        self._r.join(timeout)


class Reader:
    """Reads a file's HDR10+ timeline on a background thread. `at(ms)` gives the scene metadata
    in force at that presentation time (None: no HDR10+, or not reached yet). The Rust reader when
    the native module is there, this module's own reading otherwise."""

    def __new__(cls, path: str, native: bool = True):
        nat = _native_reader() if native and os.environ.get("REFRATA_NATIVE") != "0" else None
        if nat is not None:
            try:
                return _NativeReader(nat, path)
            except Exception:
                pass
        return super().__new__(cls)

    def __init__(self, path: str, native: bool = True):
        self.path = path
        self.tl = _Timeline()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hdr10plus", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            size = os.path.getsize(self.path)
            with open(self.path, "rb") as f:
                head = f.read(16)
                f.seek(0)
                if head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip"):
                    _scan_mp4(f, size, self._stop, self.tl)
                elif head[:4] == b"\x1a\x45\xdf\xa3":
                    _scan_mkv(f, size, self._stop, self.tl)
        except (OSError, EOFError, ValueError, struct.error, IndexError, OverflowError, MemoryError):
            pass
        finally:
            self.tl.done = True

    @property
    def done(self) -> bool:
        return self.tl.done

    def found(self) -> bool:
        return self.tl.any()

    def at(self, ms: float):
        return self.tl.at(ms)

    def scenes(self) -> int:
        return len(self.tl.items)

    def stop(self):
        self._stop.set()

    def join(self, timeout=None):
        self._thread.join(timeout)
