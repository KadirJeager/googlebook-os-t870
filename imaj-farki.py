#!/usr/bin/env python3
"""
Lists every difference between a pristine Googlebook OS partition image (EROFS) and a working
tree that is turned into the flashed ext4 image (e.g. /var/tmp/mica_work/sys).

  imaj-farki.py system     mica-imaj/guncel/system_a.erofs     /var/tmp/mica_work/sys
  imaj-farki.py system_ext mica-imaj/guncel/system_ext_a.erofs /var/tmp/mica_work/sysext
  imaj-farki.py product    mica-imaj/guncel/product_a.erofs    /var/tmp/mica_work/prod

The image is extracted into a temporary directory next to the work tree (same disk, removed
afterwards), every regular file and symlink of both sides is hashed, and the result is written
as JSON (--json) and summarised on stdout: files that are added, removed or changed in the tree.
Only content is compared; ownership, modes and SELinux labels are carried separately by the
*.meta files of the work tree.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

FSCK_EROFS = os.environ.get("FSCK_EROFS", os.path.join(os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin")), "fsck.erofs"))


def hash_tree(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if os.path.islink(path):
                out[rel] = "link:" + os.readlink(path)
            elif os.path.isfile(path):
                h = hashlib.sha256()
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                out[rel] = h.hexdigest()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("image")
    ap.add_argument("tree")
    ap.add_argument("--json", help="write the full comparison here")
    args = ap.parse_args()

    tree = os.path.abspath(args.tree)
    tmp = tempfile.mkdtemp(prefix=f".imaj-farki-{args.name}-", dir=os.path.dirname(tree))
    try:
        subprocess.run([FSCK_EROFS, f"--extract={tmp}", "--overwrite", "--no-preserve", args.image],
                       check=True, stdout=subprocess.DEVNULL)
        pristine = hash_tree(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    work = hash_tree(tree)

    added = sorted(set(work) - set(pristine))
    removed = sorted(set(pristine) - set(work))
    changed = sorted(p for p in set(work) & set(pristine) if work[p] != pristine[p])
    result = {"name": args.name, "image": os.path.abspath(args.image), "tree": tree,
              "added": added, "removed": removed, "changed": changed,
              "pristine": pristine, "work": work}
    if args.json:
        with open(args.json, "w") as f:
            json.dump(result, f, indent=1, sort_keys=True)
    print(f"{args.name}: {len(added)} added, {len(removed)} removed, {len(changed)} changed")
    for label, items in (("+", added), ("-", removed), ("~", changed)):
        for p in items:
            print(f"  {label} {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
