#!/usr/bin/env python3
"""
Checks that pristine Googlebook OS images + apply_patches.py reproduce the working tree.

  yama-dogrula.py            # uses /var/tmp/mica_work and mica-imaj/guncel/*.erofs

Steps:
  1. imaj-farki.py lists, per partition, how the working tree differs from the pristine image.
  2. The working tree and the script's other inputs are copied with btrfs reflinks into
     <work>/denetim/work (no extra disk space), and the copied partition trees are reset to the
     pristine image content: changed files are restored, added files removed, removed files
     and symlinks put back.
  3. apply_patches.py runs against that copy (GBOS_WORK).
  4. The result is compared with the working tree. Every remaining difference is a change
     that exists in the working tree but is not produced by apply_patches.py (or the reverse),
     i.e. something an OTA update would silently lose.
Exit status 0 only when nothing differs.
"""
import json
import os
import shutil
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
AUDIT = os.path.join(WORK, "denetim")
SCRATCH = os.path.join(AUDIT, "work")  # removed again when the check ends
DUMP_EROFS = os.path.join(os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin")), "dump.erofs")

PARTITIONS = [  # name, pristine image, tree directory under WORK
    ("system", "system_a.erofs", "sys"),
    ("system_ext", "system_ext_a.erofs", "sysext"),
    ("product", "product_a.erofs", "prod"),
]
sys.path.insert(0, TOOLS)
sys.dont_write_bytecode = True
from apply_patches import RELEASE_INDEPENDENT_INPUTS as SCRIPT_INPUTS  # noqa: E402


def run(cmd, **kw):
    print("[komut]", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def reflink(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    run(["cp", "-a", "--reflink=always", src, dst])


def pristine_bytes(image, rel):
    return subprocess.run([DUMP_EROFS, "--cat", f"--path=/{rel}", image],
                          check=True, stdout=subprocess.PIPE).stdout


def reset_to_pristine(tree, image, diff):
    for rel in diff["added"]:
        p = os.path.join(tree, rel)
        if os.path.islink(p) or os.path.isfile(p):
            os.unlink(p)
    for rel in diff["changed"] + diff["removed"]:
        p = os.path.join(tree, rel)
        value = diff["pristine"][rel]
        if os.path.lexists(p):
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.unlink(p)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if value.startswith("link:"):
            os.symlink(value[5:], p)
        else:
            with open(p, "wb") as f:
                f.write(pristine_bytes(image, rel))


def _remove(path):
    """Removes a tree even where extracted files are read-only."""
    def onexc(func, p, _exc):
        os.chmod(os.path.dirname(p), 0o755)
        os.chmod(p, 0o755) if os.path.isdir(p) else None
        func(p)
    if os.path.lexists(path):
        shutil.rmtree(path, onexc=onexc)


def main():
    _remove(SCRATCH)
    os.makedirs(SCRATCH)
    try:
        return _check()
    finally:
        _remove(SCRATCH)  # the reflinked copies diverge from the tree and would hold disk space


def _check():
    diffs = {}
    for name, image, tree in PARTITIONS:
        out = os.path.join(SCRATCH, f"{name}.json")
        run([sys.executable, "-I", os.path.join(TOOLS, "imaj-farki.py"), name,
             os.path.join(TOOLS, "mica-imaj", "guncel", image), os.path.join(WORK, tree), "--json", out],
            stdout=subprocess.DEVNULL)
        with open(out) as f:
            diffs[name] = json.load(f)

    for item in SCRIPT_INPUTS:
        reflink(os.path.join(WORK, item), os.path.join(SCRATCH, item))
    for name, image, tree in PARTITIONS:
        reflink(os.path.join(WORK, tree), os.path.join(SCRATCH, tree))
        reset_to_pristine(os.path.join(SCRATCH, tree), os.path.join(TOOLS, "mica-imaj", "guncel", image),
                          diffs[name])

    run([sys.executable, "-I", os.path.join(TOOLS, "apply_patches.py")],
        env=dict(os.environ, GBOS_WORK=SCRATCH))

    sys.path.insert(0, TOOLS)
    from importlib import import_module
    hash_tree = import_module("imaj-farki").hash_tree
    problems = 0
    for name, _, tree in PARTITIONS:
        want = diffs[name]["work"]
        got = hash_tree(os.path.join(SCRATCH, tree))
        only_work = sorted(set(want) - set(got))
        only_script = sorted(set(got) - set(want))
        differ = sorted(p for p in set(want) & set(got) if want[p] != got[p])
        n = len(only_work) + len(only_script) + len(differ)
        problems += n
        print(f"{name}: {n} fark")
        for label, items in (("yalnız çalışan ağaçta", only_work),
                             ("yalnız betik çıktısında", only_script),
                             ("içerik farklı", differ)):
            for p in items:
                print(f"  [{label}] {p}")
    print("SONUÇ:", "betik çalışan ağacı birebir üretiyor" if problems == 0 else f"{problems} fark")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
