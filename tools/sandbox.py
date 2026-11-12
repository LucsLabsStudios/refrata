"""Run the Windows test batteries inside Windows Sandbox, so the PC stays free to use.

    python tools/sandbox.py                      # e2e + robustness + launch, against dist\\Refrata
    python tools/sandbox.py --only e2e,robust    # a part of it
    python tools/sandbox.py --source             # against the source tree instead of dist\\Refrata

Several of the tests need the keyboard focus and the foreground window (Qt gives shortcuts only to
the active window; the Explorer and focus scenarios check what has the focus): with a person at the
PC they failed on stray keystrokes. A virtual desktop (Win+Tab) does not help, focus is per session.
Windows Sandbox is a whole disposable Windows with its own screen, keyboard and focus.

The repository and this PC's Python go in read-only (the same folders the host uses); one folder,
build\\sandbox\\<run>, is writable: the reports and the log land there. The sandbox starts the
batteries at logon (tools\\sandbox_inner.cmd) and writes done.txt when they are over; this script
waits for it, prints the summary, and closes the sandbox. Exit code 0 only when everything passed.

Needs the "Windows Sandbox" optional feature (Containers-DisposableClientVM, Windows Pro); only one
sandbox runs at a time. The sandbox shares the host's GPU through virtualization, not the GPU
itself: performance budgets measured there (tests/launch.py) say less than on the host.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
SANDBOX_EXE = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsSandbox.exe"
IN_ROOT = r"C:\refrata"          # where the repository appears inside the sandbox
IN_PY = r"C:\py"                 # this PC's Python
IN_OUT = r"C:\out"               # the one writable folder


def wsb(out: Path, py: Path) -> str:
    def folder(host, inside, ro):
        return (f"<MappedFolder><HostFolder>{escape(str(host))}</HostFolder>"
                f"<SandboxFolder>{escape(inside)}</SandboxFolder><ReadOnly>{'true' if ro else 'false'}</ReadOnly></MappedFolder>")
    return ("<Configuration><VGpu>Enable</VGpu><Networking>Enable</Networking><AudioInput>Disable</AudioInput>"
            "<VideoInput>Disable</VideoInput><ClipboardRedirection>Disable</ClipboardRedirection><MemoryInMB>8192</MemoryInMB>"
            "<MappedFolders>" + folder(ROOT, IN_ROOT, True) + folder(py, IN_PY, True) + folder(out, IN_OUT, False)
            + "</MappedFolders>"
            f"<LogonCommand><Command>cmd.exe /c {IN_ROOT}\\tools\\sandbox_inner.cmd</Command></LogonCommand>"
            "</Configuration>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="e2e,robust,launch")
    ap.add_argument("--source", action="store_true")
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument("--keep", action="store_true", help="leave the sandbox open at the end")
    args = ap.parse_args()
    if not SANDBOX_EXE.exists():
        sys.exit("Windows Sandbox is not available here (turn on the optional feature, then restart).")
    if not args.source and not (ROOT / "dist" / "Refrata" / "Refrata.exe").exists():
        sys.exit("no dist\\Refrata\\Refrata.exe: build it first (python tools/build.py --no-msix), or use --source")
    if subprocess.run(["tasklist", "/FI", "IMAGENAME eq WindowsSandboxClient.exe"], capture_output=True, text=True).stdout.count(".exe"):
        sys.exit("a Windows Sandbox is already open (only one at a time): close it first")
    py = Path(sys.base_prefix)
    run = time.strftime("%Y%m%d-%H%M%S")
    out = ROOT / "build" / "sandbox" / run
    out.mkdir(parents=True)
    (out / "job.json").write_text(json.dumps({"only": args.only.split(","), "source": args.source}), "utf8")
    cfg = out / "refrata-tests.wsb"
    cfg.write_text(wsb(out, py), "utf8")
    print(f"sandbox run {run}: {args.only} against {'the source tree' if args.source else 'dist\\Refrata'}; results in {out}", flush=True)
    t0 = time.time()
    subprocess.Popen(["cmd", "/c", "start", "", str(cfg)])
    done = out / "done.txt"
    log = out / "log.txt"
    shown = 0
    while not done.exists():
        if time.time() - t0 > args.timeout:
            print(f"timed out after {args.timeout:.0f} s; the sandbox is left open to look at", flush=True)
            sys.exit(2)
        if log.exists():   # pass the progress on as it comes
            text = log.read_text("utf8", errors="replace")
            for line in text[shown:].splitlines():
                if any(k in line for k in ("PASS", "FAIL", "passed", "==", "!", "checks")):
                    print("  [sandbox] " + line.strip(), flush=True)
            shown = len(text)
        time.sleep(3)
    result = done.read_text("utf8", errors="replace").strip()
    print(f"\nsandbox finished in {time.time() - t0:.0f} s: {result}", flush=True)
    if not args.keep:
        subprocess.run(["taskkill", "/IM", "WindowsSandboxRemoteSession.exe", "/F"], capture_output=True)
        subprocess.run(["taskkill", "/IM", "WindowsSandbox.exe", "/F"], capture_output=True)
        subprocess.run(["taskkill", "/IM", "WindowsSandboxClient.exe", "/F"], capture_output=True)
    sys.exit(0 if result.startswith("ok") else 1)


if __name__ == "__main__":
    main()
