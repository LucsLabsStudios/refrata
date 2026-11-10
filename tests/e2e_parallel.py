"""The E2E battery split across several app processes at once.

    python tests/e2e_parallel.py                       # source tree
    python tests/e2e_parallel.py --exe dist/Refrata/Refrata.exe --report build/e2e.json

Each process is a whole app with its own temporary data (main.py --selftest makes one), running a
share of the tests; the shares are balanced by the last run's times. Tests that type on the keyboard
need the window in front (Qt hands key events and shortcuts only to the active window), so they run
in one last process on their own. Every process also runs t01 (no warnings at boot) and t99 (clean
shutdown): those are checks of the process itself.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVERY = ("t01_", "t99_")                 # per process, not per share
KEY_MARKERS = ("keyClick", "keyPress", "keySequence", "QTest.key", "key(C.parts", "type_text(", "ensure_focus(", "wkey(")
LAST = ROOT / "build" / "e2e-last.json"
# Tests build on the ones before them (t07 opens a video, t08..t15 use it). A share may only begin at
# one of these, which set up what they need themselves; whatever runs between two of them stays
# together, in order, in one process (and tests/e2e.py resets the app before each such start).
CHAIN_STARTS = ("t02_", "t07_", "t16_", "t18_", "t20_", "t21_", "t26_", "t31_", "t33_", "t35_", "t36_",
                "t38_", "t41_", "t45_", "t46_", "t47_", "t50_")


def tests() -> tuple[list[str], set[str]]:
    """Every test name in tests/e2e.py, and the ones that need the keyboard."""
    src = (ROOT / "tests" / "e2e.py").read_text(encoding="utf8")
    names, keys = [], set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and re.match(r"t\d\d_", node.name):
            names.append(node.name)
            if any(m in ast.get_source_segment(src, node) for m in KEY_MARKERS):
                keys.add(node.name)
    return sorted(names), keys


def last_times() -> dict:
    try:
        return {r["name"]: r["took"] for r in json.loads(LAST.read_text(encoding="utf8"))["results"]}
    except Exception:
        return {}


def chains(names: list[str]) -> list[list[str]]:
    out = []
    for n in names:
        if not out or n.startswith(CHAIN_STARTS):
            out.append([])
        out[-1].append(n)
    return out


def shares(names: list[str], jobs: int, times: dict) -> list[list[str]]:
    """Whole chains, longest first into the least loaded share (the usual greedy balance)."""
    out = [[] for _ in range(jobs)]
    load = [0.0] * jobs
    for c in sorted(chains(names), key=lambda c: -sum(times.get(n, 5.0) for n in c)):
        i = load.index(min(load))
        out[i] += c
        load[i] += sum(times.get(n, 5.0) for n in c)
    return [sorted(s) for s in out if s]


def run_share(cmd: list[str], names: list[str], label: str, logdir: Path) -> dict:
    report = logdir / f"{label}.json"
    log = logdir / f"{label}.log"
    env = {k: v for k, v in os.environ.items() if k not in ("REFRATA_E2E_TMP", "REFRATA_DATA")}
    env.setdefault("REFRATA_NO_MEDIAKEYS", "1")
    t0 = time.time()
    try:
        code = subprocess.run(cmd + ["--selftest", "-k", ",".join(names), "--report", str(report), "--log", str(log)],
                              cwd=str(ROOT), env=env, timeout=1800, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode
    except subprocess.TimeoutExpired:   # only this share's own process is killed
        code = "timeout"
    took = time.time() - t0
    try:
        data = json.loads(report.read_text(encoding="utf8"))
    except Exception:
        data = {"results": [], "passed": 0, "total": len(names)}
    text = log.read_text(encoding="utf8", errors="replace") if log.exists() else ""
    times = {m.group(2): float(m.group(3)) for m in re.finditer(r"(PASS|FAIL)\s+(\S+)\s+\(([\d.]+)s\)", text)}
    ran = {r["name"] for r in data["results"]}
    missing = [n for n in names if n not in ran]
    return {"label": label, "code": code, "took": round(took, 1), "log": str(log), "data": data,
            "times": times, "missing": missing}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="")
    ap.add_argument("--jobs", type=int, default=min(6, max(1, (os.cpu_count() or 2) // 4)))
    ap.add_argument("-k", default="", help="only tests whose name contains one of these (comma separated)")
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    names, keys = tests()
    if a.k:
        want = [k for k in a.k.split(",") if k]
        names = [n for n in names if any(k in n for k in want) or n.startswith(EVERY)]
    every = [n for n in names if n.startswith(EVERY)]
    rest = [n for n in names if n not in every]
    batch = [n for n in rest if n not in keys]
    alone = [n for n in rest if n in keys]
    cmd = [a.exe] if a.exe else [sys.executable, str(ROOT / "main.py")]
    logdir = Path(tempfile.mkdtemp(prefix="refrata_e2e_par_"))
    groups = shares(batch, a.jobs, last_times()) if a.jobs > 1 else ([batch] if batch else [])
    t_all = time.time()
    results = []
    with ThreadPoolExecutor(max(1, len(groups))) as pool:
        futs = [pool.submit(run_share, cmd, every + g, f"share{i + 1}", logdir) for i, g in enumerate(groups)]
        results += [f.result() for f in futs]
    if alone:                            # keyboard: the window must be in front, so nothing else runs
        results.append(run_share(cmd, every + alone, "keyboard", logdir))

    ok = total = 0
    merged = []
    for r in results:
        d = r["data"]
        ok += d.get("passed", 0)
        total += d.get("total", 0)
        for t in d["results"]:
            merged.append({**t, "share": r["label"], "took": r["times"].get(t["name"], 0.0)})
        status = "ok" if r["code"] == 0 else f"exit {r['code']}"
        print(f"  [{r['label']}] {d.get('passed', 0)}/{d.get('total', 0)} in {r['took']:.0f}s ({status}) log: {r['log']}")
        for t in d["results"]:
            if not t["ok"]:
                print(f"  FAIL  {t['name']}  [{r['label']}]\n        " + t["detail"].strip().replace("\n", "\n        "))
        if r["missing"]:
            print(f"  FAIL  [{r['label']}] did not run: {', '.join(r['missing'])} (the process stopped early)")
            total += len(r["missing"])
    good = ok == total and all(r["code"] == 0 for r in results)
    print(f"\n{ok}/{total} tests passed in {time.time() - t_all:.0f}s "
          f"({len(groups)} processes in parallel, {len(alone)} keyboard tests after; t01/t99 in each)")
    out = {"passed": ok, "total": total, "results": merged,
           "about": next((r["data"].get("about") for r in results if r["data"].get("about")), {})}
    for p in filter(None, (a.report, str(LAST))):
        try:
            Path(p).parent.mkdir(exist_ok=True)
            Path(p).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf8")
        except OSError:
            pass
    sys.exit(0 if good else 1)


if __name__ == "__main__":
    main()
