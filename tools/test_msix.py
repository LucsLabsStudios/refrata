"""End-to-end test of the MSIX package (dist/Refrata-<v>-x64-dev.msix).

    python tools/test_msix.py --trust     one UAC prompt: trusts build\\lucslabs-dev.cer for package
                                          installs (LocalMachine\\TrustedPeople) and runs the Windows App
                                          Certification Kit (the Store's own checks) on the package
    python tools/test_msix.py             install, checks, E2E battery inside the package, file
                                          activation, uninstall (no admin)
    python tools/test_msix.py --untrust   one UAC prompt: removes the trust again
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import winreg
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from msix import NAME, VERSION  # noqa: E402
from refrata.media import AUDIO_EXT, PLAYLIST_EXT, VIDEO_EXT  # noqa: E402
from sign import dev_thumbprint  # noqa: E402

PKG = ROOT / "dist" / f"Refrata-{VERSION}-x64-dev.msix"
CER = ROOT / "build" / "lucslabs-dev.cer"
WORK = ROOT / "build" / "msix-test"
WACK = Path(os.environ.get("ProgramFiles(x86)", "")) / "Windows Kits" / "10" / "App Certification Kit" / "appcert.exe"
FAILS: list[str] = []


def ok(cond, what, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {what}" + (f" — {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        FAILS.append(what)
    return cond


def ps(cmd: str, check=True) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r.stdout.strip()


def package() -> dict | None:
    out = ps(f"Get-AppxPackage -Name {NAME} | Select-Object PackageFullName,PackageFamilyName,InstallLocation,"
             f"@{{n='SignatureKind';e={{[string]$_.SignatureKind}}}},@{{n='Status';e={{[string]$_.Status}}}} | "
             f"ConvertTo-Json -Compress")
    return json.loads(out) if out else None


def trusted() -> bool:
    tp = dev_thumbprint()
    return bool(tp) and ps(f"Test-Path Cert:\\LocalMachine\\TrustedPeople\\{tp}") == "True"


def elevated(script: str):
    """Runs a PowerShell script elevated (one UAC prompt) and waits for it."""
    WORK.mkdir(parents=True, exist_ok=True)
    f = WORK / "elevated.ps1"
    f.write_text(script, encoding="utf-8-sig")
    ps(f"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
       f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"{f}\"'")


def trust(run_wack: bool):
    if not CER.exists():
        raise SystemExit("build\\lucslabs-dev.cer missing — run python tools/sign.py --dev-cert")
    report = WORK / "wack.xml"
    report.unlink(missing_ok=True)
    wack = wack_lines(report) if run_wack and WACK.exists() else ""
    elevated(f"Import-Certificate -FilePath '{CER}' -CertStoreLocation Cert:\\LocalMachine\\TrustedPeople | Out-Null\n"
             + wack)
    print("trusted:", trusted())
    if wack:
        summarize_wack(report)


def summarize_wack(report: Path):
    import xml.etree.ElementTree as ET
    if not report.exists():
        print("WACK report not produced — see", WORK / "wack.log")
        return
    root = ET.parse(report).getroot()
    print("WACK overall:", root.get("OVERALL_RESULT"))
    for t in root.iter("TEST"):
        res = t.find("RESULT")
        r = (res.text or "").strip() if res is not None else ""
        if r and r != "PASS":
            msgs = [m.get("TEXT", "") for m in t.iter("MESSAGE")][:3]
            print(f"  {r:8} {t.get('NAME')}: {' | '.join(msgs)[:400]}")


def wack_lines(report: Path) -> str:
    return (f"& '{WACK}' reset | Out-Null\n"
            f"& '{WACK}' test -appxpackagepath '{PKG}' -reportoutputpath '{report}' *> '{WORK / 'wack.log'}'\n")


def untrust(run_wack: bool = False):
    """Removes the trust; with run_wack, certifies the package first, under the same UAC prompt."""
    tp = dev_thumbprint()
    report = WORK / "wack.xml"
    report.unlink(missing_ok=True)
    pre = wack_lines(report) if run_wack and WACK.exists() else ""
    elevated(pre + f"Remove-Item Cert:\\LocalMachine\\TrustedPeople\\{tp} -ErrorAction SilentlyContinue")
    print("trusted:", trusted())
    if pre:
        summarize_wack(report)


def appx_progid(ext: str, aumid: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}\OpenWithProgids") as k:
            names = [winreg.EnumValue(k, i)[0] for i in range(winreg.QueryInfoKey(k)[1])]
    except OSError:
        return None
    for n in names:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{n}\Application") as k:
                if winreg.QueryValueEx(k, "AppUserModelID")[0] == aumid:
                    return n
        except OSError:
            pass
    return None


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


def open_with_progid(path: Path, progid: str) -> bool:
    """What Explorer does when the file's default app is the package: ShellExecute through its ProgID."""
    sei = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW), fMask=0x1 | 0x100,  # CLASSNAME | NOASYNC
                            lpVerb="open", lpFile=str(path), nShow=1, lpClass=progid)
    return bool(ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)))


