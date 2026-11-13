"""On-demand timeline hover thumbnail previewer using an isolated libVLC instance."""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from pathlib import Path

import vlc
from PySide6.QtCore import QObject, QSize, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from .engine import FrameRenderer


class ScrubImageProvider(QQuickImageProvider):
    """In-memory LRU image provider serving ``image://scrub/<media_id>/<bucket_ms>``."""

    def __init__(self, capacity: int = 140):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._capacity = capacity
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, QImage] = OrderedDict()

    def put(self, key: str, img: QImage) -> None:
        with self._lock:
            self._cache[key] = img
            self._cache.move_to_end(key)
            while len(self._cache) > self._capacity:
                self._cache.popitem(last=False)

    def get(self, key: str) -> QImage | None:
        with self._lock:
            img = self._cache.get(key)
            if img is not None:
                self._cache.move_to_end(key)
            return img

    def clear_prefix(self, prefix: str) -> None:
        with self._lock:
            for k in [k for k in self._cache if k.startswith(prefix)]:
                del self._cache[k]

    def requestImage(self, id: str, size: QSize, requestedSize: QSize) -> QImage:
        img = self.get(id)
        if img is not None and not img.isNull():
            if size is not None:
                size.setWidth(img.width())
                size.setHeight(img.height())
            return img
        fallback = QImage(1, 1, QImage.Format.Format_ARGB32)
        fallback.fill(0)
        return fallback


class ScrubPreviewer(QObject):
    """Extracts 320px-wide video frames for SeekBar hover previews on a background thread."""

    previewReady = Signal(str, int, str)  # path, bucket_ms, image://scrub/...

    def __init__(self, provider: ScrubImageProvider, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._pending: tuple[str, str, int, int] | None = None
        self._clear_requested = False
        self._stopping = False
        self._thread: threading.Thread | None = None

    @staticmethod
    def _media_id(path: str) -> str:
        return hashlib.sha1(path.encode("utf8", "ignore")).hexdigest()[:12]

    @staticmethod
    def _bucket(duration_ms: int, ms: int) -> int:
        if duration_ms <= 0:
            return 0
        step = max(1000, min(15000, duration_ms // 120))
        clamped = max(0, min(int(ms), max(0, duration_ms - 250)))
        return (clamped // step) * step

    @Slot(str, int, int, result=str)
    def request(self, path: str, duration_ms: int, ms: int) -> str:
        """Return an ``image://scrub/...`` URL if cached, else queue extraction and return ``""``."""
        if not path or duration_ms <= 0 or "://" in path:
            return ""
        p = Path(path)
        if not p.is_file():
            return ""

        mid = self._media_id(path)
        bucket = self._bucket(duration_ms, ms)
        key = f"{mid}/{bucket}"
        if self._provider.get(key) is not None:
            return f"image://scrub/{key}"

        with self._cv:
            if self._stopping:
                return ""
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="scrub-previewer")
                self._thread.start()
            self._pending = (path, mid, int(duration_ms), bucket)
            self._clear_requested = False
            self._cv.notify()
        return ""

    @Slot()
    def clear(self) -> None:
        with self._cv:
            self._pending = None
            self._clear_requested = True
            self._cv.notify()

    def stop(self, timeout: float = 2.0) -> None:
        with self._cv:
            self._stopping = True
            self._pending = None
            self._clear_requested = True
            self._cv.notify_all()
            t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout)

    def _run(self) -> None:
        inst = vlc.Instance([
            "--quiet", "--verbose=-1", "--no-audio", "--no-spu",
            "--no-sub-autodetect-file", "--avcodec-hw=none",
            "--no-video-title-show", "--no-osd",
        ])
        cur_path = ""
        mp = None
        media = None
        renderer = None
        frame_event = threading.Event()
        latest_img: list[QImage | None] = [None]
        accept_frames = [False]

        def release_player() -> None:
            nonlocal cur_path, mp, media, renderer
            if mp is not None:
                try:
                    mp.stop()
                except Exception:
                    pass
                try:
                    mp.release()
                except Exception:
                    pass
                mp = None
            if media is not None:
                try:
                    media.release()
                except Exception:
                    pass
                media = None
            renderer = None
            cur_path = ""

        def on_frame(r: FrameRenderer) -> None:
            if not accept_frames[0]:
                return
            img = r.to_image()
            if img is not None and not img.isNull():
                latest_img[0] = img.copy()
                frame_event.set()

        try:
            while True:
                with self._cv:
                    while not self._stopping and self._pending is None and not self._clear_requested:
                        self._cv.wait(timeout=3.0)
                        if not self._stopping and self._pending is None and mp is not None:
                            # Idle timeout: release decoder when user stops hovering for 3s
                            self._clear_requested = True
                            break
                    if self._stopping:
                        break
                    if self._clear_requested:
                        self._clear_requested = False
                        release_player()
                        continue
                    job = self._pending
                    self._pending = None

                if job is None:
                    continue

                path, mid, duration_ms, bucket = job
                key = f"{mid}/{bucket}"
                if self._provider.get(key) is not None:
                    self.previewReady.emit(path, bucket, f"image://scrub/{key}")
                    continue

                if path != cur_path or mp is None:
                    release_player()
                    try:
                        media = inst.media_new_path(path)
                        mp = inst.media_player_new()
                        renderer = FrameRenderer(mp, 320, on_frame)
                        accept_frames[0] = False
                        mp.set_media(media)
                        cur_path = path
                        mp.play()
                        # Wait briefly for playback pipeline to start before seeking
                        t0 = time.monotonic()
                        while time.monotonic() - t0 < 2.0 and not self._stopping:
                            st = mp.get_state()
                            if st in (vlc.State.Playing, vlc.State.Paused):
                                break
                            if st in (vlc.State.Error, vlc.State.Ended):
                                break
                            time.sleep(0.02)
                        mp.set_pause(1)
                    except Exception:
                        release_player()
                        continue

                # If a newer hover position arrived while opening, skip straight to it
                with self._lock:
                    if self._pending is not None or self._stopping or self._clear_requested:
                        continue

                try:
                    latest_img[0] = None
                    frame_event.clear()
                    accept_frames[0] = True
                    mp.set_time(int(bucket))
                    # Nudge playback briefly if paused so libVLC decodes the target keyframe/frame
                    if mp.get_state() == vlc.State.Paused:
                        mp.play()
                    frame_event.wait(0.85)
                    accept_frames[0] = False
                    if mp.get_state() == vlc.State.Playing:
                        mp.set_pause(1)
                except Exception:
                    accept_frames[0] = False
                    release_player()
                    continue

                img = latest_img[0]
                if img is not None and not img.isNull() and not self._stopping:
                    self._provider.put(key, img)
                    self.previewReady.emit(path, bucket, f"image://scrub/{key}")
        finally:
            release_player()
            try:
                inst.release()
            except Exception:
                pass
