#!/usr/bin/env python3
"""
Writes Googlebook OS partition images into their slots in the tablet's super partition while
the tablet is in recovery, and verifies every slot by hashing it back from the block device.

  tablete-yaz.py --system system.img [--system-ext system_ext.img] [--product product.img]

Recovery's /tmp is a 2.5 GB tmpfs and Toybox dd short-reads from an adb pipe, so each image is
pushed in chunks of at most CHUNK_MIB, written with dd at the right offset (1 MiB blocks,
fsync) and removed again. Afterwards the page cache is dropped and each whole slot is read back
and compared with the image's SHA-256. The super header (LP metadata) must be the Googlebook OS
one images are made for (HEADER), an older Googlebook OS header, or (the first install) the
tablet's LineageOS header recorded with `super-baslik/uret.py yakala`; the last two are replaced
by the current one before any image is written. The slots are read from that header. Writing by
offset needs no mapped logical partitions: in recovery only Advanced > Enable ADB (if adb is
off), then Back to the main menu. LineageOS's own system, system_ext and product keep their
place in the lower half of super; its userdata does not carry over (Format data in recovery
before the first boot).

--sisteme-ac boots Android after a fully verified write with the reboot reason recovery's own
"Reboot system now" uses ("userrequested,recovery"). A bare "adb reboot" says "shell", which the
Samsung kernel does not hand to the PMIC, so the bootloader reopens recovery; and the first item
of recovery's Advanced menu is "Enter fastboot". Details and sources:
acilis-loglari/recovery-20261022-fastbootd/KOK-NEDEN.md.
"""
import argparse
import hashlib
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t870_cihaz import seri  # noqa: E402

SUPER = "/dev/block/by-name/super"

