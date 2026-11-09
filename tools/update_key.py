"""The key that signs Refrata's updates (Ed25519).

    python tools/update_key.py generate    # once: secret key into .env, public key into the app
    python tools/update_key.py public      # print the public key of the key in .env

The secret key lives only in the git-ignored .env (REFRATA_UPDATE_KEY), never in the repository.
Keep a copy somewhere safe: without it no new version can be published, because the installed
apps only accept updates signed by it (refrata/updater.py, UPDATE_PUBLIC_KEY). If it is ever
lost or leaked, a release made with a new key needs a manual download once.
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from refrata import ed25519  # noqa: E402

ENV = ROOT / ".env"
UPDATER = ROOT / "refrata" / "updater.py"
NAME = "REFRATA_UPDATE_KEY"


def read_secret() -> bytes | None:
    """From the environment, else from .env."""
    v = os.environ.get(NAME, "")
    if not v and ENV.exists():
        m = re.search(rf"^{NAME}=([0-9a-fA-F]{{64}})\s*$", ENV.read_text(encoding="utf8"), re.M)
        v = m.group(1) if m else ""
    return bytes.fromhex(v) if re.fullmatch(r"[0-9a-fA-F]{64}", v or "") else None


def message(asset: str, sha256: str, version: str) -> bytes:
    """What a release signs for each file (the app builds the same bytes to check it)."""
    return f"refrata-update-v1\n{asset}\n{sha256.lower()}\n{version}\n".encode("utf8")


def sign_file(path: Path, version: str, secret: bytes) -> Path:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    sig = ed25519.sign(secret, message(path.name, h.hexdigest(), version))
    out = path.with_name(path.name + ".sig")
    out.write_text(sig.hex() + "\n", encoding="ascii")
    return out


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "generate":
        if read_secret():
            raise SystemExit(f"{NAME} already exists in .env: not replacing it (see the docstring).")
        secret = os.urandom(32)
        public = ed25519.public_key(secret).hex()
        with open(ENV, "a", encoding="utf8") as f:
            f.write(f"\n# Ed25519 key that signs Refrata's updates (tools/update_key.py). Keep a backup.\n"
                    f"{NAME}={secret.hex()}\n")
        src = UPDATER.read_text(encoding="utf8")
        src, n = re.subn(r'^UPDATE_PUBLIC_KEY = "[0-9a-f]*"', f'UPDATE_PUBLIC_KEY = "{public}"', src, flags=re.M)
        if n != 1:
            raise SystemExit("UPDATE_PUBLIC_KEY not found in refrata/updater.py")
        UPDATER.write_text(src, encoding="utf8")
        print("secret key written to .env; public key in refrata/updater.py:", public)
    elif cmd == "public":
        secret = read_secret()
        print(ed25519.public_key(secret).hex() if secret else f"no {NAME} in .env")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
