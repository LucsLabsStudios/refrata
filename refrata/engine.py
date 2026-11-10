"""libVLC playback engine exposed to QML.

Video is decoded by libVLC into a CPU buffer (vmem callbacks) and pushed to one or
more QVideoSink objects, so the UI can draw anything on top of the video (no
native-window "airspace" problems) and show live previews in several places.
"""
from __future__ import annotations

import ctypes
import os
import random
import sys
import threading
import time
from pathlib import Path

import vlc
from PySide6.QtCore import (Property, QAbstractListModel, QModelIndex, QObject, QSize, Qt, QTimer, QUrl,
                            Signal, Slot)
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat, QVideoSink

from . import websites
from .media import is_audio_path, nice_title
from .playlists import is_playlist, read_playlist
from .i18n import N_, tr

# ---- raw libVLC video callbacks (python-vlc declares chroma as c_char_p, which
# cannot be written to, so we declare our own prototypes) ----------------------
SETUP_CB = ctypes.CFUNCTYPE(
    ctypes.c_uint,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_char),
    ctypes.POINTER(ctypes.c_uint),
    ctypes.POINTER(ctypes.c_uint),
    ctypes.POINTER(ctypes.c_uint),
    ctypes.POINTER(ctypes.c_uint),
)
CLEANUP_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
LOCK_CB = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
UNLOCK_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
DISPLAY_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)

_set_format_cbs = vlc.dll.libvlc_video_set_format_callbacks
_set_format_cbs.argtypes = [ctypes.c_void_p, SETUP_CB, CLEANUP_CB]
_set_format_cbs.restype = None
_set_cbs = vlc.dll.libvlc_video_set_callbacks
_set_cbs.argtypes = [ctypes.c_void_p, LOCK_CB, UNLOCK_CB, DISPLAY_CB, ctypes.c_void_p]
_set_cbs.restype = None


def _fourcc(v: int) -> str:
    s = "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4))
    return s.strip("\x00 ").strip()


class _Track(ctypes.Structure):
    """libvlc_media_track_t as C lays it out. python-vlc's MediaTrack spells the union as three
    separate pointers, which shifts bitrate/language/description to the wrong offsets."""
    _fields_ = [("codec", ctypes.c_uint32), ("original_fourcc", ctypes.c_uint32), ("id", ctypes.c_int),
                ("type", ctypes.c_int), ("profile", ctypes.c_int), ("level", ctypes.c_int),
                ("detail", ctypes.c_void_p), ("bitrate", ctypes.c_uint),
                ("language", ctypes.c_char_p), ("description", ctypes.c_char_p)]


def media_tracks(media) -> list[dict]:
    """The media's elementary streams as plain dicts. python-vlc's tracks_get() never frees the
    array libVLC allocates; this reads it and releases it."""
    # python-vlc declares the out-parameter one pointer level short (MediaTrack** for C's ***):
    # the variable typed POINTER(MediaTrack) really receives the MediaTrack** array
    pp = ctypes.POINTER(vlc.MediaTrack)()
    n = vlc.libvlc_media_tracks_get(media, ctypes.byref(pp))
    arr = ctypes.cast(pp, ctypes.POINTER(ctypes.POINTER(vlc.MediaTrack)))
    tracks = ctypes.cast(pp, ctypes.POINTER(ctypes.POINTER(_Track)))
    txt = lambda b: b.decode("utf8", "replace") if b else ""
    out = []
    try:
        for i in range(n):
            t = tracks[i].contents
            d = {"type": {0: "audio", 1: "video", 2: "text"}.get(t.type, "unknown"),
                 "id": t.id, "codec": _fourcc(t.codec), "language": txt(t.language),
                 "description": txt(t.description), "bitrate": t.bitrate}
            if not t.detail:
                pass
            elif d["type"] == "video":
                v = ctypes.cast(t.detail, ctypes.POINTER(vlc.VideoTrack)).contents
                d.update(width=int(v.width), height=int(v.height), sar_num=int(v.sar_num), sar_den=int(v.sar_den),
                         fps=(v.frame_rate_num / v.frame_rate_den) if v.frame_rate_den else 0.0)
            elif d["type"] == "audio":
                a = ctypes.cast(t.detail, ctypes.POINTER(vlc.AudioTrack)).contents
                d.update(channels=int(a.channels), rate=int(a.rate))
            out.append(d)
    finally:
        if n > 0:
            vlc.libvlc_media_tracks_release(arr, n)
    return out


class FrameRenderer:
    """Receives frames from a libVLC media player into a ctypes buffer."""

    def __init__(self, player: vlc.MediaPlayer, max_width: int, on_frame):
        self._player = player
        self.width = 0
        self.height = 0
        self._buf = None
        self._max_width = max_width
        self._on_frame = on_frame
        self._lock = threading.Lock()
        # keep references so they are not garbage collected
        self._setup = SETUP_CB(self._cb_setup)
        self._cleanup = CLEANUP_CB(lambda opaque: None)
        self._lockcb = LOCK_CB(self._cb_lock)
        self._unlock = UNLOCK_CB(lambda opaque, pic, planes: None)
        self._display = DISPLAY_CB(self._cb_display)
        _set_cbs(player._as_parameter_, self._lockcb, self._unlock, self._display, None)
        _set_format_cbs(player._as_parameter_, self._setup, self._cleanup)

    def _visible_size(self):
        """Visible size + SAR from the ES info (vmem passes the padded buffer size)."""
        media = None
        try:
            media = self._player.get_media()   # a new reference: released below
            if media is None:
                return 0, 0, 0, 0
            for t in media_tracks(media):
                if t["type"] == "video" and t["width"] and t["height"]:
                    return t["width"], t["height"], t["sar_num"], t["sar_den"]
        except Exception:
            pass
        finally:
            if media is not None:
                media.release()
        return 0, 0, 0, 0

    def _cb_setup(self, opaque, chroma, width, height, pitches, lines):
        w, h = width[0], height[0]
        vw, vh, sn, sd = self._visible_size()
        if vw and vh and vw <= w + 64 and vh <= h + 64:
            w, h = vw, vh
        if sn > 0 and sd > 0 and sn != sd:
            w = int(round(w * sn / sd))
        w, h = max(2, w & ~1), max(2, h & ~1)
        if w > self._max_width:
            h = max(2, int(h * self._max_width / w)) & ~1
            w = self._max_width
        ctypes.memmove(chroma, b"RV32", 4)
        width[0], height[0] = w, h
        pitches[0] = w * 4
        lines[0] = h
        with self._lock:
            self.width, self.height = w, h
            self._buf = ctypes.create_string_buffer(w * h * 4 + 64)
        return 1

    def _cb_lock(self, opaque, planes):
        planes[0] = ctypes.addressof(self._buf)
        return None

    def _cb_display(self, opaque, picture):
        try:
            self._on_frame(self)
        except Exception as exc:  # never let exceptions cross into libVLC
            print("frame error:", exc)

    def to_video_frame(self) -> QVideoFrame | None:
        w, h = self.width, self.height
        if not w or not h:
            return None
        fmt = QVideoFrameFormat(QSize(w, h), QVideoFrameFormat.PixelFormat.Format_BGRX8888)
        frame = QVideoFrame(fmt)
        if not frame.map(QVideoFrame.MapMode.WriteOnly):
            return None
        bpl = frame.bytesPerLine(0)
        mv = frame.bits(0)
        holder = ctypes.c_char.from_buffer(mv)
        dst = ctypes.addressof(holder)
        src = ctypes.addressof(self._buf)
        row = w * 4
        if bpl == row:
            ctypes.memmove(dst, src, row * h)
        else:
            for y in range(h):
                ctypes.memmove(dst + y * bpl, src + y * row, row)
        del holder, mv
        frame.unmap()
        return frame

    def to_image(self) -> QImage | None:
        w, h = self.width, self.height
        if not w or not h:
            return None
        raw = ctypes.string_at(self._buf, w * h * 4)
        return QImage(raw, w, h, w * 4, QImage.Format.Format_RGB32).copy()


