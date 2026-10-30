#!/usr/bin/env python3
"""
Builds the metadata helper libraries the image pipeline preloads into fsck.erofs and mkfs.erofs:

  libcapture_meta.so   records owner, mode and xattrs while a pristine EROFS image is extracted
                       by an unprivileged user (apply_patches._extract_with_meta)
  libprovide_meta.so   hands them back to mkfs.erofs when a tree is packed again
                       (apply_patches.patch_tethering_apex)

  derle.py                         # into /var/tmp/mica_work (the pipeline's GBOS_WORK)
  derle.py --hedef DIR             # another work directory (the tablet's /work/tasinan)
  derle.py --denetle [--hedef DIR] # exit 1 when a library is missing or older than its source

A library is rebuilt only when its source changed: DIR/<lib>.kaynak holds the SHA-256 of the
source it was built from. Both are built with -Werror, and libprovide_meta.so must export every
call it interposes, so a toolchain that silently drops one fails here instead of in an image.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TARGET = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
LIBS = {
    "libcapture_meta.so": ("capture_meta.c", ["lchown", "chown", "chmod", "lsetxattr", "setxattr",
                                              "geteuid", "getuid"]),
    "libprovide_meta.so": ("provide_meta.c", ["lstat", "lstat64", "fstatat", "fstatat64",
                                              "llistxattr", "listxattr", "lgetxattr", "getxattr"]),
}
CFLAGS = ["-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Werror"]


def source_digest(src):
    with open(os.path.join(HERE, src), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def exported(lib):
    out = subprocess.run(["nm", "-D", "--defined-only", lib], check=True, capture_output=True,
                         text=True).stdout
    return {line.split()[-1].split("@")[0] for line in out.splitlines() if line.strip()}


def build(lib, target, cc):
    src, symbols = LIBS[lib]
    with tempfile.TemporaryDirectory(dir=target) as tmp:
        out = os.path.join(tmp, lib)
        res = subprocess.run([cc, *CFLAGS, "-o", out, os.path.join(HERE, src), "-ldl"],
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise SystemExit(f"{lib}: derleme başarısız\n{res.stderr}")
        missing = set(symbols) - exported(out)
        if missing:
            raise SystemExit(f"{lib}: dışa açılmayan çağrılar {sorted(missing)}")
        os.chmod(out, 0o755)
        os.replace(out, os.path.join(target, lib))
    with open(os.path.join(target, lib + ".kaynak"), "w") as f:
        f.write(source_digest(src) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hedef", default=DEFAULT_TARGET)
    ap.add_argument("--denetle", action="store_true")
    ap.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = ap.parse_args()
    target = os.path.abspath(args.hedef)
    stale = [lib for lib, (src, _) in LIBS.items()
             if not os.path.exists(os.path.join(target, lib))
             or read(os.path.join(target, lib + ".kaynak")) != source_digest(src)]
    if args.denetle:
        for lib in stale:
            print(f"ESKİ: {os.path.join(target, lib)}")
        return 1 if stale else 0
    if stale and not shutil.which(args.cc):
        raise SystemExit(f"derleyici yok: {args.cc}")
    os.makedirs(target, exist_ok=True)
    for lib in LIBS:
        if lib in stale:
            build(lib, target, args.cc)
            print(f"derlendi: {os.path.join(target, lib)}")
        else:
            print(f"güncel: {os.path.join(target, lib)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
