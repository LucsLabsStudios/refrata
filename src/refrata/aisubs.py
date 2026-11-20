"""Subtitles made from the sound: speech recognition while the video plays (Faixas › Legendas por IA).

whisper.cpp (OpenAI's Whisper models) is compiled into refrata_native and runs on the GPU through
Vulkan (any NVIDIA, AMD or Intel graphics with a current driver), on the CPU otherwise:

  - the model is downloaded once, over HTTPS from the whisper.cpp project on Hugging Face, and
    checked against its SHA-256: large-v3-turbo (574 MB) with a graphics card, base (82 MB)
    without one, medium (539 MB) to translate into English. Silero VAD (885 kB) ships with the app.
  - the sound is taken from the file by a separate libVLC instance (16 kHz mono), so playback is
    not touched, and read as it is written
  - voice activity detection finds where people speak; only speech goes to Whisper, in pieces of
    up to CHUNK_S seconds cut at pauses. (Fixed 30-second blocks cut words and sentences in two,
    and silence or music is where Whisper makes sentences up.) Whisper runs with DTW word times
    (the old token times came ~0.5 s early), beam search and the sentence before as context
  - the words become subtitles by the usual rules of subtitling (build_cues), and are kept per
    file and model (in memory and on disk): rebuilding the player (a new subtitle size or colour),
    jumping back or opening the file again shows what is done at once and carries on from there
  - the text shows through the interface's subtitle overlay (tools.py, SubOverlay.qml), in the
    subtitle size, colour and background chosen in the settings
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
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
DTW = {"base": "base", "turbo": "large-v3-turbo", "medium": "medium"}   # alignment heads per model
VAD_MODEL = "ggml-silero-v5.1.2.bin"   # resources/models, MIT licence (snakers4/silero-vad)

# how the words become subtitles (build_cues): the usual rules of subtitling
LINE_CHARS = 42        # per line, two lines at most
CUE_MAX_MS = 6000      # longest a subtitle stays
CUE_MIN_MS = 1000      # shortest
READ_CPS = 20          # characters per second a viewer reads: a subtitle stays at least that long
PAUSE_MS = 700         # a pause this long ends a subtitle
LEAD_MS = 80           # a subtitle shows this much before its first word
LINGER_MS = 250        # ...and stays this much after its last
GAP_MS = 80            # between two subtitles (two frames: they never blink into each other)
SENTENCE_END = tuple(".?!…。！？")
CLAUSE_END = tuple(",;:—–")
URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
RATE = 16000
CHUNK_S = 28           # longest piece of speech sent at once (Whisper reads 30 s)
WINDOW_S = 40          # sound looked at by the VAD for the next piece
JOIN_GAP_MS = 2000     # speech this close together goes to Whisper in one piece
AHEAD_S = 150          # transcribe this far ahead of playback, then wait
CACHE_VERSION = 2
# tests share one copy of the models (REFRATA_MODEL_DIR) instead of downloading them per run
MODEL_DIR = Path(os.environ["REFRATA_MODEL_DIR"]) if os.environ.get("REFRATA_MODEL_DIR") else DATA_DIR / "models"
CACHE_DIR = DATA_DIR / "aisubs"
# subtitle credits Whisper learnt from subtitle sites and writes over noise; never said in a video.
# (Only these: "thank you for watching" is often really said. Silence and music do not reach
# Whisper any more, the VAD keeps them out, which is what stopped most made-up sentences.)
_MADE_UP = re.compile(
    r"^((subtitles|captions|legendas?|subt[ií]tulos|sous-titres|untertitel)\s+(by|pela|por|réalisés|"
    r"im auftrag|der)\b.*|.*\bamara\.org\b.*|transcri(bed|ção|pción) by .*)$", re.IGNORECASE)


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


# ------------------------------------------------------------------ words -> subtitles
def _hard_wrap(word: str) -> list:
    """A "word" longer than a line (Dum-dum-dum-deedle-dum..., a URL): cut at hyphens or slashes,
    else anywhere, so no line runs off the picture."""
    out, cur = [], ""
    for piece in re.findall(r"[^-/]*[-/]?", word):
        if not piece:
            continue
        if cur and len(cur) + len(piece) > LINE_CHARS:
            out.append(cur)
            cur = ""
        while len(piece) > LINE_CHARS:
            out.append(piece[:LINE_CHARS])
            piece = piece[LINE_CHARS:]
        cur += piece
    if cur:
        out.append(cur)
    return out


def _wrap(text: str, keep: int = -1) -> str:
    """One line when it fits; else two of about the same length, broken after punctuation if one
    is near the middle, never after a short word ("of", "a", "de") left hanging at a line's end.
    `keep`: a break the caller wants (two sentences in one subtitle: one per line), if both fit."""
    if len(text) <= LINE_CHARS:
        return text
    if 0 < keep < len(text) and keep <= LINE_CHARS + 1 and len(text) - keep - 1 <= LINE_CHARS:
        return text[:keep].rstrip() + "\n" + text[keep:].lstrip()
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if not spaces:
        return "\n".join(_hard_wrap(text)[:2])
    mid = len(text) / 2

    def cost(i):
        a, b = text[:i], text[i + 1:]
        c = abs(len(a) - len(b))
        if len(a) > LINE_CHARS or len(b) > LINE_CHARS:
            c += 1000
        if a.endswith(SENTENCE_END + CLAUSE_END):
            c -= 12
        last = a.rsplit(" ", 1)[-1]
        if len(last) <= 3 and not last.endswith(SENTENCE_END + CLAUSE_END):
            c += 8                      # "of / the turtle": keep the little word with what follows
        return c
    cut = min(spaces, key=cost)
    if abs(cut - mid) > LINE_CHARS:      # nothing sensible: plain halves
        cut = min(spaces, key=lambda i: abs(i - mid))
    return text[:cut] + "\n" + text[cut + 1:]


def _norm(words: list) -> list:
    """Clean words: no blanks, giant words cut into pieces of a line (each with its share of the
    time), "\n" kept as whisper's own sentence boundary."""
    out = []
    for t0, t1, raw in words:
        w = raw.strip()
        if not w:
            continue
        start = raw.startswith("\n")
        parts = _hard_wrap(w) if len(w) > LINE_CHARS else [w]
        step = (t1 - t0) / len(parts)
        for k, p in enumerate(parts):
            out.append([int(t0 + k * step), int(t0 + (k + 1) * step), p, start and k == 0])
    return out


