"""A matrix of real-world media files for the robustness suite, generated locally with libVLC.

Every item says what it is and what the app must do with it. Encoders missing from the bundled
libVLC (it varies by build) just leave that item out; the suite reports which ones were skipped.
Generated once into a cache folder and reused (generation takes a minute or two).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.fixtures import frame_png, tone, transcode

# name, extension, sout chain (without dst), size, kind, seconds
_AV = "acodec={acodec},ab=96,channels=2,samplerate=44100"
VIDEO_ITEMS = [
    ("h264_aac", ".mp4", "#transcode{vcodec=h264,vb=500,fps=25,width=640,height=360,venc=x264{keyint=25}," + _AV.format(acodec="mp4a") + "}:std{access=file,mux=mp4", (640, 360)),
    ("h264_mov", ".mov", "#transcode{vcodec=h264,vb=500,fps=25,width=640,height=360,venc=x264{keyint=25}," + _AV.format(acodec="mp4a") + "}:std{access=file,mux=mov", (640, 360)),
    ("vp8_vorbis", ".webm", "#transcode{vcodec=VP80,vb=500,fps=25,width=640,height=360," + _AV.format(acodec="vorb") + "}:std{access=file,mux=webm", (640, 360)),
    ("theora_vorbis", ".ogv", "#transcode{vcodec=theo,vb=500,fps=25,width=640,height=360," + _AV.format(acodec="vorb") + "}:std{access=file,mux=ogg", (640, 360)),
    ("mpeg4_mp3", ".avi", "#transcode{vcodec=mp4v,vb=600,fps=25,width=640,height=360," + _AV.format(acodec="mpga") + "}:std{access=file,mux=avi", (640, 360)),
    ("h264_ts", ".ts", "#transcode{vcodec=h264,vb=500,fps=25,width=640,height=360,venc=x264{keyint=25}," + _AV.format(acodec="mpga") + "}:std{access=file,mux=ts", (640, 360)),
    ("mpeg2", ".mpg", "#transcode{vcodec=mp2v,vb=900,fps=25,width=640,height=360," + _AV.format(acodec="mpga") + "}:std{access=file,mux=ps", (640, 360)),
    ("vertical", ".mp4", "#transcode{vcodec=h264,vb=500,fps=30,width=360,height=640,venc=x264{keyint=30}," + _AV.format(acodec="mp4a") + "}:std{access=file,mux=mp4", (360, 640)),
    ("odd_1366x768", ".mkv", "#transcode{vcodec=h264,vb=500,fps=25,width=1366,height=768,venc=x264{keyint=25}," + _AV.format(acodec="vorb") + "}:std{access=file,mux=mkv", (1366, 768)),
    ("uhd_3840x2160", ".mkv", "#transcode{vcodec=h264,vb=4000,fps=25,width=3840,height=2160,venc=x264{keyint=25,preset=ultrafast}," + _AV.format(acodec="vorb") + "}:std{access=file,mux=mkv", (3840, 2160)),
    # HEVC in MP4 (this libVLC's MKV muxer writes x265 timestamps the demuxer cannot clock)
    ("hevc", ".mp4", "#transcode{vcodec=hevc,vb=500,fps=25,width=640,height=360,venc=x265{keyint=25,bframes=0}," + _AV.format(acodec="mp4a") + "}:std{access=file,mux=mp4", (640, 360)),
    ("video_only", ".mkv", "#transcode{vcodec=h264,vb=400,fps=25,width=640,height=360,venc=x264{keyint=25},acodec=none}:std{access=file,mux=mkv", (640, 360)),
]
AUDIO_ITEMS = [
    ("mp3", ".mp3", "#transcode{vcodec=none,acodec=mp3,ab=128,channels=2,samplerate=44100}:std{access=file,mux=raw"),
    ("vorbis", ".ogg", "#transcode{vcodec=none,acodec=vorb,ab=128,channels=2,samplerate=44100}:std{access=file,mux=ogg"),
    ("opus", ".opus", "#transcode{vcodec=none,acodec=opus,ab=96,channels=2,samplerate=48000}:std{access=file,mux=ogg"),
    ("aac", ".m4a", "#transcode{vcodec=none,acodec=mp4a,ab=128,channels=2,samplerate=44100}:std{access=file,mux=mp4"),
    ("flac", ".flac", "#transcode{vcodec=none,acodec=flac,channels=2,samplerate=44100}:std{access=file,mux=raw"),
    ("mono_22k", ".wav", None),
    ("surround_51", ".mkv", "#transcode{vcodec=none,acodec=vorb,ab=256,channels=6,samplerate=48000}:std{access=file,mux=mkv"),
]

SUBS = {
    "srt": "1\n00:00:00,500 --> 00:00:09,000\nLegenda SRT com acentuação: ação, café\n\n",
    "vtt": "WEBVTT\n\n00:00:00.500 --> 00:00:09.000\nLegenda VTT de teste\n\n",
    "ass": ("[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,28,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,20,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:00.50,0:00:09.00,Default,,0,0,0,,Legenda ASS de teste\n"),
}


def _ogg_crc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else (crc << 1)
            crc &= 0xFFFFFFFF
    return crc


def _ogg_pages(data: bytes):
    i = 0
    while i < len(data):
        assert data[i:i + 4] == b"OggS", "not an Ogg page"
        nseg = data[i + 26]
        lacing = data[i + 27:i + 27 + nseg]
        body = i + 27 + nseg
        size = sum(lacing)
        yield {"head": bytearray(data[i:i + 27]), "lacing": list(lacing), "body": data[body:body + size]}
        i = body + size


def _ogg_page(head: bytearray, seq: int, segments: list[int], body: bytes) -> bytes:
    h = bytearray(head)
    h[18:22] = seq.to_bytes(4, "little")
    h[22:26] = b"\0\0\0\0"
    h[26] = len(segments)
    page = bytes(h) + bytes(segments) + body
    crc = _ogg_crc(page)
    return page[:22] + crc.to_bytes(4, "little") + page[26:]


def add_ogg_chapters(src: Path, dst: Path, chapters: list[tuple[int, str]]):
    """Copy an Ogg Vorbis file adding CHAPTERxxx / CHAPTERxxxNAME comments, which libVLC's Ogg
    demuxer reads as chapters (libVLC's own muxers cannot write chapters)."""
    pages = list(_ogg_pages(src.read_bytes()))
    # split the first pages into packets until the three Vorbis headers are complete
    packets, cur, used = [], b"", 0
    for pi, pg in enumerate(pages):
        off = 0
        for lv in pg["lacing"]:
            cur += pg["body"][off:off + lv]
            off += lv
            if lv < 255:
                packets.append(cur)
                cur = b""
        used = pi + 1
        if len(packets) >= 3:
            break
    ident, comment, setup = packets[:3]
    assert comment[:7] == b"\x03vorbis"
    p = 7
    vlen = int.from_bytes(comment[p:p + 4], "little"); p += 4 + vlen
    n = int.from_bytes(comment[p:p + 4], "little"); p += 4
    items = []
    for _ in range(n):
        ln = int.from_bytes(comment[p:p + 4], "little"); p += 4
        items.append(comment[p:p + ln]); p += ln
    for k, (ms, name) in enumerate(chapters, 1):
        s = ms // 1000
        items.append(f"CHAPTER{k:03d}={s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}.{ms % 1000:03d}".encode())
        items.append(f"CHAPTER{k:03d}NAME={name}".encode("utf8"))
    new_comment = comment[:11 + vlen] + len(items).to_bytes(4, "little") + \
        b"".join(len(x).to_bytes(4, "little") + x for x in items) + b"\x01"

    def lace(pkt):
        segs = [255] * (len(pkt) // 255) + [len(pkt) % 255]
        return segs
    out = []
    seq = 0
    first_head = pages[0]["head"]
    out.append(_ogg_page(first_head, seq, lace(ident), ident)); seq += 1
    head2 = bytearray(pages[1]["head"])
    head2[5] = 0                                  # fresh packets, not a continuation
    head2[6:14] = (0).to_bytes(8, "little")       # header pages carry granule 0
    segs, body = [], b""
    for pkt in (new_comment, setup):
        segs += lace(pkt)
        body += pkt
    while segs:                                   # at most 255 segments per page
        take = segs[:255]
        size = sum(take)
        out.append(_ogg_page(head2, seq, take, body[:size])); seq += 1
        segs, body = segs[255:], body[size:]
        head2[5] = 1 if segs and take[-1] == 255 else 0
    for pg in pages[used:]:
        out.append(_ogg_page(pg["head"], seq, pg["lacing"], pg["body"])); seq += 1
    dst.write_bytes(b"".join(out))


def _probe(vlc_mod, path: Path) -> dict:
    """What libVLC itself finds in the file (tracks and declared length), without the app."""
    import time
    inst = vlc_mod.Instance(["--quiet", "--verbose=-1", "--intf", "dummy", "--vout", "dummy", "--aout", "adummy"])
    m = inst.media_new_path(str(path))
    m.parse_with_options(vlc_mod.MediaParseFlag.local, 5000)
    t0 = time.time()
    while time.time() - t0 < 6 and getattr(m.get_parsed_status(), "value", m.get_parsed_status()) < 1:
        time.sleep(0.05)
    kinds = [getattr(t.type, "value", t.type) for t in (m.tracks_get() or [])]
    length = max(0, m.get_duration())
    if not kinds:
        # some containers (MPEG-PS) only reveal their streams once they play
        mp = inst.media_player_new()
        mp.set_media(m)
        mp.play()
        t0 = time.time()
        while time.time() - t0 < 4 and not (mp.has_vout() or (mp.get_time() > 300)):
            time.sleep(0.05)
        kinds = [getattr(t.type, "value", t.type) for t in (m.tracks_get() or [])]
        length = length or max(0, mp.get_length())
        mp.stop()
        mp.release()
    info = {"video": 1 in kinds, "audio": 0 in kinds, "length": length}
    m.release()
    inst.release()
    return info


def make_formats(root: Path) -> dict:
    """Returns {"video": [...], "audio": [...], "subs": {...}, "bad": {...}, "names": [...], "skipped": [...]}.
    Each media entry: {"name", "path", "size": (w, h) | None}."""
    import vlc

    root.mkdir(parents=True, exist_ok=True)
    src = root / "_src"
    src.mkdir(exist_ok=True)
    png, wav = src / "frame.png", src / "tone.wav"
    if not png.exists():
        frame_png(png)
    if not wav.exists():
        tone(wav, 12.0, 440)
    out = {"video": [], "audio": [], "subs": {}, "bad": {}, "names": [], "skipped": []}

    vdir = root / "video"
    vdir.mkdir(exist_ok=True)
    for name, ext, chain, size in VIDEO_ITEMS:
        dst = vdir / f"{name}{ext}"
        if not dst.exists():
            secs = 4 if "uhd" in name else 10
            opts = [f":image-duration={secs}", ":image-fps=25"]
            if "acodec=none" not in chain:
                opts.append(f":input-slave={wav.as_uri()}")
            source = png
            if size[1] > size[0]:   # portrait: start from a portrait picture (square pixels)
                source = src / "frame_portrait.png"
                if not source.exists():
                    frame_png(source, 360, 640)
            transcode(vlc, source, dst, chain + f",dst='{dst.as_posix()}'}}", options=opts, seconds_hint=secs)
        info = _probe(vlc, dst) if dst.exists() and dst.stat().st_size > 1000 else None
        if info and info["video"]:
            out["video"].append({"name": name, "path": dst, "size": size, "length": info["length"]})
        else:
            # the bundled libVLC lacks that encoder (e.g. VP8): the file came out without video
            dst.unlink(missing_ok=True)
            out["skipped"].append(f"{name} (no {'video' if info else 'file'} from this libVLC)")

    adir = root / "audio"
    adir.mkdir(exist_ok=True)
    for name, ext, chain in AUDIO_ITEMS:
        dst = adir / f"{name}{ext}"
        if not dst.exists():
            if chain is None:   # plain wave, odd format
                import math, struct, wave
                with wave.open(str(dst), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
                    w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 300 * i / 22050)))
                                           for i in range(22050 * 8)))
            else:
                transcode(vlc, wav, dst, chain + f",dst='{dst.as_posix()}'}}", seconds_hint=12)
        info = _probe(vlc, dst) if dst.exists() and dst.stat().st_size > 1000 else None
        if info and info["audio"]:
            # length 0: the stream does not say how long it is (raw FLAC without the total sample
            # count) – the player can only learn it by playing to the end
            out["audio"].append({"name": name, "path": dst, "size": None, "length": info["length"]})
        else:
            dst.unlink(missing_ok=True)
            out["skipped"].append(f"{name} (no audio from this libVLC)")

    vorbis = adir / "vorbis.ogg"
    if vorbis.exists():
        ch = adir / "capitulos.ogg"
        if not ch.exists():
            add_ogg_chapters(vorbis, ch, [(0, "Abertura"), (4000, "Meio"), (8000, "Final")])
        out["chapters"] = ch

    sdir = root / "subs"
    sdir.mkdir(exist_ok=True)
    for ext, text in SUBS.items():
        p = sdir / f"legenda.{ext}"
        p.write_text(text, encoding="utf8")
        out["subs"][ext] = p
    # a Latin-1 subtitle (what most Brazilian .srt downloads still are)
    p = sdir / "legenda_latin1.srt"
    p.write_bytes(SUBS["srt"].encode("cp1252"))
    out["subs"]["srt_latin1"] = p

    # broken files: the app must say so and stay usable, never hang or crash
    bdir = root / "bad"
    bdir.mkdir(exist_ok=True)
    base = next((v["path"] for v in out["video"] if v["name"] == "h264_aac"), None)
    (bdir / "vazio.mp4").write_bytes(b"")
    out["bad"]["empty"] = bdir / "vazio.mp4"
    if base:
        data = base.read_bytes()
        (bdir / "cortado.mp4").write_bytes(data[: len(data) // 3])
        out["bad"]["truncated"] = bdir / "cortado.mp4"
        (bdir / "sem_extensao").write_bytes(data)
        out["bad"]["no_extension"] = bdir / "sem_extensao"
        (bdir / "extensao_errada.mp3").write_bytes(data)   # a video named .mp3
        out["bad"]["wrong_extension"] = bdir / "extensao_errada.mp3"
    (bdir / "texto.mkv").write_text("isto não é um vídeo\n" * 200, encoding="utf8")
    out["bad"]["text_as_video"] = bdir / "texto.mkv"

    # names the Windows file system allows but naive code trips on
    ndir = root / "nomes"
    ndir.mkdir(exist_ok=True)
    if base:
        for nm in ("Vídeo com acentuação — ção.mp4", "日本語のビデオ.mp4", "Filme 🎬 emoji.mp4",
                   "nome com '[colchetes]' & %20 #hash.mp4", "a" * 120 + ".mp4"):
            p = ndir / nm
            if not p.exists():
                shutil.copyfile(base, p)
            out["names"].append(p)
        # past MAX_PATH (260): long-path-aware code is needed to even open it
        deep = ndir
        for i in range(6):
            deep = deep / (f"pasta_bem_comprida_{i}_" + "x" * 30)
        long_target = Path("\\\\?\\" + str(deep)) if os.name == "nt" else deep
        try:
            os.makedirs(long_target, exist_ok=True)
            lp = long_target / "video no caminho longo.mp4"
            if not lp.exists():
                shutil.copyfile(base, lp)
            out["long_path"] = Path(str(lp).replace("\\\\?\\", "", 1))
        except OSError:
            out["skipped"].append("long_path")
    return out


if __name__ == "__main__":
    import sys
    import time
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import main  # noqa: F401
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication(sys.argv)
    t = time.time()
    r = make_formats(Path(sys.argv[1]))
    for k in ("video", "audio"):
        for it in r[k]:
            print(k, it["name"], it["path"].stat().st_size)
    print("bad", list(r["bad"]), "names", len(r["names"]), "skipped", r["skipped"], "long", r.get("long_path"))
    print("took", round(time.time() - t, 1), "s")
