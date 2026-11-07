"""Localisation helper.

    python tools/i18n.py keys        list every translatable source string
    python tools/i18n.py check       verify i18n/*.json (coverage, placeholders, tags, plural forms)
    python tools/i18n.py leftovers   Portuguese text in QML that is not wrapped in qsTr()

The source language is Portuguese (qsTr("…") in QML/JS, tr("…") and N_("…") in Python).
"""
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STR = r'"((?:[^"\\\n]|\\.)*)"'
QML_RE = re.compile(r'qsTr\(\s*' + STR + r'(\s*,\s*' + STR + r'\s*,)?')
PY_RE = re.compile(r'\b(?:tr|N_)\(\s*' + STR)
PLACEHOLDER = re.compile(r'%(?:\d|n)')
TAG = re.compile(r'</?\w+[^>]*>')


def _unescape(s: str) -> str:
    return s.encode("utf8").decode("unicode_escape").encode("latin1").decode("utf8") if "\\" in s else s


def keys() -> dict:
    """source text -> {"plural": bool, "where": [file:line]}"""
    out = {}
    files = sorted(glob.glob(str(ROOT / "qml" / "*.qml"))) + sorted(glob.glob(str(ROOT / "qml" / "*.js")))
    for f in files:
        for i, line in enumerate(open(f, encoding="utf8"), 1):
            for m in QML_RE.finditer(line):
                k = _unescape(m.group(1))
                e = out.setdefault(k, {"plural": False, "where": []})
                e["where"].append(f"{Path(f).name}:{i}")
                e["plural"] |= "%n" in k
    for f in sorted(glob.glob(str(ROOT / "refrata" / "*.py"))) + [str(ROOT / "main.py")]:
        if f.endswith("i18n.py"):
            continue
        for i, line in enumerate(open(f, encoding="utf8"), 1):
            for m in PY_RE.finditer(line):
                k = _unescape(m.group(1))
                e = out.setdefault(k, {"plural": False, "where": []})
                e["where"].append(f"{Path(f).name}:{i}")
                e["plural"] |= "%n" in k
    return out


def required_forms(code: str) -> set:
    from refrata.i18n import plural_form
    return {plural_form(code, n) for n in range(0, 200)}


def check(verbose=True) -> list:
    from refrata.i18n import CODES, SOURCE
    ks = keys()
    problems = []
    for code in CODES:
        path = ROOT / "i18n" / f"{code}.json"
        try:
            table = json.loads(path.read_text(encoding="utf8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{code}: cannot read {path.name}: {exc}")
            continue
        table = {k: v for k, v in table.items() if not k.startswith("_")}
        forms = required_forms(code)
        for k, meta in ks.items():
            if code == SOURCE and not meta["plural"]:
                continue   # Portuguese is the source; its file only holds plural forms
            if k not in table:
                problems.append(f"{code}: missing  {k!r}  ({meta['where'][0]})")
                continue
            v = table[k]
            if meta["plural"]:
                if not isinstance(v, dict):
                    problems.append(f"{code}: {k!r} needs plural forms {sorted(forms)}")
                    continue
                if not forms <= set(v):
                    problems.append(f"{code}: {k!r} lacks forms {sorted(forms - set(v))}")
                values = list(v.values())
            else:
                if not isinstance(v, str) or not v.strip():
                    problems.append(f"{code}: {k!r} is empty")
                    continue
                values = [v]
            want = sorted(set(PLACEHOLDER.findall(k)) - {"%n"})
            for val in values:
                got = sorted(set(PLACEHOLDER.findall(val)) - {"%n"})
                if got != want:
                    problems.append(f"{code}: {k!r} placeholders {got} != {want}")
                if len(TAG.findall(val)) != len(TAG.findall(k)):
                    problems.append(f"{code}: {k!r} HTML tags differ")
        for k in table:
            if k not in ks:
                problems.append(f"{code}: stale  {k!r}")
    if verbose:
        print("\n".join(problems) or f"ok: {len(ks)} strings, {len(CODES)} languages")
    return problems


def leftovers():
    lit = re.compile(STR)
    pt = re.compile(r'[À-ú]|\b(de|da|do|para|com|não|uma?|os?|as?|em|no|na)\b', re.I)
    for f in sorted(glob.glob(str(ROOT / "qml" / "*.qml"))) + sorted(glob.glob(str(ROOT / "qml" / "*.js"))):
        for i, line in enumerate(open(f, encoding="utf8"), 1):
            code = line.split(" // ")[0]
            if code.strip().startswith("//"):
                continue
            for m in lit.finditer(code):
                s = m.group(1)
                if code[:m.start()].endswith("qsTr(") or not pt.search(s) or not re.search(r'[a-zà-ú]{2}', s):
                    continue
                print(f"{Path(f).name}:{i}: {s}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "keys":
        for k, v in keys().items():
            print(("[n] " if v["plural"] else "    ") + k)
    elif cmd == "leftovers":
        leftovers()
    else:
        sys.exit(1 if check() else 0)
