"""Googlebook OS updater, installed into the image trees.

    from yamalar import guncelleyici as gbos_guncelleyici
    gbos_guncelleyici.install(SYS_DIR)
    ...  # every other patch
    gbos_guncelleyici.install_removed(SYS_DIR, SYSEXT_DIR, PROD_DIR, pristine_file, REMOVED_APPS)

Everything lands in the system partition:

    system/bin/gbos-guncelleyicid, gbos-guncelleyici    service and command line (build.sh)
    system/bin/gbos-guncelleyici-is                     operations and boot guard (cihaz/)
    system/etc/init/gbos-guncelleyici.rc                starts both, creates /data/gbos-ota, /cache/gbos
    system/priv-app/GbosGuncelleyici/                   "Sistem güncellemesi" (build.sh)
    system/etc/permissions/, default-permissions/       privapp allowlist, notification grant
    system/etc/gbos-guncelleyici/kurulum.zip            signed recovery package (build.sh)
    system/etc/gbos-guncelleyici/yuvalar.conf           super header hash and slots (tablete-yaz.py)
    system/etc/gbos-guncelleyici/super-baslik.bin       that header (super-baslik/uret.py)
    system/etc/gbos-guncelleyici/kaldirilan-paketler    every package the pristine images ship and
                                                        the finished trees do not (install_removed;
                                                        the app removes their leftover Play updates)
    system/etc/selinux/plat_file_contexts               + guncelleyici/cihaz/file_contexts

The policy is not added here: yamalar/selinux.py appends selinux/politika/plat/*.cil, where the
updater's lives as 70-gbos-guncelleyici.cil (a link to guncelleyici/selinux/).

The built files come from guncelleyici/out/<abi>/ (guncelleyici/build.sh). Running install() again
gives the same tree (idempotent).
"""
import os
import re
import shutil
import struct
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUNCELLEYICI = os.path.join(TOOLS_DIR, "guncelleyici")
CIHAZ = os.path.join(GUNCELLEYICI, "cihaz")

BUILT = [
    "bin/gbos-guncelleyicid",
    "bin/gbos-guncelleyici",
    "priv-app/GbosGuncelleyici/GbosGuncelleyici.apk",
    "etc/gbos-guncelleyici/kurulum.zip",
]
SOURCES = {  # relative to cihaz/system and to the image's system/: mode
    "bin/gbos-guncelleyici-is": 0o755,
    "etc/init/gbos-guncelleyici.rc": 0o644,
    "etc/permissions/privapp-permissions-gbos-guncelleyici.xml": 0o644,
    "etc/default-permissions/default-permissions-gbos-guncelleyici.xml": 0o644,
}
FILE_CONTEXTS = "etc/selinux/plat_file_contexts"
FC_BLOCK = re.compile(r"\n?# --- GBOS_GUNCELLEYICI BEGIN.*?# --- GBOS_GUNCELLEYICI END ---\n", re.S)
# apply_patches.REMOVED_APPS keys: which pristine image the paths are in
IMAGES = {"sys": "system_a.erofs", "sysext": "system_ext_a.erofs", "prod": "product_a.erofs"}
MICA_IMAGES = os.path.join(TOOLS_DIR, "mica-imaj", "guncel")  # as apply_patches.MICA_IMAGES
DUMP_EROFS = os.path.join(os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin")), "dump.erofs")


def pristine_ls(rel, image):
    """Names in a directory of a pristine mica image."""
    out = subprocess.run([DUMP_EROFS, "--ls", f"--path=/{rel}", os.path.join(MICA_IMAGES, image)],
                         check=True, capture_output=True, text=True).stdout
    return [m.group(1) for m in re.finditer(r"^\s*\d+\s+\d+\s+(\S+)$", out, re.M)
            if m.group(1) not in (".", "..")]


def _copy(src, dst, mode):
    if not os.path.isfile(src):
        raise FileNotFoundError(f"{src} is missing (run guncelleyici/build.sh)")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, mode)


def _write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def tablete_yaz():
    import importlib.util
    spec = importlib.util.spec_from_file_location("tablete_yaz", os.path.join(TOOLS_DIR, "tablete-yaz.py"))
    ty = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ty)
    return ty


