#!/usr/bin/env python3
"""
Googlebook OS (mica) -> Samsung Galaxy Tab S7 (SM-T870 / gts7lwifi): patch and package.

  python3 ota-yamala.py                      # patch, verify, build system/system_ext/product
  python3 ota-yamala.py --bolum system       # only rebuild the given image(s)
  python3 ota-yamala.py --guncelle           # new mica release: download, new work dir, build

Steps for the work directory GBOS_WORK (default /var/tmp/mica_work):
  1. apply_patches.py patches the trees (sys, sysext, prod). Every patch starts from the
     pristine files of mica-imaj/guncel (or the LineageOS 32-bit runtime) and is idempotent.
  2. yama-dogrula.py resets reflink copies of the trees to the pristine images, runs
     apply_patches.py on them and requires the result to equal the trees byte for byte;
     otherwise a change would be lost on the next release and the build stops.
  3. make_ext4_images.py builds ext4 images sized for the super slots (reproducible with
     SOURCE_DATE_EPOCH).

--guncelle first runs mica-indir.py (Google's recovery listing; only the needed partitions
are streamed out of recovery.zip) and, if a newer release was downloaded,
yeni-surum-hazirla.py, which creates /var/tmp/mica_work-<build> from the new images; steps
1-3 then run there. The current work directory is left untouched until the new images are
flashed and verified; then replace it (see DEVIR.md).

Not automated: writing the images to the tablet (push in recovery + dd into the super slot,
see DEVIR.md "Flaş"); the tablet has to be rebooted by hand.
"""
import argparse
import os
import shutil
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
PARTITIONS = ["system", "system_ext", "product"]
NEEDED_FREE = 20 * 2**30


def run(args):
    print("[komut]", " ".join(args), flush=True)
    subprocess.run([sys.executable, "-I"] + args, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bolum", action="append", choices=PARTITIONS,
                    help="build only this image (repeatable); default: all")
    ap.add_argument("--dogrulama-yok", action="store_true",
                    help="skip the pristine image + patches == working tree check")
    ap.add_argument("--guncelle", action="store_true",
                    help="download a newer mica release and build it in a new work directory")
    args = ap.parse_args()

    if args.guncelle:
        free = shutil.disk_usage("/var/tmp").free
        if free < NEEDED_FREE:
            sys.exit(f"/var/tmp'te {free / 2**30:.1f} GiB boş; yeni sürüm için en az "
                     f"{NEEDED_FREE / 2**30:.0f} GiB gerekir (imajlar + ağaçlar + ext4).")
        before = os.path.realpath(os.path.join(TOOLS, "mica-imaj", "guncel"))
        run([os.path.join(TOOLS, "mica-indir.py")])
        after = os.path.realpath(os.path.join(TOOLS, "mica-imaj", "guncel"))
        if after == before:
            print("Yeni sürüm yok.")
            return 0
        build = os.path.basename(after)
        work = f"/var/tmp/mica_work-{build}"
        run([os.path.join(TOOLS, "yeni-surum-hazirla.py"), "--hedef", work])
        os.environ["GBOS_WORK"] = work

    run([os.path.join(TOOLS, "apply_patches.py")])
    if not args.dogrulama_yok:
        run([os.path.join(TOOLS, "yama-dogrula.py")])
    run([os.path.join(TOOLS, "make_ext4_images.py")] + (args.bolum or PARTITIONS))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        print(f"[HATA] {e}", file=sys.stderr)
        sys.exit(1)
