"""libVLC playback engine exposed to QML.

Video is decoded by libVLC into a CPU buffer (vmem callbacks) and pushed to one or
more QVideoSink objects, so the UI can draw anything on top of the video (no
native-window "airspace" problems) and show live previews in several places.
"""
from __future__ import annotations

import ctypes
import os
import random
import threading
import time
from pathlib import Path

import vlc
from PySide6.QtCore import Property, QObject, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat, QVideoSink

from .media import is_audio_path, nice_title
from .playlists import is_playlist, read_playlist

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
        try:
            media = self._player.get_media()
            for t in media.tracks_get() or []:
                if t.type == vlc.TrackType.video:
                    # python-vlc models the C union as 3 fields; the pointer lives in the first one
                    v = ctypes.cast(t.audio, ctypes.POINTER(vlc.VideoTrack)).contents
                    if v.width and v.height:
                        return int(v.width), int(v.height), int(v.sar_num), int(v.sar_den)
        except Exception:
            pass
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


class Engine(QObject):
    """The player: libVLC media player + playlist, exposed to QML."""

    stateChanged = Signal()
    timeChanged = Signal()
    loopChanged = Signal()
    mediaChanged = Signal()
    queueChanged = Signal()
    tracksChanged = Signal()
    volumeChanged = Signal()
    rateChanged = Signal()
    modeChanged = Signal()
    eqChanged = Signal()
    adjustChanged = Signal()
    videoSizeChanged = Signal()
    message = Signal(str)
    _frameReady = Signal(object)
    _endReached = Signal()
    _errorReached = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        # --verbose=-1 also silences ffmpeg's own stderr chatter (e.g. swscaler notices)
        args = ["--quiet", "--verbose=-1", "--no-video-title-show", "--no-snapshot-preview", "--no-osd"]
        if not settings.value("hwdec", True):
            args.append("--avcodec-hw=none")
        self._vlc = vlc.Instance(args)
        self._mp: vlc.MediaPlayer = self._vlc.media_player_new()
        self._pending_lock = threading.Lock()
        self._pending_busy = False
        self._dropped = 0
        self._renderer = FrameRenderer(self._mp, 1920, self._on_vlc_frame)
        self._sinks: list[QVideoSink] = []
        self._last_frame: QVideoFrame | None = None
        self._frames_seen = 0
        self._frameReady.connect(self._deliver_frame, Qt.ConnectionType.QueuedConnection)
        self._endReached.connect(self._on_end, Qt.ConnectionType.QueuedConnection)
        self._mp.event_manager().event_attach(
            vlc.EventType.MediaPlayerEndReached, lambda e: self._endReached.emit()
        )
        self._mp.event_manager().event_attach(
            vlc.EventType.MediaPlayerEncounteredError, lambda e: self._errorReached.emit()
        )
        self._errorReached.connect(self._on_error, Qt.ConnectionType.QueuedConnection)
        self._consecutive_errors = 0

        self._queue: list[dict] = []
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
        self._loop_a, self._loop_b = -1, -1   # A-B repeat (ms), -1 = unset
        self.audio_resume_min = 20 * 60_000   # audio shorter than this never resumes mid-way
        self.lastSnapshot = ""
        self._adjust = {"brightness": 1.0, "contrast": 1.0, "saturation": 1.0, "hue": 0.0, "gamma": 1.0}

        self._eq = vlc.AudioEqualizer()
        self._eq_enabled = False
        self._eq_preset = -1
        self._eq_bands = [0.0] * vlc.libvlc_audio_equalizer_get_band_count()
        self._eq_preamp = 0.0

        self._poll = QTimer(self)
        self._poll.setInterval(120)
        self._poll.timeout.connect(self._tick)
        self._poll.start()

    # ------------------------------------------------------------------ video
    def _on_vlc_frame(self, renderer: FrameRenderer):
        # Drop frames while the UI thread is still busy with the previous one,
        # so a slow UI never builds up a backlog (latency stays one frame).
        with self._pending_lock:
            if self._pending_busy:
                self._dropped += 1
                return
            self._pending_busy = True
        frame = renderer.to_video_frame()
        if frame is None:
            with self._pending_lock:
                self._pending_busy = False
            return
        self._frameReady.emit(frame)

    def _deliver_frame(self, frame: QVideoFrame):
        try:
            self._show_frame(frame)
        finally:
            with self._pending_lock:
                self._pending_busy = False

    def _show_frame(self, frame: QVideoFrame):
        first = self._frames_seen == 0
        self._frames_seen += 1
        size_changed = self._last_frame is None or self._last_frame.size() != frame.size()
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
        return self._last_frame.size().width() if self._last_frame else 0

    @Property(int, notify=videoSizeChanged)
    def videoHeight(self):
        return self._last_frame.size().height() if self._last_frame else 0

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
        self._queue = entries
        self.queueChanged.emit()
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
            self.message.emit(f"Início do trecho em {self._fmt(self._time)}. Aperte L de novo no fim.")
        elif self._loop_b < 0:
            if self._time <= self._loop_a + 500:
                self.message.emit("O fim do trecho precisa vir depois do início")
                return
            self._loop_b = self._time
            self.message.emit(f"Repetindo de {self._fmt(self._loop_a)} a {self._fmt(self._loop_b)}")
            self.seek(self._loop_a)
        else:
            self._loop_a = self._loop_b = -1
            self.message.emit("Repetição do trecho desligada")
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
        self._queue.extend(entries)
        self.queueChanged.emit()
        self.message.emit(f"{len(entries)} item(ns) adicionado(s) à fila")
        if was_empty:
            self._play_index(0)

    @Slot(str)
    def playNext(self, item):
        entries = [e for e in (self._entry(x) for x in self._flatten([item])) if e]
        if not entries:
            return
        pos = self._index + 1
        self._queue[pos:pos] = entries
        self.queueChanged.emit()
        self.message.emit("Vai tocar em seguida")
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
        del self._queue[index]
        if index < self._index:
            self._index -= 1
        elif index == self._index:
            if self._queue:
                self._play_index(min(index, len(self._queue) - 1))
            else:
                self.stop()
        self.queueChanged.emit()

    @Slot(int, int)
    def moveItem(self, src, dst):
        if not (0 <= src < len(self._queue) and 0 <= dst < len(self._queue)) or src == dst:
            return
        item = self._queue.pop(src)
        self._queue.insert(dst, item)
        if self._index == src:
            self._index = dst
        elif src < self._index <= dst:
            self._index -= 1
        elif dst <= self._index < src:
            self._index += 1
        self.queueChanged.emit()

    @Slot()
    def clearQueue(self):
        self.stop()
        self._queue = []
        self._index = -1
        self.queueChanged.emit()
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
        return {
            "path": x,
            "title": nice_title(x) if not is_url else x.rsplit("/", 1)[-1] or x,
            "isUrl": is_url,
            "audio": (not is_url) and is_audio_path(x),
        }

    def _play_index(self, index):
        self._save_resume()
        self._index = index
        e = self._queue[index]
        if e["isUrl"]:
            media = self._vlc.media_new_location(e["path"])
        else:
            media = self._vlc.media_new_path(e["path"])
            if self._start_at:
                media.add_option(f":start-time={self._start_at / 1000:.2f}")
                self._start_at = 0
            elif self._settings.value("resume", True):
                pos = self._settings.resume_position(e["path"])
                # songs always start from the top; only videos and long audio
                # (podcasts, audiobooks) pick up where they stopped
                if e["audio"] and self._settings.resume_length(e["path"]) < self.audio_resume_min:
                    pos = 0
                if pos > 0:
                    media.add_option(f":start-time={pos / 1000:.1f}")
                    self.message.emit("Continuando de onde você parou")
        self._mp.set_media(media)
        self._meta = {"title": e["title"], "artist": "", "album": "", "artwork": ""}
        self._audio_tracks, self._sub_tracks, self._tracks_polled = [], [], 0
        self._last_frame = None
        self._frames_seen = 0
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
        self._mp.play()
        self._mp.audio_set_volume(self._volume)
        self._mp.set_rate(self._rate)
        if self._eq_enabled:
            self._mp.set_equalizer(self._eq)
        self._apply_adjust()
        self._settings.add_history(e["path"], e["title"])
        self.mediaChanged.emit()
        self.queueChanged.emit()
        self.tracksChanged.emit()
        self.timeChanged.emit()
        self.videoSizeChanged.emit()

    def _on_error(self):
        """Unplayable item: tell the user and move on (unless everything failed)."""
        self._consecutive_errors += 1
        self._state = "error"
        self.stateChanged.emit()
        name = self._queue[self._index]["title"] if 0 <= self._index < len(self._queue) else ""
        self.message.emit(f"Não foi possível reproduzir “{name}”" if name else "Não foi possível reproduzir este arquivo")
        if self._consecutive_errors >= len(self._queue) or self._repeat == 2:
            return
        nxt = self._next_index(auto=True)
        if nxt is not None and nxt != self._index:
            QTimer.singleShot(0, lambda: self._play_index(nxt))

    def _on_end(self):
        if self._index >= 0 and self._index < len(self._queue):
            self._settings.set_resume(self._queue[self._index]["path"], 0, 0)
        nxt = self._next_index(auto=True)
        if nxt is None:
            self._mp.stop()
            self._state = "ended"
            self.stateChanged.emit()
        else:
            QTimer.singleShot(0, lambda: self._play_index(nxt))

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
        if self._state in ("idle", "ended", "stopped", "error") and self._queue:
            self._play_index(max(0, self._index))
            return
        self._mp.set_pause(0) if self._state == "paused" else self._mp.play()
        if self._state == "paused":
            self._expect("playing")

    @Slot()
    def pause(self):
        self._mp.set_pause(1)
        if self._state == "playing":
            self._expect("paused")

    @Slot()
    def stop(self):
        self._save_resume()
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

    @Slot(int)
    def seek(self, ms):
        if self._length > 0:
            ms = max(0, min(int(ms), self._length - 500))
            self._mp.set_time(ms)
            self._time = ms
            self.timeChanged.emit()

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
    def _tick(self):
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
        if new_state == "stopped" and self._state in ("ended", "idle"):
            new_state = self._state
        # watchdog: libVLC never left its initial state -> report instead of hanging silently
        if (new_state == "idle" and self._open_deadline and time.time() > self._open_deadline):
            self._open_deadline = 0
            new_state = "error"
            self.message.emit("Não foi possível abrir esta mídia")
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
            self._state = new_state
            self.stateChanged.emit()
        if new_state in ("playing", "paused", "loading"):
            t, ln = max(0, self._mp.get_time()), max(0, self._mp.get_length())
            if t != self._time or ln != self._length:
                self._time, self._length = t, ln
                self.timeChanged.emit()
            if self._loop_b > 0 and new_state == "playing" and self._time >= self._loop_b:
                self.seek(self._loop_a)
            if self._tracks_polled < 25 and new_state == "playing":
                self._tracks_polled += 1
                if self._tracks_polled in (3, 10, 25):
                    self._refresh_tracks()
                    self._refresh_meta()
        if self._sleep_deadline and time.time() >= self._sleep_deadline:
            self._sleep_deadline = 0
            self.pause()
            self.modeChanged.emit()
            self.message.emit("Timer de sono: reprodução pausada")

    def _refresh_meta(self):
        m = self._mp.get_media()
        if not m:
            return
        title = m.get_meta(vlc.Meta.Title) or ""
        entry = self._queue[self._index] if 0 <= self._index < len(self._queue) else None
        # libVLC returns the filename as title when no tag exists – keep our nicer one
        if entry and (not title or title == os.path.basename(entry["path"])):
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

    # ------------------------------------------------------------ properties
    @Property(str, notify=stateChanged)
    def state(self):
        return self._state

    @Property(bool, notify=stateChanged)
    def playing(self):
        return self._state == "playing"

    @Property(bool, notify=queueChanged)
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

    @Property(int, notify=queueChanged)
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
        return self._mp.audio_get_track()

    @Property(int, notify=tracksChanged)
    def subtitleTrack(self):
        return self._mp.video_get_spu()

    @Slot(int)
    def setAudioTrack(self, tid):
        self._mp.audio_set_track(tid)
        self.tracksChanged.emit()

    @Slot(int)
    def setSubtitleTrack(self, tid):
        self._mp.video_set_spu(tid)
        self.tracksChanged.emit()

    @Slot(str)
    def addSubtitle(self, path):
        if path.startswith("file:"):
            path = QUrl(path).toLocalFile()
        uri = QUrl.fromLocalFile(path).toString()
        ok = self._mp.add_slave(vlc.MediaSlaveType.subtitle, uri, True) == 0
        self.message.emit("Legenda carregada" if ok else "Não foi possível carregar a legenda")
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

    def _set_volume(self, v):
        v = max(0, min(150, int(v)))
        if v != self._volume:
            self._volume = v
            self._mp.audio_set_volume(v)
            if self._muted and v > 0:
                self._muted = False
                self._mp.audio_set_mute(False)
            self._settings.setValue("volume", v)
            self.volumeChanged.emit()

    volume = Property(int, _get_volume, _set_volume, notify=volumeChanged)

    @Property(bool, notify=volumeChanged)
    def muted(self):
        return self._muted

    @Slot()
    def toggleMute(self):
        self._muted = not self._muted
        self._mp.audio_set_mute(self._muted)
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
        self.message.emit(f"Timer de sono: {minutes} min" if minutes > 0 else "Timer de sono desligado")

    # equalizer -------------------------------------------------------------
    EQ_PT = {
        "Flat": "Plano", "Classical": "Clássica", "Club": "Clube", "Dance": "Dança",
        "Full bass": "Graves", "Full bass and treble": "Graves e agudos", "Full treble": "Agudos",
        "Headphones": "Fones de ouvido", "Large Hall": "Grande salão", "Live": "Ao vivo",
        "Party": "Festa", "Soft": "Suave", "Soft rock": "Rock suave",
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
            self.message.emit("Nada para capturar")
            return
        img = self._last_frame.toImage()
        folder = Path(self._settings.value("snapshotDir"))
        folder.mkdir(parents=True, exist_ok=True)
        stem = Path(self.currentPath).stem if not self.currentPath.count("://") else "stream"
        name = f"{stem or 'captura'}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.png"
        path = folder / name
        if img.save(str(path)):
            self.lastSnapshot = str(path)
            self.message.emit(f"Captura salva em {folder.name}")
        else:
            self.message.emit("Não foi possível salvar a captura")

    def save_state(self):
        self._save_resume()

    def shutdown(self):
        self._poll.stop()
        self._sinks.clear()
        self._mp.stop()
        self._mp.release()
