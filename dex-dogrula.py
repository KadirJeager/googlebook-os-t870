#!/usr/bin/env python3
"""
Runs the tablet's own ART verifier over every jar (and APK, apply_patches.PATCHED_APKS)
apply_patches.py changed, before the images are written (the tablet is in Android, adb root).

  dex-dogrula.py                 # working tree /var/tmp/mica_work (GBOS_WORK), exit 0 = no new errors

dexdump accepts byte sequences the ART verifier rejects: on 22 Oct 2026 a patch that left half an
instruction behind passed every host check and made zygote crash-loop. Here each changed jar
under /system/framework is verified on the tablet with dex2oat64 --compiler-filter=verify, once as
shipped in the pristine image and once as patched; a "Verification error" that only the patched
jar has fails the check. Jars on the boot class path cannot be checked this way (dex2oat skips
classes the boot class path already defines); for them it says so, and zygote verifies them when
it preloads at the next boot (cihaz-kabul/kabul.sh: acilis, onyukleme-imaji).
"""
import io
import os
import re
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import apply_patches  # noqa: E402
from t870_cihaz import seri  # noqa: E402

DEVICE_DIR = "/data/local/tmp/gbos-dex-dogrula"
DEX2OAT = "/apex/com.android.art/bin/dex2oat64"
ERROR = re.compile(r"dex2oat64\s*: (Verification error in .*)$")


def adb(*args, check=True):
    r = subprocess.run(["adb", "-s", seri(), *args], capture_output=True)
    if check and r.returncode != 0:
        raise SystemExit(f"adb {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout.decode(errors="replace")


def changed_jars(framework):
    shipped = set(apply_patches.pristine_listdir("system/framework", "system_a.erofs"))
    out = []
    for name in sorted(os.listdir(framework)):
        if not name.endswith(".jar") or name not in shipped:
            continue  # jars the patches add have no pristine copy to compare with
        pristine = apply_patches.pristine_file(f"system/framework/{name}")
        with open(os.path.join(framework, name), "rb") as f:
            if f.read() != pristine:
                out.append((name, pristine))
    return out


def changed_apks():
    """[(name, pristine bytes, patched path, path on the tablet)] of the APKs in PATCHED_APKS."""
    trees = {"sys": apply_patches.SYS_DIR, "sysext": apply_patches.SYSEXT_DIR, "prod": apply_patches.PROD_DIR}
    out = []
    for tree, rel, image, device_path in apply_patches.PATCHED_APKS:
        local = os.path.join(trees[tree], rel)
        pristine = apply_patches.pristine_file(rel, image)
        with open(local, "rb") as f:
            if f.read() != pristine:
                out.append((os.path.basename(rel), pristine, local, device_path))
    return out


def verify(local_or_bytes, name, label, dex_location=None):
    """The set of verification errors dex2oat64 logs for one jar or APK on the tablet."""
    remote = f"{DEVICE_DIR}/{label}-{name}"
    dex_location = dex_location or f"/system/framework/{name}"
    if isinstance(local_or_bytes, bytes):
        tmp = os.path.join(apply_patches.WORK, "denetim", f"dex-dogrula-{name}")
        with open(tmp, "wb") as f:
            f.write(local_or_bytes)
        try:
            adb("push", tmp, remote)
        finally:
            os.remove(tmp)
    else:
        adb("push", local_or_bytes, remote)
    adb("logcat", "-c")
    adb("shell", f"{DEX2OAT} --dex-file={remote} --dex-location={dex_location} "
                 f"--oat-file={DEVICE_DIR}/{label}.odex --compiler-filter=verify "
                 f"--instruction-set=arm64 --class-loader-context=PCL[]")
    log = adb("logcat", "-d", "-s", "dex2oat64")
    adb("shell", f"rm -f {remote} {DEVICE_DIR}/{label}.odex {DEVICE_DIR}/{label}.vdex")
    return {m.group(1).strip() for line in log.splitlines() for m in [ERROR.search(line)] if m}


def main():
    if adb("get-state", check=False).strip() != "device":
        raise SystemExit(f"{seri()} is not in Android (adb device)")
    adb("root", check=False)
    framework = os.path.join(apply_patches.SYS_DIR, "system/framework")
    boot = set(adb("shell", "echo $BOOTCLASSPATH").strip().split(":"))
    adb("shell", f"mkdir -p {DEVICE_DIR}")
    new_errors = 0
    try:
        for name, pristine in changed_jars(framework):
            if f"/system/framework/{name}" in boot:
                print(f"BAK  {name}: önyükleme sınıf yolunda; açılışta zygote doğrular (kabul.sh)")
                # dex entries the patches add to a boot jar define classes the boot class path does
                # not have yet, so dex2oat does verify them: each goes alone in a small jar
                with zipfile.ZipFile(io.BytesIO(pristine)) as zp, \
                        zipfile.ZipFile(os.path.join(framework, name)) as zn:
                    new_entries = sorted(set(zn.namelist()) - set(zp.namelist()))
                    for entry in new_entries:
                        if not entry.endswith(".dex"):
                            continue
                        buf = io.BytesIO()
                        with zipfile.ZipFile(buf, "w") as zo:
                            zo.writestr("classes.dex", zn.read(entry))
                        label = f"{name[:-4]}-{entry[:-4]}.jar"
                        errors = verify(buf.getvalue(), label, "yeni", f"/system/framework/{label}")
                        new_errors += len(errors)
                        if errors:
                            print(f"HATA {name}!{entry}: {len(errors)} doğrulama hatası")
                            for e in sorted(errors):
                                print(f"       {e}")
                        else:
                            print(f"OK   {name}!{entry}: yeni dex, doğrulama hatası yok")
                continue
            with zipfile.ZipFile(os.path.join(framework, name)) as z:
                assert any(n.endswith(".dex") for n in z.namelist()), name
            before = verify(pristine, name, "bozulmamis")
            after = verify(os.path.join(framework, name), name, "yamali")
            added = sorted(after - before)
            new_errors += len(added)
            if added:
                print(f"HATA {name}: yamanın eklediği {len(added)} doğrulama hatası")
                for e in added:
                    print(f"       {e}")
            else:
                print(f"OK   {name}: yeni doğrulama hatası yok ({len(before)} hata bozulmamış "
                      f"hâlde de var)")
        for name, pristine, local, device_path in changed_apks():
            before = verify(pristine, name, "bozulmamis", device_path)
            after = verify(local, name, "yamali", device_path)
            added = sorted(after - before)
            new_errors += len(added)
            if added:
                print(f"HATA {name}: yamanın eklediği {len(added)} doğrulama hatası")
                for e in added:
                    print(f"       {e}")
            else:
                print(f"OK   {name}: yeni doğrulama hatası yok ({len(before)} hata bozulmamış "
                      f"hâlde de var)")
    finally:
        adb("shell", f"rm -rf {DEVICE_DIR}", check=False)
    return 1 if new_errors else 0


if __name__ == "__main__":
    sys.exit(main())
