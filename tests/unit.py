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
import sys
import tempfile
import threading
import time
import traceback
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
