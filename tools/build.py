"""Build the standalone Windows package of Refrata.

    python tools/build.py              # vendor libVLC + PyInstaller + Inno Setup installer
    python tools/build.py --no-installer
    python tools/build.py --vlc-zip <vlc-3.x-win64.zip>   # use a VLC zip instead of the installed VLC

Result:
    dist/Refrata/Refrata.exe                portable app (no Python, no VLC needed)
    dist/Refrata-<version>-portable.zip
    dist/Refrata-Setup-<version>.exe        per-user installer (no admin), file associations
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from refrata import __version__  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import sign  # noqa: E402

VENDOR = ROOT / "vendor" / "vlc"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
SYSTEM_VLC = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "VideoLAN" / "VLC"
# parts of a VLC install the engine never uses (the classic Qt/skins interfaces, browser plugins)
SKIP_PLUGIN_DIRS = {"gui"}
KEEP_FILES = ["libvlc.dll", "libvlccore.dll", "COPYING.txt", "AUTHORS.txt", "README.txt", "THANKS.txt"]
KEEP_DIRS = ["plugins", "lua", "hrtfs", "locale"]


def log(msg):
    print(f"[build] {msg}", flush=True)


def vendor_vlc(zip_path: str | None):
    """Copy the parts of VLC 3 that libVLC needs into vendor/vlc."""
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    VENDOR.mkdir(parents=True)
    if zip_path:
        tmp = BUILD / "vlc-zip"
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        src = next(p for p in tmp.iterdir() if (p / "libvlc.dll").exists())
    else:
        src = SYSTEM_VLC
    if not (src / "libvlc.dll").exists():
        raise SystemExit(f"libvlc.dll not found in {src} — install VLC 3 64-bit or pass --vlc-zip")
    for f in KEEP_FILES:
        if (src / f).exists():
            shutil.copy2(src / f, VENDOR / f)
    for d in KEEP_DIRS:
        if not (src / d).exists():
            continue
        if d == "plugins":
            for sub in (src / d).iterdir():
                if sub.is_dir() and sub.name not in SKIP_PLUGIN_DIRS:
                    shutil.copytree(sub, VENDOR / d / sub.name)
        else:
            shutil.copytree(src / d, VENDOR / d)
    gen = src / "vlc-cache-gen.exe"
    if gen.exists():
        # plugins.dat must describe exactly the plugins we ship
        subprocess.run([str(gen), str(VENDOR / "plugins")], check=True, cwd=str(src))
    size = sum(p.stat().st_size for p in VENDOR.rglob("*") if p.is_file()) / 1e6
    log(f"libVLC vendored from {src} ({size:.0f} MB)")


VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v}, 0), prodvers=({v}, 0), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('041604B0', [
      StringStruct('CompanyName', 'LucsLabs Studios'),
      StringStruct('FileDescription', 'Refrata'),
      StringStruct('FileVersion', '{dotted}'),
      StringStruct('InternalName', 'Refrata'),
      StringStruct('LegalCopyright', '© LucsLabs Studios. libVLC © VideoLAN (LGPL 2.1+)'),
      StringStruct('OriginalFilename', 'Refrata.exe'),
      StringStruct('ProductName', 'Refrata'),
      StringStruct('ProductVersion', '{dotted}')])]),
    VarFileInfo([VarStruct('Translation', [0x0416, 1200])])])
"""

EXCLUDES = [
    "tkinter", "unittest", "pydoc_data", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSql", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtTextToSpeech", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtQuick3D",
    "PySide6.QtSpatialAudio", "PySide6.QtHttpServer", "PySide6.QtOpenGLWidgets",
    "PySide6.QtSvgWidgets", "PySide6.QtPrintSupport", "PySide6.QtXml", "PySide6.QtConcurrent",
]

# QML modules we import; everything else under PySide6/qml is pruned after the build
QML_KEEP = {"QtQuick", "QtQml", "QtCore", "QtMultimedia", "Qt"}  # Qt/labs is required by Dialogs
QML_QUICK_KEEP = {"Controls", "Dialogs", "Effects", "Shapes", "Window", "Layouts", "Templates", "Particles"}


# Per-Monitor V2 declared up front (the Store certification kit wants it in the manifest; it is
# also Qt 6's own default, so Qt finds it already set), long paths, Windows 10/11, comctl32 v6.
EXE_MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity type="win32" name="LucsLabsStudios.Refrata" version="1.0.0.0" processorArchitecture="amd64"/>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security><requestedPrivileges><requestedExecutionLevel level="asInvoker" uiAccess="false"/></requestedPrivileges></security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application><supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/></application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0"
                        processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/>
    </dependentAssembly>
  </dependency>
