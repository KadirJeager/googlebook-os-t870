#!/usr/bin/env python3
"""
Builds system/system_ext/product images that identify themselves as an older Googlebook OS
build, for testing the update path (flash the older images, then the real ones over them with
user data kept).

  surum-dusur-imaj.py                    # all three -> $GBOS_WORK/denetim/surum-dusur/
  surum-dusur-imaj.py --bolum system     # only the given partition(s)

The patched trees are copied with reflinks; only each partition's build.prop changes: build id
CL3B.260622.273.R1 -> CL3B.260622.272.R1, incremental 16471258 -> 16471200 and every build date
one day earlier. The version Android reports (ro.build.*) comes from product/etc/build.prop,
so product has to be part of a real downgrade. Everything else, including all T870 patches,
is identical to the real images.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
OUT = os.path.join(WORK, "denetim", "surum-dusur")
REAL = ("CL3B.260622.273.R1", "16471258")
OLD = ("CL3B.260622.272.R1", "16471200")
DAY = 86400
PARTITIONS = {  # name: (tree, build.prop inside the tree)
    "system": ("sys", "system/build.prop"),
    "system_ext": ("sysext", "etc/build.prop"),
    "product": ("prod", "etc/build.prop"),
}


def downgrade(bp):
    for real, old in zip(REAL, OLD):
        assert real in bp, f"{real} not in build.prop"
        bp = bp.replace(real, old)

    def older_utc(m):
        return f"{m.group(1)}={int(m.group(2)) - DAY}"
    bp = re.sub(r"^(ro(?:\.[a-z_]+)?\.build\.date\.utc)=(\d+)$", older_utc, bp, flags=re.M)

    def older_date(m):
        utc = re.search(rf"^{re.escape(m.group(1))}\.utc=(\d+)$", bp, re.M)
        assert utc, f"{m.group(1)}.utc missing"
        date = subprocess.run(["date", "-u", "-d", f"@{utc.group(1)}", "+%a %b %e %H:%M:%S UTC %Y"],
                              check=True, capture_output=True, text=True,
                              env=dict(os.environ, LC_ALL="C")).stdout.strip()
        return f"{m.group(1)}={date}"
    return re.sub(r"^(ro(?:\.[a-z_]+)?\.build\.date)=.*$", older_date, bp, flags=re.M)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bolum", action="append", choices=list(PARTITIONS))
    args = ap.parse_args()
    wanted = args.bolum or list(PARTITIONS)

    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    for name in wanted:
        tree, bp_rel = PARTITIONS[name]
        subprocess.run(["cp", "-a", "--reflink=always", os.path.join(WORK, tree), OUT], check=True)
        shutil.copy2(os.path.join(WORK, f"{tree}.meta"), OUT)
        bp_path = os.path.join(OUT, tree, bp_rel)
        os.remove(bp_path)  # break the reflink before writing
        with open(os.path.join(WORK, tree, bp_rel)) as f:
            bp = downgrade(f.read())
        with open(bp_path, "w") as f:
            f.write(bp)
        os.chmod(bp_path, 0o644)

    subprocess.run([sys.executable, "-I", os.path.join(TOOLS, "make_ext4_images.py")] + wanted,
                   env=dict(os.environ, GBOS_WORK=OUT), check=True)
    # The copied trees are only inputs of the images; keep just the images.
    for name in wanted:
        tree = PARTITIONS[name][0]
        shutil.rmtree(os.path.join(OUT, tree))
        os.remove(os.path.join(OUT, f"{tree}.meta"))
    for leftover in os.listdir(OUT):
        if leftover.endswith(("_fs_config", "_file_contexts")):
            os.remove(os.path.join(OUT, leftover))
    print("Eski sürüm imajları:", ", ".join(os.path.join(OUT, f"{n}.img") for n in wanted))
    return 0


if __name__ == "__main__":
    sys.exit(main())
