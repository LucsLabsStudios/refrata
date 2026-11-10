"""Publish a Refrata release on GitHub (feeds the website and the in-app updater).

    python tools/release.py --notes-file NOTES.md   # the usual: refresh components, build, test, publish
    python tools/release.py --bump                  # engine-only release: next patch version (1.4.1 → 1.4.2)
    python tools/release.py --keep-versions ...     # do not touch the VLC / yt-dlp / python-vlc versions
    python tools/release.py --skip-build --skip-tests ...   # reuse dist/ as it is (you tested it yourself)
    python tools/release.py --draft ...             # prepare without publishing

What it does, in order:
1. Components: the newest VLC 3.x (VideoLAN), yt-dlp and python-vlc 3.x (PyPI) go into
   tools/vlc-version.txt and requirements.txt, and pip installs them.
2. Builds dist/ (tools/build.py --no-msix).
3. Runs both test suites against the built app: the e2e battery (Refrata.exe --selftest) and the
   robustness suite (tests/robust.py --exe). If anything fails, nothing is published, and new
   component versions are rolled back (files and pip), so the next run starts from known-good ones.
4. Commits the component versions (if they changed), tags v<version>, pushes, and uploads
   Refrata-Setup-<v>.exe, its .sha256 (the updater refuses downloads that do not match) and
   Refrata-<v>-portable.zip; the release notes get a line for each updated component.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import vlc_engine  # noqa: E402
from refrata.updater import REPO  # noqa: E402

DIST = ROOT / "dist"
REQS = ROOT / "requirements.txt"
INIT = ROOT / "refrata" / "__init__.py"


def run(*cmd, **kw):
    print("$", " ".join(map(str, cmd)), flush=True)
    return subprocess.run(list(map(str, cmd)), check=True, cwd=str(ROOT), **kw)


def git(*args) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=str(ROOT)).stdout.strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version() -> str:
    return re.search(r'__version__ = "([^"]+)"', INIT.read_text(encoding="utf8")).group(1)


def released(v: str) -> bool:
    return subprocess.run(["gh", "release", "view", f"v{v}", "--repo", REPO], capture_output=True).returncode == 0


def _vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


# ------------------------------------------------------------------ components
def _pypi_latest(package: str, major: str = "") -> str:
    req = urllib.request.Request(f"https://pypi.org/pypi/{package}/json", headers={"User-Agent": vlc_engine.UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        meta = json.load(r)
    if not major:
        return meta["info"]["version"]
    stable = [v for v, files in meta["releases"].items()
              if files and re.fullmatch(r"[\d.]+", v) and v.split(".")[0] == major]
    return max(stable, key=_vkey)


def _pin(package: str) -> str:
    m = re.search(rf"^{re.escape(package)}==([^\s#]+)", REQS.read_text(encoding="utf8"), re.M)
    return m.group(1) if m else ""


def _set_pin(package: str, v: str):
    text = REQS.read_text(encoding="utf8")
    REQS.write_text(re.sub(rf"^({re.escape(package)}==)[^\s#]+", rf"\g<1>{v}", text, flags=re.M), encoding="utf8")


def refresh_components() -> list[tuple[str, str, str]]:
    """Move to the newest compatible versions. Returns [(name, old, new)] for what changed."""
    changed = []
    old = vlc_engine.pinned()
    new = vlc_engine.latest()
    if vlc_engine.newer(new, old):
        if vlc_engine.same_major(new, old):
            vlc_engine.PIN_FILE.write_text(new + "\n", encoding="utf8")
            changed.append(("VLC", old, new))
        else:
            print(f"AVISO: saiu o VLC {new}, uma versão principal nova; ela exige adaptar o app e não entra sozinha.")
    for package, major in (("yt-dlp", ""), ("python-vlc", "3")):
        old = _pin(package)
        new = _pypi_latest(package, major)
        if old and _vkey(new) > _vkey(old):
            _set_pin(package, new)
            changed.append((package, old, new))
    if changed:
        run(sys.executable, "-m", "pip", "install", "-q", "-r", REQS)
    return changed


def roll_back(changed):
    run("git", "checkout", "--", vlc_engine.PIN_FILE, REQS)
    run(sys.executable, "-m", "pip", "install", "-q", "-r", REQS)
    print("versões anteriores restauradas: " + ", ".join(f"{name} {old}" for name, old, _new in changed))


# ------------------------------------------------------------------ tests
def run_tests() -> bool:
    """Both suites against the built app, as a user would get it."""
    exe = DIST / "Refrata" / "Refrata.exe"
    env = dict(os.environ, REFRATA_NO_MEDIAKEYS="1")
    print("\n== testes e2e (app empacotado; vários processos ao mesmo tempo, os de teclado no fim)", flush=True)
    e2e = subprocess.run([sys.executable, "tests/e2e_parallel.py", "--exe", str(exe),
                          "--report", str(ROOT / "build" / "e2e-release.json")], cwd=str(ROOT), env=env).returncode
    print("\n== testes de robustez (app empacotado)", flush=True)
    robust = subprocess.run([sys.executable, "tests/robust.py", "--exe", str(exe),
                             "--report", str(ROOT / "build" / "robust-release.json")], cwd=str(ROOT), env=env).returncode
    return e2e == 0 and robust == 0


WSL_DISTRO = os.environ.get("REFRATA_WSL", "Ubuntu-24.04")


def linux_package(v: str) -> list[Path] | None:
    """The .deb and .tar.gz, built in WSL and tested (whole e2e battery inside the package).
    [] when there is no WSL here (the release goes out for Windows only); None when the Linux
    build or its tests failed."""
    if not shutil.which("wsl"):
        print("\n== Linux: sem WSL nesta máquina, o lançamento sai só para Windows", flush=True)
        return []
    drive, rest = str(ROOT)[0].lower(), str(ROOT)[2:].replace("\\", "/")
    root = f"/mnt/{drive}{rest}"
    script = (f'cd "{root}" && grep -vi "^winrt" requirements.txt > /tmp/refrata-req.txt && '
              f'~/refrata-venv/bin/pip install -q -r /tmp/refrata-req.txt pyinstaller && '
              f'~/refrata-venv/bin/python tools/build_linux.py --test')
    # runs while Windows builds and tests (see main): its output goes to a log, the summary at the end
    log = ROOT / "build" / "linux-release.log"
    log.parent.mkdir(exist_ok=True)
    t0 = time.time()
    with open(log, "w", encoding="utf8", errors="replace") as out:
        code = subprocess.run(["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc", script],
                              stdout=out, stderr=subprocess.STDOUT).returncode
    text = log.read_text(encoding="utf8", errors="replace").splitlines()
    keep = [ln for ln in text if "tests passed" in ln or "FAIL" in ln or ln.startswith(("[clean", "  ", "[build-linux]"))]
    print(f"\n== Linux: .deb e .tar.gz (WSL), bateria e2e no .tar.gz e instalação limpa em "
          f"Ubuntu 24.04 e Debian 13 ({time.time() - t0:.0f}s; log: {log})\n" + "\n".join(keep[-40:]), flush=True)
    if code != 0:
        return None
    out = [DIST / "linux" / f"refrata_{v}_amd64.deb", DIST / "linux" / f"Refrata-{v}-linux-x86_64.tar.gz"]
    return out if all(f.exists() for f in out) else None


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", default="")
    ap.add_argument("--notes-file")
    ap.add_argument("--bump", action="store_true", help="next patch version, for a release without app changes")
    ap.add_argument("--keep-versions", action="store_true", help="do not update VLC / yt-dlp / python-vlc")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--no-linux", action="store_true", help="Windows only (skip the WSL Linux package)")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT / "tools"))
    import update_key
    if update_key.read_secret() is None:
        sys.exit("Sem REFRATA_UPDATE_KEY no .env (tools/update_key.py): o app recusaria um lançamento sem "
                 "assinatura. Nada foi feito.")
    if git("status", "--porcelain"):
        sys.exit("Há alterações sem commit. O lançamento precisa corresponder ao código publicado.")
    v = version()
    if released(v):
        if not args.bump:
            sys.exit(f"v{v} já foi publicado. Suba a versão em refrata/__init__.py, ou use --bump.")
        parts = v.split(".")
        parts[-1] = str(int(parts[-1]) + 1)
        nv = ".".join(parts)
        INIT.write_text(INIT.read_text(encoding="utf8").replace(f'"{v}"', f'"{nv}"'), encoding="utf8")
        print(f"versão: {v} → {nv}")
        v = nv

    changed = [] if args.keep_versions else refresh_components()
    for name, old, new in changed:
        print(f"componente: {name} {old} → {new}")
    if args.bump and not changed and not git("log", "--oneline", f"v{version_before(v)}..HEAD"):
        run("git", "checkout", "--", INIT)
        sys.exit("Nada novo desde a última versão (código e componentes iguais). Nada foi publicado.")

    # Linux builds and tests in WSL meanwhile: the two take about as long, so they overlap
    from concurrent.futures import ThreadPoolExecutor
    linux_pool = ThreadPoolExecutor(1)
    linux_job = None if args.no_linux else linux_pool.submit(linux_package, v)
    if not args.skip_build or changed:
        run(sys.executable, "tools/build.py", "--no-msix")
    if not args.skip_tests or changed:
        if not run_tests():
            if changed:
                roll_back(changed)
            if args.bump:
                run("git", "checkout", "--", INIT)
            sys.exit("\nOs testes falharam: NADA foi publicado. Veja os relatórios acima.")
    linux = [] if linux_job is None else linux_job.result()
    if linux is None:
        if changed:
            roll_back(changed)
        if args.bump:
            run("git", "checkout", "--", INIT)
        sys.exit("\nO pacote Linux falhou nos testes: NADA foi publicado. Use --no-linux para sair só no Windows.")

    setup = DIST / f"Refrata-Setup-{v}.exe"
    portable = DIST / f"Refrata-{v}-portable.zip"
    for f in (setup, portable):
        if not f.exists():
            sys.exit(f"faltando: {f}")
    side = setup.with_name(setup.name + ".sha256")
    side.write_text(f"{sha256(setup)}  {setup.name}\n", encoding="ascii")
    # installed apps only take an update signed with the key in .env (tools/update_key.py)
    sys.path.insert(0, str(ROOT / "tools"))
    import update_key
    secret = update_key.read_secret()
    if secret is None:
        if args.bump:
            run("git", "checkout", "--", INIT)
        sys.exit("Sem REFRATA_UPDATE_KEY no .env: um lançamento sem assinatura não seria aceito pelo app. "
                 "Nada foi publicado.")
    sigs = [update_key.sign_file(f, v, secret) for f in (setup, portable, *linux)]

    # the versions that were tested are the ones committed
    if changed or args.bump:
        msg = f"{v}: " + ", ".join(f"{n} {new}" for n, _old, new in changed) if changed else f"{v}"
        run("git", "add", vlc_engine.PIN_FILE, REQS, INIT)
        # nothing staged when HEAD already carries this version (a release retried after a failure)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(ROOT)).returncode:
            run("git", "commit", "-q", "-m", msg + (" (componentes atualizados)" if changed else ""))
    run("git", "push", "origin", "HEAD")
    tag = f"v{v}"
    run("git", "tag", "-a", tag, "-m", f"Refrata {v}")
    run("git", "push", "origin", tag)

    notes = Path(args.notes_file).read_text(encoding="utf8") if args.notes_file else (args.notes or "")
    if changed:
        notes = notes.rstrip() + ("\n\n" if notes.strip() else "") + "**Componentes atualizados**\n" + "\n".join(
            f"- {DESCR.get(n, n)} {new} (antes {old})." for n, old, new in changed)
    if not notes.strip():
        notes = f"Refrata {v}"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf8") as f:
        f.write(notes)
    run("gh", "release", "create", tag, setup, side, portable, *linux, *sigs, "--repo", REPO,
        "--title", f"Refrata {v}", "--notes-file", f.name, *(["--draft"] if args.draft else []))
    os.unlink(f.name)
    print(f"\npublicado: https://github.com/{REPO}/releases/tag/{tag}")


DESCR = {"VLC": "Motor do VLC (libVLC)", "yt-dlp": "yt-dlp (links do YouTube e de outros sites)",
         "python-vlc": "python-vlc"}


def version_before(v: str) -> str:
    """The version this --bump started from."""
    parts = v.split(".")
    parts[-1] = str(int(parts[-1]) - 1)
    return ".".join(parts)


if __name__ == "__main__":
    main()
