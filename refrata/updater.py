"""In-app updates from GitHub Releases.

check()    → GET the latest release (JSON), compare its tag with __version__
download() → stream the installer asset to %LOCALAPPDATA%\\Refrata\\updates, verify SHA-256
install()  → start the installer silently (/VERYSILENT … /relaunch=1) and quit; Inno closes
             what is left of the app, updates in place and starts Refrata again

Only installed copies (Inno setup) update themselves. The portable zip opens the download
page instead, and the Microsoft Store package is updated by the Store.

REFRATA_UPDATE_URL points the check at another JSON document (the tests use a local server).
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QProcess, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__
from .settings import DATA_DIR
from .i18n import tr

REPO = "LucsLabsStudios/refrata"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
SITE = "https://refrata.vercel.app"
CHECK_EVERY_S = 12 * 3600
ASSET_RE = re.compile(r"^Refrata-Setup-[\d.]+\.exe$", re.I)


def parse_version(v: str) -> tuple:
    nums = re.findall(r"\d+", (v or "").split("-")[0].split("+")[0])
    return tuple(int(x) for x in (nums + ["0", "0", "0"])[:3]) if nums else (0, 0, 0)


def _packaged() -> bool:
    import ctypes
    try:
        n = ctypes.c_uint32(0)
        # 122 = ERROR_INSUFFICIENT_BUFFER → we have a package identity; 15700 = not packaged
        return ctypes.windll.kernel32.GetCurrentPackageFamilyName(ctypes.byref(n), None) == 122
    except (AttributeError, OSError):
        return False


def install_kind() -> str:
    """'installer' (Inno, self-updating), 'store' (MSIX) or 'portable' (zip / source tree)."""
    if os.environ.get("REFRATA_UPDATE_KIND"):
        return os.environ["REFRATA_UPDATE_KIND"]
    if not getattr(sys, "frozen", False):
        return "portable"
    if _packaged():
        return "store"
    return "installer" if (Path(sys.executable).parent / "unins000.exe").exists() else "portable"


class Updater(QObject):
    changed = Signal()
    progressChanged = Signal()
    quitRequested = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._net = QNetworkAccessManager(self)
        # GitHub answers every asset download with a 302 to its file servers. Follow it here, on
        # the manager: setting RedirectPolicyAttribute per request through PySide stores the enum
        # in a form Qt does not recognise, which silently turned redirects off (1.1.0 bug).
        self._net.setRedirectPolicy(QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        self._url = os.environ.get("REFRATA_UPDATE_URL") or API_LATEST
        self._kind = install_kind()
        self._state = "idle"     # idle checking uptodate available downloading ready installing error
        self._latest = ""
        self._notes = ""
        self._page = ""
        self._asset = {}         # name, url, size, sha256
        self._error = ""
        self._progress = 0.0
        self._file = None
        self._reply = None
        self._hash = None
        self._target: Path | None = None
        self._last_manual = False
        self._dismissed = False

    # ------------------------------------------------------------------ properties
    def _set(self, state, error=""):
        self._state, self._error = state, error
        self.changed.emit()

    @Property(str, notify=changed)
    def state(self):
        return self._state

    @Property(str, constant=True)
    def current(self):
        return __version__

    @Property(str, notify=changed)
    def latest(self):
        return self._latest

    @Property(str, notify=changed)
    def notes(self):
        return self._notes

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property(float, notify=progressChanged)
    def progress(self):
        return self._progress

    @Property(str, constant=True)
    def kind(self):
        return self._kind

    @Property(bool, notify=changed)
    def canSelfUpdate(self):
        return self._kind == "installer"

    @Property(bool, notify=changed)
    def bannerVisible(self):
        """Library banner: an update exists (or is on its way) and the user has not closed it."""
        newer = parse_version(self._latest) > parse_version(__version__)
        return (not self._dismissed and self._kind != "store" and newer
                and self._state in ("available", "downloading", "ready", "installing", "error"))

    @Property(bool, notify=changed)
    def auto(self):
        return bool(self._settings.value("autoUpdate", True))

    @auto.setter
    def auto(self, on):
        self._settings.setValue("autoUpdate", bool(on))
        self.changed.emit()

    @Property(float, notify=changed)
    def lastCheck(self):
        return float(self._settings.value("lastUpdateCheck", 0) or 0)

    # ------------------------------------------------------------------ check
    def start(self, delay_ms=6000):
        """Background check shortly after launch (never in the Store build, at most every 12 h)."""
        if self._kind == "store" or not self.auto:
            return
        if os.environ.get("REFRATA_E2E_TMP") and not os.environ.get("REFRATA_UPDATE_URL"):
            return  # the test battery never talks to GitHub
        if time.time() - self.lastCheck < CHECK_EVERY_S and not os.environ.get("REFRATA_UPDATE_URL"):
            return
        QTimer.singleShot(delay_ms, lambda: self.check(False))

    @Slot()
    def checkNow(self):
        self.check(True)

    def check(self, manual=False):
        if self._state in ("checking", "downloading", "installing"):
            return
        self._last_manual = manual
        self._dismissed = False
        self._set("checking")
        req = QNetworkRequest(QUrl(self._url))
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        req.setRawHeader(b"User-Agent", f"Refrata/{__version__}".encode())
        req.setTransferTimeout(15000)
        reply = self._net.get(req)
        reply.finished.connect(lambda: self._on_checked(reply))

    def _on_checked(self, reply: QNetworkReply):
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            # silent background checks just give up; manual ones explain
            self._set("error" if self._last_manual else "idle", tr("Sem conexão com o servidor de atualizações"))
            return
        try:
            data = json.loads(bytes(reply.readAll()).decode("utf8"))
            self.apply_release(data)
        except (ValueError, KeyError, TypeError) as exc:
            self._set("error" if self._last_manual else "idle", tr("Resposta inválida do servidor (%1)").replace("%1", str(exc)))

    def apply_release(self, data: dict):
        self._settings.setValue("lastUpdateCheck", time.time())
        tag = str(data.get("tag_name") or data.get("name") or "")
        self._latest = tag.lstrip("vV")
        self._notes = str(data.get("body") or "").strip()
        self._page = str(data.get("html_url") or SITE)
        assets = data.get("assets") or []
        exe = next((a for a in assets if ASSET_RE.match(a.get("name", ""))), None)
        sha = ""
        if exe:
            digest = str(exe.get("digest") or "")
            if digest.lower().startswith("sha256:"):
                sha = digest.split(":", 1)[1].strip().lower()
            side = next((a for a in assets if a.get("name", "").lower() == exe["name"].lower() + ".sha256"), None)
            self._asset = {"name": exe["name"], "url": exe.get("browser_download_url", ""),
                           "size": int(exe.get("size") or 0), "sha256": sha,
                           "shaUrl": side.get("browser_download_url", "") if side else ""}
        else:
            self._asset = {}
        if parse_version(self._latest) > parse_version(__version__):
            self._set("available")
        else:
            self._set("uptodate")

    # ------------------------------------------------------------------ download
    @Slot()
    def update(self):
        """The one button: download (if needed) and install; the portable build opens the site."""
        if self._kind != "installer":
            QDesktopServices.openUrl(QUrl(SITE + "/#download"))
            return
        if self._state == "ready":
            self.install()
        elif self._state in ("available", "error") and self._asset:
            self.download()

    @Slot()
    def download(self):
        if not self._asset.get("url"):
            self._set("error", tr("Este lançamento não tem instalador para Windows"))
            return
        if not self._asset.get("sha256") and self._asset.get("shaUrl"):
            # older GitHub responses carry no digest: fetch the .sha256 asset first
            r = self._net.get(self._request(self._asset["shaUrl"]))
            r.finished.connect(lambda: self._on_sha(r))
            self._set("downloading")
            return
        self._start_download()

    def _request(self, url):
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", f"Refrata/{__version__}".encode())
        return req

    @staticmethod
    def _http_ok(reply) -> bool:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        return reply.error() == QNetworkReply.NetworkError.NoError and (status is None or int(status) == 200)

    def _on_sha(self, reply):
        reply.deleteLater()
        text = bytes(reply.readAll()).decode("utf8", "replace").strip()
        m = re.search(r"\b[0-9a-fA-F]{64}\b", text)
        if not self._http_ok(reply) or not m:
            self._set("error", tr("Não foi possível conferir o download"))
            return
        self._asset["sha256"] = m.group(0).lower()
        self._start_download()

    @staticmethod
    def _trusted_url(url: str) -> bool:
        """Installers come over HTTPS from GitHub (release assets redirect to its CDN). The tests'
        local server is allowed through REFRATA_UPDATE_URL."""
        if os.environ.get("REFRATA_UPDATE_URL"):
            return True
        u = QUrl(url)
        host = u.host().lower()
        return u.scheme() == "https" and (host == "github.com" or host.endswith(".github.com")
                                          or host.endswith(".githubusercontent.com"))

    def _start_download(self):
        if not self._trusted_url(self._asset.get("url", "")):
            self._set("error", tr("Não foi possível conferir o download"))
            return
        if not self._asset.get("sha256"):
            self._set("error", tr("O lançamento não publica o SHA-256 do instalador"))
            return
        folder = DATA_DIR / "updates"
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("Refrata-Setup-*.exe*"):
            try:
                old.unlink()
            except OSError:
                pass
        self._target = folder / self._asset["name"]
        self._file = open(self._target.with_suffix(".part"), "wb")
        self._hash = hashlib.sha256()
        self._progress = 0.0
        self.progressChanged.emit()
        self._set("downloading")
        self._reply = self._net.get(self._request(self._asset["url"]))
        self._reply.readyRead.connect(self._on_data)
        self._reply.downloadProgress.connect(self._on_progress)
        self._reply.finished.connect(self._on_downloaded)

    def _on_data(self, reply=None):
        chunk = bytes((reply or self._reply).readAll())
        self._file.write(chunk)
        self._hash.update(chunk)

    def _on_progress(self, got, total):
        total = total if total > 0 else self._asset.get("size", 0)
        if total > 0:
            self._progress = min(1.0, got / total)
            self.progressChanged.emit()

    @Slot()
    def cancel(self):
        if self._reply is not None and self._state == "downloading":
            self._reply.abort()

    def _on_downloaded(self):
        reply, self._reply = self._reply, None
        self._on_data(reply)
        self._file.close()
        part = self._target.with_suffix(".part")
        reply.deleteLater()
        got = part.stat().st_size if part.exists() else 0
        expected = int(self._asset.get("size") or 0)
        if not self._http_ok(reply) or got == 0 or (expected and got != expected):
            part.unlink(missing_ok=True)
            cancelled = reply.error() == QNetworkReply.NetworkError.OperationCanceledError
            self._set("available" if cancelled else "error",
                      "" if cancelled else tr("O download falhou ou veio incompleto. Tente de novo."))
            return
        if self._hash.hexdigest().lower() != self._asset["sha256"]:
            part.unlink(missing_ok=True)
            self._set("error", tr("O arquivo baixado não confere (SHA-256). Nada foi instalado."))
            return
        part.replace(self._target)
        self._progress = 1.0
        self.progressChanged.emit()
        self._set("ready")

    @Property(str, notify=changed)
    def downloadedPath(self):
        return str(self._target) if self._target and self._state in ("ready", "installing") else ""

    # ------------------------------------------------------------------ install
    @Slot()
    def install(self):
        if self._state != "ready" or not self._target or not self._target.exists():
            return
        log = DATA_DIR / "updates" / "install.log"
        args = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                "/relaunch=1", f"/LOG={log}"]
        if os.environ.get("REFRATA_UPDATE_DRYRUN"):
            # tests: prove what would run without replacing the app under test
            Path(os.environ["REFRATA_UPDATE_DRYRUN"]).write_text(
                json.dumps({"exe": str(self._target), "args": args}), "utf8")
            self._set("installing")
            return
        ok, _pid = QProcess.startDetached(str(self._target), args, str(self._target.parent))
        if not ok:
            self._set("error", tr("Não foi possível iniciar o instalador"))
            return
        self._set("installing")
        # leave quickly so the installer finds the files free; the setup starts us again
        QTimer.singleShot(400, self.quitRequested.emit)

    # ------------------------------------------------------------------ misc
    @Slot()
    def dismiss(self):
        self._dismissed = True
        self.changed.emit()

    @Slot()
    def openReleasePage(self):
        QDesktopServices.openUrl(QUrl(self._page or SITE))
