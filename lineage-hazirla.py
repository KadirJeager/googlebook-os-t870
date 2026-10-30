#!/usr/bin/env python3
"""
Builds the LineageOS inputs of a Googlebook OS work directory from the LineageOS 24.0 gts7lwifi
OTA zip whose vendor, odm and boot the tablet runs.

  lineage-hazirla.py [ZIP]                  # -> $GBOS_WORK/lineage/ (default /var/tmp/mica_work)
  lineage-hazirla.py [ZIP] --work DIR
  lineage-hazirla.py [ZIP] --denetle        # only compare with what $GBOS_WORK/lineage holds

ZIP defaults to $GBOS_LINEAGE_ZIP, else the only lineage-*-gts7lwifi-signed.zip in
LineageOS-24.0-gts7lwifi/. The zip must be the one the tablet was flashed with: the vendor
partition stays LineageOS's under Googlebook OS, and the 32-bit vendor HALs load these files.

Output, in $WORK/lineage/ (each directory with KAYNAK = zip name, its SHA-256 and partitions,
and SHA256SUMS = every file with the path it was read from):
  lib32_stage/      the 32-bit Android runtime apply_patches.py installs into /system/lib
                    (mica's own runtime is 64-bit only): system/lib/** with the bionic symlinks
                    resolved into the runtime APEX, the 32-bit libraries of the runtime, i18n
                    and statsd APEXes, the bootstrap linker and system_ext's 32-bit
                    android.hidl.memory@1.0-impl.so
  vendor_stage/     vendor/lib/libbinder.so and libhidlbase.so (patched for the vendor HALs)
  vendor_features/  every vendor/etc/permissions file (the hardware the vendor declares)
  vendor_dogal_yon/ vendor/build.prop and vendor/usr/idc/sec_e-pen.idc (natural orientation)
  bolumler/         every other LineageOS file the image or the checks take, under
                    <partition>/<path in the partition> (system's without the /system prefix):
                    each partition's etc/selinux (the policy the tablet's vendor and odm were
                    built with; yamalar/selinux.py compiles and replays it), the system and
                    system_ext pieces mica lacks for the tablet's HIDL vendor (hwservicemanager,
                    hidl.allocator, wificond, hidl.memory, codec2 HIDL clients, their rc files and
                    VINTF manifests), bpf programs, input device configs, the LineageOS
                    compatibility matrix and device overlays, and the vendor rc files, VINTF
                    fragments and hals.conf of the HALs dogal-yon/hal and pil-sagligi replace

Partitions are rebuilt from the zip the way its updater writes them (lineage-dosya-cikar.py)
into a temporary directory next to the output and removed afterwards (~3 GB while running).
Every directory is assembled beside the old one and swapped in only once it is complete, so a
failure leaves the previous inputs as they were.
"""
import argparse
import glob
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True
TOOLS = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")

_spec = importlib.util.spec_from_file_location("lineage_dosya_cikar",
                                               os.path.join(TOOLS, "lineage-dosya-cikar.py"))
ldc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ldc)

