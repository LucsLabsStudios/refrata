"""Fast checks of the app's plain logic: no window, no libVLC, a few seconds.

    python tests/unit.py [-k name]

What the big batteries exercise only in passing, or cannot reach: every playlist format and the
files that try to abuse the reader, the online-subtitle client against a local stand-in server
(search, download, size and HTTPS limits, hostile names), the update signature, the yt-dlp
updater's address rules, the names both launch paths must agree on.
"""
import hashlib
import http.server
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="refrata_unit_"))
os.environ.setdefault("REFRATA_DATA", str(TMP / "data"))       # never the person's own data folder
os.environ.setdefault("REFRATA_E2E_TMP", str(TMP))
os.environ.pop("REFRATA_PYPI_URL", None)
os.environ.pop("REFRATA_OPENSUBTITLES_URL", None)

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def check(cond, what, detail=""):
    if not cond:
        raise AssertionError(what + (f" — {detail}" if detail else ""))


def raises(fn, what, *types):
    try:
        fn()
    except (types or (Exception,)) as exc:
        return exc
    raise AssertionError(what + " — nothing was raised")


def serve(handler_factory):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler_factory)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ---------------------------------------------------------------- playlists
def _media(folder: Path, *names):
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for n in names:
        p = folder / n
        p.write_bytes(b"x")
        out.append(str(p))
    return out


@test
def playlists_every_format_reads_its_items_in_order():
    from refrata import playlists as P
    d = TMP / "pl"
    a, b, c = _media(d, "a.mp3", "b é.mp3", "c.mp4")
    (d / "l.m3u").write_text(f"#EXTM3U\n#EXTINF:1,x\na.mp3\n\n{b}\nmissing.mp3\nhttp://radio.example/stream\n", encoding="utf8")
    got = P.read_playlist(str(d / "l.m3u"))
    check(got == [a, b, "http://radio.example/stream"], "m3u: relative, absolute and URL entries; missing files dropped", str(got))
    (d / "l.m3u8").write_bytes(("\ufeffb é.mp3\n").encode("utf8"))
    check(P.read_playlist(str(d / "l.m3u8")) == [b], "m3u8 with a BOM and accents")
    (d / "latin.m3u").write_bytes("b é.mp3\n".encode("cp1252"))
    check(P.read_playlist(str(d / "latin.m3u")) == [b], "an old Windows-encoded m3u still finds accented names")
    (d / "l.pls").write_text("[playlist]\nFile2=c.mp4\nfile1=a.mp3\nTitle1=x\nNumberOfEntries=2\n", encoding="utf8")
    check(P.read_playlist(str(d / "l.pls")) == [a, c], "pls: FileN in numeric order, any case")
    (d / "l.xspf").write_text('<?xml version="1.0"?><playlist xmlns="http://xspf.org/ns/0/"><trackList>'
                              f'<track><location>{Path(c).as_uri()}</location></track>'
                              '<track><location>a.mp3</location></track></trackList></playlist>', encoding="utf8")
    check(P.read_playlist(str(d / "l.xspf")) == [c, a], "xspf: file URLs and relative paths", str(P.read_playlist(str(d / "l.xspf"))))
    (d / "hls.m3u8").write_text("#EXTM3U\n#EXT-X-VERSION:3\nseg0.ts\n", encoding="utf8")
    check(P.read_playlist(str(d / "hls.m3u8")) == [str(d / "hls.m3u8")], "an HLS manifest is media itself, not a list")
    check(P.is_playlist("x.M3U") and P.is_playlist("y.xspf") and not P.is_playlist("z.mp3"), "playlist extensions")
    check(P.read_playlist(str(d / "nope.m3u")) == [], "a playlist that does not exist is an empty list, not an error")


@test
def playlists_hostile_files_are_refused():
    from refrata import playlists as P
    d = TMP / "plbad"
    (a,) = _media(d, "a.mp3")
    # entity expansion ("billion laughs"): refused before the parser sees it
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            + "".join(f'<!ENTITY lol{i} "{"&lol%s;" % (i - 1 if i > 1 else "") * 10}">' for i in range(1, 9))
            + ']><playlist><trackList><track><location>&lol8;</location></track></trackList></playlist>')
    (d / "bomb.xspf").write_text(bomb, encoding="utf8")
    t0 = time.time()
    check(P.read_playlist(str(d / "bomb.xspf")) == [], "an XSPF declaring entities is refused")
    check(time.time() - t0 < 1, "at once", f"{time.time() - t0:.1f}s")
    (d / "broken.xspf").write_text("<playlist><track>", encoding="utf8")
    check(P.read_playlist(str(d / "broken.xspf")) == [], "broken XML is an empty list")
    # a video renamed .m3u is not read into memory
    big = d / "huge.m3u"
    with open(big, "wb") as f:
        f.seek(P.MAX_PLAYLIST + 10)
        f.write(b"\n")
    check(P.read_playlist(str(big)) == [], "a playlist over the size limit is refused")
    # network paths: only on the server the playlist itself lives on (an NTLM hash is not handed out)
    (d / "unc.m3u").write_text("\\\\evil-host\\share\\x.mp3\n//evil-host/share/y.mp3\na.mp3\nfile://evil-host/share/z.mp3\n", encoding="utf8")
    got = P.read_playlist(str(d / "unc.m3u"))
    check(got == [a], "entries on another server's share are dropped, never even checked", str(got))
    check(P._unc_host(r"\\nas\media\a.mp3") == "nas" and P._unc_host("C:\\a.mp3") == "", "UNC host detection")
    (d / "junk.pls").write_bytes(os.urandom(4096))
    check(isinstance(P.read_playlist(str(d / "junk.pls")), list), "random bytes named .pls do not raise")


