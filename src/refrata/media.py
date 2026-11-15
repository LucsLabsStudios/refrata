import os
import re

VIDEO_EXT = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg",
    ".ts", ".m2ts", ".mts", ".3gp", ".ogv", ".vob", ".divx", ".rmvb", ".asf", ".f4v",
}
AUDIO_EXT = {
    ".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".alac", ".aiff",
    ".ape", ".mka", ".mid", ".midi", ".amr", ".ac3", ".dts",
}
SUB_EXT = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".smi"}
PLAYLIST_EXT = {".m3u", ".m3u8", ".pls", ".xspf"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT


def is_audio_path(p: str) -> bool:
    return os.path.splitext(p)[1].lower() in AUDIO_EXT


def is_media_path(p: str) -> bool:
    return os.path.splitext(p)[1].lower() in MEDIA_EXT | PLAYLIST_EXT


def nice_title(path: str) -> str:
    """'The.Movie.2019.1080p.x264-GRP.mkv' -> 'The Movie 2019'."""
    name = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(
        r"\b(2160p|1080p|720p|480p|4k|x264|x265|h264|h265|hevc|bluray|brrip|webrip|web dl|"
        r"web-dl|hdrip|dvdrip|aac|ac3|dts|10bit|hdr)\b.*$",
        "",
        name,
        flags=re.I,
    )
    name = re.sub(r"\s+", " ", name).strip(" -")
    return name or os.path.basename(path)