def _sentences(words: list) -> list:
    """Words -> runs that belong together: ended by a sentence's end or by a pause."""
    runs, cur = [], []
    for w in words:
        if cur and (w[0] - cur[-1][1] > PAUSE_MS
                    or (cur[-1][2].endswith(SENTENCE_END) and not _abbrev(cur[-1][2]))
                    or (w[3] and cur[-1][2].endswith(CLAUSE_END))):
            runs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        runs.append(cur)
    return runs


def _abbrev(w: str) -> bool:
    """"Mr." "Dr." "Sr." "etc.": a dot that does not end the sentence."""
    return w.lower().rstrip(".") in ("mr", "mrs", "ms", "dr", "sr", "sra", "st", "prof", "jr", "vs", "etc") \
        and w.endswith(".") and not w.endswith("..")


def _text(ws) -> str:
    return " ".join(w[2] for w in ws)


def _fits(ws) -> bool:
    return len(_text(ws)) <= 2 * LINE_CHARS and ws[-1][1] - ws[0][0] <= CUE_MAX_MS


def _split(run: list) -> list:
    """A sentence too long for one subtitle -> balanced parts, cut where it reads best: after a
    comma or the like, never leaving one or two words alone ("very alert.")."""
    if _fits(run):
        return [run]
    n = max(math.ceil(len(_text(run)) / (2 * LINE_CHARS)), math.ceil((run[-1][1] - run[0][0]) / CUE_MAX_MS), 2)
    best = None
    # choose the n-1 cuts one at a time, each near its share of the length, scored by how it reads
    total = len(_text(run))
    cuts, start = [], 0
    for part in range(1, n):
        target = total * part / n
        options = []
        for i in range(start + 2, len(run) - 1):      # at least two words each side
            left = run[start:i]
            if not _fits(left):
                break
            pos = len(_text(run[:i]))
            c = abs(pos - target)
            if run[i - 1][2].endswith(CLAUSE_END + SENTENCE_END):
                c -= 25
            if run[i][0] - run[i - 1][1] > 250:       # a breath between them
                c -= 15
            if len(run[i - 1][2]) <= 3 and not run[i - 1][2].endswith(CLAUSE_END):
                c += 15                               # "...by the / name of Bert"
            options.append((c, i))
        if not options:
            break
        best = min(options)[1]
        cuts.append(best)
        start = best
    parts, s = [], 0
    for c in cuts:
        parts.append(run[s:c])
        s = c
    parts.append(run[s:])
    out = []
    for p in parts:                                   # a part still too long (no good cut): by length
        out.extend(_split_plain(p) if not _fits(p) else [p])
    return out


