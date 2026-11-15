"""Real-time timeline hover thumbnail previewer with exact paused-frame seeking."""
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

STORYBOARD_SLOTS = 64


def _progressive_order(n: int) -> list[int]:
    """Return indices ``0..n-1`` in coarse-to-fine binary subdivision order."""
    if n <= 0:
        return []
    seen = [False] * n
    order: list[int] = []
    step = n // 2
    while step >= 1:
        for i in range(0, n, step):
            if not seen[i]:
                seen[i] = True
                order.append(i)
        step //= 2
    for i in range(n):
        if not seen[i]:
            order.append(i)
    return order


PROGRESSIVE_INDICES = _progressive_order(STORYBOARD_SLOTS)


class ScrubImageProvider(QQuickImageProvider):
    """In-memory LRU image provider serving ``image://scrub/<media_id>/<ms>``."""

    def __init__(self, capacity: int = 320):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._capacity = capacity
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, QImage] = OrderedDict()
        self._by_media: dict[str, set[int]] = {}

    def put(self, mid: str, ms: int, img: QImage) -> str:
        key = f"{mid}/{ms}"
        with self._lock:
            self._cache[key] = img
            self._cache.move_to_end(key)
            self._by_media.setdefault(mid, set()).add(ms)
            while len(self._cache) > self._capacity:
                old_key, _ = self._cache.popitem(last=False)
                if "/" in old_key:
                    omid, ob = old_key.split("/", 1)
                    s = self._by_media.get(omid)
                    if s is not None:
                        s.discard(int(ob))
        return f"image://scrub/{key}"

    def get(self, key: str) -> QImage | None:
        with self._lock:
            img = self._cache.get(key)
            if img is not None:
                self._cache.move_to_end(key)
            return img

    def lookup(self, mid: str, exact_ms: int, max_delta_ms: int) -> tuple[str, bool]:
        """Return ``(url, is_exact)`` for ``exact_ms`` or closest frame within ``max_delta_ms``."""
        exact_key = f"{mid}/{exact_ms}"
        with self._lock:
            if exact_key in self._cache:
                self._cache.move_to_end(exact_key)
                return f"image://scrub/{exact_key}", True
            buckets = self._by_media.get(mid)
            if not buckets:
                return "", False
            closest = min(buckets, key=lambda b: abs(b - exact_ms))
            if abs(closest - exact_ms) <= max_delta_ms:
                return f"image://scrub/{mid}/{closest}", False
            return "", False

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
    """Exact paused-frame seeker + background storyboard pre-warmer for SeekBar hover previews."""

    previewReady = Signal(str, int, str)  # path, target_ms, image://scrub/...

    def __init__(self, provider: ScrubImageProvider, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._pending: tuple[str, str, int, int] | None = None
        self._warm_target: tuple[str, str, int] | None = None
        self._clear_requested = False
        self._stopping = False
        self._thread: threading.Thread | None = None

    @staticmethod
    def _media_id(path: str) -> str:
        return hashlib.sha1(path.encode("utf8", "ignore")).hexdigest()[:12]

    @staticmethod
    def _step(duration_ms: int) -> int:
        if duration_ms <= 0:
            return 1000
        return max(500, duration_ms // STORYBOARD_SLOTS)

    @staticmethod
    def _quantize(duration_ms: int, ms: int) -> int:
        """Snap hover timestamps to 250ms steps (or ~500 slots on long videos) for exact scene preview."""
        if duration_ms <= 0:
            return 0
        q = max(250, min(1000, duration_ms // 500))
        clamped = max(0, min(int(ms), max(0, duration_ms - 250)))
        return (clamped // q) * q

    def _ensure_thread_locked(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="scrub-previewer")
            self._thread.start()

    @Slot(str, int)
    def prepare(self, path: str, duration_ms: int) -> None:
        """Start warming the decoder and coarse-to-fine storyboard as soon as the user moves the mouse."""
        if not path or duration_ms <= 0 or "://" in path:
            return
        if not Path(path).is_file():
            return
        mid = self._media_id(path)
        with self._cv:
            if self._stopping:
                return
            if self._warm_target and self._warm_target[0] == path and self._warm_target[2] == int(duration_ms):
                return
            self._ensure_thread_locked()
            self._warm_target = (path, mid, int(duration_ms))
            self._clear_requested = False
            self._cv.notify()

    @Slot(str, int, int, result=str)
    def request(self, path: str, duration_ms: int, ms: int) -> str:
        """Return cached URL (exact or nearby storyboard slot) and queue exact hover position."""
        if not path or duration_ms <= 0 or "://" in path:
            return ""
        mid = self._media_id(path)
        dur = int(duration_ms)
        target_ms = self._quantize(dur, ms)
        # Only fall back to a cached storyboard neighbor if it is close (within 1 storyboard step)
        max_delta = max(1200, self._step(dur))
        url, is_exact = self._provider.lookup(mid, target_ms, max_delta)
        if is_exact:
            return url

        if not Path(path).is_file():
            return url

        with self._cv:
            if self._stopping:
                return url
            self._ensure_thread_locked()
            self._warm_target = (path, mid, dur)
            self._pending = (path, mid, dur, target_ms)
            self._clear_requested = False
            self._cv.notify()
        return url

    @Slot()
    def clear(self) -> None:
        with self._cv:
            self._pending = None
            self._warm_target = None
            self._clear_requested = True
            self._cv.notify()

    def stop(self, timeout: float = 2.0) -> None:
        with self._cv:
            self._stopping = True
            self._pending = None
            self._warm_target = None
            self._clear_requested = True
            self._cv.notify_all()
            t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout)

    def _run(self) -> None:
        inst = vlc.Instance([
            "--quiet", "--verbose=-1", "--no-audio", "--no-spu",
            "--no-sub-autodetect-file", "--avcodec-fast",
            "--avcodec-hw=none", "--avcodec-threads=4",
            "--no-video-title-show", "--no-osd",
        ])
        cur_path = ""
        cur_dur = 0
        mp = None
        media = None
        renderer = None
        frame_event = threading.Event()
        latest_img: list[QImage | None] = [None]
        accept_frames = [False]
        warm_cursor = 0

        def release_player() -> None:
            nonlocal cur_path, cur_dur, mp, media, renderer, warm_cursor
            accept_frames[0] = False
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
            cur_dur = 0
            warm_cursor = 0

        def on_frame(r: FrameRenderer) -> None:
            if not accept_frames[0]:
                return
            img = r.to_image()
            if img is not None and not img.isNull():
                latest_img[0] = img.copy()
                frame_event.set()

        def ensure_open(path: str, dur: int) -> bool:
            nonlocal cur_path, cur_dur, mp, media, renderer, warm_cursor
            if path == cur_path and mp is not None:
                cur_dur = dur
                return True
            release_player()
            try:
                media = inst.media_new_path(path)
                media.add_option(":file-caching=20")
                mp = inst.media_player_new()
                renderer = FrameRenderer(mp, 256, on_frame)
                latest_img[0] = None
                frame_event.clear()
                accept_frames[0] = True
                mp.set_media(media)
                cur_path = path
                cur_dur = dur
                warm_cursor = 0
                mp.play()
                frame_event.wait(1.5)
                accept_frames[0] = False
                # Keep player PAUSED from now on: in paused state, mp.set_time(ms) decodes and
                # emits ONLY the exact target frame at `ms` (never leftover frames from playing).
                mp.set_pause(1)
                time.sleep(0.02)
                frame_event.clear()
                if latest_img[0] is not None and not latest_img[0].isNull():
                    mid0 = self._media_id(path)
                    url0 = self._provider.put(mid0, 0, latest_img[0])
                    self.previewReady.emit(path, 0, url0)
                return True
            except Exception:
                release_player()
                return False

        def grab_exact(mid: str, dur: int, target_ms: int, timeout: float = 0.65) -> str:
            key = f"{mid}/{target_ms}"
            if self._provider.get(key) is not None:
                return f"image://scrub/{key}"
            if mp is None:
                return ""
            try:
                if mp.get_state() == vlc.State.Playing:
                    mp.set_pause(1)
                clamped = max(0, min(int(target_ms), max(0, dur - 250)))
                latest_img[0] = None
                frame_event.clear()
                accept_frames[0] = True
                mp.set_time(clamped)
                deadline = time.monotonic() + timeout
                tol = max(600, dur // 100)
                while time.monotonic() < deadline and not self._stopping:
                    rem = deadline - time.monotonic()
                    if rem <= 0 or not frame_event.wait(min(0.15, rem)):
                        continue
                    cur_t = mp.get_time()
                    if cur_t < 0 or abs(cur_t - clamped) <= tol:
                        break
                    # A stale frame from an earlier interrupted seek arrived: wait for the real one
                    latest_img[0] = None
                    frame_event.clear()
                accept_frames[0] = False
                img = latest_img[0]
                if img is not None and not img.isNull():
                    return self._provider.put(mid, target_ms, img)
            except Exception:
                accept_frames[0] = False
            return ""

        try:
            while True:
                job = None
                warm = None
                with self._cv:
                    while not self._stopping and not self._clear_requested and self._pending is None:
                        if self._warm_target is not None and warm_cursor < STORYBOARD_SLOTS:
                            warm = self._warm_target
                            break
                        self._cv.wait()
                    if self._stopping:
                        break
                    if self._clear_requested:
                        self._clear_requested = False
                        release_player()
                        continue
                    if self._pending is not None:
                        job = self._pending
                        self._pending = None
                    elif self._warm_target is not None and warm_cursor < STORYBOARD_SLOTS:
                        warm = self._warm_target

                if job is not None:
                    path, mid, dur, target_ms = job
                    if not ensure_open(path, dur):
                        continue
                    url = grab_exact(mid, dur, target_ms, timeout=0.65)
                    if url and not self._stopping:
                        self.previewReady.emit(path, target_ms, url)
                    continue

                # 2. Background coarse-to-fine storyboard pre-warmer (paused seeks, yields to hover)
                if warm is not None:
                    path, mid, dur = warm
                    if not ensure_open(path, dur):
                        with self._lock:
                            if self._warm_target == warm:
                                self._warm_target = None
                        continue
                    slot_idx = PROGRESSIVE_INDICES[warm_cursor]
                    warm_cursor += 1
                    step = self._step(dur)
                    slot_ms = self._quantize(dur, slot_idx * step)
                    if self._provider.get(f"{mid}/{slot_ms}") is None:
                        url = grab_exact(mid, dur, slot_ms, timeout=0.55)
                        if url and not self._stopping:
                            self.previewReady.emit(path, slot_ms, url)
        finally:
            release_player()
            try:
                inst.release()
            except Exception:
                pass
