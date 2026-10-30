"""Googlebook OS "Tablet modu" quick settings tile, installed into the system tree.

    from yamalar import tablet_modu as gbos_tablet_modu
    gbos_tablet_modu.install(SYS_DIR)

The keyboard MCU of the Book Cover Keyboard cannot tell a cover standing behind the screen at an
angle from the laptop position; kernel patch 0002 (kernel/kaynak-yamalari) adds the switch
sec_keypad/tablet_mode and this tile turns it on and off. What lands in system/:

    priv-app/GbosTabletModu/GbosTabletModu.apk   the tile (tablet-modu/build.sh -> tablet-modu/out, built here
                                                 when stale)
    etc/init/gbos-tablet-modu.rc                 opens the node to app uids on boot

The policy is not added here: yamalar/selinux.py appends selinux/politika/plat/*.cil, where this
one lives as 71-gbos-tablet-modu.cil (a link to tablet-modu/selinux/). Running install() again
gives the same tree (idempotent).

Command line (from T870-araclar):
    python3 yamalar/tablet_modu.py install SYS_DIR
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hazir_ikili  # noqa: E402

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLET_MODU = os.path.join(TOOLS_DIR, "tablet-modu")

FILES = [
    # (source, path in the system tree, mode)
    (os.path.join(TABLET_MODU, "out", "system", "priv-app", "GbosTabletModu", "GbosTabletModu.apk"),
     "priv-app/GbosTabletModu/GbosTabletModu.apk", 0o644),
    (os.path.join(TABLET_MODU, "cihaz", "system", "etc", "init", "gbos-tablet-modu.rc"),
     "etc/init/gbos-tablet-modu.rc", 0o644),
]


def build():
    """Builds the app unless out/ already holds the build of the current sources
    (yamalar/hazir_ikili.py; the tablet's kit carries out/ only)."""
    hazir_ikili.derle(TABLET_MODU, os.path.join(TABLET_MODU, "out", "STAMP"), [FILES[0][0]],
                      "tablet-modu", kitte_kaynak=False)


def install(sys_dir):
    build()
    for src, rel, mode in FILES:
        if not os.path.isfile(src):
            raise FileNotFoundError(f"{src} is missing (run tablet-modu/build.sh)")
        dst = os.path.join(sys_dir, "system", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        os.chmod(dst, mode)
        os.chmod(os.path.dirname(dst), 0o755)
    print("[+] Tablet modu: quick settings tile and its init rule")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="komut", required=True)
    p = sub.add_parser("install")
    p.add_argument("sys_dir")
    args = ap.parse_args()
    if args.komut == "install":
        install(args.sys_dir)


if __name__ == "__main__":
    main()
