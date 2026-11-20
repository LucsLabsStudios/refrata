"""Subtitles made from the sound: speech recognition while the video plays (Faixas › Legendas por IA).

whisper.cpp (OpenAI's Whisper models) is compiled into refrata_native and runs on the GPU through
Vulkan (any NVIDIA, AMD or Intel graphics with a current driver), on the CPU otherwise:

  - the model is downloaded once, over HTTPS from the whisper.cpp project on Hugging Face, and
    checked against its SHA-256: large-v3-turbo (547 MB) with a graphics card, base (78 MB)
    without one. Measured on an RTX 2080: 13 s of speech in 0.65 s with large-v3-turbo.
  - the sound is taken from the file by a separate libVLC instance (16 kHz mono, from the point
    being watched), so playback is not touched, and read in 30-second pieces as it is written
  - each piece is transcribed (optionally translated into English) ahead of playback; a jump
    outside what is ready starts again from the new point
  - the text shows through the interface's subtitle overlay (tools.py), next to libVLC's own
    subtitles; the GIL is released for the whole recognition (no frame waits for it)
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from . import clips
from .i18n import tr
from .settings import DATA_DIR

MODELS = {
    # name: (file, bytes, sha256)
    "base": ("ggml-base-q8_0.bin", 81768585, "c577b9a86e7e048a0b7eada054f4dd79a56bbfa911fbdacf900ac5b567cbb7d9"),
    "turbo": ("ggml-large-v3-turbo-q5_0.bin", 574041195, "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"),
    # large-v3-turbo was trained without the translation task (OpenAI says so: it barely
    # translates); for "translate into English" the medium model, which does
    "medium": ("ggml-medium-q5_0.bin", 539212467, "19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f"),
}
# how the words become subtitles (build_cues): the usual rules of subtitling
LINE_CHARS = 42        # per line, two lines at most
CUE_MAX_MS = 5500      # longest a subtitle stays
CUE_MIN_MS = 1000      # shortest
PAUSE_MS = 900         # a pause this long starts a new subtitle (pauses inside a sentence: 0.3-0.7 s)
FINISH_MS = 1500       # a subtitle may stay this much longer to end its sentence (no lone "som.")
SENTENCE_END = tuple(".?!…。！？")
URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
RATE = 16000
CHUNK_S = 30
AHEAD_S = 150          # transcribe this far ahead of playback, then wait
# tests share one copy of the models (REFRATA_MODEL_DIR) instead of downloading them per run
MODEL_DIR = Path(os.environ["REFRATA_MODEL_DIR"]) if os.environ.get("REFRATA_MODEL_DIR") else DATA_DIR / "models"


def native_ok() -> bool:
    try:
        import PySide6.QtMultimedia  # noqa: F401
        import refrata_native
        if not hasattr(refrata_native, "Transcriber"):
            return False
    except Exception:
        return False
    import sys
    if sys.platform == "win32":
        # the module loads vulkan-1.dll only when speech recognition starts: it must be there
        import ctypes
        try:
            ctypes.WinDLL("vulkan-1")
        except OSError:
            return False
    return True


def pick_model(translate: bool = False) -> str:
    """With a graphics card of 3 GB or more: turbo to transcribe, medium to translate (turbo
    cannot); base otherwise."""
    from . import hdrdisplay
    big = any(g["vram_mb"] >= 3000 for g in hdrdisplay.gpus())
    if not big:
        return "base"
    return "medium" if translate else "turbo"


def _wrap(text: str) -> str:
    """One line, or two of about the same length broken at a space."""
    if len(text) <= LINE_CHARS:
        return text
    mid = len(text) // 2
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if not spaces:
        return text
    cut = min(spaces, key=lambda i: abs(i - mid))
    return text[:cut] + "\n" + text[cut + 1:]


def build_cues(words: list) -> list:
    """Words with their times [(start ms, end ms, word)] -> subtitles [(start, end, text)].

    A new subtitle at a sentence's end, at a pause, when two lines are full or after CUE_MAX_MS;
    each shows from its first word to just after its last, never over the next one. (Whisper's
    own segments are up to 30 s of text: shown as they came, a whole paragraph stood on screen
    while the speech went on.)"""
    cues, cur = [], []

    def flush():
        if cur:
            cues.append([cur[0][0], cur[-1][1] + 250, _wrap(" ".join(w for _, _, w in cur))])
            cur.clear()
    for t0, t1, raw in words:
        w = raw.strip()
        if not w:
            continue
        if cur:
            prev = cur[-1][2]
            length = sum(len(x) + 1 for _, _, x in cur) + len(w)
            # long enough already, unless this word ends the sentence a moment later
            too_long = t1 - cur[0][0] > CUE_MAX_MS and not (
                w.endswith(SENTENCE_END) and t1 - cur[0][0] <= CUE_MAX_MS + FINISH_MS)
            if (t0 - cur[-1][1] > PAUSE_MS or too_long or length > 2 * LINE_CHARS
                    or (prev.endswith(SENTENCE_END) and len(cur) >= 2)
                    or (raw.startswith("\n") and prev.endswith(SENTENCE_END + (",", ";", ":")))):
                flush()
        cur.append((t0, t1, w))
    flush()
    for i, c in enumerate(cues):
        c[1] = max(c[1], c[0] + CUE_MIN_MS)
        if i + 1 < len(cues):
            c[1] = max(c[0] + 200, min(c[1], cues[i + 1][0] - 40))
    return [tuple(c) for c in cues]


def model_path(name: str):
    return MODEL_DIR / MODELS[name][0]


def model_ready(name: str) -> bool:
    p = model_path(name)
    return p.exists() and p.stat().st_size == MODELS[name][1]


def download_model(name: str, progress=None, stop=None) -> bool:
    """Download and verify a model; progress(fraction) as it goes. False on any failure."""
    fname, size, sha = MODELS[name]
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    dst = model_path(name)
    tmp = dst.with_suffix(".part")
    h = hashlib.sha256()
    try:
        req = urllib.request.Request(URL + fname, headers={"User-Agent": "Refrata"})
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            if not r.geturl().startswith("https://"):
                return False
            got = 0
            while True:
                if stop is not None and stop.is_set():
                    return False
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                h.update(b)
                got += len(b)
                if progress:
                    progress(min(1.0, got / size))
        if got != size or h.hexdigest() != sha:
            tmp.unlink(missing_ok=True)
            return False
        os.replace(tmp, dst)
        return True
    except OSError:
        tmp.unlink(missing_ok=True)
        return False


def extract_audio(src: str, start_ms: int, dst: str, stop: threading.Event) -> bool:
    """16 kHz mono 16-bit WAV of the sound from start_ms to the end (libVLC, as fast as it goes)."""
    chain = (f"#transcode{{vcodec=none,acodec=s16l,channels=1,samplerate={RATE}}}"
             f":std{{access=file,mux=wav,dst='{clips._sout_path(dst)}'}}")
    import vlc
    inst = vlc.Instance(["--quiet", "--verbose=-1", "--no-video-title-show", "--no-stats"])
    if inst is None:
        return False
    try:
        m = inst.media_new(src)
        for o in (f":start-time={start_ms / 1000:.3f}", f":sout={chain}", ":sout-keep", ":no-sout-video"):
            m.add_option(o)
        p = inst.media_player_new()
        p.set_media(m)
        p.play()
        done = {vlc.State.Ended, vlc.State.Error, vlc.State.Stopped}
        time.sleep(0.2)
        while p.get_state() not in done and not stop.is_set():
            time.sleep(0.1)
        ok = p.get_state() == vlc.State.Ended
        p.stop()
        p.release()
        m.release()
        return ok
    finally:
        inst.release()


def _data_offset(wav: str) -> int:
    """Where the samples of a WAV start (the muxer may write an extended header), 0 if not yet."""
    try:
        with open(wav, "rb") as f:
            head = f.read(512)
    except OSError:
        return 0
    i = head.find(b"data", 12)
    return i + 8 if i >= 0 and head[:4] == b"RIFF" else 0


class AiSubs(QObject):
    """Settings › Legendas por IA on, a local file playing: reads, transcribes, hands cues over."""
    statusChanged = Signal()
    _cues = Signal(list)
    _status = Signal(str, float)

    def __init__(self, engine, tools, settings, parent=None):
        super().__init__(parent)
        self._e, self._tools, self._s = engine, tools, settings
        self._status_text = ""
        self._progress = 0.0
        self._worker = None
        self._stop = threading.Event()
        self._all: list = []
        self._path = ""
        self._model = None
        self._cues.connect(self._on_cues)
        self._status.connect(self._on_status)
        self._check = QTimer(self)
        self._check.setInterval(1000)
        self._check.timeout.connect(self._follow)
        settings.changed.connect(lambda k: k in ("aiSubs", "aiSubsTranslate", "aiModel") and self._restart())
        engine.currentIndexChanged.connect(self._restart)
        engine.mediaChanged.connect(self._maybe_start)
        self._seek_at = -1

    @Property(bool, constant=True)
    def available(self):
        return native_ok()

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status_text

    @Property(float, notify=statusChanged)
    def progress(self):
        return self._progress

    @Slot(bool)
    def setEnabled(self, on):
        self._s.setValue("aiSubs", bool(on))

    def _on_status(self, text, p):
        self._status_text, self._progress = text, p
        self.statusChanged.emit()

    def _on_cues(self, cues):
        self._all = cues
        self._tools.setCues(cues, "ai")

    def _want(self) -> str:
        p = self._e.currentPath
        if not self._s.value("aiSubs", False) or not p or "://" in p or not os.path.isfile(p):
            return ""
        return p

    def _restart(self, *_):
        self.stop()
        if self._tools.cuesName == "ai":
            self._tools.clearSecondSubtitle()
        self._all = []
        self._maybe_start()

    def _maybe_start(self, *_):
        p = self._want()
        if not p or (self._worker is not None and self._worker.is_alive() and self._path == p):
            return
        if not native_ok():
            self._on_status(tr("Legendas por IA indisponíveis neste computador"), 0)
            return
        self._start(p, max(0, self._e.time))

    def _start(self, path, at_ms):
        self.stop()
        self._path = path
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, args=(path, at_ms, self._stop), name="aisubs", daemon=True)
        self._worker.start()
        self._check.start()

    def stop(self):
        self._stop.set()
        self._check.stop()
        self._worker = None

    def _follow(self):
        """A jump outside what is transcribed: start again from there."""
        if self._worker is None or not self._worker.is_alive():
            return
        t = self._e.time
        lo, hi = getattr(self, "_covered", (0, 0))
        if t < lo - 2000 or t > hi + CHUNK_S * 1000 + 20_000:
            self._start(self._path, max(0, t - 2000))

    # ------------------------------------------------------------ worker thread
    def _run(self, path, at_ms, stop):
        import wave
        translate = bool(self._s.value("aiSubsTranslate", False))
        name = self._s.value("aiModel", "auto")  # auto | base | turbo | medium
        name = pick_model(translate) if name not in MODELS else name
        if translate and name == "turbo":
            name = "medium"                      # turbo does not translate (see MODELS)
        if not model_ready(name):
            self._status.emit(tr("Baixando o modelo de reconhecimento de fala…"), 0.0)
            ok = download_model(name, lambda f: self._status.emit(tr("Baixando o modelo de reconhecimento de fala…"), f), stop)
            if not ok:
                if not stop.is_set():
                    self._status.emit(tr("Não foi possível baixar o modelo"), 0)
                return
        if stop.is_set():
            return
        self._status.emit(tr("Preparando o reconhecimento de fala…"), 0)
        try:
            import PySide6.QtMultimedia  # noqa: F401
            from refrata_native import Transcriber
            if self._model is None or self._model[0] != name:
                self._model = (name, Transcriber(str(model_path(name))))
            asr = self._model[1]
        except Exception as exc:
            self._status.emit(tr("Legendas por IA indisponíveis neste computador"), 0)
            print("aisubs:", exc)
            return
        wav = os.path.join(DATA_DIR, f"aisubs_{threading.get_ident()}.wav")
        # the sound from the file's start, always: started at a point (libVLC's start-time) the
        # extracted sound came out up to ~2 s off, and every subtitle with it. The part before
        # the point is skipped below (audio only: the extraction runs far faster than playback).
        ex = threading.Thread(target=extract_audio, args=(path, 0, wav, stop), daemon=True)
        ex.start()
        cues = [c for c in self._all if c[1] < at_ms]
        self._covered = (at_ms, at_ms)
        pos = at_ms * RATE // 1000                # samples of the WAV consumed (or skipped)
        data_at = 0                               # where the samples start (after the header)
        lang = "auto"
        try:
            while not stop.is_set():
                if not data_at:
                    data_at = _data_offset(wav)
                    if not data_at:
                        if not ex.is_alive():
                            break
                        time.sleep(0.2)
                        continue
                avail = (os.path.getsize(wav) - data_at) // 2
                done = not ex.is_alive()
                need = CHUNK_S * RATE
                if avail - pos < need and not done:
                    time.sleep(0.2)
                    continue
                if avail - pos <= RATE // 2 and done:
                    break
                # far enough ahead of playback: wait
                start_ms = pos * 1000 // RATE
                if start_ms > self._e.time + AHEAD_S * 1000:
                    time.sleep(0.5)
                    continue
                n = min(need, avail - pos)
                with open(wav, "rb") as f:
                    f.seek(data_at + pos * 2)
                    pcm = f.read(n * 2)
                words, detected = asr.transcribe_words(pcm, start_ms, lang, translate, 4)
                if stop.is_set():
                    break                      # replaced while it ran: its cues are not wanted
                if lang == "auto" and detected:
                    lang = detected            # the film's language, kept for the next pieces
                cues = [c for c in cues if c[0] < start_ms] + build_cues(words)
                pos += n
                self._covered = (at_ms, pos * 1000 // RATE)
                self._cues.emit(list(cues))
                self._status.emit(tr("Legendas por IA: %1").replace("%1", detected or "…"), 1.0)
        finally:
            stop.set()
            ex.join(5)
            try:
                os.remove(wav)
            except OSError:
                pass
