#!/usr/bin/env python3
"""Runs one pipeline script under a Python audit hook and records what it reads from T870-araclar.

    denetim_kancasi.py KAYIT SCRIPT [ARGS...]

Appends to KAYIT, one per line:
    open <path>        a file the script (or a module it imports) opened
    list <path>        a directory it listed (os.listdir, os.scandir, os.walk, shutil.copytree)
    exec <path>        a file of T870-araclar it started or passed to a child process
Only paths under T870-araclar are recorded. kit-uret.py turns the record into the kit's contents
list (guncelleyici/kit/icerik.txt): the tablet runs the same scripts from the kit, so whatever they
read here must be there.
"""
import os
import runpy
import sys

TOOLS_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    kayit, script = sys.argv[1], os.path.abspath(sys.argv[2])
    out = open(kayit, "a", buffering=1)
    seen = set()

    def note(kind, path):
        if not isinstance(path, (str, bytes, os.PathLike)):
            return
        try:
            p = os.path.realpath(os.fsdecode(path))
        except (TypeError, ValueError):
            return
        if p != TOOLS_DIR and not p.startswith(TOOLS_DIR + os.sep):
            return
        key = (kind, p)
        if key not in seen:
            seen.add(key)
            out.write(f"{kind} {os.path.relpath(p, TOOLS_DIR)}\n")

    def hook(event, args):
        if event == "open":
            if args and isinstance(args[0], (str, bytes, os.PathLike)):
                note("open", args[0])
        elif event in ("os.listdir", "os.scandir"):
            note("list", args[0] if args and args[0] is not None else ".")
        elif event == "subprocess.Popen":
            executable, argv = args[0], args[1]
            for a in [executable, *(argv if isinstance(argv, (list, tuple)) else [argv])]:
                if isinstance(a, (str, bytes, os.PathLike)) and os.path.exists(os.fsdecode(a)):
                    note("exec", a)

    sys.addaudithook(hook)
    sys.argv = [script, *sys.argv[3:]]
    sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
