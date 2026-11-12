"""Inside Windows Sandbox: run the batteries tools/sandbox.py asked for, write C:\\out\\done.txt.

The same commands tools/release.py runs on the host, with the reports in C:\\out.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(r"C:\out")
ROOT = Path(__file__).resolve().parents[1]


def run(name, cmd, env=None):
    print(f"\n== {name}", flush=True)
    t0 = time.time()
    code = subprocess.run(cmd, cwd=str(ROOT), env=env).returncode
    print(f"== {name}: exit {code} in {time.time() - t0:.0f} s", flush=True)
    return code


def main():
    job = json.loads((OUT / "job.json").read_text("utf8"))
    exe = [] if job["source"] else ["--exe", str(ROOT / "dist" / "Refrata" / "Refrata.exe")]
    env = dict(os.environ, REFRATA_NO_MEDIAKEYS="1")
    results = {}
    time.sleep(5)   # the sandbox's desktop settling after logon
    if "unit" in job["only"]:
        results["unit"] = run("unit", [sys.executable, "tests/unit.py"])
    if "e2e" in job["only"]:
        results["e2e"] = run("e2e", [sys.executable, "tests/e2e_parallel.py", *exe, "--report", str(OUT / "e2e.json")], env)
    if "robust" in job["only"]:
        results["robust"] = run("robust", [sys.executable, "tests/robust.py", *exe, "--report", str(OUT / "robust.json")], env)
    if "robust-focus" in job["only"]:   # only the scenarios that need the keyboard focus (tests/robust.py --part)
        results["robust-focus"] = run("robust (focus)", [sys.executable, "tests/robust.py", *exe, "--part", "focus",
                                                         "--report", str(OUT / "robust-focus.json")], env)
    if "launch" in job["only"] and exe:
        results["launch"] = run("launch", [sys.executable, "tests/launch.py", *exe, "--report", str(OUT / "launch.json")])
    bad = [k for k, v in results.items() if v != 0]
    (OUT / "done.txt").write_text(("ok " if not bad else "FAILED " + ",".join(bad) + " ") + json.dumps(results), "utf8")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:   # always leave done.txt, or the host waits for nothing
        import traceback
        traceback.print_exc()
        (OUT / "done.txt").write_text(f"FAILED (runner error: {exc!r})", "utf8")