class QueueModel(QAbstractListModel):
    """The play queue for QML views, with incremental change signals: a queue of thousands of
    items must not be rebuilt (and re-converted to JavaScript) on every change. One role, so
    delegates keep using `modelData.path`, `modelData.title`..."""

    countChanged = Signal()
    ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[dict] = []

    def roleNames(self):
        return {self.ROLE: b"modelData"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == self.ROLE and 0 <= index.row() < len(self.items):
            return self.items[index.row()]
        return None

    @Property(int, notify=countChanged)
    def count(self):
        return len(self.items)

    def reset(self, items):
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()
        self.countChanged.emit()

    def insert(self, pos, entries):
        if not entries:
            return
        self.beginInsertRows(QModelIndex(), pos, pos + len(entries) - 1)
        self.items[pos:pos] = entries
        self.endInsertRows()
        self.countChanged.emit()

    def remove(self, i):
        self.beginRemoveRows(QModelIndex(), i, i)
        del self.items[i]
        self.endRemoveRows()
        self.countChanged.emit()

    def touch(self, i):
        ix = self.index(i)
        self.dataChanged.emit(ix, ix, [self.ROLE])

    def move(self, src, dst):
        # Qt's destination row is "before which row", in the list before the move
        self.beginMoveRows(QModelIndex(), src, src, QModelIndex(), dst + 1 if dst > src else dst)
        self.items.insert(dst, self.items.pop(src))
        self.endMoveRows()


class DirectRenderer:
    """libVLC decodes straight into the memory of a QVideoFrame, in a YUV layout (1.5 bytes per
    pixel, vs 4 for RGB), at the video's own size up to 4K; Qt's shader turns it into RGB on the GPU.

    The layout is the decoder's own when Qt can show it: software decoders give I420 (three
    planes), and asking for NV12 made libVLC repack every picture on the CPU (its i420_nv12
    filter, ~750 MB/s at 4K60). Anything else (10-bit, 4:2:2, hardware surfaces) goes to NV12.

    lock (decoder thread): a fresh frame is mapped and its planes handed to libVLC.
    display: the frame is unmapped and passed on (on_frame may drop it if the UI is busy).
    """

    MAX_W, MAX_H = 3840, 2160

    def __init__(self, player: vlc.MediaPlayer, on_frame):
        self._player = player
        self._on_frame = on_frame
        self.width = self.height = 0
        self._fmt: QVideoFrameFormat | None = None
        self._planes = 2
        self.chroma = ""          # what libVLC writes: "I420" or "NV12"
        self._pending: dict[int, QVideoFrame] = {}
        self._lock = threading.Lock()
        self._next = 1
        self._setup = SETUP_CB(self._cb_setup)
        self._cleanup = CLEANUP_CB(lambda opaque: None)
        self._lockcb = LOCK_CB(self._cb_lock)
        self._unlock = UNLOCK_CB(self._cb_unlock)
        self._display = DISPLAY_CB(self._cb_display)
        _set_cbs(player._as_parameter_, self._lockcb, self._unlock, self._display, None)
        _set_format_cbs(player._as_parameter_, self._setup, self._cleanup)

    _visible_size = FrameRenderer._visible_size

    def _cb_setup(self, opaque, chroma, width, height, pitches, lines):
        try:
            w, h = width[0], height[0]
            vw, vh, sn, sd = self._visible_size()
            if vw and vh and vw <= w + 64 and vh <= h + 64:
                w, h = vw, vh
            if sn > 0 and sd > 0 and sn != sd:
                w = int(round(w * sn / sd))
            if w > self.MAX_W or h > self.MAX_H:
                k = min(self.MAX_W / w, self.MAX_H / h)
                w, h = int(w * k), int(h * k)
            w, h = max(2, w & ~1), max(2, h & ~1)
            src = bytes(chroma[0:4])
            three = src in (b"I420", b"J420")   # J420: the same planes, full-range levels
            fmt = QVideoFrameFormat(QSize(w, h), QVideoFrameFormat.PixelFormat.Format_YUV420P if three
                                    else QVideoFrameFormat.PixelFormat.Format_NV12)
            # HD and up is BT.709, SD is BT.601 (what the encoders write when the stream says nothing)
            fmt.setColorSpace(QVideoFrameFormat.ColorSpace.ColorSpace_BT709 if h >= 720
                              else QVideoFrameFormat.ColorSpace.ColorSpace_BT601)
            fmt.setColorRange(QVideoFrameFormat.ColorRange.ColorRange_Full if src == b"J420"
                              else QVideoFrameFormat.ColorRange.ColorRange_Video)
            # libVLC writes with the pitches it is given: use the ones Qt allocates
            probe = QVideoFrame(fmt)
            if not probe.map(QVideoFrame.MapMode.WriteOnly):
                return 0
            n = 3 if three else 2
            bpl = [probe.bytesPerLine(i) for i in range(n)]
            probe.unmap()
            ctypes.memmove(chroma, src if three else b"NV12", 4)
            width[0], height[0] = w, h
            for i in range(n):
                pitches[i] = bpl[i]
                lines[i] = h if i == 0 else h // 2
            with self._lock:
                self.width, self.height, self._fmt, self._planes = w, h, fmt, n
                self.chroma = (src if three else b"NV12").decode()
                self._pending.clear()
            return 1
        except Exception as exc:
            print("video setup error:", exc)
            return 0

    def set_visible(self, w, h):
        """Show only the top-left w×h of the decoded picture (decoders pad to their block size;
        when libVLC did not know the visible size at setup, the padding rows would show)."""
        from PySide6.QtCore import QRect
        with self._lock:
            if self._fmt is not None and (w, h) != (self.width, self.height):
                fmt = QVideoFrameFormat(self._fmt)
                fmt.setViewport(QRect(0, 0, w, h))
                self._fmt = fmt
                self.width, self.height = w, h

    def _cb_lock(self, opaque, planes):
        with self._lock:
            fmt, n = self._fmt, self._planes
            key = self._next
            self._next += 1
        frame = QVideoFrame(fmt)
        if not frame.map(QVideoFrame.MapMode.WriteOnly):
            return None
        for i in range(n):
            planes[i] = ctypes.addressof(ctypes.c_char.from_buffer(frame.bits(i)))
        with self._lock:
            self._pending[key] = frame
            # late pictures are unlocked but never displayed: forget the oldest ones
            while len(self._pending) > 12:
                self._pending.pop(next(iter(self._pending)))
        return key   # libVLC hands this "picture" id back to unlock/display

    def _cb_unlock(self, opaque, picture, planes):
        with self._lock:
            frame = self._pending.get(picture)
        if frame is not None and frame.isMapped():
            frame.unmap()

    def _cb_display(self, opaque, picture):
        with self._lock:
            frame = self._pending.pop(picture, None)
        if frame is None:
            return
        try:
            self._on_frame(frame)
        except Exception as exc:  # never let exceptions cross into libVLC
            print("frame error:", exc)


class NativeRenderer(DirectRenderer):
    """DirectRenderer's frames, with the per-picture work in Rust (native/, module refrata_native).

    libVLC called lock / unlock / display in Python for every picture, from its decoder thread:
    each call waited for the GIL, so a busy interpreter could hold up decoding. Here those three
    callbacks are Rust functions working on a pool of frames this class prepares. The UI thread
    hears about finished pictures through a socket (QSocketNotifier), shows them and refills the
    pool. The format callback (once per file) stays in Python.
    """

    POOL = 6               # mapped frames libVLC can decode into
    RECLAIM_MS = 3000      # a picture unlocked this long ago and never shown was dropped as late

    def __init__(self, player: vlc.MediaPlayer, on_frame):
        import itertools
        import socket
        from PySide6.QtCore import QSocketNotifier
        from refrata_native import FramePool
        self._player = player
        self._on_frame = on_frame
        self.width = self.height = 0
        self._fmt: QVideoFrameFormat | None = None
        self._planes = 2
        self.chroma = ""
        self._lock = threading.Lock()
        self._frames: dict[int, QVideoFrame] = {}   # id -> mapped frame the pool knows
        self._pending: dict = {}                       # (DirectRenderer's setup clears it)
        self._ids = itertools.count(1)
        self._rd, self._wr = socket.socketpair()
        self._rd.setblocking(False)
        self._pool = FramePool(self._wr.fileno())
        self._notifier = QSocketNotifier(self._rd.fileno(), QSocketNotifier.Type.Read)
        self._notifier.activated.connect(self._on_ready)
        lock, unlock, display, opaque = self._pool.callbacks()
        # keep the ctypes wrappers alive as long as the player can call them
        self._lockcb, self._unlock, self._display = LOCK_CB(lock), UNLOCK_CB(unlock), DISPLAY_CB(display)
        self._setup = SETUP_CB(self._cb_native_setup)
        self._cleanup = CLEANUP_CB(lambda opaque: None)
        _set_cbs(player._as_parameter_, self._lockcb, self._unlock, self._display, ctypes.c_void_p(opaque))
        _set_format_cbs(player._as_parameter_, self._setup, self._cleanup)

    def _new_frame(self):
        """A mapped frame of the current format, given to the pool (call with self._lock held)."""
        if self._fmt is None:
            return
        frame = QVideoFrame(self._fmt)
        if not frame.map(QVideoFrame.MapMode.WriteOnly):
            return
        fid = next(self._ids)
        planes = [ctypes.addressof(ctypes.c_char.from_buffer(frame.bits(i))) for i in range(self._planes)]
        self._frames[fid] = frame
        self._pool.give(fid, planes)

    def _cb_native_setup(self, opaque, chroma, width, height, pitches, lines):
        ok = DirectRenderer._cb_setup(self, opaque, chroma, width, height, pitches, lines)
        if not ok:
            return ok
        try:
            sizes = [int(pitches[i]) * int(lines[i]) for i in range(self._planes)]
            with self._lock:
                for fid in self._pool.configure(sizes):   # frames of the old format
                    self._frames.pop(fid, None)
                for _ in range(self.POOL):
                    self._new_frame()
            return 1
        except Exception as exc:
            print("native video setup error:", exc)
            return 0

    def set_visible(self, w, h):
        DirectRenderer.set_visible(self, w, h)   # new frames get the viewport; the pool refills with them

    def _on_ready(self):
        try:
            while self._rd.recv(4096):
                pass
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            return
        with self._lock:
            done = [(fid, self._frames.pop(fid, None)) for fid in self._pool.take()]
            lost = self._pool.reclaim(self.RECLAIM_MS)
            for fid in lost:
                self._frames.pop(fid, None)
            for _ in range(len(done) + len(lost)):
                self._new_frame()
        for _fid, frame in done:
            if frame is None:
                continue
            frame.unmap()
            try:
                self._on_frame(frame)
            except Exception as exc:
                print("frame error:", exc)

    def stats(self):
        return self._pool.stats()


def make_renderer(player, on_frame):
    """Python's frame path, or the Rust one with REFRATA_NATIVE=1 (experimental: the UI thread still
    needs Python for every frame, so it does not beat the Python path yet; it becomes useful once
    frames go from Rust straight to Qt, see docs/ROTEIRO-DESEMPENHO-E-PLATAFORMAS.md)."""
    if os.environ.get("REFRATA_NATIVE") == "1":
        try:
            import refrata_native  # noqa: F401
            return NativeRenderer(player, on_frame)
        except ImportError:
            pass
        except Exception as exc:
            print("native frame path unavailable:", exc)
    return DirectRenderer(player, on_frame)


class Engine(QObject):
    """The player: libVLC media player + playlist, exposed to QML."""

    stateChanged = Signal()
    timeChanged = Signal()
    loopChanged = Signal()
    mediaChanged = Signal()
    resolvingChanged = Signal()
    queueChanged = Signal()
    currentIndexChanged = Signal()
    tracksChanged = Signal()
    volumeChanged = Signal()
    rateChanged = Signal()
    modeChanged = Signal()
    eqChanged = Signal()
    adjustChanged = Signal()
    videoSizeChanged = Signal()
    message = Signal(str)
    _frameReady = Signal(object, int)
    _endReached = Signal(int)   # the media generation that ended
    _errorReached = Signal()

    @property
    def _queue(self) -> list[dict]:
        return self._qmodel.items

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._pending_lock = threading.Lock()
        self._pending_busy = False
        self._dropped = 0
        self._sinks: list[QVideoSink] = []
        self._last_frame: QVideoFrame | None = None
        self._frames_seen = 0
        self._media_gen = 0   # bumped on every item change; frames carry the value they were decoded under
        self._visible_checked = False
        self._frameReady.connect(self._deliver_frame, Qt.ConnectionType.QueuedConnection)
        self._endReached.connect(self._on_end, Qt.ConnectionType.QueuedConnection)
        self._errorReached.connect(self._on_error, Qt.ConnectionType.QueuedConnection)
        self._chapters: list[dict] = []
        self._chapter = -1
        self._ext_subs: dict[str, list[str]] = {}   # file → subtitle files added by hand
        self._rd = None                              # cast device discoverer (when looking)
        self._renderers: list[dict] = []
        self._cast_ptr = None
        self._rendererEvent.connect(self._on_renderer, Qt.ConnectionType.QueuedConnection)
        self._resolve_token = None
        self._resolving = False
        self._switching = 0                  # cast switches running on a worker thread
        self._switch_lock = threading.Lock()
        self._switchDone.connect(self._on_switch_done, Qt.ConnectionType.QueuedConnection)
        self._resolved.connect(self._on_resolved, Qt.ConnectionType.QueuedConnection)
        self._deinterlace = "auto"
        # live scrubbing: while the seek bar is dragged the picture follows it. A seek libVLC is
        # still decoding delays everything queued behind it (a Pause pressed right after the drop
        # waited seconds), so the next point goes only once the last one is on screen
        self._scrub_target = None
        self._scrub_sent = None
        self._scrub_frames = 0
        self._scrub_at = 0.0
        self._want_pause_at = 0.0            # Pause pressed, libVLC not paused yet (see _tick)
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setInterval(self.SCRUB_MS)
        self._scrub_timer.timeout.connect(self._scrub_step)
        self._devices: list[dict] = []
        self._vlc = None
        self._make_player()
        # Ajustes › Decodificação por hardware applies right away (a fresh libVLC instance)
        settings.changed.connect(lambda k: k == "hwdec" and self._instance_args() != self._vlc_args and self._rebuild())
        self._consecutive_errors = 0
        # the outputs list, ready before anyone opens Tracks or Ajustes > Som (looked up off the UI thread)
        QTimer.singleShot(2500, self._enum_devices_async)

        self._qmodel = QueueModel(self)
        self._qrev = 0
        for sig in (self._qmodel.modelReset, self._qmodel.rowsInserted, self._qmodel.rowsRemoved,
                    self._qmodel.rowsMoved):
            sig.connect(self._bump_queue)
        self._index = -1
        self._shuffle = False
        self._repeat = 0  # 0 off, 1 all, 2 one
        self._state = "idle"
        self._time = 0
        self._length = 0
        self._meta = {"title": "", "artist": "", "album": "", "artwork": ""}
        self._audio_tracks: list[dict] = []
        self._sub_tracks: list[dict] = []
        self._tracks_polled = 0
        self._volume = int(settings.value("volume", 80))
        self._muted = False
        self._rate = 1.0
        self._sleep_deadline = 0.0
        self._open_deadline = 0.0
        self._expected, self._expected_until = None, 0.0
        self._start_at = 0
        self._pause_when_playing = False   # a rebuild while paused resumes paused
        self._error_handled = False
        self._loop_a, self._loop_b = -1, -1   # A-B repeat (ms), -1 = unset
        self.audio_resume_min = 20 * 60_000   # audio shorter than this never resumes mid-way
        self.lastSnapshot = ""
        self._adjust = {"brightness": 1.0, "contrast": 1.0, "saturation": 1.0, "hue": 0.0, "gamma": 1.0}

        self._eq = vlc.AudioEqualizer()
        self._eq_enabled = False
        self._eq_preset = -1
        self._eq_bands = [0.0] * vlc.libvlc_audio_equalizer_get_band_count()
        self._eq_preamp = 0.0

        self._audio_off = False
        self._first_video_frame = True   # for REFRATA_TRACE (refrata/trace.py)
        self._hw_item = ""            # path decoding on the GPU right now
        self._spu_wanted = None       # subtitle to select once a software reopen is up
        self._sw_paths: set[str] = set()   # items that showed subtitles: software from then on
        self._audio_dirty = True
        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_FAST)
        self._poll.timeout.connect(self._tick)
        self._poll.start()

    # ------------------------------------------------------------ libVLC setup
    POLL_FAST, POLL_IDLE = 120, 1000   # ms between libVLC state checks (see _pace)
    SCRUB_MS = 30                      # how often a drag checks whether libVLC is ready for the next point
    SCRUB_WAIT = 0.3                   # ...and the longest it waits for the last point's picture
    SUB_SIZES = {"small": 75, "normal": 100, "large": 135, "huge": 175}
    SUB_COLORS = {"white": 0xFFFFFF, "yellow": 0xFFE14D}

    def _instance_args(self) -> list[str]:
        """Options libVLC only reads when an instance is created (audio filters, text renderer,
        subtitle decoder). Changing any of them rebuilds the player (see _rebuild)."""
        s = self._settings
        # --verbose=-1 also silences ffmpeg's own stderr chatter (e.g. swscaler notices)
        args = ["--quiet", "--verbose=-1", "--no-video-title-show", "--no-snapshot-preview", "--no-osd"]
        if os.environ.get("REFRATA_VLC_VERBOSE"):   # diagnosis: libVLC's whole log on stderr
            args[:2] = ["--verbose=2"]
        if not s.value("hwdec", True):
            args.append("--avcodec-hw=none")
        if s.value("normalize", False):
            # evens out loud and quiet passages (dialogue vs explosions)
            args += ["--audio-filter=normvol", "--norm-buff-size=20", "--norm-max-level=1.5"]
        args.append(f"--sub-text-scale={self.SUB_SIZES.get(s.value('subSize', 'normal'), 100)}")
        args.append(f"--freetype-color={self.SUB_COLORS.get(s.value('subColor', 'white'), 0xFFFFFF)}")
        bg = s.value("subBackground", "outline")
        if bg == "box":
            args += ["--freetype-background-opacity=170", "--freetype-background-color=0",
                     "--freetype-outline-thickness=0"]
        elif bg == "none":
            args += ["--freetype-outline-thickness=0", "--freetype-shadow-opacity=0"]
        enc = s.value("subEncoding", "")
        if enc:
            args.append(f"--subsdec-encoding={enc}")
        return args

    def _make_player(self):
        old = self._vlc
        # cast discovery and devices belong to the old instance: look again on the new one
        was_discovering = getattr(self, "_rd", None) is not None
        if old is not None:
            self._stop_cast_discovery()
            self._cast_ptr = None
        self._vlc_args = self._instance_args()
        self._vlc = vlc.Instance(self._vlc_args)
        if self._vlc is None:   # an option this libVLC build does not know: play without extras
            print("libVLC refused", self._vlc_args)
            self._vlc = vlc.Instance(["--quiet", "--verbose=-1", "--no-video-title-show", "--no-osd"])
        self._mp: vlc.MediaPlayer = self._vlc.media_player_new()
        self._renderer = make_renderer(self._mp, self._on_vlc_frame)
        em = self._mp.event_manager()
        # stamped with the item it belongs to: an end that lands just as the next item starts (a
        # video finishing as the person presses Next) must not end the new one too
        em.event_attach(vlc.EventType.MediaPlayerEndReached, lambda e: self._endReached.emit(self._media_gen))
        em.event_attach(vlc.EventType.MediaPlayerEncounteredError, lambda e: self._errorReached.emit())
        dev = self._settings.value("audioDevice", "")
        if dev:
            self._mp.audio_output_device_set(None, dev)
        if old is not None:
            old.release()
            if was_discovering:
                self.startCastDiscovery()
            self.renderersChanged.emit()

    def _rebuild(self):
        """New libVLC instance with the current options; what was playing carries on from the
        same point (paused stays paused)."""
        idx, t, st = self._index, self._time, self._state
        try:
            spu = self._mp.video_get_spu()
        except OSError:
            spu = -1
        self._poll.stop()
        try:
            self._mp.stop()
            self._mp.release()
        except OSError as exc:
            print("libVLC stop on rebuild:", exc)
        self._make_player()
        self._poll.start()
        if self._deinterlace != "auto":
            self._apply_deinterlace()
        if 0 <= idx < len(self._queue) and st in ("playing", "paused", "loading"):
            self._start_at = t
            self._play_index(idx)
            if st == "paused":
                self._pause_when_playing = True
            if spu not in (-1, 0):
                QTimer.singleShot(1200, self._restore_subtitle)

    def _restore_subtitle(self):
        """After a rebuild: subtitles were on, so turn them on again (the hand-added file comes
        back as the last subtitle track)."""
        if self._mp.video_get_spu() in (-1, 0):
            self._refresh_tracks()
            tracks = [t["id"] for t in self._sub_tracks if t["id"] != -1]
            if tracks:
                self._mp.video_set_spu(tracks[-1])
                self.tracksChanged.emit()

    # ------------------------------------------------------------ chapters
    chaptersChanged = Signal()

    class _Chapter(ctypes.Structure):
        _fields_ = [("time", ctypes.c_int64), ("duration", ctypes.c_int64), ("name", ctypes.c_char_p)]

    def _read_chapters(self) -> list[dict]:
        f = vlc.dll.libvlc_media_player_get_full_chapter_descriptions
        f.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(self._Chapter)))]
        f.restype = ctypes.c_int
        rel = vlc.dll.libvlc_chapter_descriptions_release
        rel.argtypes = [ctypes.POINTER(ctypes.POINTER(self._Chapter)), ctypes.c_uint]
        rel.restype = None
        pp = ctypes.POINTER(ctypes.POINTER(self._Chapter))()
        n = f(self._mp._as_parameter_, -1, ctypes.byref(pp))
        out = []
        if n > 0:
            try:
                for i in range(n):
                    c = pp[i].contents
                    name = c.name.decode("utf8", "replace") if c.name else ""
                    out.append({"time": int(c.time), "duration": int(c.duration),
                                "name": name or tr("Capítulo %1").replace("%1", str(i + 1))})
            finally:
                rel(pp, n)
        return out

    @Property(list, notify=chaptersChanged)
    def chapters(self):
        return self._chapters

    @Property(int, notify=chaptersChanged)
    def chapter(self):
        return self._chapter

    @Slot(int)
    def setChapter(self, i):
        if 0 <= i < len(self._chapters):
            self.seek(self._chapters[i]["time"])

    @Slot()
    def nextChapter(self):
        if self._chapters and self._chapter + 1 < len(self._chapters):
            self.setChapter(self._chapter + 1)

    @Slot()
    def previousChapter(self):
        if self._chapters:
            c = self._chapter
            # like a CD player: back to the start of this chapter first, then the one before
            if c >= 0 and self._time - self._chapters[c]["time"] > 3000:
                self.setChapter(c)
            else:
                self.setChapter(max(0, c - 1))

    def _update_chapter(self):
        c = -1
        for i, ch in enumerate(self._chapters):
            if self._time >= ch["time"] - 250:
                c = i
        if c != self._chapter:
            self._chapter = c
            self.chaptersChanged.emit()

    # ------------------------------------------------------------ audio output device
    devicesChanged = Signal()

    @Slot()
    def refreshAudioDevices(self):
        self._enum_devices_async()

    _devicesFound = Signal(object)

    def _enum_devices_async(self):
        """Ask the system for its outputs off the UI thread. Windows answers in milliseconds, until
        its audio service is busy or a Bluetooth headset is slow to report: then seconds, and the
        window froze while opening the Tracks panel or Ajustes > Som (6 s seen in tests)."""
        if getattr(self, "_devices_busy", False):
            return
        self._devices_busy = True
        if not getattr(self, "_devices_wired", False):
            self._devicesFound.connect(self._on_devices_found, Qt.ConnectionType.QueuedConnection)
            self._devices_wired = True

        def work():
            try:
                found = self._enum_devices()
            except Exception as exc:   # never let a device driver take the app down
                print("audio devices:", exc)
                found = []
            self._devicesFound.emit(found)
        threading.Thread(target=work, daemon=True, name="audio-devices").start()

    def _on_devices_found(self, found):
        self._devices_busy = False
        if found:
            self._devices = found
        self.devicesChanged.emit()

    def _enum_devices(self) -> list[dict]:
        out = [{"id": "", "name": tr("Padrão do Windows") if sys.platform == "win32" else tr("Padrão do sistema")}]
        head = None
        try:
            head = self._mp.audio_output_device_enum()
            node = head
            seen = set()
            while node:
                d = node.contents
                did = d.device.decode("utf8", "replace") if isinstance(d.device, bytes) else (d.device or "")
                name = d.description.decode("utf8", "replace") if isinstance(d.description, bytes) else (d.description or "")
                if did and did not in seen:
                    seen.add(did)
                    out.append({"id": did, "name": name or did})
                node = d.next
        except Exception as exc:
            print("audio devices:", exc)
        finally:
            if head:
                vlc.libvlc_audio_output_device_list_release(head)
        return out

    @Property(list, notify=devicesChanged)
    def audioDevices(self):
        if not self._devices:
            # not known yet: the system default for now, the rest arrives with devicesChanged
            self._enum_devices_async()
            return [{"id": "", "name": tr("Padrão do Windows") if sys.platform == "win32" else tr("Padrão do sistema")}]
        return self._devices

    @Property(str, notify=devicesChanged)
    def audioDevice(self):
        return self._settings.value("audioDevice", "") or ""

    @Slot(str)
    def setAudioDevice(self, did):
        self._settings.setValue("audioDevice", did or "")
        # "" = follow Windows' default; libVLC takes None for that
        self._mp.audio_output_device_set(None, did or None)
        self.devicesChanged.emit()

    # ------------------------------------------------------------ deinterlace
    deinterlaceChanged = Signal()

    @Property(str, notify=deinterlaceChanged)
    def deinterlace(self):
        return self._deinterlace

    @Slot(str)
    def setDeinterlace(self, mode):
        """auto (libVLC decides from the stream flags), on (always, yadif 2x), off."""
        if mode not in ("auto", "on", "off") or mode == self._deinterlace:
            return
        was = self._deinterlace
        self._deinterlace = mode
        if mode == "auto" and was != "auto":
            self._rebuild()   # libVLC has no call to go back to "automatic": a fresh player is
        else:
            self._apply_deinterlace()
        self.deinterlaceChanged.emit()

    def _apply_deinterlace(self):
        self._mp.video_set_deinterlace("yadif2x" if self._deinterlace == "on" else None)

    # ------------------------------------------------------------ normalizer / subtitle style
    optionsChanged = Signal()

    @Property(bool, notify=optionsChanged)
    def normalize(self):
        return bool(self._settings.value("normalize", False))

    @Slot(bool)
    def setNormalize(self, on):
        self._set_option("normalize", bool(on))

    @Property("QVariantMap", notify=optionsChanged)
    def subtitleStyle(self):
        s = self._settings
        return {"subSize": s.value("subSize", "normal"), "subColor": s.value("subColor", "white"),
                "subBackground": s.value("subBackground", "outline"), "subEncoding": s.value("subEncoding", "")}

    @Slot(str, "QVariant")
    def setSubtitleStyle(self, key, value):
        """subSize: small|normal|large|huge · subColor: white|yellow ·
        subBackground: outline|box|none · subEncoding: "" (automatic) or a charset name."""
        if key in ("subSize", "subColor", "subBackground", "subEncoding"):
            self._set_option(key, value)

    def _set_option(self, key, value):
        if self._settings.value(key) == value:
            return
        self._settings.setValue(key, value)
        if self._instance_args() != self._vlc_args:
            self._rebuild()
        self.optionsChanged.emit()

    # ------------------------------------------------------------ cast (Chromecast)
    renderersChanged = Signal()
    _rendererEvent = Signal(object, bool)

    def _discoverer_cb(self, event):
        # libVLC thread. The item pointer is the first member of the event's union, which
        # python-vlc's EventUnion does not spell out.
        try:
            ptr = ctypes.cast(ctypes.byref(event.u), ctypes.POINTER(ctypes.c_void_p))[0]
            if not ptr:
                return
            r = vlc.Renderer(ptr)
            added = getattr(event.type, "value", event.type) == vlc.EventType.RendererDiscovererItemAdded.value
            if added:
                r.hold()   # owned by libVLC otherwise; released when the device goes away
            self._rendererEvent.emit((r, ptr), added)
        except Exception as exc:
            print("cast discovery:", exc)

    def _on_renderer(self, item, added):
        r, ptr = item
        if added:
            name = r.name()
            name = name.decode("utf8", "replace") if isinstance(name, bytes) else (name or "?")
            kind = r.type()
            kind = kind.decode() if isinstance(kind, bytes) else (kind or "")
            self._renderers.append({"name": name, "type": kind, "ptr": ptr, "r": r})
        else:
            for x in list(self._renderers):
                if x["ptr"] == ptr:
                    self._renderers.remove(x)
                    if self._cast_ptr == ptr:
                        self.setRenderer(-1)
                    x["r"].release()
        self.renderersChanged.emit()

    @Slot()
    def startCastDiscovery(self):
        """Look for Chromecasts on the network (mDNS); devices show up in `renderers`."""
        if self._rd is not None:
            return
        rd = self._vlc.renderer_discoverer_new("microdns_renderer")
        if rd is None:
            self.message.emit(tr("Transmitir não está disponível nesta versão do VLC"))
            return
        em = rd.event_manager()
        em.event_attach(vlc.EventType.RendererDiscovererItemAdded, self._discoverer_cb)
        em.event_attach(vlc.EventType.RendererDiscovererItemDeleted, self._discoverer_cb)
        if rd.start() != 0:
            rd.release()
            self.message.emit(tr("Não foi possível procurar aparelhos na rede"))
            return
        self._rd = rd
        self.renderersChanged.emit()

    def _stop_cast_discovery(self):
        if self._rd is not None:
            try:
                self._rd.stop()
                self._rd.release()
            except OSError as exc:
                print("cast discovery stop:", exc)
            self._rd = None
        for x in self._renderers:
            x["r"].release()
        self._renderers = []

    @Property(list, notify=renderersChanged)
    def renderers(self):
        return [{"name": x["name"], "type": x["type"]} for x in self._renderers]

    @Property(bool, notify=renderersChanged)
    def discovering(self):
        return self._rd is not None

    @Property(str, notify=renderersChanged)
    def castingTo(self):
        for x in self._renderers:
            if x["ptr"] == self._cast_ptr:
                return x["name"]
        return ""

    @Slot(int)
    def setRenderer(self, i):
        """-1 = this computer. libVLC picks the output when playback starts, so the current file
        restarts on the new output from the same point."""
        target = self._renderers[i] if 0 <= i < len(self._renderers) else None
        ptr = target["ptr"] if target else None
        if ptr == self._cast_ptr:
            return
        self._cast_ptr = ptr
        self._mp.set_renderer(target["r"] if target else None)
        if target:
            self.message.emit(tr("Transmitindo para %1").replace("%1", target["name"]))
        if 0 <= self._index < len(self._queue) and self._state in ("playing", "paused", "loading"):
            self._start_at = self._time
            self._play_index(self._index)
        self.renderersChanged.emit()

    # ------------------------------------------------------------ media information
    @Slot(result="QVariantMap")
    def mediaInfo(self):
        """Everything the "Informações da mídia" panel shows (and what support asks for)."""
        info = {"path": self.currentPath, "title": self.title, "tracks": [], "size": 0,
                "container": "", "length": self._length, "bitrate": 0,
                "decoded": 0, "lost": 0, "displayed": self._frames_seen, "dropped": self._dropped,
                "inputKbps": 0.0, "videoSize": f"{self.videoWidth}×{self.videoHeight}" if self.videoWidth else ""}
        p = self.currentPath
        if p and "://" not in p and os.path.isfile(p):
            info["size"] = os.path.getsize(p)
            info["container"] = os.path.splitext(p)[1][1:].upper()
            if self._length > 0:
                info["bitrate"] = int(info["size"] * 8 / (self._length / 1000) / 1000)   # kbit/s
        m = self._mp.get_media()
        if m:
            try:
                info["tracks"] = media_tracks(m)
                st = vlc.MediaStats()
                if m.get_stats(st):
                    info["decoded"] = int(st.decoded_video)
                    info["lost"] = int(st.lost_pictures)
                    info["inputKbps"] = round(float(st.input_bitrate) * 8000, 1)
            finally:
                m.release()
        return info

    # ------------------------------------------------------------------ video
    def _on_vlc_frame(self, frame: QVideoFrame):
        # Drop frames while the UI thread is still busy with the previous one,
        # so a slow UI never builds up a backlog (latency stays one frame).
        with self._pending_lock:
            if self._pending_busy:
                self._dropped += 1
                return
            self._pending_busy = True
        self._frameReady.emit(frame, self._media_gen)

    def _deliver_frame(self, frame: QVideoFrame, gen: int):
        try:
            # a frame still queued from the previous item must not count for (or show in) this one
            if gen == self._media_gen:
                self._show_frame(frame)
        finally:
            with self._pending_lock:
                self._pending_busy = False

    def _visible_rect(self, frame: QVideoFrame) -> QSize:
        vp = frame.surfaceFormat().viewport()
        return vp.size() if vp.isValid() and not vp.isEmpty() else frame.size()

    def _show_frame(self, frame: QVideoFrame):
        if self._visible_checked:
            vis = self._visible_rect(frame)
            if (vis.width(), vis.height()) != (self._renderer.width, self._renderer.height):
                return   # decoded before the visible size was known: would show the padding rows
        first = self._frames_seen == 0
        self._frames_seen += 1
        if first and self._first_video_frame:
            self._first_video_frame = False
            from . import trace
            trace.mark("first video frame")
        if not self._visible_checked:
            # padded picture? libVLC knows the visible size once the vout runs (UI thread call)
            try:
                # the file's video track has the visible size; the video output's answer can be
                # 0×0 (reused from the previous file) or the padded size for a moment at first
                vw = vh = 0
                m = self._mp.get_media()
                if m:
                    try:
                        vt = [t for t in media_tracks(m) if t["type"] == "video" and t.get("height")]
                    finally:
                        m.release()
                    if vt:
                        vw, vh = vt[0]["width"], vt[0]["height"]
                if vw <= 0 and self._frames_seen > 50:
                    vw, vh = self._mp.video_get_size(0)   # streams that never describe their tracks
                fs = frame.size()
                if 0 < vw <= fs.width() and 0 < vh <= fs.height() and (vw, vh) != (fs.width(), fs.height()) \
                        and fs.height() - vh <= 64 and fs.width() - vw <= 64:
                    self._renderer.set_visible(vw & ~1, vh & ~1)
                if vw > 0:
                    self._visible_checked = True   # libVLC knows the size now: done for this file
            except Exception as exc:
                print("visible size:", exc)
        size_changed = self._last_frame is None or self._visible_rect(self._last_frame) != self._visible_rect(frame)
        self._last_frame = frame
        for sink in list(self._sinks):
            try:
                sink.setVideoFrame(frame)
            except RuntimeError:
                self._sinks.remove(sink)
        if first or size_changed:
            self.videoSizeChanged.emit()
            self.mediaChanged.emit()

    @Slot(QObject)
    def addSink(self, sink):
        if isinstance(sink, QVideoSink) and sink not in self._sinks:
            self._sinks.append(sink)
            if self._last_frame is not None:
                sink.setVideoFrame(self._last_frame)

    @Slot(QObject)
    def removeSink(self, sink):
        if sink in self._sinks:
            self._sinks.remove(sink)

    @Property(int, notify=videoSizeChanged)
    def videoWidth(self):
        return self._visible_rect(self._last_frame).width() if self._last_frame else 0

    @Property(int, notify=videoSizeChanged)
    def videoHeight(self):
        return self._visible_rect(self._last_frame).height() if self._last_frame else 0

    # ------------------------------------------------------------- playlist
    @Slot(list)
    @Slot(list, int)
    def openFiles(self, items, start=0):
        """items: list of paths / file URLs / network URLs / playlist files."""
        before = len([x for x in items if x])
        items = self._flatten(items)
        # keep the requested start item aligned after playlist expansion
        if before != len(items):
            start = 0
        entries = [self._entry(x) for x in items if x]
        entries = [e for e in entries if e]
        if not entries:
            return
        self._qmodel.reset(entries)
        self._consecutive_errors = 0   # a new queue: earlier failures do not count against it
        self._play_index(max(0, min(start, len(entries) - 1)))

    # ---------------------------------------------------------------- A-B repeat
    @staticmethod
    def _fmt(ms):
        s = max(0, int(ms)) // 1000
        return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"

    @Slot()
    def cycleLoop(self):
        """L: mark the start, then the end (the stretch repeats), then turn it off."""
        if self._loop_a < 0:
            self._loop_a = self._time
            self.message.emit(tr("Início do trecho em %1. Aperte L de novo no fim.").replace("%1", self._fmt(self._time)))
        elif self._loop_b < 0:
            if self._time <= self._loop_a + 500:
                self.message.emit(tr("O fim do trecho precisa vir depois do início"))
                return
            self._loop_b = self._time
            self.message.emit(tr("Repetindo de %1 a %2").replace("%1", self._fmt(self._loop_a)).replace("%2", self._fmt(self._loop_b)))
            self.seek(self._loop_a)
        else:
            self._loop_a = self._loop_b = -1
            self.message.emit(tr("Repetição do trecho desligada"))
        self.loopChanged.emit()

    @Property(int, notify=loopChanged)
    def loopA(self):
        return self._loop_a

    @Property(int, notify=loopChanged)
    def loopB(self):
        return self._loop_b

    @Slot(list, int, int)
    def openFilesAt(self, items, start, ms):
        """openFiles, starting the first track at `ms` (hand-off from the quick player)."""
        self._start_at = max(0, int(ms))
        self.openFiles(items, start)

    @Slot(str)
    def openUrl(self, url):
        self.openFiles([url])

    @staticmethod
    def _flatten(items):
        out = []
        for x in items:
            if not x:
                continue
            x = str(x)
            p = QUrl(x).toLocalFile() if x.startswith("file:") else x
            if "://" not in p and is_playlist(p) and os.path.isfile(p):
                inner = read_playlist(p)
                out.extend(inner if inner != [p] else [p])
            else:
                out.append(x)
        return out

    @Slot(list)
    def enqueue(self, items):
        entries = [e for e in (self._entry(x) for x in self._flatten(items)) if e]
        if not entries:
            return
        was_empty = not self._queue
        self._qmodel.insert(len(self._queue), entries)
        self.message.emit(tr("%n item(ns) adicionado(s) à fila", len(entries)))
        if was_empty:
            self._play_index(0)

    @Slot(str)
    def playNext(self, item):
        entries = [e for e in (self._entry(x) for x in self._flatten([item])) if e]
        if not entries:
            return
        pos = self._index + 1
        self._qmodel.insert(pos, entries)
        self.message.emit(tr("Vai tocar em seguida"))
        if len(self._queue) == len(entries):
            self._play_index(0)

    @Slot(int)
    def playAt(self, index):
        if 0 <= index < len(self._queue):
            self._play_index(index)

    @Slot(int)
    def removeAt(self, index):
        if not (0 <= index < len(self._queue)):
            return
        self._qmodel.remove(index)
        if index < self._index:
            self._index -= 1
            self.currentIndexChanged.emit()
        elif index == self._index:
            if self._queue:
                self._play_index(min(index, len(self._queue) - 1))
            else:
                self._index = -1
                self.stop()
                self.currentIndexChanged.emit()
                self.mediaChanged.emit()

    @Slot(int, int)
    def moveItem(self, src, dst):
        if not (0 <= src < len(self._queue) and 0 <= dst < len(self._queue)) or src == dst:
            return
        old = self._index
        self._qmodel.move(src, dst)
        if self._index == src:
            self._index = dst
        elif src < self._index <= dst:
            self._index -= 1
        elif dst <= self._index < src:
            self._index += 1
        if self._index != old:
            self.currentIndexChanged.emit()

    @Slot()
    def clearQueue(self):
        self.stop()
        self._qmodel.reset([])
        self._index = -1
        self.currentIndexChanged.emit()
        self.mediaChanged.emit()

    def _entry(self, x):
        x = str(x)
        if x.startswith("file:"):
            x = QUrl(x).toLocalFile()
        is_url = "://" in x and not os.path.exists(x)
        if not is_url:
            x = os.path.normpath(x)  # libVLC rejects mixed '/' and '\' separators
            if not os.path.exists(x):
                return None
        if not is_url and os.path.isdir(x):
            return None
        page = is_url and websites.is_page_url(x)
        return {
            "path": x,
            "title": nice_title(x) if not is_url else x.rsplit("/", 1)[-1] or x,
            "isUrl": is_url,
            "audio": (not is_url) and is_audio_path(x),
            "page": page,          # a YouTube/Vimeo… page: yt-dlp finds the stream first
        }

    # ---------------------------------------------------------- web pages (yt-dlp)
    _resolved = Signal(int, object, str)
    _switchDone = Signal()

    @property
    def _resolving(self) -> bool:
        return getattr(self, "_resolving_on", False)

    @_resolving.setter
    def _resolving(self, on: bool):
        if on != getattr(self, "_resolving_on", None):
            self._resolving_on = on
            self.resolvingChanged.emit()

    @Property(bool, notify=resolvingChanged)
    def resolving(self):
        """yt-dlp is looking up a web page's stream (YouTube, Vimeo…)."""
        return self._resolving

    @Property(str, notify=resolvingChanged)
    def resolvingSite(self):
        if not (self._resolving and 0 <= self._index < len(self._queue)):
            return ""
        return websites.site_name(self._queue[self._index]["path"])

    def _resolve_async(self, index, e):
        self._wake()
        token = object()
        self._resolve_token = token
        self._resolving = True
        self._mp.stop()                           # the previous item must not keep playing
        self._open_deadline = time.time() + 40   # the watchdog waits for the lookup too
        # forget the previous item now, or the player keeps showing it (a song's time with no
        # frames even made the link look like audio) until the stream is found
        # until the page's real title arrives: "YouTube" reads better than "watch?v=…"
        self._meta = {"title": websites.site_name(e["path"]) or e["title"], "artist": "", "album": "", "artwork": ""}
        self._last_frame = None
        self._frames_seen = 0
        self._media_gen += 1
        self._time, self._length = 0, 0
        for sink in self._sinks:
            try:
                sink.setVideoFrame(QVideoFrame())
            except RuntimeError:
                pass
        if self._chapters or self._chapter != -1:
            self._chapters, self._chapter = [], -1
            self.chaptersChanged.emit()
        if self._state != "loading":
            self._state = "loading"
            self.stateChanged.emit()
        self.timeChanged.emit()
        self.mediaChanged.emit()
        self.currentIndexChanged.emit()

        def work():
            try:
                info, err = websites.resolve(e["path"]), ""
            except Exception as exc:
                info, err = None, str(exc)
            self._resolved.emit(index, (token, info), err)
        threading.Thread(target=work, daemon=True, name="resolve-link").start()

    def _on_resolved(self, index, payload, err):
        token, info = payload
        if token is not self._resolve_token or index != self._index or index >= len(self._queue):
            return   # the person moved on meanwhile
        self._resolving = False
        if info is None:
            print("link:", err)
            self._open_deadline = 0
            self.message.emit(tr("Não foi possível abrir este link"))
            self._on_error()
            return
        e = self._queue[index]
        e["resolved"] = info
        e["title"] = info["title"]
        self._qmodel.touch(index)
        self._play_index(index)

    def _start_ms(self, e) -> int:
        """Where this item starts: an explicit hand-off point, else the remembered position."""
        if self._start_at:
            ms, self._start_at = self._start_at, 0
            return ms
        if not self._settings.value("resume", True) or (e["isUrl"] and not e.get("page")):
            return 0
        pos = self._settings.resume_position(e["path"])
        # songs always start from the top; only videos and long audio
        # (podcasts, audiobooks) pick up where they stopped
        if e["audio"] and self._settings.resume_length(e["path"]) < self.audio_resume_min:
            pos = 0
        if pos > 0:
            self.message.emit(tr("Continuando de onde você parou"))
        return pos

    def _play_index(self, index):
        self._end_scrub()
        self._want_pause_at = 0.0
        self._wake()
        self._audio_off = False   # "Desativado" was for the previous item
        self._save_resume()
        self._index = index
        self._error_handled = False
        self._resolving = False
        self._resolve_token = None
        e = self._queue[index]
        if e.get("page"):
            info = e.get("resolved")
            if not info or time.time() - info["at"] > websites.FRESH_SECONDS:
                if not websites.available():
                    self.message.emit(tr("Links de sites não estão disponíveis nesta instalação"))
                    self._on_error()
                    return
                self._resolve_async(index, e)
                return
            media = self._vlc.media_new_location(info["video"])
            h = info["headers"]
            if h.get("User-Agent"):
                media.add_option(":http-user-agent=" + h["User-Agent"])
            if h.get("Referer"):
                media.add_option(":http-referrer=" + h["Referer"])
            if info["audio"]:
                media.add_option(":input-slave=" + info["audio"])
            if not info["live"]:
                start = self._start_ms(e)
                if start:
                    media.add_option(f":start-time={start / 1000:.1f}")
        elif e["isUrl"]:
            media = self._vlc.media_new_location(e["path"])
            self._start_at = 0
        else:
            media = self._vlc.media_new_path(e["path"])
            start = self._start_ms(e)
            if start:
                media.add_option(f":start-time={start / 1000:.2f}")
        for uri in self._ext_subs.get(e["path"], []):
            media.slaves_add(vlc.MediaSlaveType.subtitle, 4, uri)   # 4 = highest priority
        casting = self._cast_ptr is not None
        # libVLC 3 switches hardware decoding off for apps that take the pictures through callbacks
        # (as this one does) by setting the player's avcodec-hw to "none"; a per-media option wins
        # over it. The GPU then decodes and libVLC copies the picture back: ~70% less CPU at 4K60.
        # It cannot draw subtitles on those pictures, though: an item showing subtitles decodes in
        # software (see _to_software).
        self._hw_item = ""
        if self._settings.value("hwdec", True) and not casting and e["path"] not in self._sw_paths:
            media.add_option(":avcodec-hw=any")
            self._hw_item = e["path"]
        if not casting:
            self._mp.set_media(media)
            media.release()   # the player holds its own reference
        self._meta = {"title": e["title"], "artist": "", "album": "", "artwork": ""}
        self._audio_tracks, self._sub_tracks, self._tracks_polled = [], [], 0
        if self._chapters or self._chapter != -1:
            self._chapters, self._chapter = [], -1
            self.chaptersChanged.emit()
        self._last_frame = None
        self._frames_seen = 0
        self._media_gen += 1
        self._visible_checked = False
        self._time, self._length = 0, 0
        for sink in self._sinks:
            try:
                sink.setVideoFrame(QVideoFrame())
            except RuntimeError:
                pass
        self._open_deadline = time.time() + 8
        self._expected = None
        if self._loop_a >= 0:
            self._loop_a = self._loop_b = -1   # a stretch belongs to the track it was marked on
            self.loopChanged.emit()
        if self._state != "loading":
            self._state = "loading"   # the old track's "playing" must not linger on the new one
            self.stateChanged.emit()
        if casting:
            # with a Chromecast, libVLC only lets go of the previous file once the TV answers:
            # switch off the UI thread so the app never freezes waiting for it
            self._switch_on_worker(media)
        else:
            self._start_playback()
        self._settings.add_history(e["path"], e["title"])
        self.mediaChanged.emit()
        self.currentIndexChanged.emit()
        self.tracksChanged.emit()
        self.timeChanged.emit()
        self.videoSizeChanged.emit()

    def _on_error(self):
        """Unplayable item: tell the user and move on (unless everything failed)."""
        # libVLC can report one broken file twice (EncounteredError, then EndReached): one error
        # per attempt, or a short queue would count as all-broken and stop
        if self._error_handled:
            return
        self._error_handled = True
        self._consecutive_errors += 1
        self._state = "error"
        self.stateChanged.emit()
        name = self._queue[self._index]["title"] if 0 <= self._index < len(self._queue) else ""
        self.message.emit(tr("Não foi possível reproduzir “%1”").replace("%1", name) if name else tr("Não foi possível reproduzir este arquivo"))
        if self._consecutive_errors >= len(self._queue) or self._repeat == 2:
            return
        nxt = self._next_index(auto=True)
        if nxt is not None and nxt != self._index:
            self._play_soon(nxt)

    def _start_playback(self):
        self._wake()
        self._mp.play()
        self._audio_dirty = True   # applied by _tick once the audio output is up
        self._mp.set_rate(self._rate)
        if self._eq_enabled:
            self._mp.set_equalizer(self._eq)
        self._apply_adjust()

    def _switch_on_worker(self, media):
        self._wake()
        self._switching += 1

        def work():
            with self._switch_lock:   # one switch at a time, in order
                try:
                    self._mp.set_media(media)
                    media.release()
                    self._start_playback()
                except Exception as exc:
                    print("cast switch:", exc)
                finally:
                    self._switchDone.emit()
        threading.Thread(target=work, daemon=True, name="cast-switch").start()

    def _on_switch_done(self):
        self._switching = max(0, self._switching - 1)

    def _play_soon(self, index):
        """Next item after the event loop turns; skipped if the queue changed meanwhile (the person
        opened other files or cleared it in between)."""
        rev = self._qrev
        QTimer.singleShot(0, lambda: rev == self._qrev and 0 <= index < len(self._queue) and self._play_index(index))

    def _on_end(self, gen=None):
        if gen is not None and gen != self._media_gen:
            return   # the previous item's end, delivered after the switch
        # libVLC "ends" files it could not read at all (a text file named .mkv): nothing was
        # shown, no clock, no duration. That is an error for the user, not a finished song.
        if self._frames_seen == 0 and self._time <= 0 and self._length <= 0 and self._mp.get_length() <= 0:
            self._on_error()
            return
        if self._index >= 0 and self._index < len(self._queue):
            self._settings.set_resume(self._queue[self._index]["path"], 0, 0)
        nxt = self._next_index(auto=True)
        if nxt is None:
            self._mp.stop()
            self._state = "ended"
            self.stateChanged.emit()
        else:
            self._play_soon(nxt)

    def _next_index(self, auto=False):
        n = len(self._queue)
        if n == 0:
            return None
        if auto and self._repeat == 2:
            return self._index
        if auto and not self._settings.value("autoplay", True):
            return None
        if self._shuffle and n > 1:
            choices = [i for i in range(n) if i != self._index]
            return random.choice(choices)
        if self._index + 1 < n:
            return self._index + 1
        return 0 if (self._repeat == 1 or not auto) else None

    # --------------------------------------------------------------- control
    def _expect(self, state):
        """Show the requested state right away; libVLC confirms it a poll or two later."""
        self._expected, self._expected_until = state, time.time() + 0.8
        if self._state != state:
            self._state = state
            self.stateChanged.emit()

    @Slot()
    def togglePlay(self):
        self._wake()
        if self._state in ("idle", "ended", "stopped", "error") and self._queue:
            self._play_index(max(0, self._index))
            return
        # decide from our own (optimistic) state: is_playing() lags a pause by a few ms,
        # so two quick clicks would otherwise both pause
        if self._state == "playing":
            self.pause()
        elif self._state == "paused":
            self.play()
        else:
            self._mp.pause() if self._mp.is_playing() else self._mp.play()

    @Slot()
    def play(self):
        self._wake()
        if self._state in ("idle", "ended", "stopped", "error") and self._queue:
            self._play_index(max(0, self._index))
            return
        self._want_pause_at = 0.0
        self._mp.set_pause(0) if self._state == "paused" else self._mp.play()
        if self._state == "paused":
            self._expect("playing")

    @Slot()
    def pause(self):
        self._wake()
        self._mp.set_pause(1)
        self._want_pause_at = time.time()
        if self._state == "playing":
            self._expect("paused")

    @Slot()
    def stop(self):
        self._save_resume()
        self._resolving, self._resolve_token, self._open_deadline = False, None, 0   # also cancels a link lookup
        self._mp.stop()
        self._state = "stopped"
        self.stateChanged.emit()

    @Slot()
    def next(self):
        nxt = self._next_index()
        if nxt is not None:
            self._play_index(nxt)

    @Slot()
    def previous(self):
        if self._time > 4000:
            self.seek(0)
        elif self._index > 0:
            self._play_index(self._index - 1)
        elif self._queue:
            self.seek(0)

    @Slot(float)
    def scrub(self, ms):
        """The seek bar is being dragged to `ms`: show that point now (see SCRUB_MS)."""
        if self._length <= 0 or not self.seekable:
            return
        self._scrub_target = int(ms)
        if not self._scrub_timer.isActive():
            self._scrub_step()          # the first one right away, the rest paced
            self._scrub_timer.start()

    def _scrub_step(self):
        t = self._scrub_target
        if t is None or t == self._scrub_sent:
            self._scrub_timer.stop()    # the finger stopped: nothing new to show
            return
        if (self._scrub_sent is not None and self._frames_seen == self._scrub_frames
                and time.time() - self._scrub_at < self.SCRUB_WAIT):
            return                      # the last point is still being decoded
        self._scrub_sent, self._scrub_frames, self._scrub_at = t, self._frames_seen, time.time()
        self._wake()
        self._mp.set_time(max(0, min(t, self._length - 500)))

    def _end_scrub(self):
        self._scrub_target = self._scrub_sent = None
        self._scrub_timer.stop()

    @Slot(int)
    def seek(self, ms):
        self._end_scrub()               # the drop point wins over a paced scrub still pending
        self._wake()
        if self._length > 0:
            ms = max(0, min(int(ms), self._length - 500))
            self._mp.set_time(ms)
            self._time = ms
            self.timeChanged.emit()
            if self._chapters:
                self._update_chapter()

    @Slot(int)
    def skip(self, delta_ms):
        self.seek(self._time + delta_ms)

    @Slot()
    def frameStep(self):
        self._mp.next_frame()

    def _save_resume(self):
        if 0 <= self._index < len(self._queue):
            e = self._queue[self._index]
            if not e["isUrl"] and self._length > 0:
                self._settings.set_resume(e["path"], self._time, self._length)

    # ---------------------------------------------------------------- polling
    def _wake(self):
        """Poll libVLC quickly again (something is about to play, load or seek)."""
        if self._poll.interval() != self.POLL_FAST:
            self._poll.setInterval(self.POLL_FAST)
        if not self._poll.isActive():
            self._poll.start()

    def _pace(self):
        """Fast polling only while something can change on its own (playing, loading, a link being
        looked up); idle, stopped or paused, once a second is plenty (no 8 wake-ups a second in the tray)."""
        busy = self._state in ("loading", "playing") or self._resolving or self._open_deadline or self._switching
        want = self.POLL_FAST if busy else self.POLL_IDLE
        if self._poll.interval() != want:
            self._poll.setInterval(want)

    def _tick(self):
        self._pace()
        if self._switching:
            return   # a cast switch holds libVLC's player lock: asking now would freeze the UI
        st = self._mp.get_state()
        mapping = {
            vlc.State.Opening: "loading",
            vlc.State.Buffering: "loading",
            vlc.State.Playing: "playing",
            vlc.State.Paused: "paused",
            vlc.State.Stopped: "stopped",
            vlc.State.Ended: "ended",
            vlc.State.Error: "error",
            vlc.State.NothingSpecial: "idle",
        }
        new_state = mapping.get(st, "idle")
        if self._want_pause_at:
            # libVLC drops or delays a pause that lands while it is busy (a seek being decoded):
            # the person asked for it, so it is asked again until it holds
            if st == vlc.State.Paused or st not in (vlc.State.Playing, vlc.State.Buffering):
                self._want_pause_at = 0.0
            elif time.time() - self._want_pause_at > 0.4:
                self._mp.set_pause(1)
                self._want_pause_at = time.time()
        if self._resolving:
            # yt-dlp is still looking up a web page's stream
            if time.time() > self._open_deadline:
                self._resolving = False
                self._resolve_token = None
                self.message.emit(tr("Não foi possível abrir este link"))
                self._on_error()
                return
            if self._state != "loading":
                self._state = "loading"
                self.stateChanged.emit()
            return
        if new_state == "stopped" and self._state in ("ended", "idle"):
            new_state = self._state
        # watchdog: libVLC never left its initial state -> report instead of hanging silently
        if (new_state == "idle" and self._open_deadline and time.time() > self._open_deadline):
            self._open_deadline = 0
            new_state = "error"
            self.message.emit(tr("Não foi possível abrir esta mídia"))
        elif new_state not in ("idle",):
            self._open_deadline = 0
        elif self._open_deadline:
            new_state = "loading"   # set_media → play(): libVLC reports nothing yet
        if self._expected:
            if (new_state == self._expected or time.time() > self._expected_until
                    or new_state not in ("playing", "paused")):
                self._expected = None
            else:
                new_state = self._expected   # hold the optimistic state, no flicker back
        if new_state == "playing":
            self._consecutive_errors = 0
        if new_state != self._state:
            if new_state == "playing":
                self._audio_dirty = True   # a new track may bring a new audio output
            self._state = new_state
            self.stateChanged.emit()
        if self._audio_dirty and new_state in ("playing", "paused"):
            self._apply_volume()
        if new_state in ("playing", "paused", "loading"):
            t, ln = max(0, self._mp.get_time()), max(0, self._mp.get_length())
            if t != self._time or ln != self._length:
                self._time, self._length = t, ln
                self.timeChanged.emit()
                if self._chapters:
                    self._update_chapter()
            if self._pause_when_playing and new_state == "playing" and t > 0:
                self._pause_when_playing = False
                self._mp.set_pause(1)
            if self._loop_b > 0 and new_state == "playing" and self._time >= self._loop_b:
                self.seek(self._loop_a)
            if self._tracks_polled < 25 and new_state == "playing":
                self._tracks_polled += 1
                if self._tracks_polled in (3, 10, 25):
                    self._refresh_tracks()
                    self._refresh_meta()
                    # a subtitle the file turns on by itself (default / forced track) needs software decoding
                    spu = self._mp.video_get_spu()
                    if self._spu_wanted in (-1, 0):
                        pass             # the person turned subtitles off: _hold_spu keeps them off
                    elif self._hw_item and spu not in (-1, 0):
                        self._to_software(spu=spu)
        if self._sleep_deadline and time.time() >= self._sleep_deadline:
            self._sleep_deadline = 0
            self.pause()
            self.modeChanged.emit()
            self.message.emit(tr("Timer de sono: reprodução pausada"))

    def _refresh_meta(self):
        m = self._mp.get_media()   # a new reference
        if not m:
            return
        try:
            self._read_meta(m)
        finally:
            m.release()

    def _read_meta(self, m):
        title = m.get_meta(vlc.Meta.Title) or ""
        entry = self._queue[self._index] if 0 <= self._index < len(self._queue) else None
        # libVLC returns the filename as title when no tag exists – keep our nicer one
        if entry and (not title or title == os.path.basename(entry["path"]) or entry.get("page")):
            # a web page: the site's title (libVLC only sees the stream address)
            title = entry["title"]
        if entry and entry["isUrl"]:
            np = m.get_meta(vlc.Meta.NowPlaying)
            if np:
                title = np
        art = m.get_meta(vlc.Meta.ArtworkURL) or ""
        new = {
            "title": title,
            "artist": m.get_meta(vlc.Meta.Artist) or "",
            "album": m.get_meta(vlc.Meta.Album) or "",
            "artwork": art if art.startswith(("file:", "http")) else "",
        }
        if new != self._meta:
            self._meta = new
            self.mediaChanged.emit()

    def _refresh_tracks(self):
        def conv(desc):
            out = []
            for tid, name in desc or []:
                n = name.decode("utf8", "ignore") if isinstance(name, bytes) else str(name)
                # libVLC localizes names ("Disable"/"Desabilitar"); id -1 is always "off"
                out.append({"id": tid, "name": "Desativado" if tid == -1 else n})
            return out

        self._audio_tracks = conv(self._mp.audio_get_track_description())
        self._sub_tracks = conv(self._mp.video_get_spu_description())
        self.tracksChanged.emit()
        try:
            chapters = self._read_chapters()
        except Exception as exc:
            print("chapters:", exc)
            chapters = []
        if chapters != self._chapters:
            self._chapters = chapters
            self._chapter = -1
            self._update_chapter()
            self.chaptersChanged.emit()

    # ------------------------------------------------------------ properties
    @Property(str, notify=stateChanged)
    def state(self):
        return self._state

    @Property(bool, notify=stateChanged)
    def playing(self):
        return self._state == "playing"

    @Property(bool, notify=currentIndexChanged)
    def hasMedia(self):
        return 0 <= self._index < len(self._queue)

    @Property(int, notify=timeChanged)
    def time(self):
        return self._time

    @Property(int, notify=timeChanged)
    def length(self):
        return self._length

    @Property(bool, notify=timeChanged)
    def seekable(self):
        return self._length > 0

    @Property(str, notify=mediaChanged)
    def title(self):
        return self._meta["title"]

    @Property(str, notify=mediaChanged)
    def artist(self):
        return self._meta["artist"]

    @Property(str, notify=mediaChanged)
    def album(self):
        return self._meta["album"]

    @Property(str, notify=mediaChanged)
    def artwork(self):
        return self._meta["artwork"]

    @Property(str, notify=mediaChanged)
    def currentPath(self):
        if 0 <= self._index < len(self._queue):
            return self._queue[self._index]["path"]
        return ""

    @Property(bool, notify=mediaChanged)
    def isAudio(self):
        if not (0 <= self._index < len(self._queue)):
            return False
        e = self._queue[self._index]
        if e["audio"]:
            return True
        # unknown streams: no frame after a while while playing -> audio
        return self._frames_seen == 0 and self._time > 1500

    @Property(list, notify=queueChanged)
    def queue(self):
        return list(self._queue)

    @Property(QObject, constant=True)
    def queueModel(self):
        return self._qmodel

    @Property(int, notify=queueChanged)
    def queueCount(self):
        return len(self._queue)

    @Property(int, notify=queueChanged)
    def queueRevision(self):
        """Bumps on every change to the queue (also moves, which keep the count)."""
        return self._qrev

    @Slot(int, result="QVariantMap")
    def queueItem(self, i):
        return dict(self._queue[i]) if 0 <= i < len(self._queue) else {}

    def _bump_queue(self, *_):
        self._qrev += 1
        self.queueChanged.emit()

    @Property(int, notify=currentIndexChanged)
    def currentIndex(self):
        return self._index

    @Property(list, notify=tracksChanged)
    def audioTracks(self):
        return self._audio_tracks

    @Property(list, notify=tracksChanged)
    def subtitleTracks(self):
        return self._sub_tracks

    @Property(int, notify=tracksChanged)
    def audioTrack(self):
        return -1 if self._audio_off else self._mp.audio_get_track()

    @Property(int, notify=tracksChanged)
    def subtitleTrack(self):
        return self._mp.video_get_spu()

    @Slot(int)
    def setAudioTrack(self, tid):
        """Faixas › Desativado silences the audio but keeps its stream selected. Deselecting it in
        libVLC 3 broke links whose audio is a separate stream (YouTube): the clock fell back to 0
        while it was off, and the track chosen again stayed silent until the next seek."""
        if tid == -1:
            self._audio_off = True
        else:
            self._audio_off = False
            if tid != self._mp.audio_get_track():
                self._mp.audio_set_track(tid)
        self._apply_volume()
        self.tracksChanged.emit()

    @Slot(int)
    def setSubtitleTrack(self, tid):
        self._spu_wanted = None   # a newer choice: a pending re-selection after a reopen must not undo it
        if tid not in (-1, 0) and self._to_software(spu=tid):
            return
        self._mp.video_set_spu(tid)
        self.tracksChanged.emit()
        self._hold_spu(tid)

    def _hold_spu(self, spu, tries=8):
        """Make a subtitle choice stick. Given while the item is still opening (a reopen in
        software, say), libVLC takes it before the file's tracks exist and then turns on the file's
        default subtitle by itself: "off" came back on. Applied again until the item plays with
        its tracks known."""
        self._spu_wanted = spu

        def pick(n):
            if self._spu_wanted != spu:
                return   # the person chose something else meanwhile
            if self._mp.video_get_spu() != spu:
                self._mp.video_set_spu(spu)
            settled = self._state in ("playing", "paused") and bool(self._sub_tracks)
            if self._mp.video_get_spu() == spu and settled:
                self._spu_wanted = None
                self.tracksChanged.emit()
            elif n > 0:
                QTimer.singleShot(400, lambda: pick(n - 1))
            else:
                self._spu_wanted = None
                self.tracksChanged.emit()
        QTimer.singleShot(300, lambda: pick(tries))

    def _to_software(self, spu=None) -> bool:
        """Subtitles are drawn by libVLC on the decoded picture, which it cannot do for GPU-decoded
        ones on this path: the item is opened again, decoding in software, at the same point and in
        the same state, with the chosen subtitle on. False when it already decodes in software."""
        if not self._hw_item or not (0 <= self._index < len(self._queue)) \
                or self._queue[self._index]["path"] != self._hw_item:
            return False
        self._sw_paths.add(self._hw_item)
        t, st = self._time, self._state
        self._start_at = t
        self._play_index(self._index)
        if st == "paused":
            self._pause_when_playing = True
        if spu is not None:
            self._hold_spu(spu)
        return True

    @Slot(str)
    def addSubtitle(self, path):
        if path.startswith("file:"):
            path = QUrl(path).toLocalFile()
        uri = QUrl.fromLocalFile(path).toString()
        ok = self._mp.add_slave(vlc.MediaSlaveType.subtitle, uri, True) == 0
        self.message.emit(tr("Legenda carregada") if ok else tr("Não foi possível carregar a legenda"))
        if ok and self.currentPath:
            # remembered for this file: a rebuilt player (new subtitle style, normalizer…) or a
            # replay of the same file brings it back
            subs = self._ext_subs.setdefault(self.currentPath, [])
            if uri not in subs:
                subs.append(uri)
            # decoding on the GPU: reopen in software, the file comes back selected (_play_index)
            self._to_software()
        QTimer.singleShot(800, self._refresh_tracks)

    @Property(int, notify=tracksChanged)
    def subtitleDelay(self):
        return int(self._mp.video_get_spu_delay() / 1000)

    @Slot(int)
    def setSubtitleDelay(self, ms):
        self._mp.video_set_spu_delay(int(ms) * 1000)
        self.tracksChanged.emit()

    @Property(int, notify=tracksChanged)
    def audioDelay(self):
        return int(self._mp.audio_get_delay() / 1000)

    @Slot(int)
    def setAudioDelay(self, ms):
        self._mp.audio_set_delay(int(ms) * 1000)
        self.tracksChanged.emit()

    # volume -------------------------------------------------------------
    def _get_volume(self):
        return self._volume

    def _apply_volume(self):
        """Volume and mute reach libVLC through one call, and only while the audio output is settled
        (playing or paused). libVLC 3 could crash in audio_set_mute / audio_set_volume while a track
        switch was replacing its audio output; a change made then waits for _tick. Mute is volume 0."""
        self._audio_dirty = True
        if self._state in ("playing", "paused") and not self._switching:
            self._audio_dirty = False
            self._mp.audio_set_volume(0 if self._muted or self._audio_off else self._volume)

    def _set_volume(self, v):
        v = max(0, min(150, int(v)))
        if v != self._volume:
            self._volume = v
            if self._muted and v > 0:
                self._muted = False
            self._apply_volume()
            self._settings.setValue("volume", v)
            self.volumeChanged.emit()

    volume = Property(int, _get_volume, _set_volume, notify=volumeChanged)

    @Property(bool, notify=volumeChanged)
    def muted(self):
        return self._muted

    @Slot()
    def toggleMute(self):
        self._muted = not self._muted
        self._apply_volume()
        self.volumeChanged.emit()

    # rate ----------------------------------------------------------------
    def _get_rate(self):
        return self._rate

    def _set_rate(self, r):
        r = round(max(0.25, min(4.0, float(r))), 2)
        if r != self._rate:
            self._rate = r
            self._mp.set_rate(r)
            self.rateChanged.emit()

    rate = Property(float, _get_rate, _set_rate, notify=rateChanged)

    # modes -----------------------------------------------------------------
    @Property(bool, notify=modeChanged)
    def shuffle(self):
        return self._shuffle

    @Slot()
    def toggleShuffle(self):
        self._shuffle = not self._shuffle
        self.modeChanged.emit()

    @Property(int, notify=modeChanged)
    def repeat(self):
        return self._repeat

    @Slot()
    def cycleRepeat(self):
        self._repeat = (self._repeat + 1) % 3
        self.modeChanged.emit()

    @Property(int, notify=modeChanged)
    def sleepMinutes(self):
        if not self._sleep_deadline:
            return 0
        return max(1, int((self._sleep_deadline - time.time() + 59) // 60))

    @Slot(int)
    def setSleepTimer(self, minutes):
        self._sleep_deadline = time.time() + minutes * 60 if minutes > 0 else 0
        self.modeChanged.emit()
        self.message.emit(tr("Timer de sono: %1 min").replace("%1", str(minutes)) if minutes > 0 else tr("Timer de sono desligado"))

    # equalizer -------------------------------------------------------------
    EQ_PT = {
        "Flat": N_("Plano"), "Classical": N_("Clássica"), "Club": N_("Clube"), "Dance": N_("Dança"),
        "Full bass": N_("Graves"), "Full bass and treble": N_("Graves e agudos"), "Full treble": N_("Agudos"),
        "Headphones": N_("Fones de ouvido"), "Large Hall": N_("Grande salão"), "Live": N_("Ao vivo"),
        "Party": N_("Festa"), "Soft": N_("Suave"), "Soft rock": N_("Rock suave"),
    }

    @Property(list, constant=True)
    def eqPresets(self):
        n = vlc.libvlc_audio_equalizer_get_preset_count()
        names = []
        for i in range(n):
            nm = vlc.libvlc_audio_equalizer_get_preset_name(i)
            nm = nm.decode() if isinstance(nm, bytes) else str(nm)
            names.append(self.EQ_PT.get(nm, nm))
        return names

    @Property(list, constant=True)
    def eqFrequencies(self):
        out = []
        for i in range(len(self._eq_bands)):
            f = vlc.libvlc_audio_equalizer_get_band_frequency(i)
            out.append(f"{f / 1000:g}k" if f >= 1000 else f"{f:g}")
        return out

    @Property(bool, notify=eqChanged)
    def eqEnabled(self):
        return self._eq_enabled

    @Slot(bool)
    def setEqEnabled(self, on):
        self._eq_enabled = bool(on)
        self._mp.set_equalizer(self._eq if on else None)
        self.eqChanged.emit()

    @Property(int, notify=eqChanged)
    def eqPreset(self):
        return self._eq_preset

    @Property(list, notify=eqChanged)
    def eqBands(self):
        return list(self._eq_bands)

    @Property(float, notify=eqChanged)
    def eqPreamp(self):
        return self._eq_preamp

    @Slot(int)
    def setEqPreset(self, idx):
        self._eq = vlc.libvlc_audio_equalizer_new_from_preset(idx)
        self._eq_preset = idx
        self._eq_bands = [self._eq.get_amp_at_index(i) for i in range(len(self._eq_bands))]
        self._eq_preamp = self._eq.get_preamp()
        self._eq_enabled = True
        self._mp.set_equalizer(self._eq)
        self.eqChanged.emit()

    @Slot(int, float)
    def setEqBand(self, i, db):
        self._eq_bands[i] = float(db)
        self._eq.set_amp_at_index(float(db), i)
        self._eq_preset = -1
        if self._eq_enabled:
            self._mp.set_equalizer(self._eq)
        self.eqChanged.emit()

    @Slot(float)
    def setEqPreamp(self, db):
        self._eq_preamp = float(db)
        self._eq.set_preamp(float(db))
        if self._eq_enabled:
            self._mp.set_equalizer(self._eq)
        self.eqChanged.emit()

    # video adjustments -------------------------------------------------------
    @Property("QVariantMap", notify=adjustChanged)
    def adjust(self):
        return dict(self._adjust)

    @Slot(str, float)
    def setAdjust(self, key, value):
        if key in self._adjust:
            self._adjust[key] = float(value)
            self._apply_adjust()
            self.adjustChanged.emit()

    @Slot()
    def resetAdjust(self):
        self._adjust = {"brightness": 1.0, "contrast": 1.0, "saturation": 1.0, "hue": 0.0, "gamma": 1.0}
        self._apply_adjust()
        self.adjustChanged.emit()

    def _apply_adjust(self):
        a = self._adjust
        neutral = a == {"brightness": 1.0, "contrast": 1.0, "saturation": 1.0, "hue": 0.0, "gamma": 1.0}
        o = vlc.VideoAdjustOption
        self._mp.video_set_adjust_int(o.Enable, 0 if neutral else 1)
        if not neutral:
            self._mp.video_set_adjust_float(o.Brightness, a["brightness"])
            self._mp.video_set_adjust_float(o.Contrast, a["contrast"])
            self._mp.video_set_adjust_float(o.Saturation, a["saturation"])
            self._mp.video_set_adjust_float(o.Hue, a["hue"])
            self._mp.video_set_adjust_float(o.Gamma, a["gamma"])

    # snapshot ------------------------------------------------------------------
    @Slot()
    def snapshot(self):
        if self._last_frame is None:
            self.message.emit(tr("Nada para capturar"))
            return
        img = self._last_frame.toImage()
        folder = Path(self._settings.value("snapshotDir"))
        folder.mkdir(parents=True, exist_ok=True)
        stem = Path(self.currentPath).stem if not self.currentPath.count("://") else "stream"
        name = f"{stem or 'captura'}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.png"
        path = folder / name
        if img.save(str(path)):
            self.lastSnapshot = str(path)
            self.message.emit(tr("Captura salva em %1").replace("%1", folder.name))
        else:
            self.message.emit(tr("Não foi possível salvar a captura"))

    def save_state(self):
        self._save_resume()

    def shutdown(self):
        self._poll.stop()
        self._sinks.clear()
        try:
            self._stop_cast_discovery()
        except Exception as exc:
            print("cast discovery shutdown:", exc)
        # the app is leaving: a libVLC fault here (it can hit one after Windows invalidates the
        # audio device) must not cut the rest of the shutdown short
        if sys.platform != "win32":
            # libVLC 3 on Linux aborts in glibc (__pthread_tpp_change_priority) while its threads
            # are stopped at exit; settings and the resume point are saved already and the process
            # leaves right after (main.hard_exit), so the system reclaims everything instead
            self._mp.set_pause(1)
            return
        try:
            self._mp.stop()
            self._mp.release()
        except OSError as exc:
            print("libVLC shutdown:", exc)
