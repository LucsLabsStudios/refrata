"""Synthetic test media, generated locally (no user files needed).

- WAV tones written with the `wave` module
- MKV video (H.264, 2 audio tracks) transcoded by libVLC from a generated PNG
- SRT subtitle, M3U / PLS / XSPF playlists, a corrupt "video" file
"""
from __future__ import annotations

import math
import os
import struct
import time
import wave
from pathlib import Path


def tone(path: Path, seconds: float, freq: float, rate=44100):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            v = int(9000 * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))


def frame_png(path: Path, w=640, h=360):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter

    img = QImage(w, h, QImage.Format.Format_RGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#FF7A2F"))
    g.setColorAt(1, QColor("#3E2BFF"))
    p.fillRect(0, 0, w, h, g)
    p.setPen(QColor("white"))
    f = QFont("Segoe UI")
    f.setPixelSize(64)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "Refrata")
    p.end()
    img.save(str(path))


def transcode(vlc_mod, src: Path, dst: Path, sout: str, options=(), seconds_hint=30):
    inst = vlc_mod.Instance(["--quiet", "--verbose=-1", "--no-video-title-show", "--intf", "dummy"])
    media = inst.media_new_path(str(src))
    for o in options:
        media.add_option(o)
    media.add_option(f":sout={sout}")
    media.add_option(":sout-keep")
    mp = inst.media_player_new()
    mp.set_media(media)
    mp.play()
    t0 = time.time()
    while time.time() - t0 < seconds_hint + 30:
        st = mp.get_state()
        if st in (vlc_mod.State.Ended, vlc_mod.State.Error, vlc_mod.State.Stopped) and time.time() - t0 > 0.5:
            break
        time.sleep(0.1)
    mp.stop()
    mp.release()
    inst.release()
    return dst.exists() and dst.stat().st_size > 1000


def make_all(root: Path) -> dict:
    import vlc

    root.mkdir(parents=True, exist_ok=True)
    media = root / "media"
    media.mkdir(exist_ok=True)
    music = media / "Musicas"
    music.mkdir(exist_ok=True)
    films = media / "Filmes"
    films.mkdir(exist_ok=True)
    out = {}

    out["tone_a"] = music / "Tom A 440.wav"
    out["tone_b"] = music / "Tom B 660.wav"
    out["tone_c"] = music / "Tom C 880.wav"
    tone(out["tone_a"], 3.0, 440)
    tone(out["tone_b"], 3.0, 660)
    tone(out["tone_c"], 3.0, 880)
    out["tone_long"] = root / "extras" / "tone_long.wav"
    out["tone_long"].parent.mkdir(exist_ok=True)
    tone(out["tone_long"], 40.0, 330)
    out["tone_alt"] = root / "extras" / "tone_alt.wav"
    tone(out["tone_alt"], 40.0, 550)

    png = root / "extras" / "frame.png"
    frame_png(png)

    # 30 s H.264 video with two audio tracks (from the two long tones)
    out["video"] = films / "Filme.Teste.2024.1080p.x264.mkv"
    if not out["video"].exists():
        ok = transcode(
            vlc, png, out["video"],
            # keyint=25 → one keyframe/cluster per second, so libVLC's clock advances smoothly
            "#transcode{vcodec=h264,vb=600,fps=25,width=640,height=360,venc=x264{keyint=25},"
            "acodec=vorb,ab=96,channels=2,samplerate=44100}"
            f":std{{access=file,mux=mkv,dst='{out['video'].as_posix()}'}}",
            options=[":image-duration=30", ":image-fps=25",
                     f":input-slave={out['tone_long'].as_uri()}#{out['tone_alt'].as_uri()}"],
        )
        if not ok:
            raise RuntimeError("could not create the test video with libVLC")
    # a second, shorter video (remux of the first, 30 s is fine) for queue tests
    out["video2"] = films / "Segundo Video.mkv"
    if not out["video2"].exists():
        out["video2"].write_bytes(out["video"].read_bytes())

    out["srt"] = root / "extras" / "legenda.srt"
    out["srt"].write_text(
        "1\n00:00:00,500 --> 00:00:25,000\nLegenda de teste do Refrata\n\n", encoding="utf8")

    out["corrupt"] = films / "corrompido.mp4"
    out["corrupt"].write_bytes(os.urandom(64 * 1024))

    pl = root / "extras"
    out["m3u"] = pl / "lista.m3u"
    out["m3u"].write_text(
        "#EXTM3U\n#EXTINF:3,Tom A\n../media/Musicas/Tom A 440.wav\n"
        f"{out['tone_b']}\n", encoding="utf8")
    out["pls"] = pl / "lista.pls"
    out["pls"].write_text(
        f"[playlist]\nFile2={out['tone_b']}\nFile1={out['tone_a']}\nNumberOfEntries=2\n", encoding="utf8")
    out["xspf"] = pl / "lista.xspf"
    out["xspf"].write_text(
        '<?xml version="1.0"?><playlist version="1" xmlns="http://xspf.org/ns/0/"><trackList>'
        f'<track><location>{out["tone_c"].as_uri()}</location></track>'
        f'<track><location>{out["tone_a"].as_uri()}</location></track>'
        "</trackList></playlist>", encoding="utf8")
    out["hls"] = pl / "stream.m3u8"
    out["hls"].write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n", encoding="utf8")
    out["media_root"] = media
    out["music_dir"] = music
    out["films_dir"] = films
    return out


if __name__ == "__main__":
    import sys
    from PySide6.QtGui import QGuiApplication

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import main  # noqa: F401  (sets libVLC paths)

    app = QGuiApplication(sys.argv)
    t = time.time()
    r = make_all(Path(sys.argv[1]))
    for k, v in r.items():
        print(k, v, v.stat().st_size if isinstance(v, Path) and v.is_file() else "")
    print("took", round(time.time() - t, 1), "s")