def find_window(text: str):
    u = ctypes.windll.user32
    hits = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        b = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(h, b, 512)
        if text in b.value and u.IsWindowVisible(h):
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            hits.append((h, b.value, pid.value))
        return True

    u.EnumWindows(proto(cb), 0)
    return hits


def wait_exit(pid: int, timeout: float):
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
    if not h:
        return "gone"
    try:
        if k.WaitForSingleObject(h, int(timeout * 1000)) != 0:
            return "timeout"
        code = wintypes.DWORD()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        return code.value
    finally:
        k.CloseHandle(h)


def test(skip_selftest: bool):
    if not PKG.exists():
        raise SystemExit(f"{PKG} not found — run python tools/build.py")
    if not trusted():
        raise SystemExit("the development certificate is not trusted yet — run python tools/test_msix.py --trust")
    if package():
        raise SystemExit(f"{NAME} is already installed; remove it first (Remove-AppxPackage)")
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"MSIX test — {PKG.name}")

    t = time.time()
    try:
        ps(f"Add-AppxPackage -Path '{PKG}' -ErrorAction Stop")
        err = ""
    except RuntimeError as e:
        err = str(e)
    p = package()
    ok(bool(p) and p["Status"] == "Ok", f"package installs ({time.time() - t:.0f}s)", err[:400])
    if not p:
        return
    loc, pfn = Path(p["InstallLocation"]), p["PackageFamilyName"]
    aumid = f"{pfn}!Refrata"
    ok(p["SignatureKind"] == "Developer", "installed as a signed developer package", p["SignatureKind"])
    ok((loc / "Refrata.exe").exists() and (loc / "_internal" / "vlc" / "libvlc.dll").exists(),
       "app and bundled libVLC inside the package")
    ok((loc / "resources.pri").exists(), "resource index (tile and taskbar icons) packaged")
    alias = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WindowsApps" / "refrata.exe"
    ok(alias.exists(), "command-line alias refrata.exe registered")
    start = ps(f"(Get-StartApps | Where-Object AppID -eq '{aumid}').Name")
    ok(start == "Refrata", "listed in the Start menu", start)
    exts = sorted(set(VIDEO_EXT) | set(AUDIO_EXT) | set(PLAYLIST_EXT))
    missing = [e for e in exts if not appx_progid(e, aumid)]
    ok(not missing, f"'Abrir com' registered for all {len(exts)} types", str(missing))
    ok(appx_progid(".srt", aumid) is None, "subtitles are not claimed")

    if not skip_selftest:
        report, log = WORK / "selftest.json", WORK / "selftest.log"
        report.unlink(missing_ok=True)
        t = time.time()
        r = subprocess.run([str(alias), "--selftest", "--report", str(report), "--log", str(log)], timeout=900)
        data = json.loads(report.read_text(encoding="utf8")) if report.exists() else {}
        ok(r.returncode == 0 and data.get("passed") == data.get("total") and data.get("total", 0) > 40,
           f"packaged app passes its E2E battery: {data.get('passed')}/{data.get('total')} ({time.time() - t:.0f}s)",
           "; ".join(f"{x['name']}: {x['detail'][:300]}" for x in data.get("results", []) if not x["ok"]))
        ok(data.get("package") == pfn, "app knows it runs from the package", str(data.get("package")))
        ok(str(loc).lower() in data.get("vlc", "").lower(), "packaged app uses its own libVLC", data.get("vlc", ""))
        ok(data.get("about", {}).get("installed") is True, "About card offers the default-apps button")

    # file activation through the package's ProgID (= double-click when Refrata is the default app).
    # Activation does not inherit our environment, so the app uses the user's real data folder:
    # snapshot it and put it back afterwards.
    import shutil
    real = Path(os.environ["LOCALAPPDATA"]) / "Refrata"
    snap = WORK / "real-data-backup"
    shutil.rmtree(snap, ignore_errors=True)
    had_real = real.exists()
    if had_real:
        shutil.copytree(real, snap)

    def restore_real():
        shutil.rmtree(real, ignore_errors=True)
        if had_real:
            shutil.copytree(snap, real)
            shutil.rmtree(snap, ignore_errors=True)

    from PySide6.QtGui import QGuiApplication
    from tests.fixtures import tone
    QGuiApplication(sys.argv)
    media = WORK / "Abrir Com Teste.wav"
    tone(media, 20.0, 520)
    progid = appx_progid(".wav", aumid)
    ok(bool(progid) and open_with_progid(media, progid), "shell opens a .wav through the package", str(progid))
    hits, end = [], time.time() + 30
    while time.time() < end and not hits:
        time.sleep(0.3)
        hits = find_window("Abrir Com Teste")
    ok(bool(hits) and "Refrata" in hits[0][1], "packaged app opens and plays the file",
       hits[0][1] if hits else "no window")
    for h, _, pid in hits:
        ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
        ok(wait_exit(pid, 15) in (0, "gone"), "closing the window exits cleanly")
    # Windows may keep AppData writes in the package's private folder or let them through
    cache = Path(os.environ["LOCALAPPDATA"]) / "Packages" / pfn
    places = (list(cache.rglob("Refrata/settings.json")) if cache.exists() else []) + [real / "settings.json"]
    found = [s for s in places if s.exists() and any(
        Path(x["path"]) == media for x in json.loads(s.read_text(encoding="utf8")).get("history", []))]
    ok(bool(found), "file recorded in the history on exit", ", ".join(map(str, places)))
    restore_real()

    t = time.time()
    try:
        ps(f"Remove-AppxPackage -Package '{p['PackageFullName']}' -ErrorAction Stop")
        err = ""
    except RuntimeError as e:
        err = str(e)
    ok(package() is None, f"package uninstalls ({time.time() - t:.0f}s)", err[:400])
    time.sleep(1.0)
    ok(not alias.exists(), "alias removed")
    left = [e for e in exts if appx_progid(e, aumid)]
    ok(not left, "'Abrir com' entries removed", str(left))
    ok(not cache.exists(), "package data removed with the app")

    media.unlink(missing_ok=True)
    print(f"\n{'ALL MSIX CHECKS PASSED' if not FAILS else f'{len(FAILS)} MSIX check(s) FAILED'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trust", action="store_true")
    ap.add_argument("--no-wack", action="store_true")
    ap.add_argument("--untrust", action="store_true")
    ap.add_argument("--wack", action="store_true", help="with --untrust: run the certification kit first")
    ap.add_argument("--skip-selftest", action="store_true")
    a = ap.parse_args()
    if a.trust:
        trust(not a.no_wack)
    elif a.untrust:
        untrust(run_wack=a.wack)
    else:
        test(a.skip_selftest)
        sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
