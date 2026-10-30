#!/usr/bin/env python3
"""Builds the tablet's update kit: what guncelleyici/motor/motor.py needs to prepare a new mica
release on the tablet itself (download, tree, apply_patches.py, ext4 images) without the computer.

    kit-uret.py denetle          run the pipeline once under denetim_kancasi.py on a scratch tree
                                 of the current release and write guncelleyici/kit/icerik.txt
    kit-uret.py uret DIR         assemble the kit into DIR (kit/, tasinan/, ev/, KIT)
    kit-uret.py gonder DIR       put DIR on the tablet as /data/gbos-ota/motor/{kit,tasinan,ev} and
                                 run the updater app's daily check: a kit the running image was not
                                 made with makes the tablet prepare and install the same release
                                 again by itself (charging, unmetered network, then idle)

Layout on the tablet (/data/gbos-ota/motor = /work inside the Linux environment, motor.py):
    kit/        T870-araclar files listed in icerik.txt, plus every source file of the modules
                whose prebuilt binaries the kit carries (their out/STAMP hashes those sources) and
                their out/; kit/mica-imaj -> ../mica-imaj
    tasinan/    the release-independent inputs yeni-surum-hazirla.py carries over
                (apply_patches.RELEASE_INDEPENDENT_INPUTS without the two metadata libraries,
                which motor.py builds there for the tablet's own fsck.erofs/mkfs.erofs)
    ev/         HOME of the pipeline: Android/Sdk/build-tools/37.0.0 (d8 and apksigner from the
                SDK's own jars, run by the environment's JDK; zipalign and dexdump from
                Debian), Android/Sdk/platforms/android-37.0/android.jar, and
                .android-certs/googlebookos-t870/uygulama.p12, the app key apply_patches.py
                re-signs changed APKs with. The update package key (ota.*) never leaves the
                computer: the tablet uses the signed package it already has.
    KIT         commit the kit was built from and its contents hash

icerik.txt is reviewed and committed like source; denetle refreshes it when the pipeline changes.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time

KIT_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(os.path.dirname(KIT_DIR))
ICERIK = os.path.join(KIT_DIR, "icerik.txt")
HOOK = os.path.join(KIT_DIR, "denetim_kancasi.py")
SDK = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK") or os.path.expanduser("~/Android/Sdk")
BUILD_TOOLS = os.path.join(SDK, "build-tools", "37.0.0")
ANDROID_JAR = os.path.join(SDK, "platforms", "android-37.0", "android.jar")
APP_KEY = os.path.join(os.environ.get("GBOS_ANAHTAR", os.path.expanduser("~/.android-certs/googlebookos-t870")),
                       "uygulama.p12")
CURRENT_WORK = os.environ.get("GBOS_TASINAN", "/var/tmp/mica_work")
TABLET_MOTOR = "/data/gbos-ota/motor"

# Scripts the motor runs or imports from the kit besides those denetle observes.
ENTRY = ["mica-indir.py", "mica_disk.py", "yeni-surum-hazirla.py", "apply_patches.py",
         "make_ext4_images.py", "tablete-yaz.py", "t870_cihaz.py", "guncelleyici/motor/motor.py",
         "guncelleyici/motor/ortam-kur.sh", "guncelleyici/motor/e2fsdroid-kur.sh",
         # hashed by dogal-yon/hal/stamp.sh
         "lineage-dosya-cikar.py",
         # super headers (tablete-yaz.py, motor.py); the stored ones this checkout recorded are
         # added by header_files()
         "super-baslik/uret.py", "super-baslik/SHA256SUMS",
         # the denials yamalar/selinux.py replays (the device logs themselves stay off the kit)
         "selinux/reddedilenler.txt"]
# Directories whose sources a prebuilt binary's out/STAMP hashes (yamalar/hazir_ikili.py): the kit
# carries all their tracked files and out/, so the stamp matches on the tablet.
STAMPED = ["pil-sagligi", "mica-servisleri", "dogal-yon/hal"]
# Directories the motor runs as a whole (guncelleyici/yerel/derle.py builds the metadata libraries
# from these sources on the tablet; selinux/araclar/kur.sh builds secilc there).
WHOLE = ["guncelleyici/yerel", "selinux/araclar"]
# Never in the kit: evidence, device logs, notes and caches.
SKIP_PARTS = {"kanit", "acilis-loglari", "__pycache__", ".pytest_cache"}
# Inputs the tablet has itself: the release it downloaded (kit/mica-imaj -> ../mica-imaj) and its
# own /vendor, which yamalar/dogal_yon_hal.py reads there instead of the LineageOS zip.
EXCLUDE_PREFIXES = ("mica-imaj/", "LineageOS-24.0-gts7lwifi/")

WRAPPERS = {
    # name: shell body; $SDK is ev/Android/Sdk on the tablet
    "d8": 'exec java -Xmx2048M -cp "$(dirname "$0")/lib/d8.jar" com.android.tools.r8.D8 "$@"',
    "apksigner": 'exec java -Xmx1024M -jar "$(dirname "$0")/lib/apksigner.jar" "$@"',
    "zipalign": 'exec /usr/bin/zipalign "$@"',
    "dexdump": 'exec /usr/bin/dexdump "$@"',
}


def log(msg):
    print(msg, flush=True)


def git(*args):
    return subprocess.run(["git", "-C", TOOLS_DIR, *args], check=True, capture_output=True,
                          text=True).stdout


def tracked(prefix):
    return [p for p in git("ls-files", "--", prefix).splitlines() if p]


def skipped(rel):
    return bool(SKIP_PARTS.intersection(rel.split("/")))


# --- denetle -------------------------------------------------------------------------------------

def denetle(args):
    """Runs the computer pipeline once on a scratch copy of the current release under the audit
    hook and writes icerik.txt from what it read."""
    scratch = tempfile.mkdtemp(prefix="gbos-kit-denetim-", dir="/var/tmp")
    work = os.path.join(scratch, "work")
    kayit = os.path.join(scratch, "kayit.txt")
    env = dict(os.environ, GBOS_WORK=work, SOURCE_DATE_EPOCH=str(int(time.time())), PYTHONUNBUFFERED="1")
    # make_ext4_images.py reads only the work tree (and is in ENTRY), so it is not run here.
    steps = [
        ["yeni-surum-hazirla.py", "--hedef", work],
        ["apply_patches.py"],
    ]
    try:
        for step in steps:
            log(f"== {' '.join(step)}")
            subprocess.run([sys.executable, HOOK, kayit, os.path.join(TOOLS_DIR, step[0]), *step[1:]],
                           cwd=TOOLS_DIR, env=env, check=True)
        files = icerik_from_record(kayit)
    finally:
        if not args.koru:
            shutil.rmtree(scratch, ignore_errors=True)
    with open(ICERIK + ".tmp", "w") as f:
        f.write("# T870-araclar files the image pipeline read in one run (kit-uret.py denetle).\n")
        f.write("# Generated; review the diff before committing.\n")
        for rel in files:
            f.write(rel + "\n")
    os.replace(ICERIK + ".tmp", ICERIK)
    log(f"icerik.txt: {len(files)} files")


def per_checkout(rel):
    """What differs from checkout to checkout never goes into the content list: the super headers
    and their sums (uret.py yakala) and the inputs the tablet has itself (EXCLUDE_PREFIXES: the
    downloaded release, the LineageOS zip)."""
    return (rel.startswith("super-baslik/") and (rel.endswith(".bin.xz") or rel.endswith("SHA256SUMS"))
            or rel.startswith(EXCLUDE_PREFIXES))


def icerik_from_record(kayit):
    files = set()
    with open(kayit) as f:
        for line in f:
            kind, rel = line.rstrip("\n").split(" ", 1)
            if rel == "." or rel.startswith("..") or skipped(rel):
                continue
            if per_checkout(rel):
                continue  # header_files() adds what this checkout has
            path = os.path.join(TOOLS_DIR, rel)
            if kind in ("open", "exec") and os.path.isfile(path):
                files.add(rel)
            elif kind == "list" and os.path.isdir(path) and rel.split("/")[0] not in (".", ""):
                for root, dirs, names in os.walk(path):
                    dirs[:] = [d for d in dirs if d not in SKIP_PARTS]
                    for n in names:
                        r = os.path.relpath(os.path.join(root, n), TOOLS_DIR)
                        if not skipped(r) and not r.endswith(".pyc"):
                            files.add(r)
    return sorted(files)


# --- uret ----------------------------------------------------------------------------------------

def read_icerik():
    with open(ICERIK) as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]


def header_files():
    """The super headers this checkout knows (super-baslik/SHA256SUMS) that are stored as files:
    the tablet's own LineageOS header recorded with uret.py yakala and, on the author's checkout,
    the first Googlebook OS header. googlebookos-2 is computed from lineage, not stored."""
    sys.path.insert(0, os.path.join(TOOLS_DIR, "super-baslik"))
    import uret  # noqa: E402
    return {f"super-baslik/{name}.bin.xz" for name in uret.SHA
            if os.path.isfile(os.path.join(TOOLS_DIR, "super-baslik", f"{name}.bin.xz"))}


def kit_files():
    files = {f for f in read_icerik() if not per_checkout(f)}
    files |= set(ENTRY) | header_files()
    for d in STAMPED:
        if not os.path.isdir(os.path.join(TOOLS_DIR, d)):
            continue
        files.update(tracked(d))
        out = os.path.join(TOOLS_DIR, d, "out")
        for root, _dirs, names in os.walk(out):
            files.update(os.path.relpath(os.path.join(root, n), TOOLS_DIR) for n in names)
    for d in WHOLE:
        files.update(tracked(d))
    return sorted(f for f in files
                  if not skipped(f) and not f.endswith(".pyc") and not f.startswith(EXCLUDE_PREFIXES))


def copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=True)


def uret(args):
    dest = os.path.abspath(args.dizin)
    if os.path.exists(dest) and os.listdir(dest):
        raise SystemExit(f"{dest} boş değil")
    # The work directory whose inputs the kit carries; dogal-yon/hal/stamp.sh reads the LineageOS
    # zip record there (lineage/bolumler/KAYNAK), as it does in the tablet's work directory.
    os.environ.setdefault("GBOS_WORK", CURRENT_WORK)
    sys.path.insert(0, os.path.join(TOOLS_DIR, "yamalar"))
    import hazir_ikili  # noqa: E402
    for d in STAMPED:
        src = os.path.join(TOOLS_DIR, d)
        if os.path.isdir(src) and os.path.isfile(os.path.join(src, "stamp.sh")):
            stamp_file = os.path.join(src, "out", "STAMP")
            if not os.path.isfile(stamp_file) or open(stamp_file).read().strip() != hazir_ikili.stamp(src):
                raise SystemExit(f"{d}/out kaynaklarla uyuşmuyor; önce derlenmeli")
    missing = [f for f in kit_files() if not os.path.isfile(os.path.join(TOOLS_DIR, f))]
    if missing:
        raise SystemExit("kitte olması gereken dosyalar yok:\n  " + "\n  ".join(missing))

    kit = os.path.join(dest, "kit")
    h = hashlib.sha256()
    files = kit_files()
    for rel in files:
        copy(os.path.join(TOOLS_DIR, rel), os.path.join(kit, rel))
        with open(os.path.join(TOOLS_DIR, rel), "rb") as f:
            h.update(rel.encode() + b"\0" + hashlib.sha256(f.read()).digest())
    os.symlink("../mica-imaj", os.path.join(kit, "mica-imaj"))
    # The vendor files yamalar/dogal_yon_hal.py checks its HALs against, from the LineageOS zip the
    # tablet's /vendor was flashed from (the Linux environment may not read /vendor).
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "vendor_dosyalari", os.path.join(TOOLS_DIR, "dogal-yon", "hal", "araclar", "vendor-dosyalari.py"))
    vd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vd)
    shutil.copytree(vd.stage(), os.path.join(kit, "dogal-yon", "hal", "vendor-kopya"))
    # Every prebuilt binary's stamp, computed inside the assembled kit, must equal its out/STAMP:
    # a source a stamp.sh hashes but the kit lacks shows here, not on the tablet.
    for d in STAMPED:
        script = os.path.join(kit, d, "stamp.sh")
        if not os.path.isfile(script):
            continue
        r = subprocess.run(["bash", script], capture_output=True, text=True)
        with open(os.path.join(kit, d, "out", "STAMP")) as f:
            expected = f.read().strip()
        if r.returncode != 0 or r.stdout.strip() != expected:
            raise SystemExit(f"{d}: kitteki damga out/STAMP ile uyuşmuyor\n{r.stderr[-1500:]}")

    tasinan = os.path.join(dest, "tasinan")
    sys.path.insert(0, TOOLS_DIR)
    import apply_patches  # noqa: E402
    for item in apply_patches.RELEASE_INDEPENDENT_INPUTS:
        if item.endswith(".so"):
            continue  # built on the tablet by guncelleyici/yerel/derle.py
        src = os.path.join(CURRENT_WORK, item)
        if not os.path.exists(src):
            raise SystemExit(f"taşınan girdi yok: {src}")
        os.makedirs(os.path.dirname(os.path.join(tasinan, item)), exist_ok=True)
        subprocess.run(["cp", "-a", "--reflink=auto", src, os.path.join(tasinan, item)], check=True)

    ev = os.path.join(dest, "ev")
    bt = os.path.join(ev, "Android", "Sdk", "build-tools", "37.0.0")
    for jar in ("d8.jar", "apksigner.jar"):
        copy(os.path.join(BUILD_TOOLS, "lib", jar), os.path.join(bt, "lib", jar))
    for name, body in WRAPPERS.items():
        path = os.path.join(bt, name)
        with open(path, "w") as f:
            f.write("#!/bin/sh\n# kit-uret.py: the tablet's stand-in for the SDK tool of the same name\n" + body + "\n")
        os.chmod(path, 0o755)
    copy(ANDROID_JAR, os.path.join(ev, "Android", "Sdk", "platforms", "android-37.0", "android.jar"))
    key_dir = os.path.join(ev, ".android-certs", "googlebookos-t870")
    copy(APP_KEY, os.path.join(key_dir, "uygulama.p12"))
    os.chmod(os.path.join(key_dir, "uygulama.p12"), 0o600)
    os.chmod(key_dir, 0o700)

    commit = git("rev-parse", "HEAD").strip()
    dirty = git("status", "--porcelain", "--", *files).strip()
    with open(os.path.join(dest, "KIT"), "w") as f:
        f.write(f"commit {commit}{'+kirli' if dirty else ''}\n")
        f.write(f"icerik {h.hexdigest()}\n")
        f.write(f"dosya {len(files)}\n")
        # the commit's time, not the clock: the image carries KIT (yamalar/kit_imaj.py) and the
        # same commit must give the same image
        f.write(f"tarih {git('log', '-1', '--format=%cI').strip()}\n")
    log(f"kit: {dest} ({len(files)} dosya, commit {commit[:7]}{' +kirli' if dirty else ''})")


# --- gonder --------------------------------------------------------------------------------------

def gonder(args):
    src = os.path.abspath(args.dizin)
    adb = ["adb", "-s", args.seri] if args.seri else ["adb"]
    with tempfile.TemporaryDirectory(prefix="gbos-kit-", dir="/var/tmp") as tmp:
        tar = os.path.join(tmp, "kit.tar")
        subprocess.run(["tar", "--owner=0", "--group=0", "-C", src, "-cf", tar, "kit", "tasinan", "ev", "KIT"], check=True)
        subprocess.run([*adb, "push", tar, f"{TABLET_MOTOR}.kit.tar"], check=True)
    # Replace kit/ and ev/ whole; tasinan/ keeps what motor.py built there (metadata libraries).
    script = f"""set -e
