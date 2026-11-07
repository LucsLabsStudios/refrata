"""MSIX package of Refrata (Microsoft Store and local testing).

    python tools/msix.py            # packs dist/Refrata (run tools/build.py first)

Outputs in dist/:
    Refrata-<v>-x64-store.msix   for Partner Center; the Store signs it. Needs installer/store.json
                                 with the identity Partner Center shows under Product identity
                                 (copy installer/store.example.json).
    Refrata-<v>-x64-dev.msix     signed with the development certificate, for testing on this PC.
                                 Windows installs it only once build\\lucslabs-dev.cer is trusted
                                 (Cert:\\LocalMachine\\TrustedPeople, admin, done by tools/test_msix.py
                                 --trust). Unsigned packages are not an option: Windows refuses them
                                 when they contain an executable (0x80073D2B).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from refrata import __version__  # noqa: E402
from refrata.media import AUDIO_EXT, PLAYLIST_EXT, VIDEO_EXT  # noqa: E402
from sign import DEV_SUBJECT, TIMESTAMP, create_dev_cert, dev_thumbprint, find_signtool  # noqa: E402

APP = ROOT / "dist" / "Refrata"
BUILD = ROOT / "build" / "msix"
ASSETS = ROOT / "assets" / "msix"
VERSION = ".".join((__version__.split(".") + ["0"] * 4)[:3]) + ".0"  # the Store wants revision 0
NAME = "LucsLabsStudios.Refrata"
STUDIO = "LucsLabs Studios"
DESCRIPTION = "Player de vídeo e música da LucsLabs Studios, com todo o motor do VLC numa interface nova."
GROUPS = [("video", "Vídeo", VIDEO_EXT), ("audio", "Música", AUDIO_EXT), ("playlist", "Playlist", PLAYLIST_EXT)]


def log(msg):
    print(f"[msix] {msg}", flush=True)


def sdk_tool(name: str) -> Path:
    st = find_signtool()
    tool = st.parent / name if st else None
    if not tool or not tool.exists():
        raise SystemExit(f"{name} not found — install the Windows SDK")
    return tool


ALIAS = """
        <uap3:Extension Category="windows.appExecutionAlias" Executable="Refrata.exe" EntryPoint="Windows.FullTrustApplication">
          <uap3:AppExecutionAlias>
            <desktop:ExecutionAlias Alias="refrata.exe"/>
          </uap3:AppExecutionAlias>
        </uap3:Extension>"""


def manifest(name: str, publisher: str, publisher_display: str, alias: bool = True) -> str:
    assoc = [ALIAS] if alias else []
    for key, label, exts in GROUPS:
        types = "".join(f"<uap:FileType>{e.lower()}</uap:FileType>" for e in sorted(exts))
        assoc.append(f"""
        <uap:Extension Category="windows.fileTypeAssociation">
          <uap:FileTypeAssociation Name="refrata-{key}">
            <uap:DisplayName>{escape(label)} (Refrata)</uap:DisplayName>
            <uap:Logo>Assets\\Square44x44Logo.png</uap:Logo>
            <uap:SupportedFileTypes>{types}</uap:SupportedFileTypes>
          </uap:FileTypeAssociation>
        </uap:Extension>""")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:uap3="http://schemas.microsoft.com/appx/manifest/uap/windows10/3"
         xmlns:desktop="http://schemas.microsoft.com/appx/manifest/desktop/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap uap3 desktop rescap">
  <Identity Name="{escape(name)}" Publisher="{escape(publisher)}" Version="{VERSION}" ProcessorArchitecture="x64"/>
  <Properties>
    <DisplayName>Refrata</DisplayName>
    <PublisherDisplayName>{escape(publisher_display)}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
    <Description>{escape(DESCRIPTION)}</Description>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26100.0"/>
  </Dependencies>
  <Resources>
    <Resource Language="pt-BR"/>
  </Resources>
  <Applications>
    <Application Id="Refrata" Executable="Refrata.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="Refrata" Description="{escape(DESCRIPTION)}" BackgroundColor="transparent"
                          Square150x150Logo="Assets\\Square150x150Logo.png" Square44x44Logo="Assets\\Square44x44Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150Logo.png" Square71x71Logo="Assets\\SmallTile.png"
                         Square310x310Logo="Assets\\LargeTile.png" ShortName="Refrata"/>
      </uap:VisualElements>
      <Extensions>{"".join(assoc)}
      </Extensions>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust"/>
  </Capabilities>
</Package>
"""


def pack(kind: str, name: str, publisher: str, publisher_display: str) -> Path:
    proj = BUILD / kind
    shutil.rmtree(proj, ignore_errors=True)
    (proj / "Assets").mkdir(parents=True)
    for f in ASSETS.glob("*.png"):
        shutil.copy2(f, proj / "Assets" / f.name)
    (proj / "AppxManifest.xml").write_text(manifest(name, publisher, publisher_display), encoding="utf-8")

    pri = sdk_tool("makepri.exe")
    cfg = proj.parent / f"priconfig-{kind}.xml"
    subprocess.run([str(pri), "createconfig", "/cf", str(cfg), "/dq", "pt-BR", "/pv", "10.0.0", "/o"],
                   check=True, stdout=subprocess.DEVNULL)
    subprocess.run([str(pri), "new", "/pr", str(proj), "/cf", str(cfg), "/mn", str(proj / "AppxManifest.xml"),
                    "/of", str(proj / "resources.pri"), "/o"], check=True, stdout=subprocess.DEVNULL)

    lines = ["[Files]"]
    for base in (proj, APP):
        for f in sorted(base.rglob("*")):
            if f.is_file():
                lines.append(f'"{f}" "{f.relative_to(base)}"')
    mapping = proj.parent / f"mapping-{kind}.txt"
    mapping.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = ROOT / "dist" / f"Refrata-{VERSION}-x64-{kind}.msix"
    out.unlink(missing_ok=True)
    r = subprocess.run([str(sdk_tool("makeappx.exe")), "pack", "/f", str(mapping), "/p", str(out), "/o"],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"makeappx failed:\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}")
    if kind == "dev":
        # the manifest publisher equals the development certificate's subject, as MSIX requires
        subprocess.run([str(find_signtool()), "sign", "/fd", "SHA256", "/sha1", dev_thumbprint() or create_dev_cert(),
                        "/s", "My", "/tr", TIMESTAMP, "/td", "SHA256", "/q", str(out)], check=True)
    log(f"{kind}: {out} ({out.stat().st_size / 1e6:.0f} MB)")
    return out


def store_identity() -> dict | None:
    f = ROOT / "installer" / "store.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    if not d.get("publisher", "").startswith("CN=") or "XXXX" in d.get("publisher", ""):
        raise SystemExit("installer/store.json: fill name/publisher/publisherDisplayName from Partner Center")
    return d


def main():
    if not (APP / "Refrata.exe").exists():
        raise SystemExit("dist/Refrata not found — run python tools/build.py first")
    out = [pack("dev", NAME, DEV_SUBJECT, STUDIO)]
    ident = store_identity()
    if ident:
        out.append(pack("store", ident["name"], ident["publisher"], ident.get("publisherDisplayName", STUDIO)))
    else:
        log("store package skipped: copy installer/store.example.json to installer/store.json "
            "and fill in the identity from Partner Center")
    return out


if __name__ == "__main__":
    main()