def slots_conf():
    """yuvalar.conf from tablete-yaz.py, the one place the slots are defined: the header the
    images are made for, its slots, and the older headers an install may start from (each of
    their slots lies inside this header's; gbos-guncelleyici-is kur, kurtarma/update-binary)."""
    ty = tablete_yaz()
    lines = ["# Googlebook OS super layout (tablete-yaz.py): header hash, slots in MiB",
             f"super_baslik_sha256 {ty.GBOS_HEADER_SHA256}"]
    lines += [f"bolum {name} {first} {size}" for name, (first, size) in sorted(ty.SLOTS.items())]
    for name, slots in sorted(ty.HEADER_SLOTS.items()):
        if name == ty.HEADER:
            continue
        if all(slots[n][0] == ty.SLOTS[n][0] and slots[n][1] <= ty.SLOTS[n][1] for n in ty.SLOTS):
            lines.append(f"onceki_baslik {ty.BASLIK.SHA[name]}")
    return "\n".join(lines) + "\n"


# --- package name of an APK, from its binary AndroidManifest.xml (no aapt2 needed) ----------

RES_STRING_POOL, RES_XML, RES_XML_START_ELEMENT = 0x0001, 0x0003, 0x0102
UTF8_FLAG = 1 << 8


def _string_pool(data, off):
    _type, header_size, _size, count, _style_count, flags, strings_start, _styles = \
        struct.unpack_from("<HHIIIIII", data, off)
    offsets = struct.unpack_from(f"<{count}I", data, off + header_size)
    base = off + strings_start
    out = []
    for o in offsets:
        p = base + o
        if flags & UTF8_FLAG:
            n = data[p]
            p += 2 if n & 0x80 else 1           # UTF-16 length, skipped
            n = data[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | data[p + 1]
                p += 2
            else:
                p += 1
            out.append(data[p:p + n].decode("utf-8"))
        else:
            n = struct.unpack_from("<H", data, p)[0]
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p + 2)[0]
                p += 4
            else:
                p += 2
            out.append(data[p:p + 2 * n].decode("utf-16-le"))
    return out


def manifest_package(axml):
    """The package attribute of <manifest> in a compiled AndroidManifest.xml."""
    typ, header_size, size = struct.unpack_from("<HHI", axml, 0)
    if typ != RES_XML:
        raise ValueError("not a compiled XML file")
    strings = None
    off = header_size
    while off < size:
        ctype, cheader, csize = struct.unpack_from("<HHI", axml, off)
        if ctype == RES_STRING_POOL:
            strings = _string_pool(axml, off)
        elif ctype == RES_XML_START_ELEMENT:
            name_idx = struct.unpack_from("<I", axml, off + 16 + 4)[0]
            if strings is None:
                raise ValueError("string pool after the first element")
            if strings[name_idx] == "manifest":
                attr_start, attr_size, attr_count = struct.unpack_from("<HHH", axml, off + 16 + 8)
                a = off + 16 + attr_start
                for i in range(attr_count):
                    _ns, name, raw, _vsize, _res0, _vtype, _vdata = struct.unpack_from(
                        "<IIIHBBI", axml, a + i * attr_size)
                    if strings[name] == "package" and raw != 0xFFFFFFFF:
                        return strings[raw]
                raise ValueError("<manifest> has no package attribute")
        off += csize
    raise ValueError("no <manifest> element")


def apk_package(apk_bytes):
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(apk_bytes)) as z:
        return manifest_package(z.read("AndroidManifest.xml"))


# Where the images keep their packages: (image key, directory in the pristine image, tree)
APP_DIRS = [("sys", "system/app"), ("sys", "system/priv-app"),
            ("sysext", "app"), ("sysext", "priv-app"),
            ("prod", "app"), ("prod", "priv-app"), ("prod", "overlay")]


def pristine_packages(pristine_file, list_dir=pristine_ls):
    """Package names of the APKs in the pristine images' app directories."""
    names = set()
    for key, rel in APP_DIRS:
        image = IMAGES[key]
        for entry in list_dir(rel, image):
            if entry.endswith(".apk"):
                apks = [f"{rel}/{entry}"]
            else:
                apks = [f"{rel}/{entry}/{n}" for n in list_dir(f"{rel}/{entry}", image) if n.endswith(".apk")]
            for apk in apks:
                names.add(apk_package(pristine_file(apk, image)))
    return names


def tree_packages(trees):
    """Package names of the APKs in the finished trees' app directories ({key: tree root})."""
    names = set()
    for key, rel in APP_DIRS:
        top = os.path.join(trees[key], rel)
        if not os.path.isdir(top):
            continue
        for entry in sorted(os.listdir(top)):
            path = os.path.join(top, entry)
            if entry.endswith(".apk") and os.path.isfile(path):
                apks = [path]
            elif os.path.isdir(path):
                apks = [os.path.join(path, n) for n in sorted(os.listdir(path)) if n.endswith(".apk")]
            else:
                continue
            for apk in apks:
                with open(apk, "rb") as f:
                    names.add(apk_package(f.read()))
    return names