</assembly>
"""


def write_spec() -> Path:
    BUILD.mkdir(exist_ok=True)
    parts = [int(x) for x in __version__.split(".")] + [0] * 4
    vfile = BUILD / "version_info.txt"
    vfile.write_text(VERSION_INFO.format(v=", ".join(map(str, parts[:3])), dotted=__version__), encoding="utf8")
    mfile = BUILD / "Refrata.exe.manifest"
    mfile.write_text(EXE_MANIFEST, encoding="utf8")
    spec = BUILD / "Refrata.spec"
    r = lambda p: repr(str(p))  # noqa: E731
    spec.write_text(f"""# generated by tools/build.py
a = Analysis(
    [{r(ROOT / 'main.py')}],
    pathex=[{r(ROOT)}],
    datas=[({r(ROOT / 'qml')}, 'qml'), ({r(ROOT / 'assets')}, 'assets'), ({r(ROOT / 'i18n')}, 'i18n'), ({r(VENDOR)}, 'vlc')],
    hiddenimports=['tests', 'tests.e2e', 'tests.fixtures', 'tests.windialogs', 'PySide6.QtTest',
                   'PySide6.QtNetwork', 'PySide6.QtMultimedia', 'PySide6.QtQuickControls2',
                   'PySide6.QtWidgets', 'PySide6.QtAxContainer'],
    excludes={EXCLUDES!r},
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Refrata', console=False,
          icon={r(ROOT / 'assets' / 'icon.ico')}, version={r(vfile)}, manifest={r(mfile)}, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='Refrata', upx=False)