# ---------------------------------------------------------------- online subtitles
class _Subs(http.server.BaseHTTPRequestHandler):
    log, mode = [], "ok"

    def log_message(self, *a):
        pass

    def _send(self, body: bytes, code=200):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).log.append(("GET", self.path, self.headers.get("Api-Key")))
        if self.path.startswith("/subtitles"):
            data = [{"attributes": {"language": "pt-BR", "release": "Filme.2024.1080p", "download_count": n * 10,
                                    "moviehash_match": n == 2, "hearing_impaired": n == 3,
                                    "files": [{"file_id": 100 + n, "file_name": f"f{n}.srt"}]}} for n in (1, 2, 3, 1)]
            data.append({"attributes": {"language": "en", "files": []}})
            self._send(json.dumps({"data": data}).encode())
        elif self.path == "/file.srt":
            self._send(b"1\n00:00:01,000 --> 00:00:02,000\nOla\n" if type(self).mode != "huge" else b"x" * (9 * 1024 * 1024))
        else:
            self._send(b"{}", 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).log.append(("POST", self.path, body))
        host = f"http://127.0.0.1:{self.server.server_address[1]}"
        mode = type(self).mode
        if mode == "quota":
            self._send(json.dumps({"message": "You have downloaded your allowed 20 subtitles"}).encode())
        else:
            self._send(json.dumps({"link": ("http://plain.example/file.srt" if mode == "http" else host + "/file.srt")}).encode())


@test
def online_subtitles_search_download_and_limits():
    from refrata import subsearch as S
    srv, base = serve(_Subs)
    os.environ["REFRATA_OPENSUBTITLES_URL"] = base
    os.environ["REFRATA_OPENSUBTITLES_KEY"] = "test-key"
    try:
        check(S.guess_query(r"C:\v\The.Movie.2019.1080p.x264-GRP.mkv") == "The Movie 2019", "the title is guessed from a release name",
              S.guess_query(r"C:\v\The.Movie.2019.1080p.x264-GRP.mkv"))
        check(S.guess_query("Um_Filme_Bom.WEB-DL.mkv") == "Um Filme Bom", "underscores and web-dl", S.guess_query("Um_Filme_Bom.WEB-DL.mkv"))
        movie = TMP / "Filme.2024.1080p.mkv"
        movie.write_bytes(os.urandom(200_000))
        h = S.movie_hash(str(movie))
        check(len(h) == 16 and int(h, 16) >= 0, "the file hash is 16 hex digits", h)
        check(S.movie_hash(str(movie)) == h, "and stable")
        small = TMP / "tiny.mkv"
        small.write_bytes(b"x" * 1000)
        check(not S.movie_hash(str(small)), "too small a file has no hash (the search goes by name)")
        res = S.search(str(movie), "pt-br,en")
        check([r["fileId"] for r in res] == [102, 103, 101], "exact match first, then by downloads, duplicates once", str(res))
        check(res[0]["exact"] and res[0]["language"] == "pt-BR", "with its fields", str(res[0]))
        gets = [p_ for m, p_, _ in _Subs.log if m == "GET"]
        check(len(gets) == 1 and "moviehash=" in gets[0], "results for this very file end the search (no second query by name)", str(gets))
        check(all(k == "test-key" for m, _, k in _Subs.log if m == "GET"), "the API key goes in the header")
        res = S.search(str(small), "pt-br")            # by name only
        ids = [r["fileId"] for r in res]
        check(ids == [102, 103, 101], "by name: exact first, then by downloads, duplicates once", str(ids))
        check(res[1]["hearingImpaired"] is True, "hearing-impaired flag")
        out = S.download(102, TMP / "subs", 'Fi:lme/..\\x?', "pt-BR")
        p = Path(out)
        check(p.parent == TMP / "subs" and p.exists() and b"Ola" in p.read_bytes(), "the subtitle lands in the folder asked", out)
        check(not any(c in p.name for c in '\\/:*?"<>|'), "characters a file name cannot have are replaced (no path escape)", p.name)
        _Subs.mode = "huge"
        raises(lambda: S.download(102, TMP / "subs", "big", "en"), "a subtitle over the size limit is refused", RuntimeError)
        check(not (TMP / "subs" / "big.en.srt").exists(), "and nothing is written")
        _Subs.mode = "quota"
        exc = raises(lambda: S.download(102, TMP / "subs", "q", "en"), "the service's own refusal is passed on", RuntimeError)
        check("allowed" in str(exc), "with its message", str(exc))
        _Subs.mode = "http"
        os.environ.pop("REFRATA_OPENSUBTITLES_URL")     # as installed: a plain-http link is refused
        S_api = S._api
        S._api = lambda: base
        try:
            exc = raises(lambda: S.download(102, TMP / "subs", "h", "en"), "a download link that is not HTTPS is refused", RuntimeError)
            check("https" in str(exc), "saying so", str(exc))
        finally:
            S._api = S_api
    finally:
        _Subs.mode = "ok"
        os.environ.pop("REFRATA_OPENSUBTITLES_URL", None)
        os.environ.pop("REFRATA_OPENSUBTITLES_KEY", None)
        srv.shutdown()


