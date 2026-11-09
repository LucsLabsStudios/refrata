"""The VLC engine (libVLC) that Refrata bundles: which version, where to get it, is there a newer one.

The version lives in tools/vlc-version.txt. The build downloads that exact release from VideoLAN
(the official win64 zip), checks its SHA-256 and copies libVLC out of it, so every build (on this
PC or on GitHub Actions) ships the same engine, whatever VLC is installed on the machine.

    python tools/vlc_engine.py            # pinned vs latest
    python tools/vlc_engine.py --fetch    # download + verify the pinned zip (prints its path)

To move to a new engine: put the new version in tools/vlc-version.txt, build, run the test suites
(tests/e2e via --selftest, tests/robust.py), release.
"""
from __future__ import annotations

import hashlib
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN_FILE = ROOT / "tools" / "vlc-version.txt"
CACHE = ROOT / "build" / "cache"
BASE = "https://download.videolan.org/pub/videolan/vlc"
UA = "Refrata-build (+https://github.com/LucsLabsStudios/refrata)"


def pinned() -> str:
    return PIN_FILE.read_text(encoding="utf8").strip()


def _get(url: str, timeout=30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _key(v: str):
    return tuple(int(x) for x in v.split("."))


def latest() -> str:
    """Newest VLC release that has a Windows 64-bit zip (VideoLAN's "last" folder)."""
    html = _get(f"{BASE}/last/win64/").decode("utf8", "replace")
    found = set(re.findall(r"vlc-(\d+(?:\.\d+){2,3})-win64\.zip\b", html))
    if not found:
        raise RuntimeError("no win64 zip listed at VideoLAN")
    return max(found, key=_key)


def newer(a: str, b: str) -> bool:
    """a is a newer version than b."""
    return _key(a) > _key(b)


def same_major(a: str, b: str) -> bool:
    """libVLC 4 changes the API: a jump of the first number is a port, not an update."""
    return a.split(".")[0] == b.split(".")[0]


def fetch(version: str | None = None) -> Path:
    """The official win64 zip of `version`, downloaded once into build/cache and verified."""
    version = version or pinned()
    name = f"vlc-{version}-win64.zip"
    url = f"{BASE}/{version}/win64/{name}"
    expected = _get(url + ".sha256").decode("ascii").split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeError(f"bad checksum file for {name}")
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / name
    if dest.exists() and _sha256(dest) == expected:
        return dest
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    got = _sha256(tmp)
    if got != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{name}: SHA-256 does not match VideoLAN's ({got} != {expected})")
    tmp.replace(dest)
    return dest


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def main():
    if "--fetch" in sys.argv:
        print(fetch())
        return
    pin, last = pinned(), latest()
    print(f"bundled: {pin}   latest at VideoLAN: {last}")
    if newer(last, pin):
        print("newer engine available" + ("" if same_major(last, pin) else " (new major version: needs porting)"))
        sys.exit(10)


if __name__ == "__main__":
    main()