def _split_plain(run: list) -> list:
    out, cur = [], []
    for w in run:
        if cur and not _fits(cur + [w]):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def build_cues(words: list) -> list:
    """Words with their times [(start ms, end ms, word)] -> subtitles [(start, end, text)].

    Each sentence is one subtitle when it fits two lines of LINE_CHARS and CUE_MAX_MS, else it is
    split in balanced parts at commas and breaths. Short sentences said together share one
    subtitle (one per line), so "You. And you." does not flash by in pieces. Each subtitle shows
    from just before its first word, long enough to be read (READ_CPS), never over the next one.
    A word starting with "\n" begins one of whisper's own segments (about a sentence)."""
    groups, breaks = [], []       # the words of each subtitle; where its second line should start
    for run in _sentences(_norm(words)):
        for part in _split(run):
            prev = groups[-1] if groups else None
            gap = part[0][0] - prev[-1][1] if prev else 1 << 30
            # a short sentence right after a short one: together, if they fit (one per line)
            if (prev is not None and breaks[-1] < 0 and gap <= PAUSE_MS
                    and (prev[-1][1] - prev[0][0] < 1600 or len(_text(prev)) < 24)
                    and len(_text(prev)) <= LINE_CHARS and len(_text(part)) <= LINE_CHARS
                    and _fits(prev + part) and prev[-1][2].endswith(SENTENCE_END + CLAUSE_END)):
                breaks[-1] = len(_text(prev))
                prev.extend(part)
                continue
            # a word or two left alone by a pause ("Duck" ... "and cover."), or a very short
            # sentence before a longer one ("Hey, Bert." "Come on out..."): with what follows
            if (prev is not None and breaks[-1] < 0 and gap <= 1500 and _fits(prev + part)
                    and (len(_text(prev)) <= 8 and not prev[-1][2].endswith(SENTENCE_END)
                         or len(_text(prev)) <= 14 and prev[-1][1] - prev[0][0] < 1200)):
                prev.extend(part)
                continue
            groups.append(list(part))
            breaks.append(-1)
    cues = [[g[0][0] - LEAD_MS, g[-1][1] + LINGER_MS, _wrap(_text(g), k)] for g, k in zip(groups, breaks)]
    for i, c in enumerate(cues):
        c[0] = max(0, c[0], cues[i - 1][1] + GAP_MS if i else 0)
        chars = len(c[2].replace("\n", ""))
        c[1] = max(c[1], c[0] + max(CUE_MIN_MS, chars * 1000 // READ_CPS))
        c[1] = min(c[1], c[0] + CUE_MAX_MS + 1500)
        if i + 1 < len(cues):
            c[1] = max(c[0] + 300, min(c[1], cues[i + 1][0] - LEAD_MS - GAP_MS))
    return [tuple(c) for c in cues if not _MADE_UP.match(c[2].replace("\n", " ").strip(" .!-♪"))]


# ------------------------------------------------------------------ models and sound
def model_path(name: str):
    return MODEL_DIR / MODELS[name][0]


def model_ready(name: str) -> bool:
    p = model_path(name)
    return p.exists() and p.stat().st_size == MODELS[name][1]


def vad_path() -> Path:
    from . import vlcpath
    return vlcpath.resources_dir() / "models" / VAD_MODEL


def download_model(name: str, progress=None, stop=None) -> bool:
    """Download and verify a model; progress(fraction) as it goes. False on any failure."""
    fname, size, sha = MODELS[name]
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    dst = model_path(name)
    tmp = dst.with_suffix(".part")
    h = hashlib.sha256()
    from urllib.request import Request, urlopen   # only when a model is downloaded (~75 ms off every start)
    try:
        req = Request(URL + fname, headers={"User-Agent": "Refrata"})
        with urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
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


def pieces(speech: list, from_ms: int, until_ms: int, final: bool) -> tuple:
    """Stretches of speech [(start, end)] found in the sound from `from_ms` to `until_ms` -> the
    next piece for Whisper: speech close together (JOIN_GAP_MS), up to CHUNK_S seconds, ending at
    a pause. Speech running into `until_ms` may go on past it: it is left for the next window
    (which starts where it starts), unless this is the end of the file.
    Returns ((start, end) or None, where to look next)."""
    take = []
    for a, b in speech:
        if take and (b - take[0][0] > CHUNK_S * 1000 or a - take[-1][1] > JOIN_GAP_MS):
            break
        take.append((a, b))
    if not take:
        return None, (until_ms if final else max(from_ms + 1000, until_ms - 3000))
    if not final and take[-1][1] >= until_ms - 300:
        if len(take) > 1:
            take.pop()                   # cut short by the window: next time, whole
        elif take[0][0] > from_ms + 1000:
            return None, take[0][0] - 100   # starts late in the window: the next one starts there
    a, b = take[0][0], min(take[-1][1], take[0][0] + CHUNK_S * 1000)
    return (a, b), b


def _merge_ranges(ranges: list) -> list:
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 500:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _covered(ranges: list, t: int) -> tuple | None:
    for a, b in ranges:
        if a - 500 <= t <= b:
            return a, b
    return None


def cache_key(path: str, model: str, translate: bool) -> str:
    try:
        st = os.stat(path)
        ident = f"{os.path.abspath(path).lower()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        ident = os.path.abspath(path).lower()
    return hashlib.sha1(f"{ident}|{model}|{int(translate)}|{CACHE_VERSION}".encode()).hexdigest()[:20]


# ------------------------------------------------------------------ the app side
class AiSubs(QObject):
    """Settings › Legendas por IA on, a local file playing: reads, transcribes, hands cues over."""
    statusChanged = Signal()
    _words = Signal(str, int, int, list, str)     # key, from, to, words, language
    _status = Signal(str, float)

    def __init__(self, engine, tools, settings, parent=None):
        super().__init__(parent)
        self._e, self._tools, self._s = engine, tools, settings
        self._status_text = ""
        self._progress = 0.0
        self._worker = None
        self._stop = threading.Event()
        self._path = ""
        self._key = ""
        self._model = None                 # (name, Transcriber)
        self._vad = None
        self._all: list = []               # the file's words [(start, end, word)], sorted
        self._done: list = []              # [[from, to]] ms transcribed
        self._lang = "auto"
        self._from = 0                     # where the running worker started...
        self._front = 0                    # ...and how far it got
        self._failed = ""                  # the key whose model could not be had: no retry each second
        self._words.connect(self._on_words)
        self._status.connect(self._on_status)
        self._check = QTimer(self)
        self._check.setInterval(1000)
        self._check.timeout.connect(self._follow)
        self._save = QTimer(self)
        self._save.setSingleShot(True)
        self._save.setInterval(2000)
        self._save.timeout.connect(self._write_cache)
        settings.changed.connect(self._on_setting)
        # a new item, or the same file opened again by a player rebuild (a new subtitle size or
        # colour, hardware decoding toggled): _follow_item tells them apart
        engine.currentIndexChanged.connect(self._follow_item)
        engine.mediaChanged.connect(self._follow_item)

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

    # ------------------------------------------------------------ what to transcribe
    def _want(self) -> str:
        p = self._e.currentPath
        if not self._s.value("aiSubs", False) or not p or "://" in p or not os.path.isfile(p):
            return ""
        return p

    def _choice(self) -> tuple:
        translate = bool(self._s.value("aiSubsTranslate", False))
        name = self._s.value("aiModel", "auto")  # auto | base | turbo | medium
        name = pick_model(translate) if name not in MODELS else name
        if translate and name == "turbo":
            name = "medium"                      # turbo does not translate (see MODELS)
        return name, translate

    def _on_setting(self, key):
        if key in ("aiSubs", "aiSubsTranslate", "aiModel"):
            self._restart()

    def _follow_item(self, *_):
        p = self._want()
        if p and p == self._path:
            # the same file again: what is done stays, the worker (if any) carries on
            if self._all and self._tools.cuesName != "ai":
                self._show()
            if self._worker is None or not self._worker.is_alive():
                self._maybe_start()
            return
        self._restart()

    def _restart(self, *_):
        self.stop()
        self._write_cache()
        self._path, self._key, self._all, self._done, self._lang = "", "", [], [], "auto"
        self._failed = ""
        if self._tools.cuesName == "ai":
            self._tools.clearSecondSubtitle()
        if not self._want():
            if not self._s.value("aiSubs", False):
                self._on_status("", 0)
            return
        self._maybe_start()

    def _maybe_start(self, *_):
        p = self._want()
        if not p or (self._worker is not None and self._worker.is_alive() and self._path == p):
            return
        if not native_ok():
            self._on_status(tr("Legendas por IA indisponíveis neste computador"), 0)
            return
        name, translate = self._choice()
        key = cache_key(p, name, translate)
        if key != self._key:
            self._path, self._key = p, key
            self._read_cache()
            if self._all:
                self._show()
        at = max(0, self._e.time)
        if self._complete_from(at):
            self._status.emit(tr("Legendas por IA: %1").replace("%1", self._lang_name()), 1.0)
            self._check.start()            # a jump back to a part not done still starts one
            return                         # all done already (the cache): nothing to read again
        self._start(at)

    def _complete_from(self, t) -> bool:
        span = _covered(self._done, t)
        ln = self._e.length
        return bool(span) and ln > 0 and span[1] >= ln - 1500

    def _lang_name(self) -> str:
        return {"pt": "português", "en": "English", "es": "español"}.get(self._lang, self._lang
                                                                         if self._lang != "auto" else "…")

    def _start(self, at_ms):
        self.stop()
        name, translate = self._choice()
        self._stop = threading.Event()
        self._from = self._front = at_ms
        self._worker = threading.Thread(target=self._run, args=(self._path, self._key, name, translate, at_ms,
                                                                list(self._done), self._stop),
                                        name="aisubs", daemon=True)
        self._worker.start()
        self._check.start()

    def stop(self):
        self._stop.set()
        self._check.stop()
        self._worker = None

    def _follow(self):
        """A jump to a part not transcribed, and not about to be: start again from there (what
        is done stays)."""
        if not self._path or not self._want() or self._failed == self._key:
            return
        t = self._e.time
        if self._complete_from(t) or 0 < self._e.length - 3000 <= t:
            return
        alive = self._worker is not None and self._worker.is_alive()
        if alive and (_covered(self._done, t) or self._from - 2000 <= t <= self._front + WINDOW_S * 1000):
            return                         # done, or on its way
        self._start(max(0, t - 2000))      # (the worker skips what is done)

    # ------------------------------------------------------------ results
    def _on_words(self, key, lo, hi, words, lang):
        if key != self._key:
            return                         # a worker of a file or setting no longer shown
        self._front = max(self._front, hi)
        if lang:
            self._lang = lang
        self._all = sorted([w for w in self._all if not lo <= w[0] < hi] + [tuple(w) for w in words])
        self._done = _merge_ranges(self._done + [[lo, hi]])
        self._show()
        self._save.start()
        self._status.emit(tr("Legendas por IA: %1").replace("%1", self._lang_name()), 1.0)

    def _show(self):
        self._tools.setCues(build_cues(self._all), "ai")

    def _cache_file(self) -> Path:
        return CACHE_DIR / f"{self._key}.json"

    def _read_cache(self):
        self._all, self._done, self._lang = [], [], "auto"
        try:
            d = json.loads(self._cache_file().read_text(encoding="utf8"))
            if d.get("v") == CACHE_VERSION:
                self._all = [tuple(w) for w in d["words"] if len(w) == 3]
                self._done = _merge_ranges([list(r) for r in d["done"] if len(r) == 2])
                self._lang = d.get("lang") or "auto"
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _write_cache(self):
        if not self._key or not self._done:
            return
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_file().with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": CACHE_VERSION, "words": self._all, "done": self._done,
                                       "lang": self._lang}, ensure_ascii=False), encoding="utf8")
            os.replace(tmp, self._cache_file())
            # the cache stays small: the 200 most recent files
            files = sorted(CACHE_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
            for f in files[:-200]:
                f.unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------ worker thread
    def _load(self, name):
        import PySide6.QtMultimedia  # noqa: F401
        from refrata_native import Transcriber, Vad
        if self._model is None or self._model[0] != name:
            self._model = None
            try:
                self._model = (name, Transcriber(str(model_path(name)), DTW[name]))
            except Exception as exc:       # DTW refused (memory): whisper's own word times
                print("aisubs: no DTW:", exc)
                self._model = (name, Transcriber(str(model_path(name))))
        if self._vad is None:
            try:
                self._vad = Vad(str(vad_path()))
            except Exception as exc:
                print("aisubs: no VAD:", exc)
                self._vad = False
        return self._model[1], self._vad

    def _run(self, path, key, name, translate, at_ms, done, stop):
        if not model_ready(name):
            self._status.emit(tr("Baixando o modelo de reconhecimento de fala…"), 0.0)
            ok = download_model(name, lambda f: self._status.emit(tr("Baixando o modelo de reconhecimento de fala…"), f), stop)
            if not ok:
                if not stop.is_set():
                    self._failed = key
                    self._status.emit(tr("Não foi possível baixar o modelo"), 0)
                return
        if stop.is_set():
            return
        self._status.emit(tr("Preparando o reconhecimento de fala…"), 0)
        try:
            asr, vad = self._load(name)
        except Exception as exc:
            self._failed = key
            self._status.emit(tr("Legendas por IA indisponíveis neste computador"), 0)
            print("aisubs:", exc)
            return
        # greedy, with whisper's own temperature fallback when unsure: measured against beam search
        # (5) with these quantised models, beam got words wrong ("braw" for "brown") and dropped
        # full stops, and was slower
        beam = 1
        wav = os.path.join(DATA_DIR, f"aisubs_{threading.get_ident()}.wav")
        # the sound from the file's start, always: started at a point (libVLC's start-time) the
        # extracted sound came out up to ~2 s off, and every subtitle with it. The part before
        # the point is skipped below (audio only: the extraction runs far faster than playback).
        ex = threading.Thread(target=extract_audio, args=(path, 0, wav, stop), daemon=True)
        ex.start()
        pos = at_ms                                # ms of the sound handled
        data_at = 0
        lang = self._lang if self._lang != "auto" else "auto"
        prompt, prompt_end = "", -1
        try:
            while not stop.is_set():
                span = _covered(done, pos)
                if span:                           # done before (cache, an earlier pass)
                    pos = span[1]
                if not data_at:
                    data_at = _data_offset(wav)
                    if not data_at:
                        if not ex.is_alive():
                            break
                        time.sleep(0.2)
                        continue
                final = not ex.is_alive()          # read before the size: nothing more comes after
                avail_ms = (os.path.getsize(wav) - data_at) // 2 * 1000 // RATE
                if avail_ms - pos < WINDOW_S * 1000 and not final:
                    time.sleep(0.2)
                    continue
                if avail_ms - pos <= 500 and final:
                    break
                if pos > self._e.time + AHEAD_S * 1000:      # far enough ahead of playback: wait
                    time.sleep(0.5)
                    continue
                end = min(avail_ms, pos + WINDOW_S * 1000)
                pcm = self._read(wav, data_at, pos, end)
                if vad:
                    speech = vad.speech(pcm, pos, 0.5, 300, 200, float(CHUNK_S - 2))
                else:                              # no VAD: plain pieces
                    speech = [(pos, end)]
                piece, nxt = pieces(speech, pos, end, final and end >= avail_ms)
                if piece is None:                  # no speech up to nxt: on from there
                    self._words.emit(key, pos, nxt, [], "")
                    pos = max(nxt, pos + 100)
                    continue
                a, b = piece
                use_prompt = prompt if a - prompt_end < 10_000 else ""
                words, detected = asr.transcribe_words(self._read(wav, data_at, a, b), a, lang, translate, 4,
                                                       use_prompt, beam)
                if stop.is_set():
                    break                          # replaced while it ran: its words are not wanted
                if lang == "auto" and detected and words:
                    lang = detected                # the film's language, kept for the next pieces
                words = self._fit(words, [s for s in speech if s[0] < b])
                self._words.emit(key, pos, b, words, detected if words else "")
                if words:
                    prompt = " ".join(w[2].strip() for w in words)[-220:]
                    prompt_end = b
                pos = max(nxt, pos + 500)
        finally:
            stop.set()
            ex.join(5)
            try:
                os.remove(wav)
            except OSError:
                pass

    @staticmethod
    def _read(wav, data_at, a_ms, b_ms) -> bytes:
        a, b = a_ms * RATE // 1000, b_ms * RATE // 1000
        with open(wav, "rb") as f:
            f.seek(data_at + a * 2)
            return f.read(max(0, b - a) * 2)

    @staticmethod
    def _fit(words, speech):
        """A word ends at the latest where its stretch of speech ends (the VAD hears the voice
        stop; whisper's time for the last word before a pause ran on into the silence)."""
        out = []
        for t0, t1, w in words:
            for a, b in speech:
                if a - 300 <= t0 <= b:
                    t1 = min(t1, b)
                    break
            out.append((t0, max(t0 + 60, t1), w))
        return out
