#!/usr/bin/env python3
"""The tablet's LineageOS vendor files the natural-orientation HALs are built and checked against.

The HAL libraries are linked against the vendor's own shared libraries (the copies the vendor HAL
processes load from /vendor/lib64), so they are read out of the LineageOS OTA zip the tablet's
vendor partition was written from, with lineage-dosya-cikar.py, into
    $GBOS_DOGAL_YON_CACHE/vendor-<first 16 hex of the zip's sha256>/
together with the vendor HAL binaries, rc files and VINTF fragments the tests compare with. The
directory is reused while its SHA256SUMS lists exactly FILES; otherwise it is extracted again.

    vendor-dosyalari.py         prints the directory (extracting first if needed)
"""
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
# The zip lineage-hazirla.py built the work directory's LineageOS inputs from (named in
# lineage/bolumler/KAYNAK, looked up in LineageOS-24.0-gts7lwifi/); GBOS_LINEAGE_ZIP overrides.
KAYNAK = os.path.join(os.environ.get("GBOS_WORK", "/var/tmp/mica_work"), "lineage", "bolumler", "KAYNAK")


def _zip():
    if os.environ.get("GBOS_LINEAGE_ZIP"):
        return os.environ["GBOS_LINEAGE_ZIP"]
    try:
        with open(KAYNAK) as f:
            name = f.read().split()[0]
    except (OSError, IndexError):
        return ""
    return os.path.join(TOOLS_DIR, "LineageOS-24.0-gts7lwifi", name)


ZIP = _zip()
CACHE_ROOT = os.environ.get("GBOS_DOGAL_YON_CACHE", os.path.expanduser("~/.cache/gbos-dogal-yon"))

SENSOR_LIBS = [
    "android.hardware.sensors@1.0.so", "android.hardware.sensors@2.0.so",
    "android.hardware.sensors@2.0-ScopedWakelock.so", "android.hardware.sensors@2.1.so",
    "android.hardware.sensors-V3-ndk.so", "android.hardware.common-V2-ndk.so",
    "android.hardware.common.fmq-V1-ndk.so", "libbase.so", "libcutils.so", "libfmq.so",
    "libhidlbase.so", "libpower.so", "libutils.so", "libc++.so",
]
CAMERA_LIBS = [
    "android.hardware.camera.common-V1-ndk.so", "android.hardware.camera.device-V1-ndk.so",
    "android.hardware.camera.provider-V1-ndk.so", "android.hardware.camera.metadata-V1-ndk.so",
    "android.hardware.graphics.common-V7-ndk.so", "libgralloctypes.so", "libui.so",
    "libhardware.so", "libbase.so", "libcutils.so", "libfmq.so", "libhidlbase.so",
    "libutils.so", "libc++.so", "android.hardware.common-V2-ndk.so",
    "android.hardware.common.fmq-V1-ndk.so",
]
OTHER = [
    "/bin/hw/android.hardware.sensors-service.samsung-multihal",
    "/bin/hw/android.hardware.camera.provider-service.samsung",
    "/lib64/camera.device-impl.samsung.so",
    "/etc/init/android.hardware.sensors-service.samsung-multihal.rc",
    "/etc/init/android.hardware.camera.provider-service.samsung.rc",
    "/etc/vintf/manifest/android.hardware.sensors-samsung-multihal.xml",
    "/etc/vintf/manifest/android.hardware.camera.provider-service.samsung.xml",
    "/etc/sensors/hals.conf",
]
FILES = sorted({f"/lib64/{n}" for n in SENSOR_LIBS + CAMERA_LIBS} | set(OTHER))


def zip_sha256():
    h = hashlib.sha256()
    with open(ZIP, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def listed(out):
    try:
        with open(os.path.join(out, "SHA256SUMS")) as f:
            return sorted(line.split()[2][len("vendor"):] for line in f if line.strip())
    except (OSError, IndexError):
        return None


def stage():
    if not os.path.isfile(ZIP):
        raise SystemExit(f"LineageOS zip missing: {ZIP or KAYNAK} (run lineage-hazirla.py with the zip "
                         "the tablet was flashed with, or set GBOS_LINEAGE_ZIP)")
    out = os.path.join(CACHE_ROOT, f"vendor-{zip_sha256()[:16]}")
    if listed(out) == FILES:
        return out
    tmp = out + ".tmp"
    subprocess.run(["rm", "-rf", tmp], check=True)
    subprocess.run([sys.executable, "-I", os.path.join(TOOLS_DIR, "lineage-dosya-cikar.py"), ZIP,
                    "vendor", *FILES, "--cikti", tmp], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["rm", "-rf", out], check=True)
    os.replace(tmp, out)
    return out


if __name__ == "__main__":
    print(stage())
