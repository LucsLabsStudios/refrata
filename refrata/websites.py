"""Links to video pages (YouTube, Vimeo…): yt-dlp finds the actual streams, libVLC plays them.

YouTube serves picture and sound as separate streams above 360p: the video stream is opened
and the audio one is attached as an input slave. H.264 is preferred (hardware decoding on
every PC) up to 1080p. Stream addresses expire after a few hours, so a resolution is reused
only for a while.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

from . import ytdlp

PAGE_HOSTS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com", "music.youtube.com", "vimeo.com", "dailymotion.com",
    "dai.ly", "twitch.tv", "soundcloud.com", "bandcamp.com", "bilibili.com", "tiktok.com", "x.com",
    "twitter.com", "facebook.com", "fb.watch", "instagram.com", "reddit.com", "streamable.com", "rumble.com",
    "odysee.com", "kick.com", "mixcloud.com", "ted.com", "archive.org", "globoplay.globo.com",
)
FORMAT = "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b"
FRESH_SECONDS = 3 * 3600


def available() -> bool:
    try:
        return ytdlp.available()
    except Exception:
        return False


def is_page_url(url: str) -> bool:
    """A web page with a player in it (not a direct media file or a stream manifest)."""
    if not url.lower().startswith(("http://", "https://")):
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in PAGE_HOSTS)


SITE_NAMES = {
    "youtube.com": "YouTube", "youtu.be": "YouTube", "youtube-nocookie.com": "YouTube",
    "music.youtube.com": "YouTube Music", "vimeo.com": "Vimeo", "dailymotion.com": "Dailymotion",
    "dai.ly": "Dailymotion", "twitch.tv": "Twitch", "soundcloud.com": "SoundCloud", "bandcamp.com": "Bandcamp",
    "bilibili.com": "Bilibili", "tiktok.com": "TikTok", "x.com": "X", "twitter.com": "X",
    "facebook.com": "Facebook", "fb.watch": "Facebook", "instagram.com": "Instagram", "reddit.com": "Reddit",
    "streamable.com": "Streamable", "rumble.com": "Rumble", "odysee.com": "Odysee", "kick.com": "Kick",
    "mixcloud.com": "Mixcloud", "ted.com": "TED", "archive.org": "Internet Archive",
    "globoplay.globo.com": "Globoplay",
}


def site_name(url: str) -> str:
    """"YouTube" for a youtube.com link; the bare host for anything else."""
    host = (urlparse(url).hostname or "").lower()
    for h in sorted(SITE_NAMES, key=len, reverse=True):   # music.youtube.com before youtube.com
        if host == h or host.endswith("." + h):
            return SITE_NAMES[h]
    return host.removeprefix("www.")


_cache: dict[str, dict] = {}


def resolve(url: str) -> dict:
    """{"title", "video", "audio" (or ""), "headers", "duration" (ms), "thumbnail", "live"}.
    Raises on failure (no network, private video, site changed…)."""
    hit = _cache.get(url)
    if hit and time.time() - hit["at"] < FRESH_SECONDS:
        return hit
    ytdlp.check_soon()   # at most once a day, in the background
    try:
        info = _extract(url)
    except Exception:
        # sites change and break old yt-dlp versions: get the newest one and try once more
        if not ytdlp.check(force=True):
            raise
        info = _extract(url)
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError("empty playlist")
        info = entries[0]
    fmts = info.get("requested_formats") or [info]
    video = fmts[0].get("url") or info.get("manifest_url") or ""
    if not video:
        raise RuntimeError("no stream found")
    audio = fmts[1].get("url", "") if len(fmts) > 1 else ""
    out = {"title": info.get("title") or url, "video": video, "audio": audio,
           "headers": dict(fmts[0].get("http_headers") or info.get("http_headers") or {}),
           "duration": int((info.get("duration") or 0) * 1000), "thumbnail": info.get("thumbnail") or "",
           "live": bool(info.get("is_live")), "at": time.time()}
    _cache[url] = out
    return out


_extract_lock = threading.Lock()   # a newer yt-dlp is swapped in between two lookups, never during one


def _extract(url: str) -> dict:
    with _extract_lock:
        ytdlp.activate()
        try:
            import yt_dlp
        except Exception:
            if not ytdlp.active():
                raise
            ytdlp.reject()            # a downloaded copy that needs something this build lacks
            import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                "socket_timeout": 15, "format": FORMAT}
        with yt_dlp.YoutubeDL(opts) as y:
            return y.extract_info(url, download=False)
