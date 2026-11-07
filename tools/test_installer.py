"""End-to-end test of the Windows installer (dist/Refrata-Setup-<version>.exe).

    python tools/test_installer.py [--skip-selftest]

1. silent per-user install into a temp folder (file associations on, no desktop icon)
2. files, Start-menu shortcut, uninstaller and registry entries (ProgID, Abrir com, Apps padrão)
3. the full E2E battery, run by the *installed* Refrata.exe --selftest
4. "double-click in Explorer": the ProgID's registered command opens a file in a new window
5. silent uninstall removes the app folder, shortcut and every registry entry
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import winreg
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from refrata import __version__  # noqa: E402
from refrata.media import AUDIO_EXT, PLAYLIST_EXT, VIDEO_EXT  # noqa: E402

SETUP = ROOT / "dist" / f"Refrata-Setup-{__version__}.exe"
PROGID = "Refrata.Media"
CAP = r"Software\LucsLabs Studios\Refrata\Capabilities"
FAILS: list[str] = []


def ok(cond, what, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {what}" + (f" — {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        FAILS.append(what)
    return cond


def reg(path, name=""):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def reg_exists(path):
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, path).Close()
        return True
    except OSError:
        return False


def start_menu_link() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Refrata.lnk"


def windows_of(pid):
    u = ctypes.windll.user32
    out = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(h):
            b = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(h, b, 512)
            out.append((h, b.value))
        return True

    u.EnumWindows(proto(cb), 0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-selftest", action="store_true")
    a = ap.parse_args()
    if not SETUP.exists():
        raise SystemExit(f"{SETUP} not found — run python tools/build.py")
    if reg_exists(rf"Software\Classes\{PROGID}"):
        raise SystemExit("Refrata is already installed for this user; uninstall it before running this test.")

    work = Path(tempfile.mkdtemp(prefix="refrata_setup_"))
    target = work / "Refrata"
    print(f"Installer test — {SETUP.name} → {target}")

    # 1. install
    t = time.time()
    r = subprocess.run([str(SETUP), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER",
                        f"/DIR={target}", "/TASKS=assoc", f"/LOG={work / 'setup.log'}"], timeout=600)
    ok(r.returncode == 0, f"silent install exit code 0 ({time.time() - t:.0f}s)", str(r.returncode))
    exe = target / "Refrata.exe"

    # 2. files + registry
    ok(exe.exists(), "Refrata.exe installed")
    ok((target / "unins000.exe").exists(), "uninstaller installed")
    ok((target / "_internal" / "vlc" / "libvlc.dll").exists(), "bundled libVLC installed")
    ok((target / "LICENSES" / "VLC-COPYING.txt").exists(), "licences installed")
    ok(start_menu_link().exists(), "Start-menu shortcut created")
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop" / "Refrata.lnk"
    ok(not desktop.exists(), "no desktop icon when the task is off")
    cmd = reg(rf"Software\Classes\{PROGID}\shell\open\command")
    ok(cmd == f'"{exe}" "%1"', "ProgID open command points at the installed exe", str(cmd))
    ok(reg(rf"Software\Classes\{PROGID}\DefaultIcon") == f'"{exe}",0', "ProgID icon")
    exts = sorted(set(VIDEO_EXT) | set(AUDIO_EXT) | set(PLAYLIST_EXT))
    missing = [e for e in exts if reg(rf"Software\Classes\{e}\OpenWithProgids", PROGID) is None]
    ok(not missing, f"'Abrir com' registered for all {len(exts)} types", str(missing))
    ok(reg(r"Software\RegisteredApplications", "Refrata") == CAP, "listed in Apps padrão")
    ok(reg(CAP + r"\FileAssociations", ".mkv") == PROGID, "capabilities map .mkv to the ProgID")
    ok(reg(r"Software\Classes\Applications\Refrata.exe\SupportedTypes", ".mp4") == "", "SupportedTypes lists .mp4")
    ok(reg(rf"Software\Classes\.srt\OpenWithProgids", PROGID) is None, "subtitles are not claimed")

    # 2b. Authenticode: setup, app and the uninstaller Inno generated carry the same intact signature
    import sign
    if sign.config():
        sigs = {f.name: sign.describe(f) for f in (SETUP, exe, target / "unins000.exe")}
        dev = sign.DEV_SUBJECT if os.environ.get("REFRATA_SIGN_DEV") else None
        for name, d in sigs.items():
            good = d["status"] == "Valid" or (dev and d["signer"] == dev and d["status"] == "UnknownError")
            ok(good and d["timestamp"], f"{name} signed and timestamped ({d['status']})", f"{d['status']}: {d['message']}")
        ok(len({d["thumbprint"] for d in sigs.values()}) == 1, "all three signed by the same certificate")
    else:
        print("  skip  signature checks (no REFRATA_SIGN_* configured)")

    # 3. the E2E battery inside the installed copy
    if not a.skip_selftest:
        report = work / "selftest.json"
        log = work / "selftest.log"
        t = time.time()
        r = subprocess.run([str(exe), "--selftest", "--report", str(report), "--log", str(log)], timeout=900)
        data = json.loads(report.read_text(encoding="utf8")) if report.exists() else {}
        ok(r.returncode == 0 and data.get("passed") == data.get("total") and data.get("total", 0) > 40,
           f"installed app passes its E2E battery: {data.get('passed')}/{data.get('total')} ({time.time() - t:.0f}s)",
           "; ".join(f"{x['name']}: {x['detail'][:200]}" for x in data.get("results", []) if not x["ok"]))
        ok(str(target) in data.get("vlc", ""), "installed app uses its own libVLC", data.get("vlc", ""))

    # 4. what Explorer does on double-click: run the registered command with the file
    from PySide6.QtGui import QGuiApplication
    from tests.fixtures import tone
    QGuiApplication(sys.argv)
    media = work / "Abrir Com Teste.wav"
    tone(media, 20.0, 520)
    data_dir = work / "assoc-data"
    env = dict(os.environ, REFRATA_DATA=str(data_dir))
    env.pop("REFRATA_E2E_TMP", None)
    p = subprocess.Popen(cmd.replace("%1", str(media)), env=env)
    title = ""
    end = time.time() + 30
    while time.time() < end and "Abrir Com Teste" not in title:
        time.sleep(0.3)
        title = " | ".join(t for _, t in windows_of(p.pid))
    ok("Abrir Com Teste" in title and "Refrata" in title, "registered command opens and plays the file", title)
    for h, _ in windows_of(p.pid):
        ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
    try:
        code = p.wait(15)
    except subprocess.TimeoutExpired:
        p.kill()
        code = "timeout"
    ok(code == 0, "closing the window exits cleanly", str(code))
    hist = json.loads((data_dir / "settings.json").read_text(encoding="utf8")).get("history", []) \
        if (data_dir / "settings.json").exists() else []
    ok(any(Path(h["path"]) == media for h in hist), "file recorded in the history on exit")

    # 5. uninstall
    r = subprocess.run([str(target / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=300)
    end = time.time() + 60
    while time.time() < end and exe.exists():  # the uninstaller finishes in a child process
        time.sleep(0.5)
    time.sleep(1.0)
    ok(r.returncode == 0, "silent uninstall exit code 0", str(r.returncode))
    ok(not exe.exists() and not (target / "_internal").exists(), "app files removed")
    ok(not start_menu_link().exists(), "Start-menu shortcut removed")
    ok(not reg_exists(rf"Software\Classes\{PROGID}"), "ProgID removed")
    left = [e for e in exts if reg(rf"Software\Classes\{e}\OpenWithProgids", PROGID) is not None]
    ok(not left, "'Abrir com' entries removed", str(left))
    ok(reg(r"Software\RegisteredApplications", "Refrata") is None, "removed from Apps padrão")
    ok(not reg_exists(r"Software\LucsLabs Studios"), "company key cleaned up")
    ok(not reg_exists(r"Software\Classes\Applications\Refrata.exe"), "Applications entry removed")

    shutil.rmtree(work, ignore_errors=True)
    print(f"\n{'ALL INSTALLER CHECKS PASSED' if not FAILS else f'{len(FAILS)} installer check(s) FAILED'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
