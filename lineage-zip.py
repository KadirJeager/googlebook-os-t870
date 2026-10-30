#!/usr/bin/env python3
"""
The LineageOS zip the work directory was built from, and the boot and recovery images in it.

  lineage-zip.py                          # prints the zip's path
  lineage-zip.py cikar boot.img DEST      # writes boot.img (or recovery.img) from the zip to DEST

lineage-hazirla.py records the zip it read in $GBOS_WORK/lineage/bolumler/KAYNAK (name and
SHA-256); the zip itself is looked up in LineageOS-24.0-gts7lwifi/ next to this script.
GBOS_LINEAGE_ZIP names another path for it. Before an image is taken from the zip, the zip's
SHA-256 is checked against KAYNAK: the kernel, recovery and the partitions of the image then all
come from the same LineageOS build.
"""
import argparse
import hashlib
import os
import shutil
import sys
import zipfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
KAYNAK = os.path.join(WORK, "lineage", "bolumler", "KAYNAK")
IMAGES = ("boot.img", "recovery.img", "dtbo.img")


def kaynak():
    """(zip name, sha256) recorded by lineage-hazirla.py."""
    try:
        with open(KAYNAK) as f:
            fields = f.read().split()
    except OSError:
        raise SystemExit(f"{KAYNAK} yok: önce lineage-hazirla.py ZIP")
    sha = next((x.split("=", 1)[1] for x in fields[1:] if x.startswith("sha256=")), None)
    if not fields or not sha:
        raise SystemExit(f"{KAYNAK}: zip adı ya da sha256 okunamadı")
    return fields[0], sha


def zip_path():
    name, _ = kaynak()
    path = os.environ.get("GBOS_LINEAGE_ZIP") or os.path.join(TOOLS, "LineageOS-24.0-gts7lwifi", name)
    if not os.path.isfile(path):
        raise SystemExit(f"LineageOS zip'i yok: {path} (GBOS_LINEAGE_ZIP ile gösterilebilir)")
    return path


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cikar(image, dest):
    if image not in IMAGES:
        raise SystemExit(f"{image}: yalnız {', '.join(IMAGES)}")
    path = zip_path()
    _, want = kaynak()
    got = sha256(path)
    if got != want:
        raise SystemExit(f"{path}: sha256 {got}, çalışma dizini {want} ile kuruldu "
                         "(lineage-hazirla.py bu zip'le yeniden çalıştırılmalı)")
    tmp = dest + ".tmp"
    with zipfile.ZipFile(path) as zf, zf.open(image) as src, open(tmp, "wb") as out:
        shutil.copyfileobj(src, out, 1 << 20)
    os.replace(tmp, dest)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="komut")
    p = sub.add_parser("cikar")
    p.add_argument("imaj", choices=IMAGES)
    p.add_argument("hedef")
    args = ap.parse_args()
    if args.komut == "cikar":
        cikar(args.imaj, args.hedef)
    else:
        print(zip_path())
    return 0


if __name__ == "__main__":
    sys.exit(main())