# ---------------------------------------------------------------- updates
@test
def update_signature_accepts_only_the_real_one():
    from refrata import ed25519, updater as U
    secret = hashlib.sha256(b"unit test key").digest()
    public = ed25519.public_key(secret)
    msg = U.update_message("Refrata-Setup-9.9.9.exe", "ab" * 32, "9.9.9")
    check(msg == b"refrata-update-v1\nRefrata-Setup-9.9.9.exe\n" + b"ab" * 32 + b"\n9.9.9\n", "the signed message names file, hash and version", repr(msg))
    sig = ed25519.sign(secret, msg)
    check(ed25519.verify(public, msg, sig), "a good signature verifies")
    for what, m, s, k in (("another version", U.update_message("Refrata-Setup-9.9.9.exe", "ab" * 32, "9.9.8"), sig, public),
                          ("another file", U.update_message("Refrata-9.9.9-portable.zip", "ab" * 32, "9.9.9"), sig, public),
                          ("another hash", U.update_message("Refrata-Setup-9.9.9.exe", "ac" * 32, "9.9.9"), sig, public),
                          ("a flipped bit", msg, bytes([sig[0] ^ 1]) + sig[1:], public),
                          ("another key", msg, sig, ed25519.public_key(hashlib.sha256(b"other").digest())),
                          ("a short signature", msg, sig[:40], public), ("an empty one", msg, b"", public)):
        check(not ed25519.verify(k, m, s), f"refused: {what}")
    key = U._public_key()
    check(key is not None and len(key) == 32, "the app carries its 32-byte public key", str(key))
    check(U.parse_version("1.4.10") > U.parse_version("1.4.9") > U.parse_version("1.4"), "versions compare by number, not text")
    check(U.parse_version("v2.0.0") > U.parse_version("1.99.99"), "a leading v is fine")


@test
def update_downloads_only_from_trusted_https_addresses():
    from refrata import updater as U
    trusted = U.Updater._trusted_url
    saved = os.environ.pop("REFRATA_E2E_TMP", None)      # as installed (the tests allow their local server)
    try:
        for url, ok in (("https://github.com/LucsLabsStudios/refrata/releases/download/v1/x.exe", True),
                        ("https://objects.githubusercontent.com/github-production-release-asset/1/x", True),
                        ("http://github.com/LucsLabsStudios/refrata/releases/download/v1/x.exe", False),
                        ("https://github.com.evil.example/x.exe", False), ("https://evil.example/github.com/x.exe", False),
                        ("file:///C:/x.exe", False), ("ftp://github.com/x.exe", False), ("", False),
                        ("http://127.0.0.1:8000/x.exe", False)):
            check(bool(trusted(url)) is ok, f"{'accepted' if ok else 'refused'}: {url or '(empty)'}")
    finally:
        if saved is not None:
            os.environ["REFRATA_E2E_TMP"] = saved


@test
def ytdlp_updater_only_talks_https_to_pypi():
    from refrata import ytdlp as Y
    for url in ("http://pypi.org/pypi/yt-dlp/json", "https://pypi.org.evil.example/x.whl", "https://example.com/yt_dlp.whl",
                "file:///C:/x.whl", "ftp://files.pythonhosted.org/x.whl"):
        exc = raises(lambda url=url: Y._get(url, 1000, timeout=2), f"refused: {url}", RuntimeError)
        check("refused" in str(exc), f"refused before any connection: {url}", str(exc))
    check(Y._vkey("2025.10.1") > Y._vkey("2025.9.30"), "yt-dlp versions compare by number")


# ---------------------------------------------------------------- launching and names
@test
def both_launch_paths_agree_on_the_instance_name():
    from refrata import fastlaunch as F, single as S
    check(F.server_name() == S.server_name() and F.server_name("x") == S.server_name("x"), "one pipe name for the fast path and the app")
    check(F.server_name() != F.server_name("x"), "tags give other names")
    saved = os.environ["REFRATA_DATA"]
    try:
        os.environ["REFRATA_DATA"] = saved + "-other"
        other = F.server_name()
    finally:
        os.environ["REFRATA_DATA"] = saved
    check(other != F.server_name(), "another data folder is another instance (tests never reach the person's Refrata)")
    for flag in ("--selftest", "--scenario", "--new-window", "--background", "--screenshot"):
        check(F.try_hand_over(["Refrata", flag, "x"]) == "", f"{flag} never hands over to a running Refrata")
    here = str(Path(__file__).resolve())
    check(F._files(["Refrata", "--flag", here, "https://x.example/v.mp4"]) == [here, "https://x.example/v.mp4"],
          "files become absolute, links stay as they are, flags are skipped")
    for flag in F.VALUE_FLAGS:
        check(F._files(["Refrata", flag, "value", here]) == [here],
              f"{flag}'s value is never forwarded as media")


@test
def update_bad_responses_leave_checking_and_allow_recovery():
    from types import SimpleNamespace
    from refrata import updater as U

    class Reply:
        def __init__(self, data):
            self.data = data
        def deleteLater(self):
            pass
        def error(self):
            return U.QNetworkReply.NetworkError.NoError
        def readAll(self):
            return json.dumps(self.data).encode("utf8")

    for manual in (False, True):
        states = []
        obj = SimpleNamespace(_last_manual=manual,
                              _settings=SimpleNamespace(setValue=lambda *args: None),
                              _set=lambda state, *args: states.append(state))
        obj.apply_release = lambda data: U.Updater.apply_release(obj, data)
        for data in ([], None, 3, {"assets": [None]}, {"assets": ["bad"]}):
            U.Updater._on_checked(obj, Reply(data))
            check(states[-1] == ("error" if manual else "idle"),
                  "invalid responses leave the checking state")
        U.Updater._on_checked(obj, Reply({"tag_name": "v0.0.0", "assets": []}))
        check(states[-1] == "uptodate", "a valid response works after an invalid one")


