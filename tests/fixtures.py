"""Synthetic test media, generated locally (no user files needed).

- WAV tones written with the `wave` module
- MKV video (H.264, 2 audio tracks) transcoded by libVLC from a generated PNG
- SRT subtitle, M3U / PLS / XSPF playlists, a corrupt "video" file
"""
from __future__ import annotations

import math
import os
import struct
import sys
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


def _hdr10plus_json(path: Path, frames: int, cut: int):
    """x265's HDR10+ input (the "LLC JSON"): one entry per frame, scene 0 peaking at 203 nits, scene
    1 (from frame `cut`) at 800. MaxScl and percentiles are in 0.1-nit units, as in the bitstream."""
    import json
    scenes = []
    for i in range(frames):
        hi = i >= cut
        peak = 8000 if hi else 2030
        scenes.append({
            "BezierCurveData": {"Anchors": [102, 205, 307, 410, 512, 614, 717, 819, 922],
                                "KneePointX": 100, "KneePointY": 200},
            "LuminanceParameters": {
                "AverageRGB": 900 if hi else 300,
                "LuminanceDistributions": {"DistributionIndex": [1, 5, 10, 25, 50, 75, 90, 95, 99],
                                           "DistributionValues": [10, 20, 40, 100, 200, 500, 1000, 1500,
                                                                  peak - 100]},
                "MaxScl": [peak, peak - 100, peak - 200]},
            "NumberOfWindows": 1, "TargetedSystemDisplayMaximumLuminance": 400,
            "SceneFrameIndex": i - (cut if hi else 0), "SceneId": int(hi), "SequenceFrameIndex": i})
    text = json.dumps({"JSONInfo": {"HDR10plusProfile": "B", "Version": "1.0"}, "SceneInfo": scenes})
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def make_pipeline(root: Path) -> dict:
    """Media for the video pipeline (HDR10, HLG, 8K, a 24 fps clip with sound), made with ffmpeg
    for the robustness suite only (tests/robust.py _fixtures; the e2e runs do not need them):
    the bundled libVLC encodes HEVC but cannot write the HDR colour description. Without ffmpeg on
    PATH these are left out and the scenarios that need them say so (robust.py feature_hdr_*).

    The HDR10 clip is an SDR test pattern converted to PQ with SDR white at 203 nits (BT.2408) and
    MaxCLL 203, so nothing needs compressing: brought back to SDR, it must match `hdr_ref`, its
    frame at 2 s converted by ffmpeg (PQ to light, BT.2020 to BT.709, sRGB)."""
    import shutil
    import subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        return {}
    root.mkdir(parents=True, exist_ok=True)
    src = "testsrc2=size=1280x720:rate=24"
    still = "smptehdbars=size=1280x720:rate=24"   # HDR: any frame equals the reference
    to_hdr = ("zscale=tin=bt709:min=bt709:pin=bt709:rin=tv:t={t}:m=2020_ncl:p=bt2020:r=tv:npl=203,"
              "format=yuv420p10le")
    x265 = "log-level=error:keyint=24:colorprim=bt2020:colormatrix=bt2020nc:transfer={t}{extra}"
    jobs = {
        "hdr10": ["-f", "lavfi", "-i", still, "-t", "6", "-vf", to_hdr.format(t="smpte2084"),
                  "-c:v", "libx265", "-preset", "ultrafast",
                  "-x265-params", x265.format(t="smpte2084", extra=":hdr10=1:max-cll=203,100:"
                                              "master-display=G(8500,39850)B(6550,2300)R(35400,14600)"
                                              "WP(15635,16450)L(2030000,50)"),
                  "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"],
        "hlg": ["-f", "lavfi", "-i", still, "-t", "6", "-vf", to_hdr.format(t="arib-std-b67"),
                "-c:v", "libx265", "-preset", "ultrafast", "-tag:v", "hvc1",
                "-x265-params", x265.format(t="arib-std-b67", extra=""),
                "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc"],
        # what a perfect conversion of hdr10 shows: its own frame at 2 s, PQ undone by ffmpeg
        "hdr_ref": ["-ss", "2", "-i", str(root / "hdr10.mkv"), "-frames:v", "1", "-vf",
                    "zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,"
                    "zscale=t=iec61966-2-1:m=bt709:r=pc,format=rgb24"],
        # the same picture, but its metadata claims 4000-nit highlights that never come: only a
        # tone mapper that measures the frame (shaders/peak.frag) shows it as graded
        "hdrlie": ["-f", "lavfi", "-i", still, "-t", "6", "-vf", to_hdr.format(t="smpte2084"),
                   "-c:v", "libx265", "-preset", "ultrafast",
                   "-x265-params", x265.format(t="smpte2084", extra=":hdr10=1:max-cll=4000,400:"
                                               "master-display=G(8500,39850)B(6550,2300)R(35400,14600)"
                                               "WP(15635,16450)L(40000000,50)"),
                   "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"],
        # HDR10+: two scenes, 203 nits for 3 s then 800 (hdr10plus.json, written below); the file's
        # static metadata says 1000, so the first scene only looks right with the scene's own peak
        "hdr10plus": ["-f", "lavfi", "-i", still, "-t", "6", "-vf", to_hdr.format(t="smpte2084"),
                      "-c:v", "libx265", "-preset", "ultrafast",
                      "-x265-params", x265.format(t="smpte2084", extra=":hdr10=1:hdr10-opt=1:"
                                                  "dhdr10-info=hdr10plus.json:max-cll=1000,400:"
                                                  "master-display=G(8500,39850)B(6550,2300)R(35400,14600)"
                                                  "WP(15635,16450)L(10000000,50)"),
                      "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"],
        "uhd8k": ["-f", "lavfi", "-i", "testsrc2=size=7680x4320:rate=24", "-t", "3",
                  "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error:keyint=24",
                  "-pix_fmt", "yuv420p"],
        # 4K60 for the frame-pacing checks (robust.py perf_4k60_*): H.264 SDR, and HEVC 10-bit HDR10+
        "uhd60": ["-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=60", "-t", "12", "-c:v", "libx264",
                  "-preset", "ultrafast", "-g", "60", "-b:v", "40M", "-pix_fmt", "yuv420p"],
        "uhd60hdr": ["-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=60", "-t", "12", "-vf",
                     to_hdr.format(t="smpte2084"), "-c:v", "libx265", "-preset", "ultrafast", "-b:v", "40M",
                     "-x265-params", x265.format(t="smpte2084", extra=":keyint=60:hdr10=1:hdr10-opt=1:"
                                                 "dhdr10-info=uhd60.json:max-cll=1000,400:"
                                                 "master-display=G(8500,39850)B(6550,2300)R(35400,14600)"
                                                 "WP(15635,16450)L(10000000,50)"),
                     "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"],
        "motion24": ["-f", "lavfi", "-i", src, "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                     "-t", "20", "-c:v", "libx264", "-preset", "ultrafast", "-g", "24", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-shortest"],
    }
    # speech for the subtitles made from the sound (Windows' own voice; elsewhere skipped)
    if sys.platform == "win32" and not (root / "speech.mp4").exists():
        wav = root / "speech.wav"
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.Rate = -1; $s.SetOutputToWaveFile('{wav}'); $s.Speak('Welcome to Refrata. This video player can "
              "now write subtitles from the sound. The quick brown fox jumps over the lazy dog. Every morning the "
              "city wakes up slowly. Thank you for watching this test.'); $s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=120)
        if wav.exists():
            jobs["speech"] = ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24", "-i", str(wav), "-shortest",
                              "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"]
    elif (root / "speech.mp4").exists():
        jobs["speech"] = []
    ext = {"hdr_ref": ".png", "hdr10": ".mkv", "hlg": ".mp4", "uhd8k": ".mp4", "motion24": ".mp4", "speech": ".mp4",
           "hdrlie": ".mkv", "hdr10plus": ".mkv", "uhd60": ".mp4", "uhd60hdr": ".mkv"}
    _hdr10plus_json(root / "hdr10plus.json", frames=144, cut=72)
    _hdr10plus_json(root / "uhd60.json", frames=720, cut=360)
    out = {}
    for name, args in jobs.items():
        dst = root / (name + ext[name])
        if not dst.exists():
            tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
            # cwd=root: x265 takes the HDR10+ file by a relative name (its option list splits on ':')
            r = subprocess.run([ff, "-v", "error", "-y", *args, str(tmp)], capture_output=True, timeout=600,
                               cwd=str(root))
            if r.returncode != 0 or not tmp.exists():
                print(f"pipeline fixture {name} not made: {r.stderr.decode(errors='replace')[-300:]}")
                tmp.unlink(missing_ok=True)
                continue
            tmp.replace(dst)
        out["pipe_" + name] = dst
    return out


if __name__ == "__main__":
    import sys
    from PySide6.QtGui import QGuiApplication

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import refrata.__main__  # noqa: F401  (sets libVLC paths)

    app = QGuiApplication(sys.argv)
    t = time.time()
    r = make_all(Path(sys.argv[1]))
    for k, v in r.items():
        print(k, v, v.stat().st_size if isinstance(v, Path) and v.is_file() else "")
    print("took", round(time.time() - t, 1), "s")
