"""Saving a part of a video: as MP4, or as an animated GIF.

Each export runs on its own thread with its own libVLC instance, so playback is untouched:

  - MP4: the streams are copied, not re-encoded: no quality lost, HDR kept, fast. The clip starts
    at the keyframe at or before the start asked for (a cut elsewhere would start undecodable).
  - GIF: libVLC's scene filter writes the frames (480 px wide, 15 per second) and Pillow assembles
    them with one shared palette; HDR frames are tone mapped to sRGB first, like snapshots.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time

GIF_WIDTH = 480
GIF_FPS = 15
GIF_MAX_MS = 15_000


def _vlc_run(args: list[str], src: str, opts: list[str], timeout: float, discard_video=False) -> bool:
    import ctypes
    import vlc
    inst = vlc.Instance(["--quiet", "--verbose=-1", "--no-video-title-show", "--no-osd", "--no-stats"] + args)
    if inst is None:
        return False
    keep = []
    try:
        m = inst.media_new(src)
        for o in opts:
            m.add_option(o)
        p = inst.media_player_new()
        if discard_video:
            # pictures go to a buffer nobody reads: no window ("--vout=dummy" crashes this libVLC)
            buf = ctypes.create_string_buffer(64 * 36 * 4)
            LOCK = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
            UNLOCK = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
            DISPLAY = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)

            def lock(_o, planes):
                planes[0] = ctypes.cast(buf, ctypes.c_void_p)
                return None
            keep = [buf, LOCK(lock), UNLOCK(lambda _o, _p, _pl: None), DISPLAY(lambda _o, _p: None)]
            vlc.libvlc_video_set_callbacks(p, keep[1], keep[2], keep[3], None)
            vlc.libvlc_video_set_format(p, b"RV32", 64, 36, 64 * 4)
        p.set_media(m)
        p.play()
        end = time.time() + timeout
        done = {vlc.State.Ended, vlc.State.Error, vlc.State.Stopped}
        time.sleep(0.3)
        while time.time() < end and p.get_state() not in done:
            time.sleep(0.05)
        ok = p.get_state() == vlc.State.Ended
        p.stop()
        p.release()
        m.release()
        return ok
    finally:
        inst.release()
        del keep


def _sout_path(p: str) -> str:
    # libVLC's sout chain takes the path between quotes; quotes and backslashes in it are escaped
    return p.replace("\\", "\\\\").replace("'", "\\'")


def export_mp4(src: str, start_ms: int, end_ms: int, dst: str, hdr: bool = False) -> bool:
    if end_ms <= start_ms:
        return False
    tmp = dst + ".part.mp4"
    # streams copied: no quality lost, HDR kept, sound in sync. (libVLC 3's transcode after a
    # start-time offsets the sound by about the start time: measured 2 s late from a 2 s start.)
    chain = f"#std{{access=file,mux=mp4,dst='{_sout_path(tmp)}'}}"
    dur = (end_ms - start_ms) / 1000
    ok = _vlc_run([], src, [f":start-time={start_ms / 1000:.3f}", f":stop-time={end_ms / 1000:.3f}",
                            f":sout={chain}", ":sout-keep", ":no-sout-all"], timeout=max(30.0, dur * 4 + 20))
    if ok and os.path.exists(tmp) and os.path.getsize(tmp) > 1000:
        os.replace(tmp, dst)
        return True
    if os.path.exists(tmp):
        os.remove(tmp)
    return False


def export_gif(src: str, start_ms: int, end_ms: int, dst: str, fps_src: float, hdr: str = "") -> bool:
    end_ms = min(end_ms, start_ms + GIF_MAX_MS)
    if end_ms <= start_ms:
        return False
    work = tempfile.mkdtemp(prefix="refrata_gif_")
    try:
        ratio = max(1, int(round((fps_src or 30) / GIF_FPS)))
        dur = (end_ms - start_ms) / 1000
        _vlc_run(["--no-audio", "--video-filter=scene", "--scene-format=png",
                  f"--scene-ratio={ratio}", f"--scene-path={work}", "--scene-prefix=f", "--no-scene-replace",
                  f"--scene-width={GIF_WIDTH}"],
                 src, [f":start-time={start_ms / 1000:.3f}", f":stop-time={end_ms / 1000:.3f}", ":rate=4"],
                 timeout=dur * 3 + 20, discard_video=True)
        frames = sorted(f for f in os.listdir(work) if f.lower().endswith(".png"))
        if not frames:
            return False
        from PIL import Image
        from PySide6.QtGui import QColorSpace, QImage
        images = []
        for name in frames[: int(GIF_MAX_MS / 1000 * GIF_FPS) + 2]:
            img = QImage(os.path.join(work, name))
            if img.isNull():
                continue
            if hdr in ("pq", "hlg"):
                TF = QColorSpace.TransferFunction
                img = img.convertToFormat(QImage.Format.Format_RGBA64)
                img.setColorSpace(QColorSpace(QColorSpace.Primaries.Bt2020, TF.St2084 if hdr == "pq" else TF.Hlg))
                img = img.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
            img = img.convertToFormat(QImage.Format.Format_RGB888)
            buf = bytes(img.constBits())[: img.bytesPerLine() * img.height()]
            images.append(Image.frombuffer("RGB", (img.width(), img.height()), buf, "raw", "RGB", img.bytesPerLine(), 1))
        if not images:
            return False
        # one palette for the whole clip (no flicker between frames), dithered
        pal = images[len(images) // 2].quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        frames_p = [im.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG) for im in images]
        tmp = dst + ".part.gif"
        frames_p[0].save(tmp, save_all=True, append_images=frames_p[1:], loop=0,
                         duration=int(1000 * ratio / (fps_src or 30)), optimize=True, disposal=1)
        os.replace(tmp, dst)
        return True
    except Exception as exc:
        print("gif export:", exc)
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_async(fn, args, done):
    """fn(*args) on a worker thread; done(ok) afterwards (from that thread)."""
    def work():
        try:
            ok = bool(fn(*args))
        except Exception as exc:
            print("export:", exc)
            ok = False
        done(ok)
    t = threading.Thread(target=work, name="clip-export", daemon=True)
    t.start()
    return t