M={TABLET_MOTOR}; T=$M.kit.yeni
rm -rf $T; mkdir -p $T $M
tar -C $T -xf $M.kit.tar
rm -f $M.kit.tar
rm -rf $M/kit.eski $M/ev.eski
[ -d $M/kit ] && mv $M/kit $M/kit.eski
[ -d $M/ev ] && mv $M/ev $M/ev.eski
mv $T/kit $T/ev $M/
mkdir -p $M/tasinan && cp -a $T/tasinan/. $M/tasinan/
cp $T/KIT $M/KIT
rm -rf $T $M/kit.eski
if [ -d $M/ev.eski/.local ]; then mv $M/ev.eski/.local $M/ev/; fi
if [ -d $M/ev.eski/.cache ]; then mv $M/ev.eski/.cache $M/ev/; fi
rm -rf $M/ev.eski
chmod 700 $M
cat $M/KIT
"""
    subprocess.run([*adb, "shell", script], check=True)
    # Planlayici.DENETIM (job 1): sees kit_farkli and schedules the preparation
    subprocess.run([*adb, "shell", "cmd", "jobscheduler", "run", "-f", "com.gbos.guncelleyici", "1"],
                   check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("denetle")
    p.add_argument("--koru", action="store_true", help="keep the scratch tree and the record")
    p = sub.add_parser("uret")
    p.add_argument("dizin")
    p = sub.add_parser("gonder")
    p.add_argument("dizin")
    p.add_argument("--seri", default=os.environ.get("ANDROID_SERIAL"))
    args = ap.parse_args()
    {"denetle": denetle, "uret": uret, "gonder": gonder}[args.cmd](args)


if __name__ == "__main__":
    main()
