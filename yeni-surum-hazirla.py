#!/usr/bin/env python3
"""
Creates a fresh work directory for a Googlebook OS release from its pristine images.

  yeni-surum-hazirla.py                          # release mica-imaj/guncel -> /var/tmp/mica_work-<build>
  yeni-surum-hazirla.py --hedef DIR
  GBOS_WORK=DIR python3 ota-yamala.py            # then patch, verify and build images there

The partition trees (sys, sysext, prod) are extracted from mica-imaj/guncel/*.erofs together
with their ownership, modes and xattrs (*.meta, captured through libcapture_meta.so), which
make_ext4_images.py turns into the image's fs_config and file contexts. The LineageOS 32-bit
runtime and the metadata helper libraries are taken from the current work directory with
reflinks; they do not depend on the Googlebook OS release. Nothing of the previous release's
trees is reused.
"""
import argparse
import json
import os
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
IMAGES = os.path.join(TOOLS, "mica-imaj", "guncel")
# Where the release-independent inputs (CARRIED) come from: the computer's current work directory,
# or the kit's tasinan/ on the tablet (guncelleyici/motor/motor.py sets GBOS_TASINAN).
CURRENT_WORK = os.environ.get("GBOS_TASINAN", "/var/tmp/mica_work")
sys.path.insert(0, TOOLS)
sys.dont_write_bytecode = True
from apply_patches import META_LIBS, RELEASE_INDEPENDENT_INPUTS as CARRIED  # noqa: E402


def main():
    with open(os.path.join(IMAGES, "kaynak.json")) as f:
        build = json.load(f)["release"]["buildidentifier"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hedef", default=f"{CURRENT_WORK}-{build}")
    args = ap.parse_args()
    dest = os.path.abspath(args.hedef)
    if os.path.realpath(dest) == os.path.realpath(CURRENT_WORK):
        sys.exit("hedef mevcut çalışma dizini olamaz")
    if os.path.exists(dest) and os.listdir(dest):
        sys.exit(f"{dest} boş değil")
    os.makedirs(dest, exist_ok=True)

    for item in CARRIED:
        if item in META_LIBS and os.environ.get("GBOS_META_LIB_DIR"):
            continue  # preloaded from GBOS_META_LIB_DIR (the tablet), not from the work tree
        target = os.path.join(dest, item)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        subprocess.run(["cp", "-a", "--reflink=auto", os.path.join(CURRENT_WORK, item), target],
                       check=True)

    os.environ["GBOS_WORK"] = dest
    import importlib
    import apply_patches  # noqa: E402
    importlib.reload(apply_patches)  # reads GBOS_WORK at import

    apply_patches.extract_trees(dest, IMAGES)
    print(f"Hazır: {dest} ({build}). Sonraki adım: GBOS_WORK={dest} python3 ota-yamala.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
