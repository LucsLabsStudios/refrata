"""Links to video pages (YouTube, Vimeo…): yt-dlp finds the actual streams, libVLC plays them.

YouTube serves picture and sound as separate streams above 360p: the video stream is opened
and the audio one is attached as an input slave. H.264 is preferred (hardware decoding on
every PC) up to 1080p. Stream addresses expire after a few hours, so a resolution is reused
only for a while.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

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
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False


def is_page_url(url: str) -> bool:
    """A web page with a player in it (not a direct media file or a stream manifest)."""
    if not url.lower().startswith(("http://", "https://")):
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in PAGE_HOSTS)


_cache: dict[str, dict] = {}


def resolve(url: str) -> dict:
    """{"title", "video", "audio" (or ""), "headers", "duration" (ms), "thumbnail", "live"}.
    Raises on failure (no network, private video, site changed…)."""
    hit = _cache.get(url)
    if hit and time.time() - hit["at"] < FRESH_SECONDS:
        return hit
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "socket_timeout": 15, "format": FORMAT}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=False)
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