def install_removed(sys_dir, sysext_dir, prod_dir, pristine_file, removed_apps, list_dir=pristine_ls):
    """kaldirilan-paketler: every package the pristine images ship and the finished trees do not,
    whichever patch took it out. Android keeps a removed system app's Play update as an ordinary
    app; the updater app removes those leftovers after the update (PaketTemizleyici)."""
    gone = pristine_packages(pristine_file, list_dir) - tree_packages(
        {"sys": sys_dir, "sysext": sysext_dir, "prod": prod_dir})
    listed = set(removed_packages(pristine_file, removed_apps, list_dir))
    if not listed <= gone:
        raise SystemExit(f"REMOVED_APPS packages still in the trees: {sorted(listed - gone)}")
    etc = os.path.join(sys_dir, "system", "etc", "gbos-guncelleyici")
    _write(os.path.join(etc, "kaldirilan-paketler"),
           "# Packages the pristine images ship and this image does not (yamalar/guncelleyici.py\n"
           "# install_removed); their leftover Play updates are removed after an update\n"
           "# (com.gbos.guncelleyici PaketTemizleyici).\n"
           + "".join(p + "\n" for p in sorted(gone)))
    print(f"[+] Packages the image no longer ships: {', '.join(sorted(gone))}")


def removed_packages(pristine_file, removed_apps, list_dir=pristine_ls):
    """Package names of every APK REMOVED_APPS takes out of the image, sorted."""
    names = set()
    for key, paths in removed_apps.items():
        image = IMAGES[key]
        prefix = "system/" if key == "sys" else ""
        for rel in paths:
            rel = prefix + rel
            if rel.endswith(".apk"):
                apks = [rel]
            elif rel.endswith(".xml"):
                continue
            else:
                apks = [f"{rel}/{n}" for n in list_dir(rel, image) if n.endswith(".apk")]
                if not apks:
                    raise SystemExit(f"{rel}: no APK in the pristine {image}")
            for apk in apks:
                names.add(apk_package(pristine_file(apk, image)))
    return sorted(names)


def _replace_block(path, pattern, block):
    with open(path) as f:
        text = f.read()
    text = pattern.sub("", text).rstrip("\n") + "\n"
    with open(path, "w") as f:
        f.write(text + "\n" + block)


PROPERTY_CONTEXTS = "etc/selinux/plat_property_contexts"


def install_property_contexts(sys_dir):
    """persist.vold.dont_commit_checkpoint gets gbos_vold_commit_prop (the updater sets it, vold
    reads it), one block at the end of plat_property_contexts (replaced, not stacked)."""
    with open(os.path.join(CIHAZ, "property_contexts")) as f:
        _replace_block(os.path.join(sys_dir, "system", PROPERTY_CONTEXTS), FC_BLOCK, f.read())


def install_file_contexts(sys_dir):
    """The updater's labels as one block at the end of plat_file_contexts (replaced, not stacked)."""
    with open(os.path.join(CIHAZ, "file_contexts")) as f:
        _replace_block(os.path.join(sys_dir, "system", FILE_CONTEXTS), FC_BLOCK, f.read())


def install(sys_dir, abi="arm64"):
    """Installs the updater into the system tree at sys_dir (system-as-root: sys_dir/system)."""
    system = os.path.join(sys_dir, "system")
    built = os.path.join(GUNCELLEYICI, "out", abi, "system")
    for rel in BUILT:
        mode = 0o755 if rel.startswith("bin/") else 0o644
        _copy(os.path.join(built, rel), os.path.join(system, rel), mode)
    for rel, mode in SOURCES.items():
        _copy(os.path.join(CIHAZ, "system", rel), os.path.join(system, rel), mode)
    etc = os.path.join(system, "etc", "gbos-guncelleyici")
    _write(os.path.join(etc, "yuvalar.conf"), slots_conf())
    ty = tablete_yaz()
    with open(os.path.join(etc, "super-baslik.bin"), "wb") as f:
        f.write(ty.BASLIK.header(ty.HEADER))
    os.chmod(os.path.join(etc, "super-baslik.bin"), 0o644)
    install_file_contexts(sys_dir)
    install_property_contexts(sys_dir)
    print(f"[+] Installed the updater (guncelleyici/BENIOKU.md), super header {ty.HEADER}")


if __name__ == "__main__":
    sys.exit("normally called from apply_patches.py (see the docstring)")