@test
def media_types_and_page_links():
    from refrata import websites as W
    from refrata.media import is_media_path
    for p, ok in (("a.MP4", True), ("b.flac", True), ("c.mkv", True), ("d.txt", False), ("e.exe", False), ("noext", False), ("f.srt", False)):
        check(bool(is_media_path(p)) is ok, f"is_media_path({p}) is {ok}")
    for u, ok in (("https://www.youtube.com/watch?v=x", True), ("https://youtu.be/x", True), ("https://vimeo.com/123", True),
                  ("https://example.com/video.mp4", False), ("C:\\v\\a.mp4", False), ("http://192.168.0.2/stream.m3u8", False)):
        check(bool(W.is_page_url(u)) is ok, f"is_page_url({u}) is {ok}")
    check(W.site_name("https://www.youtube.com/watch?v=x").lower().startswith("youtube"), "a site's name for the loading text",
          W.site_name("https://www.youtube.com/watch?v=x"))


@test
def settings_survive_garbage_and_keep_types():
    from refrata import settings as St
    check(St._clean_history("nope") == [] and St._clean_history([1, None, {}, {"path": ""}]) == [], "a broken history is an empty one")
    h = St._clean_history([{"path": "a", "title": 5, "time": "x", "pos": 1.9, "len": True}])
    check(h == [{"path": "a", "title": "", "time": 0, "pos": 1, "len": 0}], "history fields of the wrong type are reset", str(h))


# ---------------------------------------------------------------- one Refrata per user
def _qt():
    from PySide6.QtCore import QCoreApplication
    return QCoreApplication.instance() or QCoreApplication(["unit"])


# The app has one instance server (and one updater) for its whole life. Destroying one here, with
# its sockets' last events still queued, crashed the next test's event loop: they are kept instead.
_KEPT = []


def _pump(fn, timeout=15.0):
    """fn on another thread while this one keeps Qt's events going (the instance server lives here)."""
    from PySide6.QtCore import QCoreApplication
    box = {}

    def work():
        try:
            box["v"] = fn()
        except Exception as exc:   # handed to the test below
            box["e"] = exc
    t = threading.Thread(target=work, daemon=True)
    t.start()
    end = time.time() + timeout
    while t.is_alive() and time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.004)
    check(not t.is_alive(), "the call came back in time")
    QCoreApplication.processEvents()
    if "e" in box:
        raise box["e"]
    return box.get("v")


_LAUNCH = """
import json, sys
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from refrata import single as S
name, out = sys.argv[2], []
for kind, files, mid in json.loads(sys.argv[3]):
    if kind == "send":
        out.append(S.send_to_running(files, name, msg_id=mid))
    else:   # a whole second launch: True when it was told to leave
        out.append(S.claim_or_hand_over(files, name, wait_s=6) is None)
print(json.dumps(out))
"""


def _launches(name, *steps):
    """Other launches of Refrata, in a process of their own, while this one keeps answering."""
    def run():
        r = subprocess.run([sys.executable, "-c", _LAUNCH, str(ROOT), name, json.dumps(steps)],
                           capture_output=True, text=True, timeout=60)
        check(r.returncode == 0, "the other launch ran", r.stderr[-400:])
        return json.loads(r.stdout.strip().splitlines()[-1])
    return _pump(run, 70)


@test
def second_launch_hands_its_files_to_the_first():
    _qt()
    from refrata import single as S
    name = S.server_name("unit-" + uuid.uuid4().hex[:8])
    check(S.send_to_running(["x.mp4"], name, timeout_ms=200) is False, "nobody listening: the request is not taken")
    first = S.claim_or_hand_over([], name)
    _KEPT.append(first)
    check(isinstance(first, S.InstanceServer), "the first launch becomes the instance", repr(first))
    got, shows = [], []
    first.received.connect(lambda files: got.append(list(files)))
    first.activateRequested.connect(lambda: shows.append(1))
    try:
        # the window is still being built: requests are answered, and kept
        res = _launches(name, ["send", ["a é.mp4"], ""], ["claim", ["b.mp4", "c.mp4"], ""], ["send", [], ""])
        check(res == [True, True, True], "requests sent while the app starts are accepted; a second launch is told to leave", str(res))
        check(got == [] and shows == [], "nothing reaches the app before it is ready", f"{got} {shows}")
        first.release()
        check(got == [["a é.mp4"], ["b.mp4", "c.mp4"]] and shows == [1], "what waited arrives in order once it is ready", f"{got} {shows}")
        # from here on, at once; the fast path and the full path may both deliver one request: it counts once
        res = _launches(name, ["send", ["d.mp4"], ""], ["send", [], ""], ["send", ["e.mp4"], "same-id"], ["send", ["e.mp4"], "same-id"])
        check(res == [True, True, True, True], "every later request is acknowledged, the repeat included", str(res))
        check(got[2:] == [["d.mp4"], ["e.mp4"]], "later requests arrive directly, the repeated one once", str(got))
        check(shows == [1, 1], "a plain relaunch asks for the window")
    finally:
        first.close()
        first.lock.unlock()
    check(S.send_to_running(["x.mp4"], name, timeout_ms=200) is False, "after it closes nobody answers")
    again = S.claim_or_hand_over([], name, wait_s=2)
    _KEPT.append(again)
    check(isinstance(again, S.InstanceServer), "and the next launch becomes the instance", repr(again))
    again.close()
    again.lock.unlock()