""", encoding="utf8")
    return spec


def prune_qml(app_dir: Path):
    qml = app_dir / "_internal" / "PySide6" / "qml"
    if not qml.exists():
        return
    removed = 0
    for d in qml.iterdir():
        if d.is_dir() and d.name not in QML_KEEP:
            shutil.rmtree(d)
            removed += 1
    quick = qml / "QtQuick"
    for d in quick.iterdir() if quick.exists() else []:
        if d.is_dir() and d.name not in QML_QUICK_KEEP:
            shutil.rmtree(d)
            removed += 1
    # Controls: only the Basic style is used (plus the shared impl)
    ctrl = quick / "Controls"
    for d in ctrl.iterdir() if ctrl.exists() else []:
        if d.is_dir() and d.name not in {"Basic", "impl"}:
            shutil.rmtree(d)
            removed += 1
    log(f"pruned {removed} unused QML module folders")


# Qt libraries pulled in by QML plugin collection that the app never loads
QT_DROP_PREFIXES = (
    "Qt6WebEngine", "QtWebEngineProcess", "Qt6WebChannel", "Qt6WebSockets", "Qt6WebView", "Qt6Pdf",
    "Qt63D", "Qt6Quick3D", "Qt6Charts", "Qt6DataVisualization", "Qt6Graphs", "Qt6Location", "Qt6Positioning",
    "Qt6Sensors", "Qt6Bluetooth", "Qt6Nfc", "Qt6Sql", "Qt6Scxml", "Qt6StateMachine", "Qt6TextToSpeech",
    "Qt6RemoteObjects", "Qt6VirtualKeyboard", "Qt6SpatialAudio", "Qt6Designer", "Qt6Help", "Qt6HttpServer",
    "Qt6SerialPort", "Qt6SerialBus", "Qt6Quick3DPhysics", "Qt6QuickTimeline", "Qt6Test",
)
QT_KEEP_EXACT = {"Qt6Test.dll"}  # QtTest powers the built-in --selftest
QT_DROP_PLUGIN_DIRS = {"sqldrivers", "position", "geoservices", "sensors", "webview", "designer",
                       "qmltooling", "virtualkeyboard", "canbus", "scxmldatamodel", "renderers",
                       "sceneparsers", "geometryloaders", "renderplugins", "assetimporters", "texttospeech"}


def prune_qt(app_dir: Path):
    ps = app_dir / "_internal" / "PySide6"
    freed = 0
    for p in list(ps.rglob("*")):
        if not p.exists():
            continue
        if p.is_file() and p.name.startswith(QT_DROP_PREFIXES) and p.name not in QT_KEEP_EXACT:
            freed += p.stat().st_size
            p.unlink()
    for d in (ps / "plugins").iterdir() if (ps / "plugins").exists() else []:
        if d.is_dir() and d.name in QT_DROP_PLUGIN_DIRS:
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d)
    # the app runs on the qwindows platform plugin; the Direct2D one is unused (and the Store kit flags it)
    for d in [ps / "resources", ps / "plugins" / "platforms" / "qdirect2d.dll"] \
            + list((ps / "translations").glob("qtwebengine*")):
        if d.exists():
            freed += sum(f.stat().st_size for f in ([d] if d.is_file() else d.rglob("*")) if f.is_file())
            shutil.rmtree(d) if d.is_dir() else d.unlink()
    log(f"pruned unused Qt libraries ({freed / 1e6:.0f} MB)")


def pyinstaller():
    spec = write_spec()
    shutil.rmtree(DIST / "Refrata", ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--distpath", str(DIST), "--workpath", str(BUILD / "pyi"), str(spec)], check=True, cwd=str(ROOT))
    app = DIST / "Refrata"
    shutil.rmtree(app / "_internal" / "assets" / "msix", ignore_errors=True)  # package art, not app data
    prune_qml(app)
    prune_qt(app)
    lic = app / "LICENSES"
    lic.mkdir(exist_ok=True)
    shutil.copy2(VENDOR / "COPYING.txt", lic / "VLC-COPYING.txt")
    shutil.copy2(ROOT / "LICENSE", app / "LICENSE.txt")
    (lic / "README.txt").write_text(
        "Refrata © LucsLabs Studios, software livre sob a GNU GPL 3.0 (ver ..\\LICENSE.txt).\n"
        "Código-fonte: https://github.com/LucsLabsStudios/refrata\n"
        "Inclui o libVLC e os módulos do VLC 3 (VideoLAN), distribuídos sob LGPL 2.1+ / GPL 2+ (ver VLC-COPYING.txt),\n"
        "e o Qt for Python (PySide6), sob LGPL 3. Os binários são redistribuídos sem modificação.\n", encoding="utf8")
    size = sum(p.stat().st_size for p in app.rglob("*") if p.is_file()) / 1e6
    log(f"app folder ready: {app} ({size:.0f} MB)")
    return app


def portable_zip(app: Path) -> Path:
    out = DIST / f"Refrata-{__version__}-portable.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in app.rglob("*"):
            if p.is_file():
                z.write(p, Path("Refrata") / p.relative_to(app))
    log(f"portable zip: {out} ({out.stat().st_size / 1e6:.0f} MB)")
    return out


def find_iscc() -> Path | None:
    for c in [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
              Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
              Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe"]:
        if c.exists():
            return c
    found = shutil.which("ISCC")
    return Path(found) if found else None


def write_assoc() -> Path:
    """[Registry] lines for every extension the player handles (subtitles excluded)."""
    from refrata.media import AUDIO_EXT, PLAYLIST_EXT, VIDEO_EXT
    exts = sorted(set(VIDEO_EXT) | set(AUDIO_EXT) | set(PLAYLIST_EXT))
    lines = []
    for e in exts:
        lines.append(f'Root: HKA; Subkey: "Software\\Classes\\{e}\\OpenWithProgids"; ValueType: string; '
                     f'ValueName: "{{#ProgId}}"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoc')
        lines.append(f'Root: HKA; Subkey: "Software\\Classes\\Applications\\{{#AppExe}}\\SupportedTypes"; '
                     f'ValueType: string; ValueName: "{e}"; ValueData: ""; Tasks: assoc')
        lines.append(f'Root: HKA; Subkey: "{{#CapKey}}\\FileAssociations"; ValueType: string; '
                     f'ValueName: "{e}"; ValueData: "{{#ProgId}}"; Tasks: assoc')
    out = BUILD / "assoc.iss"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    log(f"{len(exts)} file types registered by the installer")
    return out


def installer() -> Path:
    write_assoc()
    iscc = find_iscc()
    if not iscc:
        raise SystemExit("Inno Setup 6 not found (winget install JRSoftware.InnoSetup)")
    signer = sign.inno_signtool()
    extra = [f"/S{signer}", "/DSign"] if signer else []
    subprocess.run([str(iscc), "/Q", f"/DAppVersion={__version__}", f"/DRoot={ROOT}", *extra,
                    str(ROOT / "installer" / "Refrata.iss")], check=True)
    out = DIST / f"Refrata-Setup-{__version__}.exe"
    log(f"installer: {out} ({out.stat().st_size / 1e6:.0f} MB)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vlc-zip")
    ap.add_argument("--no-installer", action="store_true")
    ap.add_argument("--no-msix", action="store_true")
    ap.add_argument("--skip-vendor", action="store_true")
    ap.add_argument("--skip-build", action="store_true", help="reuse dist/Refrata (sign + zip + installer only)")
    a = ap.parse_args()
    app = DIST / "Refrata"
    if not (a.skip_build and (app / "Refrata.exe").exists()):
        if not (a.skip_vendor and (VENDOR / "libvlc.dll").exists()):
            vendor_vlc(a.vlc_zip)
        app = pyinstaller()
    if not sign.sign([app / "Refrata.exe"]):
        log("not signed (set REFRATA_SIGN_* — see tools/sign.py)")
    portable_zip(app)
    if not a.no_installer:
        installer()
    if not a.no_msix:
        import msix
        msix.main()


if __name__ == "__main__":
    main()