def _baslik():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "super_baslik", os.path.join(os.path.dirname(os.path.abspath(__file__)), "super-baslik", "uret.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BASLIK = _baslik()
# partition: (first MiB in super, size in MiB), for each Googlebook OS LP header this checkout
# knows (super-baslik/SHA256SUMS), read from the header itself. Every slot of an older header
# starts where the newer header's does and is not larger, so images made for an older header
# still mount under a newer one.
HEADER_SLOTS = {name: BASLIK.slots(BASLIK.header(name)) for name in BASLIK.googlebook_headers()}
# The header the images are made for: the current one, or for a bridge image an older one
# (GBOS_SUPER_BASLIK, set by guncelleyici/motor/motor.py when the running updater does not know
# the current header yet; such an image only brings the updater that does).
HEADER = os.environ.get("GBOS_SUPER_BASLIK") or BASLIK.CURRENT
if HEADER not in HEADER_SLOTS:
    BASLIK.header(HEADER)  # explains what is missing: no header recorded yet, or an unknown name
    raise SystemExit(f"GBOS_SUPER_BASLIK: unknown header {HEADER}")
KOPRU = HEADER != BASLIK.CURRENT
SLOTS = HEADER_SLOTS[HEADER]
GBOS_HEADER_SHA256 = BASLIK.SHA[HEADER]
CHUNK_MIB = 1024
MIB = 1 << 20
# Same reason as recovery_main.cpp's Device::REBOOT; "userrequested" makes sec_debug write
# PON_RESTART_REASON_NORMALBOOT.
SYSTEM_BOOT_REASON = "userrequested,recovery"
BOOT_TIMEOUT_S = 600


def adb(*args, **kw):
    return subprocess.run(["adb", "-s", seri(), *args], check=True, **kw)


def sh(cmd):
    return adb("shell", cmd, capture_output=True, text=True).stdout.strip()


def sha256_file(path, start=0, length=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(start)
        left = length if length is not None else os.path.getsize(path) - start
        while left:
            block = f.read(min(MIB * 8, left))
            if not block:
                break
            h.update(block)
            left -= len(block)
    return h.hexdigest()


def write_image(name, path):
    first, size_mib = SLOTS[name]
    size = os.path.getsize(path)
    if size != size_mib * MIB:
        sys.exit(f"{name}: image is {size} bytes, slot is {size_mib * MIB}")
    for off in range(0, size_mib, CHUNK_MIB):
        count = min(CHUNK_MIB, size_mib - off)
        chunk = f"/tmp/gbos-{name}-{off}.img"
        local = path + f".part{off}"
        with open(path, "rb") as src, open(local, "wb") as dst:
            src.seek(off * MIB)
            left = count * MIB
            while left:
                block = src.read(min(MIB * 8, left))
                dst.write(block)
                left -= len(block)
        try:
            want = sha256_file(local)
            adb("push", local, chunk, stdout=subprocess.DEVNULL)
            got = sh(f"sha256sum {chunk}").split()[0]
            if got != want:
                sys.exit(f"{name}: chunk {off} corrupted in transfer")
            sh(f"dd if={chunk} of={SUPER} bs=1048576 seek={first + off} conv=notrunc,fsync")
        finally:
            os.remove(local)
            sh(f"rm -f {chunk}")
        print(f"  {name}: {off + count}/{size_mib} MiB", flush=True)


def write_header():
    """Writes the current header into the first MiB of super and reads it back."""
    local = f"/var/tmp/gbos-super-baslik-{os.getpid()}.bin"
    with open(local, "wb") as f:
        f.write(BASLIK.header(HEADER))
    try:
        adb("push", local, "/tmp/gbos-super-baslik.bin", stdout=subprocess.DEVNULL)
    finally:
        os.remove(local)
    if sh("sha256sum /tmp/gbos-super-baslik.bin").split()[0] != GBOS_HEADER_SHA256:
        sys.exit("super header corrupted in transfer")
    sh(f"dd if=/tmp/gbos-super-baslik.bin of={SUPER} bs=1048576 count=1 conv=notrunc,fsync; "
       "rm -f /tmp/gbos-super-baslik.bin; sync; echo 3 > /proc/sys/vm/drop_caches")
    got = sh(f"dd if={SUPER} bs=1048576 count=1 2>/dev/null | sha256sum").split()[0]
    if got != GBOS_HEADER_SHA256:
        sys.exit(f"super header reads back as {got}; nothing else was written")


def adb_state():
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        fields = line.split("\t")
        if len(fields) == 2 and fields[0] == seri():
            return fields[1]
    return None


def boot_system():
    """Reboots from recovery into Android and waits until Android reports boot completed."""
    print(f"[+] reboot {SYSTEM_BOOT_REASON}", flush=True)
    subprocess.run(["adb", "-s", seri(), "shell", "reboot", SYSTEM_BOOT_REASON],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + BOOT_TIMEOUT_S
    left_recovery = False
    while time.monotonic() < deadline:
        time.sleep(5)
        state = adb_state()
        if state != "recovery":
            left_recovery = True
        if state == "recovery" and left_recovery:
            print("[HATA] the tablet came back into recovery instead of Android", flush=True)
            return False
        if state == "device":
            done = subprocess.run(["adb", "-s", seri(), "shell", "getprop", "sys.boot_completed"],
                                  capture_output=True, text=True).stdout.strip()
            if done == "1":
                build = sh("getprop ro.build.display.id")
                print(f"[OK] Android booted: {build}", flush=True)
                return True
    print(f"[HATA] Android did not report boot completed within {BOOT_TIMEOUT_S} s "
          f"(adb state: {adb_state()})", flush=True)
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--system")
    ap.add_argument("--system-ext")
    ap.add_argument("--product")
    ap.add_argument("--sisteme-ac", action="store_true",
                    help="after a verified write, boot Android and wait for boot completed")
    args = ap.parse_args()
    images = {n: p for n, p in (("system", args.system), ("system_ext", args.system_ext),
                                ("product", args.product)) if p}
    if not images:
        sys.exit("no image given")
    if adb_state() != "recovery":
        sys.exit("tablet is not in recovery")
    header = sh(f"dd if={SUPER} bs=1048576 count=1 2>/dev/null | sha256sum").split()[0]
    older = [n for n in HEADER_SLOTS if n != HEADER and BASLIK.SHA[n] == header]
    first_install = header == BASLIK.SHA["lineage"]
    if first_install:
        older = ["lineage"]
    if header != GBOS_HEADER_SHA256 and not older:
        sys.exit(f"unexpected super header {header}; refusing to write (a tablet still on "
                 "LineageOS: record its header first with super-baslik/uret.py yakala)")
    if older:
        # the images are made for the current header: it goes first (a superset of the older one,
        # so whatever is in the slots still mounts if the writes below are interrupted)
        print(f"[+] super header {older[0]} -> {HEADER}", flush=True)
        write_header()

    for name, path in images.items():
        print(f"[+] {name} <- {path}", flush=True)
        write_image(name, path)

    sh("sync; echo 3 > /proc/sys/vm/drop_caches")
    ok = True
    for name, path in images.items():
        first, size_mib = SLOTS[name]
        want = sha256_file(path)
        got = sh(f"dd if={SUPER} bs=1048576 skip={first} count={size_mib} 2>/dev/null | sha256sum").split()[0]
        print(f"[{'OK' if got == want else 'HATA'}] {name} {got}", flush=True)
        ok &= got == want
    if not ok:
        return 1
    if first_install:
        print("[+] First install: in recovery, Factory reset > Format data before booting "
              "Googlebook OS (LineageOS's userdata does not carry over); not booting now", flush=True)
        return 0
    if args.sisteme_ac and not boot_system():
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