@test
def fast_path_talks_to_the_instance_server():
    """refrata/fastlaunch.py (no Qt, start of main.py) against the real QLocalServer."""
    _qt()
    from refrata import fastlaunch as F, single as S
    check(F._roundtrip(b'{"open": [], "id": "0"}\n', 0.5) == b"", "no Refrata running: no answer, quickly")
    srv = S.InstanceServer()          # the default name: this test's own data folder
    _KEPT.append(srv)
    check(srv.listen(), "listening")
    got = []
    srv.received.connect(lambda files: got.append(list(files)))
    try:
        payload = json.dumps({"open": ["C:\\v\\a é.mp4", "https://x.example/v"], "id": "fast-1"}).encode("utf8") + b"\n"
        check(_pump(lambda: F._roundtrip(payload, 3)).startswith(b"ok"), "the running app takes the request")
        check(got == [["C:\\v\\a é.mp4", "https://x.example/v"]], "with the paths as sent, accents included", str(got))
        check(_pump(lambda: F._roundtrip(payload, 3)).startswith(b"ok") and len(got) == 1, "sent again with the same id: opened once")
        check(_pump(lambda: F._roundtrip(b"not json\n", 3)) == b"bad", "a line that is not a request is refused")
        check(_pump(lambda: F._roundtrip(b'[1, 2]\n', 3)) == b"bad", "so is JSON of another shape")
        check(_pump(lambda: F._roundtrip(b'{"open": 5}\n', 3)) == b"bad", "and a file list that is not a list")
        check(len(got) == 1, "none of those opened anything", str(got))
        junk = b"x" * (S.MAX_MESSAGE + 4096)
        check(_pump(lambda: F._roundtrip(junk, 5)) == b"", "megabytes without a line end are dropped")
        for _ in range(50):
            _qt().processEvents()
            time.sleep(0.01)
        check(not srv._buffers, "and nothing of them is kept", str({k: len(v) for k, v in srv._buffers.items()}))
        check(_pump(lambda: F._roundtrip(payload.replace(b"fast-1", b"fast-2"), 3)).startswith(b"ok") and len(got) == 2,
              "the server keeps working after the junk")
    finally:
        srv.close()


@test
def fast_path_reads_the_command_line_like_the_app():
    from refrata import fastlaunch as F
    here = str(Path(__file__).resolve())
    check(F._files(["Refrata", here]) == [here], "a file")
    check(F._files(["Refrata", "--page", "settings", "--theme", "dark", "--size", "800x600", "--delay", "50", here]) == [here],
          "the value of an option is not a file to open", str(F._files(["Refrata", "--page", "settings", here])))
    check(F._files(["Refrata", "--panel", "eq", "-k", "x", "--report", "r.json", "--log", "l.txt"]) == [], "every option with a value")
    check(F._files(["Refrata", "--page"]) == [], "an option at the very end")
    rel = os.path.relpath(here)
    check(F._files(["Refrata", rel]) == [os.path.abspath(rel)], "relative paths become absolute (the running app has another folder)")


# ---------------------------------------------------------------- startup helpers
@test
def startup_trace_writes_a_timeline_only_when_asked():
    out = TMP / "trace.txt"
    code = ("from refrata import trace; import time; trace.mark('one'); time.sleep(0.08); trace.mark('two');"
            "print(trace.enabled())")
    env = dict(os.environ, REFRATA_TRACE=str(out))
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
    check(r.stdout.strip() == "True", "enabled with REFRATA_TRACE", r.stdout + r.stderr)
    lines = out.read_text("utf8").splitlines()
    check(len(lines) == 2 and lines[0].endswith("ms  one") and lines[1].endswith("ms  two"), "one line per step", str(lines))
    a, b = (float(ln.split("ms")[0]) for ln in lines)
    check(0 <= a < 60000 and b >= a + 60, "times count from the start of the process and grow", f"{a} {b}")
    env.pop("REFRATA_TRACE")
    out.unlink()
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
    check(r.stdout.strip() == "False" and not out.exists(), "without it: off, and nothing is written", r.stdout + r.stderr)
    bad = dict(env, REFRATA_TRACE=str(TMP / "no-such-folder" / "t.txt"))
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=bad, capture_output=True, text=True, timeout=60)
    check(r.returncode == 0, "a trace file that cannot be written never stops the app", r.stderr)


