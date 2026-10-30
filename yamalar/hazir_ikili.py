"""Prebuilt Android binaries and apps of the image pipeline (pil-sagligi, mica-servisleri,
dogal-yon/hal; terminal, tablet-modu and yuz-tanima, whose sources the kit does not carry).

A module's out/STAMP is the hash its stamp.sh prints over what its build.sh reads from the source
tree. It names neither the mica release nor any path, so the same out/ serves every release and
every machine, the tablet's kit included (the NDK has no aarch64 Linux host; the tablet cannot
build these).

The release is checked on its own (uyum_denetle): every symbol the binary imports must be
exported, under its version, by the device libraries of the mica release being patched
(pil-sagligi/araclar/cihaz-kutuphaneleri.py: libbinder_ndk, liblog and bionic, all LLNDK). LLNDK is a
stable ABI, so a new release breaks a binary only when a symbol disappears, and that shows here
instead of on the tablet at boot.
"""
import os
import shutil
import subprocess
import tempfile

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CIHAZ_KUTUPHANELERI = os.path.join(TOOLS_DIR, "pil-sagligi", "araclar", "cihaz-kutuphaneleri.py")
SEMBOL_DENETIMI = os.path.join(TOOLS_DIR, "pil-sagligi", "araclar", "sembol-denetimi.py")
NDK_READELF = os.path.join(os.environ.get("ANDROID_NDK") or os.path.join(
                               os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK")
                               or os.path.expanduser("~/Android/Sdk"), "ndk", "30.0.16248370"),
                           "toolchains", "llvm", "prebuilt", "linux-x86_64", "bin", "llvm-readelf")


class KitEski(RuntimeError):
    """out/ does not hold the build of the current sources and this machine cannot build it."""


def stamp(src):
    # Through bash, not exec: on the tablet the kit lies where the Linux environment may read but
    # not execute (guncelleyici/motor/motor.py).
    return subprocess.run(["bash", os.path.join(src, "stamp.sh")], check=True, capture_output=True,
                          text=True).stdout.strip()


def guncel(src, stamp_file, binaries):
    if not all(os.path.isfile(b) for b in binaries) or not os.path.isfile(stamp_file):
        return False
    with open(stamp_file) as f:
        return f.read().strip() == stamp(src)


def derle(src, stamp_file, binaries, name, kitte_kaynak=True):
    """Builds with src/build.sh unless out/ already holds the build of the current sources. In the
    tablet's kit (GBOS_KIT set) a stale out/ is an error: the kit must be rebuilt on the computer.
    A module whose sources the kit does not carry (kitte_kaynak=False: the apps, built with the
    SDK, which the tablet has not) is checked on the computer when the kit is made; in the kit its
    outputs only have to be there."""
    if os.environ.get("GBOS_KIT") and not kitte_kaynak:
        missing = [b for b in binaries if not os.path.isfile(b)]
        if missing:
            raise KitEski(f"{name}: kitte yok: {', '.join(missing)}")
        return
    if guncel(src, stamp_file, binaries):
        return
    if os.environ.get("GBOS_KIT"):
        raise KitEski(f"{name}: out/ kaynaklarla uyuşmuyor; kit bilgisayarda yeniden üretilmeli")
    subprocess.run([os.path.join(src, "build.sh")], check=True)
    if not guncel(src, stamp_file, binaries):
        raise RuntimeError(f"{name} inputs changed during the build")


def readelf():
    tool = os.environ.get("READELF")
    if tool:
        return tool
    if os.path.isfile(NDK_READELF):
        return NDK_READELF
    found = shutil.which("readelf")
    if not found:
        raise RuntimeError("no readelf (NDK llvm-readelf or binutils)")
    return found


def uyum_denetle(binary, name):
    """Every dynamic symbol `binary` imports resolves in the release's device libraries."""
    with tempfile.TemporaryDirectory(prefix="gbos-uyum-", dir=os.environ.get("TMPDIR", "/var/tmp")) as lib:
        subprocess.run(["python3", "-I", CIHAZ_KUTUPHANELERI, lib], check=True, capture_output=True)
        r = subprocess.run(["python3", "-I", SEMBOL_DENETIMI, "--yalniz-ice", readelf(), binary, lib],
                           capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{name}: this mica release does not provide every symbol the binary "
                           f"imports\n{r.stdout}{r.stderr}")
    return r.stdout.strip()