# 32-bit libraries apply_patches.py needs in /system/lib that LineageOS keeps only inside APEXes
# (its /system/lib has no ICU, malloc debug or statsd libraries, and libc/libm/libdl are
# symlinks into the runtime APEX).
APEX_LIBS = {
    "com.android.runtime": ["libc_malloc_debug.so", "libc_malloc_hooks.so"],
    "com.android.i18n": ["libandroidicu.so", "libicu.so", "libicu_jni.so", "libicui18n.so",
                         "libicuuc.so"],
    "com.android.os.statsd": ["libstats_jni.so", "libstatspull.so", "libstatssocket.so"],
}
VENDOR_STAGE = ["/lib/libbinder.so", "/lib/libhidlbase.so"]
VENDOR_DOGAL_YON = ["/build.prop", "/usr/idc/sec_e-pen.idc"]
# bolumler/: a path ending in / means every file below that directory.
BOLUMLER = {
    "system": [
        "/etc/selinux/", "/etc/bpf/", "/usr/idc/",
        "/etc/vintf/manifest/manifest_services_android.frameworks.vibrator.xml",
        "/lib64/libcodec2_hidl_client@1.1.so", "/lib64/libcodec2_hidl_client@1.2.so",
    ],
    "system_ext": [
        "/etc/selinux/", "/etc/vintf/manifest.xml",
        "/etc/vintf/manifest/android.hidl.allocator@1.0-service.xml",
        "/bin/hw/android.hidl.allocator@1.0-service", "/bin/hwservicemanager", "/bin/wificond",
        "/etc/init/android.hidl.allocator@1.0-service.rc", "/etc/init/hwservicemanager.rc",
        "/etc/init/wificond.rc", "/lib64/hw/android.hidl.memory@1.0-impl.so",
    ],
    "product": [
        "/etc/selinux/", "/etc/vintf/compatibility_matrix.lineage.xml",
        "/overlay/framework-res__lineage_gts7lwifi__auto_generated_rro_product.apk",
        "/overlay/SettingsProvider__lineage_gts7lwifi__auto_generated_rro_product.apk",
        "/overlay/StorageManager__lineage_gts7lwifi__auto_generated_rro_product.apk",
        "/overlay/SystemUI__lineage_gts7lwifi__auto_generated_rro_product.apk",
    ],
    "vendor": [
        "/etc/selinux/", "/etc/sensors/hals.conf",
        "/etc/init/android.hardware.camera.provider-service.samsung.rc",
        "/etc/init/android.hardware.sensors-service.samsung-multihal.rc",
        "/etc/init/android.hardware.health-service.samsung.rc",
        "/etc/vintf/manifest/android.hardware.camera.provider-service.samsung.xml",
        "/etc/vintf/manifest/android.hardware.sensors-samsung-multihal.xml",
        "/etc/vintf/manifest/android.hardware.health-service.samsung.xml",
    ],
    "odm": ["/etc/selinux/"],
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def default_zip():
    env = os.environ.get("GBOS_LINEAGE_ZIP")
    if env:
        return env
    found = sorted(glob.glob(os.path.join(TOOLS, "LineageOS-24.0-gts7lwifi",
                                          "lineage-*-gts7lwifi-signed.zip")))
    if len(found) != 1:
        raise SystemExit("give the LineageOS zip the tablet was flashed with (argument or "
                         f"GBOS_LINEAGE_ZIP); LineageOS-24.0-gts7lwifi/ holds {len(found)}")
    return found[0]


def ext4_entries(image, path):
    """(name, kind, target) of every entry of directory `path` of an ext4 image; kind is
    'f' (regular file), 'd' (directory) or 'l' (symlink, with its target)."""
    r = subprocess.run(["debugfs", "-R", f"ls -p {path}", image], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"{path}: cannot list ({r.stderr.strip()[-200:]})")
    out = []
    for line in r.stdout.splitlines():
        e = line.split("/")
        # /inode/mode/uid/gid/name/size/
        if len(e) < 7 or e[5] in (".", ".."):
            continue
        kind = int(e[2], 8) & 0o170000
        if kind == 0o100000:
            out.append((e[5], "f", None))
        elif kind == 0o040000:
            out.append((e[5], "d", None))
        elif kind == 0o120000:
            t = subprocess.run(["debugfs", "-R", f"stat {path}/{e[5]}", image],
                               capture_output=True, text=True).stdout
            target = t.split('Fast link dest: "', 1)[1].split('"', 1)[0] if "Fast link dest" in t else None
            if target is None:
                raise SystemExit(f"{path}/{e[5]}: symlink target not readable")
            out.append((e[5], "l", target))
    if not out:
        raise SystemExit(f"{path}: empty or missing in the image")
    return out


def erofs_entries(image, path):
    """(name, kind, None) of every entry of directory `path` of an erofs image ('f' or 'd')."""
    r = subprocess.run([ldc.DUMP_EROFS, "--ls", f"--path={path}", image], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"{path}: cannot list ({r.stderr.strip()[-200:]})")
    out = []
    # NID TYPE FILENAME rows after the inode header; type 1 regular file, 2 directory
    for e in (line.split(None, 2) for line in r.stdout.splitlines()):
        if len(e) != 3 or not e[0].isdigit() or e[2] in (".", ".."):
            continue
        if e[1] not in ("1", "2"):
            raise SystemExit(f"{path}/{e[2]}: entry type {e[1]} is not a file or directory")
        out.append((e[2], "f" if e[1] == "1" else "d", None))
    if not out:
        raise SystemExit(f"{path}: empty or missing in the image")
    return out


def is_ext4(image):
    with open(image, "rb") as f:
        f.seek(1024 + 0x38)
        return f.read(2) == b"\x53\xef"


def tree(image, path):
    """Paths of the regular files below directory `path`, relative to it, sorted."""
    entries = ext4_entries if is_ext4(image) else erofs_entries
    out = []
    for name, kind, target in entries(image, path):
        if kind == "f":
            out.append(name)
        elif kind == "d":
            out += [f"{name}/{sub}" for sub in tree(image, f"{path}/{name}")]
        else:
            raise SystemExit(f"{path}/{name} -> {target}: symlinks are not expected here")
    return sorted(out)


class Stage:
    """A directory being assembled: files are copied in, their sources recorded."""

    def __init__(self, root, name, zip_line):
        self.final = os.path.join(root, name)
        self.dir = tempfile.mkdtemp(prefix=f".{name}-", dir=root)
        self.sums = []
        self.partitions = []
        self.zip_line = zip_line

    def add(self, image, src, rel, label):
        dest = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        ldc.read_file(image, src, dest)
        os.chmod(dest, 0o644)
        self.sums.append(f"{sha256(dest)}  {rel}  {label}")

    def finish(self):
        with open(os.path.join(self.dir, "SHA256SUMS"), "w") as f:
            f.write("\n".join(self.sums) + "\n")
        with open(os.path.join(self.dir, "KAYNAK"), "w") as f:
            f.write(f"{self.zip_line}  bolum={','.join(self.partitions)}\n")
        old = self.final + ".eski"
        if os.path.exists(self.final):
            os.rename(self.final, old)
        os.rename(self.dir, self.final)
        if os.path.exists(old):
            shutil.rmtree(old)

    def abandon(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def apex_payload(system_img, apex, tmp):
    """The ext4 payload of /system/apex/<apex>.apex, extracted into tmp."""
    packed = os.path.join(tmp, f"{apex}.apex")
    ldc.read_file(system_img, f"/system/apex/{apex}.apex", packed)
    payload = os.path.join(tmp, f"{apex}.payload.img")
    with zipfile.ZipFile(packed) as z, z.open("apex_payload.img") as src, open(payload, "wb") as out:
        shutil.copyfileobj(src, out, 1 << 20)
    os.remove(packed)
    return payload


def build_lib32(stage, system_img, tmp):
    payloads = {apex: apex_payload(system_img, apex, tmp) for apex in APEX_LIBS}
    stage.partitions.append("system")

    def walk(path, rel):
        for name, kind, target in ext4_entries(system_img, path):
            if kind == "d":
                walk(f"{path}/{name}", f"{rel}/{name}")
            elif kind == "f":
                stage.add(system_img, f"{path}/{name}", f"{rel}/{name}", f"system{path}/{name}")
            else:
                # /system/lib/libc.so -> /apex/com.android.runtime/lib/bionic/libc.so
                parts = target.split("/")
                if len(parts) < 4 or parts[1] != "apex" or parts[2] not in payloads:
                    raise SystemExit(f"{path}/{name} -> {target}: unexpected symlink")
                inner = "/" + "/".join(parts[3:])
                stage.add(payloads[parts[2]], inner, f"{rel}/{name}", f"{parts[2]}.apex{inner}")

    walk("/system/lib", "system_lib/lib")
    for apex, libs in APEX_LIBS.items():
        for lib in libs:
            stage.add(payloads[apex], f"/lib/{lib}", f"system_lib/lib/{lib}", f"{apex}.apex/lib/{lib}")
    stage.add(system_img, "/system/bin/bootstrap/linker", "bin/linker", "system/system/bin/bootstrap/linker")
    for p in payloads.values():
        os.remove(p)


def build_lib32_sysext(stage, sysext_img):
    stage.partitions.append("system_ext")
    lib = "/lib/hw/android.hidl.memory@1.0-impl.so"
    stage.add(sysext_img, lib, f"sysext_lib{lib}", f"system_ext{lib}")


def build_vendor(stages, vendor_img):
    for stage, paths in stages:
        stage.partitions.append("vendor")
        if paths is None:  # every file of /etc/permissions
            paths = [f"/etc/permissions/{n}" for n in ldc.list_files(vendor_img, "/etc/permissions")]
        for path in paths:
            stage.add(vendor_img, path, os.path.basename(path), f"vendor{path}")


def build_bolumler(stage, part, image):
    """bolumler/<part>/: the BOLUMLER files of one partition."""
    stage.partitions.append(part)
    prefix = "/system" if part == "system" else ""  # system is system-as-root
    for path in BOLUMLER[part]:
        files = [path.rstrip("/") + "/" + f for f in tree(image, prefix + path.rstrip("/"))] \
            if path.endswith("/") else [path]
        for f in files:
            stage.add(image, prefix + f, f"{part}{f}", f"{part}{prefix}{f}")


def compare(new_root, old_root, names):
    """Lines describing every difference between the `names` directories of two lineage/ trees
    (KAYNAK and SHA256SUMS aside)."""
    def files(root):
        out = {}
        for name in names:
            for d, _, files_ in os.walk(os.path.join(root, name)):
                for n in files_:
                    if n in ("KAYNAK", "SHA256SUMS"):
                        continue
                    p = os.path.join(d, n)
                    out[os.path.relpath(p, root)] = sha256(p)
        return out
    a, b = files(new_root), files(old_root)
    diff = [f"only in the zip: {k}" for k in sorted(a.keys() - b.keys())]
    diff += [f"only in {old_root}: {k}" for k in sorted(b.keys() - a.keys())]
    diff += [f"different: {k}" for k in sorted(a.keys() & b.keys()) if a[k] != b[k]]
    return diff


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", nargs="?")
    ap.add_argument("--work", default=WORK)
    ap.add_argument("--denetle", action="store_true",
                    help="build into a temporary directory and compare with WORK/lineage")
    args = ap.parse_args()
    zip_path = os.path.abspath(args.zip or default_zip())
    if not zipfile.is_zipfile(zip_path):
        raise SystemExit(f"{zip_path}: not a zip")
    zip_line = f"{os.path.basename(zip_path)}  sha256={sha256(zip_path)}"

    target = os.path.join(args.work, "lineage")
    root = tempfile.mkdtemp(prefix=".lineage-denetim-", dir=args.work) if args.denetle else target
    os.makedirs(root, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".bolumler-", dir=root)
    names = ["lib32_stage", "vendor_stage", "vendor_features", "vendor_dogal_yon", "bolumler"]
    stages = {n: Stage(root, n, zip_line) for n in names}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            # One partition image on disk at a time.
            for part in ("system", "system_ext", "product", "vendor", "odm"):
                image = os.path.join(tmp, f"{part}.img")
                ldc.rebuild(zf, part, image)
                if part == "system":
                    build_lib32(stages["lib32_stage"], image, tmp)
                elif part == "system_ext":
                    build_lib32_sysext(stages["lib32_stage"], image)
                elif part == "vendor":
                    build_vendor([(stages["vendor_stage"], VENDOR_STAGE),
                                  (stages["vendor_features"], None),
                                  (stages["vendor_dogal_yon"], VENDOR_DOGAL_YON)], image)
                build_bolumler(stages["bolumler"], part, image)
                os.remove(image)
        for s in stages.values():
            s.finish()
    except BaseException:
        for s in stages.values():
            s.abandon()
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if args.denetle:
        try:
            diff = compare(root, target, names)
        finally:
            shutil.rmtree(root)
        print("\n".join(diff) if diff else f"{target}: identical to {os.path.basename(zip_path)}")
        return 1 if diff else 0
    for n in names:
        print(f"{os.path.join(target, n)}: {len(stages[n].sums)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