@test
def libvlc_is_found_in_the_documented_order():
    from refrata import vlcpath as V
    keys = ("PYTHON_VLC_LIB_PATH", "PYTHON_VLC_MODULE_PATH", "VLC_PLUGIN_PATH", "PATH")
    saved = {k: os.environ.get(k) for k in keys}
    cands = V.candidates
    try:
        check(V.app_root() == ROOT, "from source, the app's folder is the repository", str(V.app_root()))
        c = cands()
        check(c[0] == ROOT / "vlc" and c.index(ROOT / "vendor" / "vlc") < len(c) - 1 or sys.platform != "win32",
              "a bundled copy comes before the repository's, and both before an installed VLC", str(c))
        os.environ["PYTHON_VLC_LIB_PATH"] = str(TMP / "given" / "libvlc.dll")
        check(V.configure() == TMP / "given", "a path given by the caller wins")
        os.environ.pop("PYTHON_VLC_LIB_PATH")
        if sys.platform == "win32":
            fake = TMP / "fakevlc"
            (fake / "plugins").mkdir(parents=True, exist_ok=True)
            (fake / "libvlc.dll").write_bytes(b"")
            half = TMP / "halfvlc"
            half.mkdir(exist_ok=True)
            (half / "libvlc.dll").write_bytes(b"")           # no libvlccore.dll: not a usable copy
            os.environ.pop("VLC_PLUGIN_PATH", None)
            V.candidates = lambda: [TMP / "nothing-here", half, fake]
            check(V.configure() is None, "a folder without both DLLs is not taken")
            check("PYTHON_VLC_LIB_PATH" not in os.environ, "and nothing is set")
            (fake / "libvlccore.dll").write_bytes(b"")
            check(V.configure() == fake, "the first folder holding libvlc.dll and libvlccore.dll is used", str(V.configure()))
            check(os.environ["PYTHON_VLC_LIB_PATH"] == str(fake / "libvlc.dll") and os.environ["PYTHON_VLC_MODULE_PATH"] == str(fake)
                  and os.environ["VLC_PLUGIN_PATH"] == str(fake / "plugins") and os.environ["PATH"].startswith(str(fake) + os.pathsep),
                  "python-vlc and the DLL search are pointed at it")
            check(V.preload_qt() == 0, "from source there are no bundled Qt DLLs to preload")
    finally:
        V.candidates = cands
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@test
def linux_shares_the_system_ffmpeg_only_when_it_is_the_same_major():
    """refrata.vlcpath.prefer_system_ffmpeg with a stand-in Qt folder, ldconfig and loader."""
    import ctypes
    import PySide6
    from refrata import vlcpath as V
    lib = TMP / "fakeqt" / "Qt" / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    for n, ver in (("avutil", "59.8.100"), ("swresample", "5.1.100"), ("swscale", "8.1.100"), ("avcodec", "61.3.100"), ("avformat", "61.1.100")):
        (lib / f"lib{n}.so.{ver}").write_bytes(b"")

    def listing(avcodec_major, skip=()):
        rows = [("libavutil.so.59", "/usr/lib/x86_64-linux-gnu/libavutil.so.59"),
                ("libavutil.so.59", "/usr/lib/i386-linux-gnu/libavutil.so.59"),
                ("libswresample.so.5", "/usr/lib/x86_64-linux-gnu/libswresample.so.5"),
                ("libswscale.so.8", "/usr/lib/x86_64-linux-gnu/libswscale.so.8"),
                (f"libavcodec.so.{avcodec_major}", f"/usr/lib/x86_64-linux-gnu/libavcodec.so.{avcodec_major}"),
                ("libavformat.so.61", "/usr/lib/x86_64-linux-gnu/libavformat.so.61")]
        text = "512 libs found in cache `/etc/ld.so.cache'\n"
        for i, (so, path) in enumerate(rows):
            if so in skip:
                continue
            arch = "libc6" if "i386" in path else "libc6,x86-64"
            text += f"\t{so} ({arch}) => {path}\n"
        return text

    real_file, real_run, real_cdll = PySide6.__file__, subprocess.run, ctypes.CDLL
    state = {"out": "", "fail": None, "loaded": []}

    def fake_run(cmd, **kw):
        if cmd and "ldconfig" in str(cmd[0]):
            if state["out"] is None:
                raise FileNotFoundError(cmd[0])
            return subprocess.CompletedProcess(cmd, 0, state["out"], "")
        return real_run(cmd, **kw)

    def fake_cdll(path, mode=0, **kw):
        if state["fail"] and state["fail"] in str(path):
            raise OSError("cannot open " + str(path))
        state["loaded"].append(str(path))
        return object()
    PySide6.__file__ = str(TMP / "fakeqt" / "__init__.py")
    subprocess.run, ctypes.CDLL = fake_run, fake_cdll
    try:
        state["out"] = listing(61)
        got = V.prefer_system_ffmpeg()
        want = [f"/usr/lib/x86_64-linux-gnu/lib{n}.so.{v}" for n, v in
                (("avutil", 59), ("swresample", 5), ("swscale", 8), ("avcodec", 61), ("avformat", 61))]
        check(got == want and state["loaded"] == want, "same major: the system's five libraries, 64-bit ones, in dependency order", str(got))
        state.update(out=listing(60), loaded=[])
        check(V.prefer_system_ffmpeg() == [] and state["loaded"] == [], "another major (Ubuntu 24.04): nothing is loaded")
        state.update(out=listing(61, skip=("libswscale.so.8",)), loaded=[])
        got = V.prefer_system_ffmpeg()
        check(len(got) == 4 and not any("swscale" in p for p in got), "a library the system lacks is skipped", str(got))
        state.update(out=listing(61), loaded=[], fail="libswscale")
        got = V.prefer_system_ffmpeg()
        check(got == want[:2], "a library that does not load stops the list there (no half-mixed set after it)", str(got))
        state.update(out=None, loaded=[], fail=None)
        check(V.prefer_system_ffmpeg() == [], "no ldconfig: nothing is done")
        PySide6.__file__ = str(TMP / "no-qt-here" / "__init__.py")
        state["out"] = listing(61)
        check(V.prefer_system_ffmpeg() == [], "a Qt without its own FFmpeg: nothing is done")
    finally:
        PySide6.__file__, subprocess.run, ctypes.CDLL = real_file, real_run, real_cdll


# ---------------------------------------------------------------- shortcuts
class _Store:
    def __init__(self, **kv):
        self.d = dict(kv)

    def value(self, k, default=None):
        return self.d.get(k, default)

    def setValue(self, k, v):
        self.d[k] = v


