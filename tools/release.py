"""Publish a Refrata release on GitHub (feeds the website and the in-app updater).

    python tools/release.py --notes "O que mudou"            # build + tag + release
    python tools/release.py --skip-build --notes-file NOTES.md
    python tools/release.py --draft ...                       # prepare without publishing

Uploads Refrata-Setup-<v>.exe, its .sha256 (the updater refuses downloads that do not match)
and Refrata-<v>-portable.zip to the tag v<version> of LucsLabsStudios/refrata.
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from refrata import __version__  # noqa: E402
from refrata.updater import REPO  # noqa: E402

DIST = ROOT / "dist"


def run(*cmd, **kw):
    print("$", " ".join(map(str, cmd)), flush=True)
    return subprocess.run(list(map(str, cmd)), check=True, cwd=str(ROOT), **kw)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--notes", default="")
    ap.add_argument("--notes-file")
    ap.add_argument("--draft", action="store_true")
    args = ap.parse_args()

    tag = f"v{__version__}"
    existing = subprocess.run(["gh", "release", "view", tag, "--repo", REPO], capture_output=True)
    if existing.returncode == 0:
        sys.exit(f"{tag} já foi publicado. Suba a versão em refrata/__init__.py antes.")
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=str(ROOT)).stdout
    if dirty.strip():
        sys.exit("Há alterações sem commit. O lançamento precisa corresponder ao código publicado.")

    if not args.skip_build:
        run(sys.executable, "tools/build.py", "--no-msix")
    setup = DIST / f"Refrata-Setup-{__version__}.exe"
    portable = DIST / f"Refrata-{__version__}-portable.zip"
    for f in (setup, portable):
        if not f.exists():
            sys.exit(f"faltando: {f}")
    side = setup.with_name(setup.name + ".sha256")
    side.write_text(f"{sha256(setup)}  {setup.name}\n", encoding="ascii")

    run("git", "tag", "-a", tag, "-m", f"Refrata {__version__}")
    run("git", "push", "origin", tag)
    notes = ["--notes-file", args.notes_file] if args.notes_file else ["--notes", args.notes or f"Refrata {__version__}"]
    run("gh", "release", "create", tag, setup, side, portable, "--repo", REPO,
        "--title", f"Refrata {__version__}", *notes, *(["--draft"] if args.draft else []))
    print(f"\npublicado: https://github.com/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
