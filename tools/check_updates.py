"""Is anything Refrata bundles out of date?

    python tools/check_updates.py              # table; exit code 10 when something is behind
    python tools/check_updates.py --markdown   # the same as a Markdown report (GitHub issue body)

Checks the VLC engine (tools/vlc-version.txt vs VideoLAN), yt-dlp (YouTube and other sites break
old versions within weeks) and python-vlc (the libVLC bindings), against requirements.txt.
Used by .github/workflows/watch-updates.yml (weekly) and before a release (tools/release.py).
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import vlc_engine  # noqa: E402

WHY = {
    "VLC (libVLC)": "o motor que decodifica vídeo e áudio: correções de segurança e de formatos",
    "yt-dlp": "links do YouTube e de outros sites: versões antigas param de funcionar quando os sites mudam",
    "python-vlc": "a ponte entre o app e o libVLC",
}


def _pin(package: str) -> str:
    m = re.search(rf"^{re.escape(package)}==([^\s#]+)", (ROOT / "requirements.txt").read_text(encoding="utf8"),
                  re.M | re.I)
    return m.group(1) if m else ""


def _pypi_latest(package: str) -> str:
    req = urllib.request.Request(f"https://pypi.org/pypi/{package}/json", headers={"User-Agent": vlc_engine.UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["info"]["version"]


def _vkey(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def report() -> list[dict]:
    rows = []
    pin, last = vlc_engine.pinned(), vlc_engine.latest()
    rows.append({"name": "VLC (libVLC)", "have": pin, "latest": last, "behind": vlc_engine.newer(last, pin),
                 "note": "" if vlc_engine.same_major(last, pin) else "versão principal nova: exige adaptar o app"})
    for pkg in ("yt-dlp", "python-vlc"):
        have, last = _pin(pkg), _pypi_latest(pkg)
        rows.append({"name": pkg, "have": have, "latest": last, "behind": _vkey(last) > _vkey(have), "note": ""})
    return rows


def markdown(rows) -> str:
    out = ["O Refrata está com componentes desatualizados:", "",
           "| Componente | No app | Mais recente | Para que serve |", "|---|---|---|---|"]
    for r in rows:
        if r["behind"]:
            note = f" ({r['note']})" if r["note"] else ""
            out.append(f"| {r['name']} | {r['have']} | **{r['latest']}**{note} | {WHY[r['name']]} |")
    out += ["", "**Como atualizar**", "",
            "Os componentes entram sozinhos no próximo lançamento: `python tools/release.py` busca as versões "
            "novas, gera o build, roda as duas baterias de testes e só publica se tudo passar (se algo falhar, "
            "volta às versões anteriores). Sem mudanças no app, uma versão só de componentes sai com "
            "`python tools/release.py --bump`.", ""]
    if any(r["behind"] and r["note"] for r in rows):
        out += ["Atenção: uma versão principal nova do VLC não entra sozinha; ela exige adaptar o app.", ""]
    out += [
            "_Aberto automaticamente por `.github/workflows/watch-updates.yml`. Esta issue é atualizada a cada "
            "verificação e pode ser fechada depois do lançamento._"]
    return "\n".join(out)


def main():
    rows = report()
    if "--markdown" in sys.argv:
        print(markdown(rows) if any(r["behind"] for r in rows) else "")
    else:
        for r in rows:
            flag = "NEWER: " + r["latest"] if r["behind"] else "ok"
            print(f"{r['name']:<14} {r['have']:<12} {flag}  {r['note']}")
    sys.exit(10 if any(r["behind"] for r in rows) else 0)


if __name__ == "__main__":
    main()