@test
def shortcuts_one_key_per_action_whatever_the_file_says():
    _qt()
    from PySide6.QtCore import Qt
    from refrata import shortcuts as K
    s = K.Shortcuts(_Store())
    check(s.map == K.DEFAULTS and not s.customized, "defaults to begin with")
    check(len(set(K.DEFAULTS.values())) == len(K.DEFAULTS), "no two default actions share a key")
    check(not set(K.DEFAULTS.values()) & K.RESERVED, "no default uses a reserved key")
    check(all(K.normalize(v) == v for v in K.DEFAULTS.values()), "defaults are written the way Qt spells them",
          str([v for v in K.DEFAULTS.values() if K.normalize(v) != v]))
    # taking another action's key leaves that one without a key
    check(s.assign("mute", "n") == "next", "the action that had the key is named")
    check(s.keyOf("mute") == "N" and s.keyOf("next") == "" and s.customized, "one key, one action", str(s.map))
    check(s._settings.d["shortcuts"] == {"mute": "N", "next": ""}, "only what differs from the defaults is stored", str(s._settings.d))
    check(s.assign("mute", "Esc") == "reserved" and s.assign("mute", "F11") == "reserved" and s.keyOf("mute") == "N", "Esc and F11 stay fixed")
    check(s.assign("nope", "Q") == "" and s.assign("mute", "") == "" and s.keyOf("mute") == "N", "unknown actions and empty keys change nothing")
    check(s.keyOf("nope") == "", "an unknown action has no key")
    # what was stored comes back
    again = K.Shortcuts(_Store(shortcuts=dict(s._settings.d["shortcuts"])))
    check(again.map == s.map, "stored choices are read back", str(again.map))
    # one action back to its default: whoever holds that key now loses it
    s.assign("info", "M")
    s.reset("mute")
    check(s.keyOf("mute") == "M" and s.keyOf("info") == "", "reset gives the default back and frees it from the other action", str(s.map))
    s.reset("nope")
    s.resetAll()
    check(s.map == K.DEFAULTS and not s.customized and s._settings.d["shortcuts"] == {}, "restore all")
    # a hand-edited file: duplicates, reserved keys, junk
    odd = K.Shortcuts(_Store(shortcuts={"mute": "f", "next": "Esc", "prev": 7, "ghost": "Q", "open": "ctrl+shift+o"}))
    m = odd.map
    check(m["fullscreen"] == "F" and m["mute"] == "", "two actions on one key: the first in the list keeps it", f"{m['fullscreen']!r} {m['mute']!r}")
    check(m["next"] == "" and m["prev"] == "P" and "ghost" not in m and m["open"] == "Ctrl+Shift+O", "reserved keys, wrong types and unknown actions are dropped", str(m))
    keys = [v for v in m.values() if v]
    check(len(keys) == len(set(keys)), "still one key per action")
    # key presses → text
    ctrl, shift = Qt.KeyboardModifier.ControlModifier.value, Qt.KeyboardModifier.ShiftModifier.value
    check(s.fromKey(Qt.Key.Key_O.value, ctrl) == "Ctrl+O" and s.fromKey(Qt.Key.Key_Left.value, shift) == "Shift+Left", "a key press becomes its shortcut text")
    check(s.fromKey(Qt.Key.Key_Control.value, ctrl) == "" and s.fromKey(Qt.Key.Key_Shift.value, shift) == "", "a lone modifier is still waiting for its key")
    check(s.fromKey(Qt.Key.Key_Plus.value, ctrl) == "Ctrl++", "plus is a key too", s.fromKey(Qt.Key.Key_Plus.value, ctrl))
    check(s.label("") == "—" and s.label("Shift+Left") == "Shift + ←" and s.label("+") == "+" and s.label("Ctrl++") == "Ctrl + +",
          "labels: arrows as arrows, plus kept", f"{s.label('Shift+Left')!r} {s.label('Ctrl++')!r}")


