#!/usr/bin/env python3
"""
First install of Googlebook OS on a Galaxy Tab S7 (SM-T870) that runs LineageOS 24.0, from
LineageOS recovery. Writes, and verifies each by reading it back from the block device:

  boot        kernel/cikti/yamali/boot.img      LineageOS's boot with the patched kernel
                                                (kernel/build.sh boot yamali)
  recovery    $GBOS_WORK/denetim/recovery/recovery-gbos.img
                                                LineageOS's recovery that also trusts the
                                                updater's key (recovery-anahtar-ekle.py)
  super       $GBOS_WORK/denetim/imaj/*.img     system, system_ext, product (imaj-uret.sh),
                                                through tablete-yaz.py, which first replaces
                                                the LineageOS super header with Googlebook OS's

  ilk-kurulum.py            # asks before writing
  ilk-kurulum.py --evet     # writes without asking

Before it: the tablet's LineageOS super header is recorded (super-baslik/uret.py yakala) and
the images are built for it (imaj-uret.sh). The tablet is in LineageOS recovery with ADB on
(Advanced > Enable ADB). LineageOS's own system, system_ext and product stay in the lower half
of super, so LineageOS can be restored by writing its boot, recovery and super header back.
Afterwards, in recovery: Factory reset > Format data, then Reboot system now.
"""
import argparse
import hashlib
import os
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
from t870_cihaz import seri  # noqa: E402

WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
IMAJ = os.path.join(WORK, "denetim", "imaj")
BOOT = os.path.join(TOOLS, "kernel", "cikti", "yamali", "boot.img")
RECOVERY = os.path.join(WORK, "denetim", "recovery", "recovery-gbos.img")
SUPER_IMAGES = (("system", "system.img"), ("system-ext", "system_ext.img"), ("product", "product.img"))
MIB = 1 << 20


def adb(*args, **kw):
    return subprocess.run(["adb", "-s", seri(), *args], check=True, **kw)


def sh(cmd):
    return adb("shell", cmd, capture_output=True, text=True).stdout.strip()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 * MIB), b""):
            h.update(block)
    return h.hexdigest()


def state():
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        fields = line.split("\t")
        if len(fields) == 2 and fields[0] == seri():
            return fields[1]
    return None


def write_partition(name, path):
    """Writes `path` to /dev/block/by-name/<name>; the partition must be exactly as large or
    larger (the rest is left as it is) and reads back with the image's hash over its length."""
    dev = f"/dev/block/by-name/{name}"
    size = os.path.getsize(path)
    part = int(sh(f"blockdev --getsize64 {dev}"))
    if size > part:
        sys.exit(f"{name}: image {size} bytes, partition {part}")
    want = sha256(path)
    tmp = f"/tmp/gbos-{name}.img"
    adb("push", path, tmp, stdout=subprocess.DEVNULL)
    try:
        if sh(f"sha256sum {tmp}").split()[0] != want:
            sys.exit(f"{name}: image corrupted in transfer; nothing written")
        sh(f"dd if={tmp} of={dev} bs=1048576 conv=notrunc,fsync; sync; echo 3 > /proc/sys/vm/drop_caches")
    finally:
        sh(f"rm -f {tmp}")
    got = sh(f"head -c {size} {dev} | sha256sum").split()[0]
    print(f"[{'OK' if got == want else 'HATA'}] {name} {got}", flush=True)
    if got != want:
        sys.exit(f"{name} reads back differently; restore LineageOS's {name}.img from its zip")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evet", action="store_true", help="write without asking")
    args = ap.parse_args()

    images = {opt: os.path.join(IMAJ, name) for opt, name in SUPER_IMAGES}
    missing = [p for p in [BOOT, RECOVERY, *images.values()] if not os.path.isfile(p)]
    if missing:
        sys.exit("missing: " + ", ".join(missing) + " (kernel/build.sh boot yamali, "
                 "recovery-anahtar-ekle.py, imaj-uret.sh)")
    sums = os.path.join(IMAJ, "SHA256SUMS")
    if os.path.isfile(sums):
        r = subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=IMAJ)
        if r.returncode != 0:
            sys.exit(f"{IMAJ}: images do not match their SHA256SUMS")
    if state() != "recovery":
        sys.exit("the tablet is not in recovery (LineageOS recovery, Advanced > Enable ADB)")

    print("Writes boot, recovery and Googlebook OS's system, system_ext and product to "
          f"{seri()}.\nLineageOS's own partitions in super stay; its userdata has to be formatted "
          "afterwards.", flush=True)
    if not args.evet and input("Continue? [y/N] ").strip().lower() not in ("y", "yes", "e", "evet"):
        return 1

    # super first: tablete-yaz.py refuses a super header it does not know before anything is
    # written, so a tablet whose LineageOS header was not recorded keeps its boot and recovery
    cmd = [sys.executable, "-I", os.path.join(TOOLS, "tablete-yaz.py")]
    for opt, path in images.items():
        cmd += [f"--{opt}", path]
    if subprocess.run(cmd).returncode != 0:
        return 1
    write_partition("boot", BOOT)
    write_partition("recovery", RECOVERY)
    print("[+] Done. In recovery: Factory reset > Format data, then Reboot system now.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
