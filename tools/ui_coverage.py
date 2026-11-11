"""Every named control in the interface is used by some test, or is on the list of those still owed one.

    python tools/ui_coverage.py            # check (the release gate)
    python tools/ui_coverage.py --update   # rewrite the list after adding tests (it may only shrink)

A control gets an objectName so that tests can reach it. One that no test mentions is a button
nobody presses before a release. tests/ui_untested.txt lists the ones still owed a test: a new
control missing from the tests fails here, and the list itself is not allowed to grow.
"""
import glob
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIST = ROOT / "tests" / "ui_untested.txt"


def controls() -> dict:
    out = {}
    for f in sorted(glob.glob(str(ROOT / "qml" / "*.qml"))):
        for m in re.finditer(r'objectName:\s*"([A-Za-z0-9_]+)"', open(f, encoding="utf-8").read()):
            out.setdefault(m.group(1), os.path.basename(f))
    return out


def untested() -> dict:
    tests = "".join(open(p, encoding="utf-8").read() for p in glob.glob(str(ROOT / "tests" / "*.py")))
    return {n: f for n, f in controls().items() if f'"{n}' not in tests and f"'{n}" not in tests}


def main():
    missing = untested()
    known = set(LIST.read_text(encoding="utf8").split()) if LIST.exists() else set()
    if "--update" in sys.argv:
        grown = sorted(set(missing) - known) if known else []
        if grown:
            raise SystemExit("the list may only shrink; these controls need a test: " + ", ".join(grown))
        LIST.write_text("\n".join(sorted(missing)) + "\n", encoding="utf8")
        print(f"{len(missing)} controls still owed a test (of {len(controls())})")
        return
    new = sorted(set(missing) - known)
    done = sorted(known - set(missing))
    total = len(controls())
    print(f"interface controls: {total}; used by a test: {total - len(missing)} ({100 * (total - len(missing)) // total}%); "
          f"still owed a test: {len(missing)}")
    if done:
        print("now tested (run with --update to take them off the list): " + ", ".join(done))
    if new:
        print("FAIL: controls no test uses: " + ", ".join(f"{n} ({missing[n]})" for n in new))
        sys.exit(1)


if __name__ == "__main__":
    main()
