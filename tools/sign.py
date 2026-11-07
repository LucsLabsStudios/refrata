"""Authenticode signing for the Refrata build (signtool from the Windows SDK).

The certificate comes from the environment, first match wins:

    REFRATA_SIGN_AZURE=<metadata.json>      Azure Artifact Signing (ex-Trusted Signing); also needs
    REFRATA_SIGN_AZURE_DLIB=<path>          Azure.CodeSigning.Dlib.dll (az login / env credentials)
    REFRATA_SIGN_PFX=<file.pfx>             certificate file (+ REFRATA_SIGN_PFX_PASSWORD)
    REFRATA_SIGN_THUMBPRINT=<sha1>          certificate in CurrentUser\\My (USB token, cloud HSM)
    REFRATA_SIGN_DEV=1                      the self-signed development certificate (see --dev-cert)

    python tools/sign.py --dev-cert         create the development certificate (CurrentUser\\My only;
                                            nothing is added to the trusted roots)
    python tools/sign.py --verify <files>   show the signature of each file
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV_SUBJECT = "CN=LucsLabs Studios (desenvolvimento), O=LucsLabs Studios, C=BR"
TIMESTAMP = os.environ.get("REFRATA_SIGN_TIMESTAMP", "http://timestamp.digicert.com")
DESCRIPTION = "Refrata"
DESCRIPTION_URL = "mailto:lucslabs.studios@gmail.com"


def find_signtool() -> Path | None:
    found = shutil.which("signtool")
    if found:
        return Path(found)
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    tools = sorted(kits.glob("10.*/x64/signtool.exe"), key=lambda p: [int(x) for x in p.parent.parent.name.split(".")])
    return tools[-1] if tools else None


def _ps(script: str) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()


def dev_thumbprint() -> str | None:
    out = _ps(f"Get-ChildItem Cert:\\CurrentUser\\My -CodeSigningCert | "
              f"Where-Object {{ $_.Subject -eq '{DEV_SUBJECT}' -and $_.NotAfter -gt (Get-Date) }} | "
              f"Sort-Object NotAfter -Descending | Select-Object -First 1 -ExpandProperty Thumbprint")
    return out or None


def create_dev_cert() -> str:
    tp = dev_thumbprint()
    if not tp:
        tp = _ps(f"(New-SelfSignedCertificate -Type CodeSigningCert -Subject '{DEV_SUBJECT}' "
                 f"-KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 -KeyExportPolicy NonExportable "
                 f"-CertStoreLocation Cert:\\CurrentUser\\My -NotAfter (Get-Date).AddYears(3)).Thumbprint")
    cer = ROOT / "build" / "lucslabs-dev.cer"
    cer.parent.mkdir(exist_ok=True)
    _ps(f"Export-Certificate -Cert Cert:\\CurrentUser\\My\\{tp} -FilePath '{cer}' | Out-Null")
    return tp


def config() -> tuple[str, list[str]] | None:
    """(label, signtool arguments that pick the certificate) or None when signing is off."""
    env = os.environ
    if env.get("REFRATA_SIGN_AZURE"):
        dlib = env.get("REFRATA_SIGN_AZURE_DLIB", "")
        if not Path(dlib).exists():
            raise SystemExit("REFRATA_SIGN_AZURE_DLIB must point at Azure.CodeSigning.Dlib.dll")
        return "Azure Artifact Signing", ["/dlib", dlib, "/dmdf", env["REFRATA_SIGN_AZURE"]]
    if env.get("REFRATA_SIGN_PFX"):
        args = ["/f", env["REFRATA_SIGN_PFX"]]
        if env.get("REFRATA_SIGN_PFX_PASSWORD"):
            args += ["/p", env["REFRATA_SIGN_PFX_PASSWORD"]]
        return "PFX " + Path(env["REFRATA_SIGN_PFX"]).name, args
    if env.get("REFRATA_SIGN_THUMBPRINT"):
        return "store " + env["REFRATA_SIGN_THUMBPRINT"], ["/sha1", env["REFRATA_SIGN_THUMBPRINT"], "/s", "My"]
    if env.get("REFRATA_SIGN_DEV"):
        tp = dev_thumbprint() or create_dev_cert()
        return "development certificate (self-signed)", ["/sha1", tp, "/s", "My"]
    return None


def sign_args(cert_args: list[str]) -> list[str]:
    return ["sign", "/fd", "SHA256", "/td", "SHA256", "/tr", TIMESTAMP,
            "/d", DESCRIPTION, "/du", DESCRIPTION_URL, *cert_args]


def sign(files: list[Path]) -> bool:
    cfg = config()
    if not cfg:
        return False
    tool = find_signtool()
    if not tool:
        raise SystemExit("signtool.exe not found — install the Windows SDK (winget install Microsoft.WindowsSDK)")
    label, cert_args = cfg
    print(f"[sign] {label}: {', '.join(f.name for f in files)}", flush=True)
    subprocess.run([str(tool), *sign_args(cert_args), "/q", *map(str, files)], check=True)
    return True


def inno_signtool() -> str | None:
    """The ISCC /S definition, so Inno signs the setup *and* the uninstaller it embeds."""
    cfg = config()
    tool = find_signtool()
    if not cfg or not tool:
        return None
    q = lambda s: f"$q{s}$q" if (" " in s or not s) else s  # noqa: E731
    return "bvsign=" + " ".join([q(str(tool)), *map(q, sign_args(cfg[1])), "/q", "$f"])


def describe(path: Path) -> dict:
    out = _ps(f"$s = Get-AuthenticodeSignature -LiteralPath '{path}'; "
              "[pscustomobject]@{status=[string]$s.Status; signer=$s.SignerCertificate.Subject; "
              "thumbprint=$s.SignerCertificate.Thumbprint; timestamp=$s.TimeStamperCertificate.Subject; "
              "message=$s.StatusMessage} | ConvertTo-Json -Compress")
    return json.loads(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-cert", action="store_true")
    ap.add_argument("--verify", nargs="+")
    a = ap.parse_args()
    if a.dev_cert:
        tp = create_dev_cert()
        print(f"development certificate {tp} in Cert:\\CurrentUser\\My; public part at build\\lucslabs-dev.cer")
    for f in a.verify or []:
        d = describe(Path(f))
        print(f"{Path(f).name}: {d['status']} — {d['signer']} — timestamp: {d['timestamp']}")


if __name__ == "__main__":
    sys.exit(main())