# ---------------------------------------------------------------- updates: the paths the app battery does not take
class _Release(http.server.BaseHTTPRequestHandler):
    files: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = type(self).files.get(self.path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _until(cond, what, timeout=15.0):
    from PySide6.QtCore import QCoreApplication
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError("timed out: " + what)


@test
def update_refuses_what_it_cannot_verify():
    _qt()
    from refrata import ed25519, updater as U
    srv, base = serve(_Release)
    key = hashlib.sha256(b"unit release key").digest()
    saved = {k: os.environ.get(k) for k in ("REFRATA_UPDATE_PUBKEY", "REFRATA_UPDATE_KIND", "REFRATA_UPDATE_DRYRUN")}
    os.environ["REFRATA_UPDATE_PUBKEY"] = ed25519.public_key(key).hex()
    os.environ["REFRATA_UPDATE_KIND"] = "installer"
    dry = TMP / "unit_dryrun.json"
    os.environ["REFRATA_UPDATE_DRYRUN"] = str(dry)
    name = "Refrata-Setup-9.9.9.exe"
    blob = os.urandom(150_000)
    sha = hashlib.sha256(blob).hexdigest()
    F = _Release.files
    F.clear()
    F["/" + name] = blob
    updates = Path(os.environ["REFRATA_DATA"]) / "updates"

    def release(**asset):
        a = {"name": name, "size": len(blob), "browser_download_url": base + "/" + name}
        a.update(asset)
        extra = [{"name": name + suffix, "browser_download_url": base + "/" + name + suffix}
                 for suffix in (".sha256", ".sig") if "/" + name + suffix in F]
        return {"tag_name": "v9.9.9", "body": " notas ", "html_url": base + "/page", "assets": [a] + extra}

    def sign(version="9.9.9", digest=sha):
        F["/" + name + ".sig"] = ed25519.sign(key, U.update_message(name, digest, version)).hex().encode()
    keep = _KEPT
    real = U.Updater

    def Updater(store):
        keep.append(real(store))   # an app has one for its whole life: none is destroyed mid-test
        return keep[-1]
    try:
        up = Updater(_Store())
        check(up.kind == "installer" and up.canSelfUpdate and up.current, "an installed copy updates itself")
        # a release without a Windows installer
        up.apply_release({"tag_name": "v9.9.9", "assets": [{"name": "refrata_9.9.9_amd64.deb"}, {"name": "Refrata-9.9.9-portable.zip"}]})
        check(up.state == "available" and up.latest == "9.9.9" and up.bannerVisible, "newer version seen")
        up.update()
        check(up.state == "available", "the button has nothing to download")
        up.download()
        check(up.state == "error" and "instalador" in up.error, "asked directly, it says there is no installer", up.error)
        # no hash anywhere: refused
        up.apply_release(release())
        up.download()
        check(up.state == "error" and "SHA-256" in up.error, "a release that publishes no SHA-256 is refused", up.error)
        # hash in a side file (GitHub answers without digests): fetched first; signature still required
        F["/" + name + ".sha256"] = f"{sha}  {name}\n".encode()
        up.apply_release(release())
        up.download()
        _until(lambda: up.state != "downloading", "side file fetched")
        check(up.state == "error" and "assinado" in up.error, "hash found, but an unsigned release goes no further", up.error)
        check(not list(updates.glob("*.exe*")) and not list(updates.glob("*.part")), "nothing was downloaded")
        # a signature that is not hex, then one for another version, then for another hash
        for what, put in (("not hex", lambda: F.__setitem__("/" + name + ".sig", b"<html>not a signature</html>")),
                          ("made for another version", lambda: sign(version="9.9.8")),
                          ("made for another file hash", lambda: sign(digest="0" * 64))):
            put()
            up.apply_release(release())
            up.download()
            _until(lambda: up.state != "downloading", what)
            check(up.state == "error" and "assinado" in up.error and not list(updates.glob("*.exe*")), f"signature {what}: refused, nothing downloaded", up.error)
        # everything right: side-file hash + signature → downloaded, verified, ready
        sign()
        up.apply_release(release())
        up.update()
        _until(lambda: up.state in ("ready", "error"), "download")
        check(up.state == "ready" and Path(up.downloadedPath).read_bytes() == blob and up.progress == 1.0, "a signed release with a side-file hash installs", up.error)
        check(not list(updates.glob("*.part")), "no partial file is left")
        up.update()
        cmd = json.loads(dry.read_text("utf8"))
        check(up.state == "installing" and cmd["exe"] == up.downloadedPath and "/VERYSILENT" in cmd["args"], "the button then starts the installer", str(cmd))
        up.check(True)
        check(up.state == "installing", "a check while installing changes nothing")
        # the side file answers 404 / holds no hash
        for what, body in (("missing", None), ("without a hash in it", b"see the website\n")):
            up = Updater(_Store())
            F.pop("/" + name + ".sha256", None)
            data = release()
            data["assets"].append({"name": name + ".sha256", "browser_download_url": base + "/" + name + ".sha256"})
            if body is not None:
                F["/" + name + ".sha256"] = body
            up.apply_release(data)
            up.download()
            _until(lambda: up.state != "downloading", "side file " + what)
            check(up.state == "error" and "conferir" in up.error, f"side file {what}: the download cannot be checked", up.error)
        # the file on the server is not the one that was signed: size, then content
        F["/" + name + ".sha256"] = f"{sha}  {name}\n".encode()
        sign()
        for what, served, word in (("shorter", blob[:-10], "incompleto"), ("altered", bytes([blob[0] ^ 1]) + blob[1:], "SHA-256")):
            F["/" + name] = served
            up = Updater(_Store())
            up.apply_release(release())
            up.download()
            _until(lambda: up.state in ("ready", "error"), what)
            check(up.state == "error" and word in up.error, f"a {what} file is refused", f"{up.state} {up.error}")
            check(not list(updates.glob("*.exe")) and not list(updates.glob("*.part")) and up.downloadedPath == "", "and removed")
            up.install()
            check(up.state == "error", "nothing to install")
        F["/" + name] = blob
        # answers that are not a release
        F["/latest.json"] = b"<html>rate limited</html>"
        F["/list.json"] = b"[1, 2, 3]"
        for path in ("/latest.json", "/list.json", "/gone.json"):
            up = Updater(_Store())
            up._url = base + path
            up.check(True)
            _until(lambda: up.state != "checking", path)
            check(up.state == "error" and up.error, f"asked by hand, {path} is explained", up.state)
            up.check(False)
            _until(lambda: up.state != "checking", path)
            check(up.state == "idle" and not up.bannerVisible, f"in the background, {path} stays quiet", up.state)
        # same or older version, odd tags, the Store build, the banner's close button
        up = Updater(_Store())
        for tag, state in ((up.current, "uptodate"), ("v0.0.1", "uptodate"), ("", "uptodate"), ("nightly", "uptodate"), ("v99.0", "available")):
            up.apply_release({"tag_name": tag})
            check(up.state == state, f"tag {tag!r} → {state}", up.state)
        check(up.bannerVisible, "banner for the newer one")
        up.dismiss()
        check(not up.bannerVisible, "closed by the person")
        up._kind = "store"
        up._dismissed = False
        check(not up.bannerVisible, "the Store build never offers its own update")
        store = _Store()
        up = Updater(store)
        check(up.auto is True, "automatic updates are on unless turned off")
        up.auto = False
        check(store.d["autoUpdate"] is False and up.auto is False, "the switch is stored")
        check(U.parse_version("") == (0, 0, 0) and U.parse_version("1.5.0-beta2") == (1, 5, 0) and U.parse_version("2") == (2, 0, 0), "odd version texts")
    finally:
        srv.shutdown()
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def main():
    only = sys.argv[sys.argv.index("-k") + 1] if "-k" in sys.argv else ""
    t0 = time.time()
    ok = 0
    chosen = [t for t in TESTS if only in t.__name__]
    for t in chosen:
        try:
            t()
            ok += 1
            print(f"  PASS  {t.__name__}")
        except Exception as exc:
            detail = str(exc) if isinstance(exc, AssertionError) else traceback.format_exc()
            print(f"  FAIL  {t.__name__}\n        {detail.strip()}")
    print(f"\n{ok}/{len(chosen)} unit tests passed in {time.time() - t0:.1f}s")
    sys.exit(0 if ok == len(chosen) else 1)


if __name__ == "__main__":
    main()
