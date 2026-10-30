#!/usr/bin/env python3
"""Copies the device libraries the health HAL links against out of the pristine mica images.

On the tablet the HAL runs from /product/bin/hw, i.e. in linkerconfig's "product" section, where
only LLNDK libraries of the system partition are visible (mica's /system/etc/llndk.libraries.txt).
The binary needs exactly these, all LLNDK:
    libbinder_ndk.so, liblog.so     /system/lib64 of mica's system image
    libc.so, libm.so, libdl.so      lib64/bionic of mica's com.android.runtime APEX
build.sh links against the first two and checks every undefined symbol (with its version) of
the binary against all five.

    cihaz-kutuphaneleri.py OUT_DIR      writes the five files and OUT_DIR/KAYNAK
"""
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import zipfile

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SYSTEM_IMG = os.path.join(TOOLS_DIR, "mica-imaj", "guncel", "system_a.erofs")
DUMP_EROFS = os.environ.get("DUMP_EROFS", os.path.join(os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin")), "dump.erofs"))
SYSTEM_LIBS = ("libbinder_ndk.so", "liblog.so")
BIONIC_LIBS = ("libc.so", "libm.so", "libdl.so")
RUNTIME_APEX = "/system/apex/com.android.runtime.apex"


def cat(image, path):
    r = subprocess.run([DUMP_EROFS, "--cat", f"--path={path}", image], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise SystemExit(f"cannot read {path} from {image}: {r.stderr.decode()[-200:]}")
    return r.stdout


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    files = {}
    for lib in SYSTEM_LIBS:
        files[lib] = cat(SYSTEM_IMG, f"/system/lib64/{lib}")
    apex = cat(SYSTEM_IMG, RUNTIME_APEX)
    payload = zipfile.ZipFile(io.BytesIO(apex)).read("apex_payload.img")
    with tempfile.NamedTemporaryFile(dir=out, prefix=".runtime-", suffix=".img") as img:
        img.write(payload)
        img.flush()
        for lib in BIONIC_LIBS:
            files[lib] = cat(img.name, f"/lib64/bionic/{lib}")
    lines = [f"{os.path.realpath(SYSTEM_IMG)}  sha256 of {RUNTIME_APEX}: {hashlib.sha256(apex).hexdigest()}"]
    for name, data in files.items():
        with open(os.path.join(out, name), "wb") as f:
            f.write(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    with open(os.path.join(out, "KAYNAK"), "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
