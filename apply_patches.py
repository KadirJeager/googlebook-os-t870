#!/usr/bin/env python3
import os
import re
import sys
import zipfile
import glob
import hashlib
import io
import shutil
import struct
import uuid
import subprocess
import zlib
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True  # no __pycache__ left next to the tools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yamalar import selinux as gbos_selinux  # noqa: E402
from yamalar import mica_servisleri  # noqa: E402
from yamalar import terminal as gbos_terminal  # noqa: E402
from yamalar import kamera_jpeg as gbos_kamera_jpeg  # noqa: E402
from yamalar import pil_sagligi as gbos_pil_sagligi  # noqa: E402
from yamalar import dogal_yon as gbos_dogal_yon  # noqa: E402
from yamalar import dogal_yon_hal as gbos_dogal_yon_hal  # noqa: E402
from yamalar import aosp_terminal as gbos_aosp_terminal  # noqa: E402
from yamalar import guncelleyici as gbos_guncelleyici  # noqa: E402
from yamalar import tablet_modu as gbos_tablet_modu  # noqa: E402
from yamalar import kit_imaj as gbos_kit_imaj  # noqa: E402
from yamalar import hazir_ikili  # noqa: E402

# The work tree lives on the NVMe disk (never in /tmp); GBOS_WORK points the script at another
# copy of it, e.g. a reflink copy used to check that pristine images + patches == working tree.
WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_FACE = os.path.join(TOOLS_DIR, "yuz-tanima")
FACE_OUTPUTS = ["out/FaceUnlock.apk", "out/sense-face-hal.jar", "out/FaceUnlockOverlay.apk"]
# What yuz-tanima/getir.sh downloads (not in the repository): the patched FaceUnlock checkout and
# the compilers yuz-tanima/build.sh uses
FACE_FETCHED = ["FaceUnlock/AndroidManifest.xml", "tools/kotlinc/bin/kotlinc", "tools/dex-tools-v2.4"]
MICA_IMAGES = os.path.join(TOOLS_DIR, "mica-imaj", "guncel")  # release in use, see mica-indir.py
EROFS_BIN = os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin"))
# Android SDK (ANDROID_HOME, as the SDK tools name it, or this project's older ANDROID_SDK) and the
# build-tools version the pipeline is pinned to
SDK = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK") or os.path.expanduser("~/Android/Sdk")
BUILD_TOOLS = os.environ.get("ANDROID_BUILD_TOOLS") or os.path.join(SDK, "build-tools", "37.0.0")
ZIPALIGN = os.environ.get("ZIPALIGN", os.path.join(BUILD_TOOLS, "zipalign"))
DEXDUMP = os.environ.get("DEXDUMP", os.path.join(BUILD_TOOLS, "dexdump"))
APKSIGNER = os.environ.get("APKSIGNER", os.path.join(BUILD_TOOLS, "apksigner"))
# The project's app signing key (anahtar-olustur.sh creates it; never on the tablet).
# Every APK apply_patches.py changes is re-signed with it: the Google signature no longer matches.
APP_KEYSTORE = os.path.join(os.environ.get("GBOS_ANAHTAR", os.path.expanduser("~/.android-certs/googlebookos-t870")),
                            "uygulama.p12")
APP_KEY_ALIAS, APP_KEY_PASS = "uygulama", "gbos-uygulama"
# The adb keys the image trusts (/product/etc/security/adb_keys): the extra keys in adb_keys.ek
# next to the signing key (other computers, Termux on the tablet; optional, one per line), then
# the building computer's own ~/.android/adbkey.pub. The tablet's kit has neither and uses the
# copy the computer wrote into WORK, which it carries like the other inputs below.
ADB_KEYS_EXTRA = os.path.join(os.path.dirname(APP_KEYSTORE), "adb_keys.ek")
ADB_KEY_OWN = os.path.join(os.path.expanduser("~"), ".android", "adbkey.pub")
ADB_KEYS = os.path.join(WORK, "adb_keys")
# Every other file the image takes from LineageOS, under <partition>/<path in the partition>
# (lineage-hazirla.py reads them out of the zip the tablet was flashed with).
LINEAGE_PARTS = os.path.join(WORK, "lineage/bolumler")
STAGE_32 = os.path.join(WORK, "lineage/lib32_stage")
# The 32-bit vendor libraries apply_patches.py patches, taken from the LineageOS vendor partition the
# tablet runs (lineage-dosya-cikar.py; identical to the tablet's /vendor/lib, see its KAYNAK file).
STAGE_VENDOR_32 = os.path.join(WORK, "lineage/vendor_stage")
# Everything this script reads from WORK besides the partition trees. None of it depends on the
# Googlebook OS release: yeni-surum-hazirla.py carries it into a new release's work directory and
# yama-dogrula.py into its scratch copy, both from this list.
# Where the metadata helpers preloaded into fsck.erofs/mkfs.erofs live: the work directory on the
# computer; on the tablet the Linux environment's own /var/lib/gbos-guncelleyici, because that
# environment may not map code from /data/gbos-ota (gbos_ota_data_file has no execute for
# gbos_linux; guncelleyici/motor/motor.py sets GBOS_META_LIB_DIR).
META_LIB_DIR = os.environ.get("GBOS_META_LIB_DIR", WORK)
META_LIBS = ("libcapture_meta.so", "libprovide_meta.so")
RELEASE_INDEPENDENT_INPUTS = ["libcapture_meta.so", "libprovide_meta.so", "lineage/lib32_stage",
                              "lineage/vendor_stage", "lineage/vendor_features",
                              "lineage/vendor_dogal_yon", "lineage/bolumler", "adb_keys"]

SYS_DIR = os.path.join(WORK, "sys")
SYSEXT_DIR = os.path.join(WORK, "sysext")
PROD_DIR = os.path.join(WORK, "prod")
ORIG_DIR = os.path.join(WORK, "orig")
VENDOR_BINDER_OVERRIDE = "system/lib/gbos-vendor-override"

SKIP_32_LIBS = {
    "libhwui.so",
    "libpdfium.so",
    "libGLESv2_angle.so",
    "libinputflinger.so",
    "libclang_rt.asan-arm-android.so",
    "libandroid_runtime.so",
    "service.incremental.so",
}

def adb_keys():
    """WORK/adb_keys, written from ADB_KEYS_EXTRA and ADB_KEY_OWN on the computer; the tablet's
    kit (GBOS_KIT) uses the carried copy."""
    if os.environ.get("GBOS_KIT"):
        if not os.path.isfile(ADB_KEYS):
            raise SystemExit(f"{ADB_KEYS} missing: the kit must carry it (kit-uret.py uret)")
        return ADB_KEYS
    if not os.path.isfile(ADB_KEY_OWN):
        raise SystemExit(f"{ADB_KEY_OWN} missing: run `adb start-server` once to create this "
                         "computer's adb key (the image trusts it for USB debugging)")
    keys = []
    for path in (ADB_KEYS_EXTRA, ADB_KEY_OWN):
        if os.path.isfile(path):
            with open(path) as f:
                keys += [line.strip() for line in f if line.strip() and not line.startswith("#")]
    for key in keys:
        if not key.startswith("QAAAA"):  # base64 of adb's RSA public key header
            raise SystemExit(f"not an adb public key: {key[:40]}...")
    data = "\n".join(keys) + "\n"
    old = open(ADB_KEYS).read() if os.path.exists(ADB_KEYS) else None
    if old != data:
        with open(ADB_KEYS, "w") as f:
            f.write(data)
    return ADB_KEYS


def comment_out_prop(text, line):
    """Comments out an exact build.prop line; idempotent (never stacks '#')."""
    return re.sub(rf"^#*{re.escape(line)}$", "#" + line, text, flags=re.M)


def set_prop(text, key, value):
    """Sets an existing build.prop property in place; the key must already be present."""
    new, n = re.subn(rf"^{re.escape(key)}=.*$", f"{key}={value}", text, flags=re.M)
    assert n == 1, f"build.prop: expected exactly one {key}= line, found {n}"
    return new


PLAT_FILE_CONTEXTS = "system/etc/selinux/plat_file_contexts"
FC_BLOCK_BEGIN = "# --- GOOGLEBOOKOS_T870_PATCH: apply_patches.py ---"
FC_BLOCK_END = "# --- GOOGLEBOOKOS_T870_PATCH: apply_patches.py end ---"
# Every label apply_patches.py itself adds to plat_file_contexts. The block is always written
# from this list, so an entry dropped here also disappears from an already patched tree.
PLAT_FC_ENTRIES = (
    "/system/bin/netbpfload u:object_r:bpfloader_exec:s0",
    "/system/lib/bootstrap(/.*)? u:object_r:system_lib_file:s0",
)


def add_plat_file_contexts(*entries):
    """Writes apply_patches.py's own block (PLAT_FC_ENTRIES) at the end of plat_file_contexts;
    each caller names the entries it relies on, which must be in that list.

    The block is rewritten sorted from the list, so the file does not depend on the order of the
    calls or on whether the tree was patched before. Copies of the entries outside the block, left by older
    versions of this script, are dropped together with the blank line those versions wrote before
    each of them. Modules (yamalar/) keep their own blocks after it."""
    unknown = set(entries) - set(PLAT_FC_ENTRIES)
    assert not unknown, f"add {sorted(unknown)} to PLAT_FC_ENTRIES"
    path = os.path.join(SYS_DIR, PLAT_FILE_CONTEXTS)
    with open(path) as f:
        lines = f.read().split("\n")
    block = set(PLAT_FC_ENTRIES)
    if FC_BLOCK_BEGIN in lines:
        b, e = lines.index(FC_BLOCK_BEGIN), lines.index(FC_BLOCK_END)
        del lines[b:e + 1]
        while b and lines[b - 1] == "":  # the blank line an older version left before the block
            b -= 1
            del lines[b]
    kept = []
    for line in lines:
        if line in block:
            if kept and kept[-1] == "":
                kept.pop()
            continue
        kept.append(line)
    lines = kept
    while lines and lines[-1] == "":
        lines.pop()
    lines += [FC_BLOCK_BEGIN, *sorted(block), FC_BLOCK_END, ""]
    with open(path, "w") as f:
        f.write("\n".join(lines))


def pristine_file(rel, image="system_a.erofs"):
    """Bytes of a file as shipped in the pristine Googlebook OS image (mica-imaj)."""
    return subprocess.run(
        [os.path.join(EROFS_BIN, "dump.erofs"), "--cat", f"--path=/{rel}",
         os.path.join(MICA_IMAGES, image)],
        check=True, stdout=subprocess.PIPE).stdout


def apply_byte_patches(data, patches, what):
    """Replaces each (old, new) pattern, which must occur exactly once."""
    for old, new in patches:
        assert len(old) == len(new), f"{what}: patch changes length"
        n = data.count(old)
        assert n == 1, f"{what}: expected 1 match for {old.hex()}, found {n}"
        data = data.replace(old, new)
    return data


def fix_dex_header(dex):
    """Recomputes the SHA-1 signature and Adler-32 checksum of a modified dex file."""
    dex = bytearray(dex)
    dex[12:32] = hashlib.sha1(bytes(dex[32:])).digest()
    struct.pack_into("<I", dex, 8, zlib.adler32(bytes(dex[12:])) & 0xFFFFFFFF)
    return bytes(dex)


def rewrite_jar_entries(src_jar, dst_jar, replacements):
    """Copies a jar entry by entry (same order, compression and stored-entry alignment),
    replacing the data of the named entries; a name the jar does not have is appended as a
    stored entry (dated like the jar's first entry, so the result is reproducible)."""
    tmp = dst_jar + ".tmp"
    with zipfile.ZipFile(src_jar) as zin, zipfile.ZipFile(tmp, "w") as zout:
        infos = zin.infolist()
        names = {i.filename for i in infos}
        for info in infos:
            data = replacements.get(info.filename, zin.read(info.filename))
            ni = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            ni.compress_type = info.compress_type
            ni.external_attr = info.external_attr
            ni.create_system = info.create_system
            zout.writestr(ni, data)
        for name in sorted(n for n in replacements if n not in names):
            ni = zipfile.ZipInfo(name, date_time=infos[0].date_time)
            ni.compress_type = zipfile.ZIP_STORED
            ni.external_attr = infos[0].external_attr
            ni.create_system = infos[0].create_system
            zout.writestr(ni, replacements[name])
    subprocess.run([ZIPALIGN, "-f", "-p", "4", tmp, dst_jar], check=True, stdout=subprocess.DEVNULL)
    os.remove(tmp)


def sign_apk(src_apk, dst_apk):
    """Signs an already zipaligned APK with the project's app key (v2 + v3 only: RSA PKCS#1 v1.5
    signatures carry no timestamp, so the same input gives the same bytes and yama-dogrula.py
    can reproduce the tree; v1 would add dated META-INF entries, v4 a separate .idsig file)."""
    assert os.path.exists(APP_KEYSTORE), f"app signing key missing: {APP_KEYSTORE} (anahtar-olustur.sh)"
    subprocess.run([APKSIGNER, "sign", "--ks", APP_KEYSTORE, "--ks-key-alias", APP_KEY_ALIAS,
                    "--ks-pass", f"pass:{APP_KEY_PASS}", "--key-pass", f"pass:{APP_KEY_PASS}",
                    "--v1-signing-enabled", "false", "--v2-signing-enabled", "true",
                    "--v3-signing-enabled", "true", "--v4-signing-enabled", "false",
                    "--out", dst_apk, src_apk], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([APKSIGNER, "verify", dst_apk], check=True, stdout=subprocess.DEVNULL)
    os.chmod(dst_apk, 0o644)


def meta_root(meta_path):
    """Root directory a *.meta capture was recorded from: its ROOT line, or for older
    captures the path of the first entry (which was the root itself)."""
    with open(meta_path) as f:
        return f.readline().rstrip("\n").split("\t")[1]


def patch_netbpfload():
    src = os.path.join(WORK, "teth_clean/bin/netbpfload")
    dst_sys = os.path.join(SYS_DIR, "system/bin/netbpfload")
    dst_teth = os.path.join(WORK, "teth/bin/netbpfload")
    with open(src, "rb") as f:
        data = bytearray(f.read())

    # 0) Patch 0x99a4: 54002181 (b.ne 0x9dd4, st_dev check between /system/bin/netbpfload and /apex/com.android.tethering) -> d503201f (nop)
    assert data[0x99a4:0x99a8] == bytes.fromhex("81210054"), f"Unexpected bytes at 0x99a4: {data[0x99a4:0x99a8].hex()}"
    data[0x99a4:0x99a8] = bytes.fromhex("1f2003d5")

    # 1) Patch 0xa6b0: 54000188 (b.hi 0xa6e0) -> 1400000c (b 0xa6e0)
    assert data[0xa6b0:0xa6b4] == bytes.fromhex("88010054"), f"Unexpected bytes at 0xa6b0: {data[0xa6b0:0xa6b4].hex()}"
    data[0xa6b0:0xa6b4] = bytes.fromhex("0c000014")

    # 2) Patch 0xb140: 360002c0 (tbz w0, #0x0, 0xb198) -> 14000016 (b 0xb198, transfer control directly to /system/bin/bpfloader)
    assert data[0xb140:0xb144] == bytes.fromhex("c0020036"), f"Unexpected bytes at 0xb140: {data[0xb140:0xb144].hex()}"
    data[0xb140:0xb144] = bytes.fromhex("16000014")

    # 3) Patch 0x11e10..0x11e28: set w8 = 0x050a00ef (5.10.239)
    expected = bytes.fromhex("081d08530841092b62280054e90b40b90801092b22280054")
    assert data[0x11e10:0x11e28] == expected, f"Unexpected bytes at 0x11e10: {data[0x11e10:0x11e28].hex()}"
    patch_insns = (
        struct.pack("<I", 0x52801de8) +  # movz w8, #0x00ef
        struct.pack("<I", 0x72a0a148) +  # movk w8, #0x050a, lsl #16
        struct.pack("<I", 0xd503201f) * 4
    )
    data[0x11e10:0x11e28] = patch_insns

    for dst, mode in [(dst_sys, 0o755), (dst_teth, 0o750)]:
        with open(dst, "wb") as f:
            f.write(data)
        os.chmod(dst, mode)
    print("[+] Patched netbpfload in /system/bin and /apex/com.android.tethering/bin for kernel 4.19 (st_dev nop, kver=5.10.239, direct bpfloader)")

TETHERING_APEX = "system/apex/com.google.android.tethering.apex"


def _apex_payload_layout(apex):
    """Locates apex_payload.img (a stored zip entry) and its AVB footer inside an APEX."""
    info = zipfile.ZipFile(io.BytesIO(apex)).getinfo("apex_payload.img")
    assert info.compress_type == zipfile.ZIP_STORED, "apex_payload.img must be stored"
    n, m = struct.unpack_from("<HH", apex, info.header_offset + 26)
    data_off = info.header_offset + 30 + n + m
    payload = apex[data_off:data_off + info.file_size]
    magic, _maj, _min, image_size, _vb_off, _vb_size = struct.unpack(">4sIIQQQ", payload[-64:-28])
    assert magic == b"AVBf", "AVB footer not found in apex_payload.img"
    eocd = apex.rfind(b"PK\x05\x06")
    pos = struct.unpack_from("<I", apex, eocd + 16)[0]
    cd_crc = None
    while apex[pos:pos + 4] == b"PK\x01\x02":
        nl, el, cl = struct.unpack_from("<HHH", apex, pos + 28)
        if apex[pos + 46:pos + 46 + nl] == b"apex_payload.img":
            cd_crc = pos + 16
        pos += 46 + nl + el + cl
    assert cd_crc is not None, "apex_payload.img missing from the central directory"
    return {"data_off": data_off, "size": info.file_size, "image_size": image_size,
            "local_crc": info.header_offset + 14, "cd_crc": cd_crc,
            "uuid": str(uuid.UUID(bytes=payload[1024 + 48:1024 + 64]))}


def _extract_with_meta(erofs_path, dest, meta_path):
    """Extracts an EROFS image as an unprivileged user, recording ownership, modes and xattrs
    (security.selinux, capabilities) in meta_path through libcapture_meta.so."""
    for p in (dest, meta_path):
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)
    with open(meta_path, "w") as f:
        f.write(f"ROOT\t{dest}\n")  # capture_meta.so appends after this line
    env = dict(os.environ, LD_PRELOAD=os.path.join(META_LIB_DIR, "libcapture_meta.so"), META_LOG=meta_path)
    subprocess.run([os.path.join(EROFS_BIN, "fsck.erofs"), f"--extract={dest}", "--preserve",
                    "--xattrs", erofs_path], env=env, check=True, stdout=subprocess.DEVNULL)
    with open(meta_path) as f:
        kinds = {line.split("\t", 1)[0] for line in f}
    assert {"CHOWN", "CHMOD", "XATTR"} <= kinds, f"{meta_path}: incomplete capture {kinds}"


# The pristine partition trees of the work directory and the images they come from
TREES = [("system_a.erofs", "sys"), ("system_ext_a.erofs", "sysext"), ("product_a.erofs", "prod")]


def extract_trees(dest=WORK, images=MICA_IMAGES):
    """Extracts the Googlebook OS release in `images` into dest/{sys,sysext,prod} with their
    ownership, modes and xattrs (dest/<tree>.meta), and records the release in dest/SURUM."""
    import json
    with open(os.path.join(images, "kaynak.json")) as f:
        build = json.load(f)["release"]["buildidentifier"]
    for image, tree in TREES:
        print(f"[+] {image} -> {tree} (+ {tree}.meta)", flush=True)
        _extract_with_meta(os.path.join(images, image), os.path.join(dest, tree),
                           os.path.join(dest, f"{tree}.meta"))
    with open(os.path.join(dest, "SURUM"), "w") as f:
        f.write(build + "\n")
    return build


def ensure_trees():
    """A work directory without partition trees (the first build on a computer) gets them from
    mica-imaj/guncel. One with only some of them is refused: it is half of an extraction."""
    have = [t for _, t in TREES if os.path.isdir(os.path.join(WORK, t))]
    if len(have) == len(TREES):
        return
    if have:
        raise SystemExit(f"{WORK} has only {', '.join(have)} of the partition trees; remove them "
                         "or use a new GBOS_WORK")
    print(f"[+] {WORK}: no partition trees yet, extracting {os.path.realpath(MICA_IMAGES)}", flush=True)
    extract_trees()


def patch_tethering_apex():
    # Pristine APEX straight from the Googlebook OS image; its payload is extracted afresh on
    # every run, so a new release never reuses the previous release's tree.
    apex = pristine_file(TETHERING_APEX)
    lay = _apex_payload_layout(apex)
    payload = apex[lay["data_off"]:lay["data_off"] + lay["size"]]
    pristine_img = os.path.join(WORK, "teth_payload.img")
    with open(pristine_img, "wb") as f:
        f.write(payload[:lay["image_size"]])
    teth_clean = os.path.join(WORK, "teth_clean")
    teth_meta = os.path.join(WORK, "teth.meta")
    _extract_with_meta(pristine_img, teth_clean, teth_meta)

    teth_dir = os.path.join(WORK, "teth")
    if os.path.exists(teth_dir):
        shutil.rmtree(teth_dir)
    shutil.copytree(teth_clean, teth_dir, symlinks=True)

    patch_netbpfload()

    p1_old = bytes.fromhex("083d10530861090b08010a0b")
    p1_new = bytes.fromhex("e81d805248a1a0721f2003d5")

    p2_old = bytes.fromhex("083d105300e4006f0861090b08010a0b")
    p2_new = bytes.fromhex("e81d805200e4006f48a1a0721f2003d5")

    p3_old = bytes.fromhex("293d1053e0e7076f2861080b08010a0b")
    p3_new = bytes.fromhex("e81d8052e0e7076f48a1a0721f2003d5")

    targets = {
        "lib64/libandroid_net_connectivity_com_android_net_module_util_jni.so": (2, 0, 0),
        "lib64/libframework-connectivity-jni.so": (1, 0, 0),
        "lib64/libnetd_resolv.so": (1, 0, 1),
        "lib64/libnetd_updatable.so": (1, 1, 0),
        "lib64/libnetworkstats.so": (1, 2, 0),
        "lib64/libservice-connectivity.so": (1, 7, 0),
    }

    for rel, (c1, c2, c3) in targets.items():
        p = os.path.join(teth_dir, rel)
        with open(p, "rb") as f:
            d = f.read()
        assert d.count(p1_old) == c1, f"{rel}: p1 {d.count(p1_old)} != {c1}"
        assert d.count(p2_old) == c2, f"{rel}: p2 {d.count(p2_old)} != {c2}"
        assert d.count(p3_old) == c3, f"{rel}: p3 {d.count(p3_old)} != {c3}"
        d = d.replace(p1_old, p1_new).replace(p2_old, p2_new).replace(p3_old, p3_new)
        with open(p, "wb") as f:
            f.write(d)

    new_erofs = os.path.join(WORK, "teth_new_payload.img")
    if os.path.exists(new_erofs):
        os.remove(new_erofs)
    env = os.environ.copy()
    env["LD_PRELOAD"] = os.path.join(META_LIB_DIR, "libprovide_meta.so")
    env["META_LOG"] = teth_meta
    env["META_OLD_PREFIX"] = meta_root(teth_meta)
    env["META_NEW_PREFIX"] = teth_dir
    subprocess.run(
        [
            os.path.join(EROFS_BIN, "mkfs.erofs"),
            "-zlz4hc",
            "-b", "4096",
            "-T", "0",
            "-U", lay["uuid"],
            new_erofs,
            teth_dir,
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    with open(new_erofs, "rb") as f:
        erofs_data = f.read()
    erofs_area = lay["image_size"]
    assert len(erofs_data) <= erofs_area, f"New EROFS too large: {len(erofs_data)} > {erofs_area}"
    # apexd mounts the pre-installed APEX without dm-verity (no ro.boot.veritymode) and only
    # verifies the signed vbmeta, so the original hashtree + vbmeta + AVB footer are kept.
    full_payload = erofs_data.ljust(erofs_area, b"\x00") + payload[erofs_area:]
    assert len(full_payload) == lay["size"]

    apex_raw = bytearray(apex)
    new_crc = struct.pack("<I", zlib.crc32(full_payload) & 0xffffffff)
    apex_raw[lay["data_off"]:lay["data_off"] + lay["size"]] = full_payload
    apex_raw[lay["local_crc"]:lay["local_crc"] + 4] = new_crc
    apex_raw[lay["cd_crc"]:lay["cd_crc"] + 4] = new_crc

    dst_apex = os.path.join(SYS_DIR, TETHERING_APEX)
    with open(dst_apex, "wb") as f:
        f.write(apex_raw)
    os.chmod(dst_apex, 0o644)
    print(f"[+] Rebuilt com.google.android.tethering.apex (EROFS={len(erofs_data)} bytes, CRC32={new_crc.hex()})")

def install_32bit_runtime():
    os.makedirs(ORIG_DIR, exist_ok=True)
    # 1) Move unused Dell/Purwa firmware images out of /system/etc to free ~89.4 MB in system.img
    for fw in ["bluetooth.img", "dsp.img", "modem.img"]:
        src_fw = os.path.join(SYS_DIR, "system/etc", fw)
        dst_fw = os.path.join(ORIG_DIR, fw)
        if os.path.exists(src_fw):
            shutil.move(src_fw, dst_fw)
            print(f"[+] Moved unused {fw} to {dst_fw}")

    # 2) Patch 32-bit linker to read /system/etc/ld.config.32 instead of /linkerconfig/ld.config.txt
    src_linker = os.path.join(STAGE_32, "bin/linker")
    with open(src_linker, "rb") as f:
        ldata = f.read()
    old_cfg = b"/linkerconfig/ld.config.txt\x00"
    new_cfg = b"/system/etc/ld.config.32\x00\x00\x00\x00"
    assert len(old_cfg) == len(new_cfg)
    assert old_cfg in ldata, "Could not find /linkerconfig/ld.config.txt in 32-bit linker"
    ldata_patched = ldata.replace(old_cfg, new_cfg)

    for rel_dst in ["system/bin/linker", "system/bin/bootstrap/linker"]:
        dst_p = os.path.join(SYS_DIR, rel_dst)
        os.makedirs(os.path.dirname(dst_p), exist_ok=True)
        if os.path.islink(dst_p) or os.path.exists(dst_p):
            os.remove(dst_p)
        with open(dst_p, "wb") as f:
            f.write(ldata_patched)
        os.chmod(dst_p, 0o755)

    # 3) Write /system/etc/ld.config.32 for the 32-bit vendor HALs (audio, c2, widevine, cas).
    # It follows the [vendor] section linkerconfig generates for the 64-bit vendor processes:
    # the vendor namespace is isolated and takes only the LLNDK libraries from the system
    # namespace. Each side then loads its own libbinder: the vendor one (talks to vndservicemanager
    # with the VNDR header) and the system one behind libbinder_ndk (servicemanager, SYST). A
    # single shared copy, as an earlier flat configuration had, broke one of the two: codec2's
    # AIDL allocator lookup failed, its fallback path called mremap(MREMAP_MAYMOVE), which the
    # vendor seccomp policy forbids, and the encoder died (acilis-loglari/codec2-20261022).
    # The vendor copies of libbinder/libhidlbase abort with "threadpool cannot be shrunk" on
    # Googlebook OS, so patched copies of just those two live in VENDOR_BINDER_OVERRIDE, first in
    # the vendor namespace. (A bind mount over /vendor/lib is refused by the Samsung kernel
    # during early boot.) mica's runtime APEX is 64-bit only: bionic comes from /system/lib and
    # /system/lib/bootstrap.
    llndk = ["libEGL.so", "libGLESv1_CM.so", "libGLESv2.so", "libGLESv3.so", "libRS.so",
             "libandroid_net.so", "libapexsupport.so", "libbinder_ndk.so", "libc.so",
             "libcamera_metadata.so", "libcgrouprc.so", "libdl.so", "libft2.so", "liblog.so",
             "libm.so", "libmediandk.so", "libnativewindow.so", "libnpumanager.so",
             "libselinux.so", "libsync.so", "libvendorsupport.so", "libvndksupport.so",
             "libvulkan.so", "libwrapfd.so", "libdl_android.so", "libfdtrack.so",
             "libclang_rt.asan-arm-android.so", "libclang_rt.hwasan-arm-android.so",
             "libclang_rt.ubsan_standalone-arm-android.so"]
    system_paths = "/system/lib:/system/lib/bootstrap:/system/lib/hw:/system_ext/lib:/system_ext/lib/hw"
    ld_cfg_content = "\n".join([
        "dir.vendor = /odm/bin/",
        "dir.vendor = /vendor/bin/",
        "dir.system = /system/bin/",
        "dir.system = /system_ext/bin/",
        "dir.system = /product/bin/",
        "",
        "[vendor]",
        "additional.namespaces = system",
        "namespace.default.isolated = true",
        "namespace.default.visible = true",
        f"namespace.default.search.paths = /{VENDOR_BINDER_OVERRIDE}:/odm/lib:/odm/lib/hw:"
        "/vendor/lib:/vendor/lib/hw:/vendor/lib/egl",
        f"namespace.default.permitted.paths = /{VENDOR_BINDER_OVERRIDE}:/odm:/vendor:/system/vendor",
        "namespace.default.links = system",
        "namespace.default.link.system.shared_libs = " + ":".join(llndk),
        "namespace.system.isolated = false",
        f"namespace.system.search.paths = {system_paths}",
        "",
        "[system]",
        "namespace.default.isolated = false",
        f"namespace.default.search.paths = {system_paths}:/product/lib",
        "",
    ])
    ld_cfg_path = os.path.join(SYS_DIR, "system/etc/ld.config.32")
    with open(ld_cfg_path, "w") as f:
        f.write(ld_cfg_content)
    os.chmod(ld_cfg_path, 0o644)

    # 4) Copy 32-bit libraries into /system/lib
    src_lib_root = os.path.join(STAGE_32, "system_lib/lib")
    dst_lib_root = os.path.join(SYS_DIR, "system/lib")
    if os.path.exists(dst_lib_root):
        shutil.rmtree(dst_lib_root)
    os.makedirs(dst_lib_root, exist_ok=True)

    copied_count = 0
    for root, dirs, files in os.walk(src_lib_root):
        rel_dir = os.path.relpath(root, src_lib_root)
        target_dir = dst_lib_root if rel_dir == "." else os.path.join(dst_lib_root, rel_dir)
        os.makedirs(target_dir, exist_ok=True)
        for fl in files:
            if fl in SKIP_32_LIBS:
                continue
            src_file = os.path.join(root, fl)
            dst_file = os.path.join(target_dir, fl)
            shutil.copy2(src_file, dst_file)
            os.chmod(dst_file, 0o644)
            copied_count += 1
    # 5) Label the 32-bit bionic in /system/lib/bootstrap as an ordinary system library. mica's
    # runtime APEX is 64-bit only, so it is the libc, libm and libdl the 32-bit vendor HALs map
    # (hal_audio_default, mediacodec, vendor_hal_drm_widevine, hal_cas_default; seen in
    # /proc/*/maps). plat_file_contexts gives the directory system_bootstrap_lib_file, which only
    # early-boot domains may map: enforcing would then stop those HALs.
    add_plat_file_contexts("/system/lib/bootstrap(/.*)? u:object_r:system_lib_file:s0")
    print(f"[+] Installed 32-bit linker, /system/etc/ld.config.32, and {copied_count} 32-bit libraries in /system/lib")

def patch_binder_and_hidlbase_threadpool():
    # Prevent "Binder threadpool cannot be shrunk after starting" abort when a pre-started
    # threadpool (e.g. from audio/camera/drm libs loaded during init) is followed by
    # configureRpcThreadpool(1, true) / setThreadPoolMaxThreadCount(N) in HIDL/Binder services.
    # 32-bit libraries come from the LineageOS 32-bit runtime, 64-bit ones from Googlebook OS.
    stage_32_lib = os.path.join(STAGE_32, "system_lib/lib")

    def stage32(name):
        with open(os.path.join(stage_32_lib, name), "rb") as f:
            return f.read()

    def vendor32(name):
        with open(os.path.join(STAGE_VENDOR_32, name), "rb") as f:
            return f.read()

    sources = {
        "libbinder32.so": lambda: stage32("libbinder.so"),
        "libbinder32-vendor.so": lambda: vendor32("libbinder.so"),
        "libhidlbase32-vendor.so": lambda: vendor32("libhidlbase.so"),
        "libbinder64.so": lambda: pristine_file("system/lib64/libbinder.so"),
        "libhidlbase32.so": lambda: stage32("libhidlbase.so"),
        "libhidlbase64.so": lambda: pristine_file("system/lib64/libhidlbase.so"),
    }

    # Each entry: source, destination, [(old, new), ...] (every old pattern unique).
    patches = [
        (
            "libbinder32.so",
            os.path.join(SYS_DIR, "system/lib/libbinder.so"),
            [
                # threadpool shrink check
                (bytes.fromhex("012904d10099d4e8af2f914230d3"),
                 bytes.fromhex("012904d10099d4e8af2f914200bf")),
                # isVndservicemanagerEnabled() -> false: the 32-bit vendor audio HAL otherwise
                # waits for a vndservicemanager that Googlebook OS does not run.
                (bytes.fromhex("4e53fcff80b5044804217844"),
                 bytes.fromhex("4e53fcff0020704704217844")),
            ],
        ),
        (
            "libbinder64.so",
            os.path.join(SYS_DIR, "system/lib64/libbinder.so"),
            [(bytes.fromhex("28fddf081f050071c100005468220291e90340f908fddfc83f0108eb83040054"),
              bytes.fromhex("28fddf081f050071c100005468220291e90340f908fddfc83f0108eb1f2003d5"))],
        ),
        (
            "libhidlbase32.so",
            os.path.join(SYS_DIR, "system/lib/libhidlbase.so"),
            [(bytes.fromhex("95f83c00012802d16869844252d3"),
              bytes.fromhex("95f83c00012802d16869844200bf"))],
        ),
        # The vendor namespace's copies (ld.config.32): same threadpool checks, vendor flavour.
        (
            "libbinder32-vendor.so",
            os.path.join(SYS_DIR, VENDOR_BINDER_OVERRIDE, "libbinder.so"),
            [(bytes.fromhex("012904d10099d4e8af2f914230d3"),
              bytes.fromhex("012904d10099d4e8af2f914200bf"))],
        ),
        (
            "libhidlbase32-vendor.so",
            os.path.join(SYS_DIR, VENDOR_BINDER_OVERRIDE, "libhidlbase.so"),
            [(bytes.fromhex("95f83c00012802d16869844252d3"),
              bytes.fromhex("95f83c00012802d16869844200bf"))],
        ),
        (
            "libhidlbase64.so",
            os.path.join(SYS_DIR, "system/lib64/libhidlbase.so"),
            [(bytes.fromhex("08c042391f05007181000054682640f99f0208ebc3070054"),
              bytes.fromhex("08c042391f05007181000054682640f99f0208eb1f2003d5"))],
        ),
    ]
    override = os.path.join(SYS_DIR, VENDOR_BINDER_OVERRIDE)
    os.makedirs(override, exist_ok=True)
    for src_name, dst_path, byte_patches in patches:
        patched = apply_byte_patches(sources[src_name](), byte_patches, src_name)
        with open(dst_path, "wb") as f:
            f.write(patched)
        os.chmod(dst_path, 0o644)
    print("[+] Patched Binder & HIDL threadpool shrink check in 32/64-bit libbinder.so and libhidlbase.so")

def jar_byte_patches():
    """Byte patches per (jar file, dex entry); patch_framework and the tests share this table."""
    return {
        # Taskbar / desktop shell: three device checks of desktop-services return false.
        ("desktop-services.jar", "classes.dex"): [
            (bytes.fromhex("1a00369e71100d1000000c"), bytes.fromhex("1200110000000000000000")),
            (bytes.fromhex("2b0000007100b71300000a0039000a001a00cd"),
             bytes.fromhex("2b0000000e00000000000000000000001a00cd")),
            (bytes.fromhex("160000007100b71300000a0039000a001a00cd"),
             bytes.fromhex("160000000e00000000000000000000001a00cd")),
        ],
        # Companion device secure channel (CompanionTransportManager.createTransport): a
        # debuggable build authenticates with the fixed "CDM" pre-shared key, a user build with
        # attestation, and the two steps are mutually exclusive, so a user-build phone and this
        # debuggable tablet abort each other's handshake ("Expected ATTESTATION; Found
        # PRE_SHARED_KEY" on the phone, "Expected PRE_SHARED_KEY; Found ATTESTATION" here) and no
        # transport is ever built: no DND, task continuity or App Cast. ro.debuggable=1 stays for
        # adb root, gbos-guncelleyicid and gbos_linux; the if-eqz that would enter the test-key
        # branch becomes "goto" + "nop" over the same two code units, jumping straight to the
        # pre-shared-key/attestation path at 0x48.
        ("services.jar", "classes.dex"): [
            (bytes.fromhex("7100823f0000" "0a00" "38001a00"),
             bytes.fromhex("7100823f0000" "0a00" "281a" "0000")),
        ],
        # Window manager: freeform windows stay fully on screen after rotating
        # (Task.computeFreeformBounds / fitWithinBounds). DisplayRotation$LaptopController stays
        # as mica ships it: SLATE (SW_TABLET_MODE=1: keyboard detached, cover folded back or
        # "Tablet modu" tile on) enables auto-rotation; LID_OPEN (laptop posture) locks landscape.
        ("services.jar", "classes3.dex"): [
            (bytes.fromhex("310015034042c8038733"), bytes.fromhex("310015037f7fc8038733")),
            (bytes.fromhex("873315040042c8048744"), bytes.fromhex("873315047f7fc8048744")),
            (bytes.fromhex("12026e10e00c06000a"), bytes.fromhex("12026e10cc0c06000a")),
            # Companion device peer attestation (AttestationVerificationPeerDeviceVerifier,
            # verifyAttestationInternal): this tablet is rooted with an unlocked bootloader, so the
            # phone's PROFILE_PEER_DEVICE verifier rejects its attestation with
            # FLAG_FAILURE_BOOT_STATE and the CDM secure channel never forms (no DND/tasks/App Cast;
            # "Remote device failed to verify local attestation" on the phone). The call to
            # checkAttestationForPeerDeviceProfile, whose result is ORed into the accumulator, becomes
            # `const/4 v12, 0` + nops; chain, binding and ownership checks still run. The phone side is
            # Root Flags' tr.rootflags.syshook hook plus its lnd.b App Cast hook (25 Oct 2026).
            (bytes.fromhex("70409f749ba7" "0a0c"), bytes.fromhex("120c00000000" "0000")),
        ],
    }


def patch_framework():
    """Patches applied to pristine files read straight from the Googlebook OS image."""
    lib64 = os.path.join(SYS_DIR, "system/lib64")
    framework = os.path.join(SYS_DIR, "system/framework")
    oat = os.path.join(framework, "oat/arm64")

    data = apply_byte_patches(pristine_file("system/lib64/libinputflinger.so"), [
        # InputDispatcher: policy flag fix for the tablet's touchscreen and S-Pen.
        (bytes.fromhex("00013fd6d5008052c0"), bytes.fromhex("00013fd695008052c0")),
    ], "libinputflinger.so")
    with open(os.path.join(lib64, "libinputflinger.so"), "wb") as f:
        f.write(data)

    jar_patches = jar_byte_patches()
    by_jar = {}
    for (jar, entry), byte_patches in jar_patches.items():
        by_jar.setdefault(jar, {})[entry] = byte_patches
    for jar, entries in by_jar.items():
        src = os.path.join(WORK, "denetim", f"pristine-{jar}")
        os.makedirs(os.path.dirname(src), exist_ok=True)
        with open(src, "wb") as f:
            f.write(pristine_file(f"system/framework/{jar}"))
        try:
            replacements = {}
            with zipfile.ZipFile(src) as z:
                for entry, byte_patches in entries.items():
                    replacements[entry] = apply_byte_patches(z.read(entry), byte_patches,
                                                             f"{jar}!{entry}")
                if jar == "services.jar":
                    fix_compile_time_feature_checks(z, replacements)
            # every entry is written once, so patches of several dex files of one jar all stay
            rewrite_jar_entries(src, os.path.join(framework, jar),
                                {e: fix_dex_header(d) for e, d in replacements.items()})
        finally:
            os.remove(src)
        # The prebuilt odex/vdex/art were compiled from the unpatched dex. ART rejects them at
        # runtime, but odrefresh only checks that they exist and would never recompile; without
        # them it compiles the patched jar into /data on boot.
        stem = jar[:-len(".jar")]
        for ext in ("odex", "vdex", "art"):
            for suffix in ("", ".fsv_meta"):
                p = os.path.join(oat, f"{stem}.{ext}{suffix}")
                if os.path.lexists(p):
                    os.remove(p)

    patch_ro_system_features(framework)

    # netbpfload is copied to /system/bin and must run in the bpfloader domain.
    add_plat_file_contexts("/system/bin/netbpfload u:object_r:bpfloader_exec:s0")
    print("[+] Patched libinputflinger, desktop-services.jar, services.jar (stale odex removed), file_contexts")


DEX_INSN = re.compile(r"^([0-9a-f]{6}): [0-9a-f ]+?\s*\|([0-9a-f]{4}): (.*)$")
DEX_METHOD = re.compile(r"^[0-9a-f]{6}:\s+\|\[[0-9a-f]+\] (.*)$")


def dexdump(dex):
    tmp = os.path.join(WORK, "denetim", f"dexdump-{os.getpid()}.dex")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(dex)
    try:
        # string constants are printed as they are stored (MUTF-8), not always valid UTF-8
        return subprocess.run([DEXDUMP, "-d", tmp], check=True,
                              capture_output=True).stdout.decode(errors="replace")
    finally:
        os.remove(tmp)


def dex_methods(dump):
    """{"pkg.Class.method:(proto)ret": [(file offset, instruction text), ...]} of a dexdump -d."""
    methods, current = {}, None
    for line in dump.splitlines():
        m = DEX_METHOD.match(line)
        if m:
            current = methods.setdefault(m.group(1), [])
            continue
        m = DEX_INSN.match(line)
        if m and current is not None:
            current.append((int(m.group(1), 16), m.group(3)))
    return methods


def dex_method_code(dex, cls, name, proto):
    """(offset of the first instruction, registers, code units) of a method in a dex file, read
    from dexdump so the patch follows the method when a new mica release moves it."""
    in_class = False
    lines = iter(dexdump(dex).splitlines())
    for line in lines:
        if line.startswith("Class #"):
            in_class = False
        elif line.strip() == f"Class descriptor  : '{cls}'":
            in_class = True
        elif in_class and line.strip() == f"name          : '{name}'" and \
                next(lines).strip() == f"type          : '{proto}'":
            regs = units = None
            for line in lines:
                m = re.match(r"\s+registers\s+: (\d+)", line)
                regs = int(m.group(1)) if m else regs
                m = re.match(r"\s+insns size\s+: (\d+)", line)
                units = int(m.group(1)) if m else units
                m = re.match(r"([0-9a-f]{6}): [0-9a-f ]+\|0000: ", line)
                if m:
                    return int(m.group(1), 16), regs, units
    return None


def dex_string_index(dex, text):
    """Index of `text` in the dex string pool (ASCII strings: MUTF-8 equals UTF-8)."""
    size, table = struct.unpack_from("<II", dex, 0x38)
    want = text.encode()
    for i in range(size):
        off = struct.unpack_from("<I", dex, table + 4 * i)[0]
        while dex[off] & 0x80:  # skip the uleb128 UTF-16 length
            off += 1
        off += 1
        if dex[off:off + len(want) + 1] == want + b"\0":
            return i
    return None


SYSTEM_SERVER_START = "com.android.server.SystemServer.startOtherServices:(Lcom/android/server/utils/TimingsTraceAndSlog;)V"
# hasSystemFeature(<compile-time feature>) calls whose consumer R8 deleted outright (the result is
# not read at all). Each is either rebuilt here or explained; a new one stops the build.
DROPPED_FEATURE_CHECKS = {
    (SYSTEM_SERVER_START, "android.hardware.wifi.rtt"): "rebuilt by restore_wifi_aware_rtt()",
    (SYSTEM_SERVER_START, "android.hardware.wifi.aware"): "rebuilt by restore_wifi_aware_rtt(); "
        "the second call (RangingService over Wi-Fi Aware only) stays dropped: RangingService "
        "still starts on UWB or Bluetooth LE channel sounding, and 12 code units do not fit in 9",
    ("com.android.server.net.NetworkPolicyManagerService.waitForAdminData:()V",
     "android.software.device_admin"): "boot no longer waits up to 10 s for device policy data; "
        "admin apps are read when DevicePolicyManagerService reports them",
    ("com.android.server.usage.AppStandbyController.waitForAdminData:()V",
     "android.software.device_admin"): "as NetworkPolicyManagerService: standby buckets of admin "
        "apps follow when DevicePolicyManagerService reports them",
}
RO_SYSTEM_FEATURES = ("Lcom/android/internal/pm/RoSystemFeatures;", "maybeHasFeature",
                      "(Ljava/lang/String;I)Ljava/lang/Boolean;")


def compile_time_features():
    """The features mica's framework.jar fixes at build time, read from the pristine
    RoSystemFeatures.maybeHasFeature (its const-strings), so a new release is followed."""
    with zipfile.ZipFile(io.BytesIO(pristine_file("system/framework/framework.jar"))) as z:
        for e in z.namelist():
            if not re.fullmatch(r"classes\d*\.dex", e):
                continue
            dex = z.read(e)
            if RO_SYSTEM_FEATURES[0].encode()[1:-1] not in dex:
                continue
            name = "com.android.internal.pm.RoSystemFeatures.maybeHasFeature:" + RO_SYSTEM_FEATURES[2]
            insns = dex_methods(dexdump(dex)).get(name)
            if insns:
                found = {m.group(1) for _, t in insns
                         for m in [re.match(r'const-string(?:/jumbo)? v\d+, "([^"]+)"', t)] if m}
                assert found, "RoSystemFeatures.maybeHasFeature has no feature strings"
                return found
    raise SystemExit("framework.jar: RoSystemFeatures.maybeHasFeature not found")


def feature_check_sites(methods, features):
    """(method, feature, index of the hasSystemFeature call, kind) for every call on a
    compile-time feature: kind is "used" (move-result), "folded" (const/4 vX, #0) or "dropped"."""
    sites = []
    for method, insns in methods.items():
        for i in range(1, len(insns)):
            if "PackageManager;.hasSystemFeature:(Ljava/lang/String;)Z" not in insns[i][1]:
                continue
            m = re.match(r'const-string(?:/jumbo)? v\d+, "([^"]+)"', insns[i - 1][1])
            if not m or m.group(1) not in features:
                continue
            nxt = insns[i + 1][1] if i + 1 < len(insns) else ""
            kind = ("used" if nxt.startswith("move-result ") else
                    "folded" if re.match(r"const/4 v\d+, #int 0 ", nxt) else "dropped")
            sites.append((method, m.group(1), i, kind))
    return sites


def fix_compile_time_feature_checks(z, replacements):
    """services.jar was compiled with the features of RoSystemFeatures fixed to "absent": R8 turned
    hasSystemFeature(<feature>) into a call whose result is replaced by false or thrown away
    (DevicePolicyManagerService never enabled itself, Wi-Fi Aware and RTT never started).
    A folded result is put back (const/4 vX, #0 -> move-result vX, same size); dropped ones are
    rebuilt or listed in DROPPED_FEATURE_CHECKS."""
    features = compile_time_features()
    vendor = vendor_features()
    unexplained, fixed = [], []
    seen_dropped = set()
    for entry in sorted(e for e in z.namelist() if re.fullmatch(r"classes\d*\.dex", e)):
        dex = bytearray(fix_dex_header(replacements.get(entry, z.read(entry))))
        if not any(f.encode() in dex for f in features):
            continue
        methods = dex_methods(dexdump(bytes(dex)))
        for method, feature, i, kind in feature_check_sites(methods, features):
            if kind == "folded":
                off, text = methods[method][i + 1]
                reg = int(re.match(r"const/4 v(\d+),", text).group(1))
                assert dex[off] == 0x12 and dex[off + 1] == reg, f"{method}: {text}"
                dex[off:off + 2] = bytes([0x0a, reg])  # move-result vX
                fixed.append(f"{method.split(':')[0]} ({feature})")
            elif kind == "dropped":
                seen_dropped.add((method, feature))
                if (method, feature) not in DROPPED_FEATURE_CHECKS:
                    unexplained.append(f"{method} ({feature})")
        if SYSTEM_SERVER_START in methods:
            restore_wifi_aware_rtt(dex, methods[SYSTEM_SERVER_START], vendor)
        replacements[entry] = bytes(dex)
    if unexplained:
        raise SystemExit("services.jar: dropped compile-time feature checks without a rule "
                         "(DROPPED_FEATURE_CHECKS in apply_patches.py):\n  " + "\n  ".join(unexplained))
    stale = set(DROPPED_FEATURE_CHECKS) - seen_dropped
    assert not stale, f"DROPPED_FEATURE_CHECKS lists checks this release no longer has: {stale}"
    print(f"[+] services.jar: compile-time feature checks put back: {fixed}")


def restore_wifi_aware_rtt(dex, insns, vendor):
    """SystemServer started RttService and WifiAwareService only behind hasSystemFeature; R8 kept
    the two calls but deleted the starts. Those 18 code units (getPackageManager, the feature
    string, hasSystemFeature, twice) become the two starts, built from the WifiService start just
    before them (iget mSystemServiceManager, class name, APEX jar, startServiceFromJar: 9 units
    each) with v0, v3, v4 as there; v4 is written again before any read after this point. The
    tablet's vendor declares both features, so the starts are unconditional on this image."""
    if not {"android.hardware.wifi.rtt", "android.hardware.wifi.aware"} <= vendor:
        return
    texts = [t for _, t in insns]
    k = next(i for i, t in enumerate(texts) if t.startswith('const-string v3, "com.android.server.wifi.WifiService"'))
    assert texts[k - 1].startswith("iget-object v0, v1, Lcom/android/server/SystemServer;.mSystemServiceManager")
    assert texts[k + 1].startswith('const-string v4, "/apex/com.android.wifi/javalib/service-wifi.jar"')
    assert texts[k + 2].startswith("invoke-virtual {v0, v3, v4}, Lcom/android/server/SystemServiceManager;.startServiceFromJar")
    template = bytes(dex[insns[k - 1][0]:insns[k + 3][0]])
    assert len(template) == 18, len(template)
    r = next(i for i, t in enumerate(texts) if t.startswith('const-string v3, "android.hardware.wifi.rtt"'))
    window = texts[r - 2:r + 6]
    shape = [r"invoke-virtual \{v6\}, Landroid/content/Context;\.getPackageManager", r"move-result-object v0",
             r'const-string v3, "android\.hardware\.wifi\.rtt"', r"invoke-virtual \{v0, v3\}, .*hasSystemFeature",
             r"invoke-virtual \{v6\}, Landroid/content/Context;\.getPackageManager", r"move-result-object v0",
             r'const-string v3, "android\.hardware\.wifi\.aware"', r"invoke-virtual \{v0, v3\}, .*hasSystemFeature"]
    assert all(re.match(a, b) for a, b in zip(shape, window)) and len(window) == 8, window
    assert not texts[r + 6].startswith("move-result"), texts[r + 6]
    start, end = insns[r - 2][0], insns[r + 6][0]
    assert end - start == 36, end - start
    body = b""
    for cls in ("com.android.server.wifi.rtt.RttService", "com.android.server.wifi.aware.WifiAwareService"):
        idx = dex_string_index(bytes(dex), cls)
        assert idx is not None and idx < 0x10000, f"{cls}: string index {idx}"
        body += template[:4] + bytes([0x1a, 0x03]) + struct.pack("<H", idx) + template[8:]
    dex[start:end] = body
    print("[+] SystemServer: RttService and WifiAwareService start again (APEX service-wifi.jar)")


def patch_keystore_attestation(z):
    """Samsung's keymaster_swd TA on SM-T870 refuses any ID attestation tag (ATTESTATION_ID_*)
    with KM_ERROR_CANNOT_ATTEST_IDS (-66) once the Knox warranty fuse is blown (sw_fuse_blown=1),
    while normal key attestation without ATTESTATION_ID_* succeeds and returns the 3-cert hardware
    chain. CrossDeviceServices (com.google.ambient.streaming, Llnd;.a) calls
    setDevicePropertiesAttestationIncluded(true), so without this patch its key generation fails
    with ProviderException (-66). In addition, when com.google.ambient.streaming verifies the
    paired phone's attestation chain (Llnd;.b), an unlocked-bootloader phone reports boot state
    UNVERIFIED, which Llnd;.b only accepts when Llmz;.a() sees Build.TYPE == "userdebug" or "eng".
    In AndroidKeyStoreKeyPairGeneratorSpi.addAttestationParameters (framework.jar!classes3.dex),
    the 149-code-unit block from `invoke-virtual KeyGenParameterSpec.isDevicePropertiesAttestationIncluded`
    through the end of the ATTESTATION_ID_{BRAND,DEVICE,PRODUCT,MANUFACTURER,MODEL} additions is
    replaced, same size, with: if ActivityThread.currentPackageName() has hashCode 0x01099be4
    ("com.google.ambient.streaming"), set Build.TYPE = "userdebug", then jump straight to the
    getAttestationIds() check at the end of the block (skipping all ATTESTATION_ID_* tags)."""
    cls = "Landroid/security/keystore2/AndroidKeyStoreKeyPairGeneratorSpi;"
    name, proto = "addAttestationParameters", "(Ljava/util/List;)V"
    method_full = "android.security.keystore2.AndroidKeyStoreKeyPairGeneratorSpi.addAttestationParameters:(Ljava/util/List;)V"
    for entry in sorted(e for e in z.namelist() if re.fullmatch(r"classes\d*\.dex", e)):
        dex = z.read(entry)
        if cls.encode()[1:-1] not in dex:
            continue
        methods = dex_methods(dexdump(dex))
        if method_full not in methods:
            continue
        insns = methods[method_full]
        i_start = next(i for i, (_o, t) in enumerate(insns)
                       if "KeyGenParameterSpec;.isDevicePropertiesAttestationIncluded:()Z" in t)
        assert re.match(r"move-result v2$", insns[i_start + 1][1]), insns[i_start + 1]
        assert re.match(r"if-eqz v2, [0-9a-f]{4} // \+0093$", insns[i_start + 2][1]), insns[i_start + 2]
        i_end = next(i for i, (_o, t) in enumerate(insns[i_start:], i_start)
                     if "KeyGenParameterSpec;.getAttestationIds:()[I" in t) - 1
        assert "AndroidKeyStoreKeyPairGeneratorSpi;.mSpec:" in insns[i_end][1], insns[i_end]
        start, end = insns[i_start][0], insns[i_end][0]
        units = (end - start) // 2
        assert units == 0x97, f"{method_full}: expected 151 code units, got {units}"
        x = gbos_kamera_jpeg.Dex(dex)
        m_cur_pkg = x.method_index("Landroid/app/ActivityThread;", "currentPackageName", "()Ljava/lang/String;")
        m_hash = x.method_index("Ljava/lang/Object;", "hashCode", "()I")
        s_userdebug = x.strings.index("userdebug")
        f_build_type = next(i for i, f in enumerate(x.fields)
                            if f == ("Landroid/os/Build;", "Ljava/lang/String;", "TYPE"))
        # 22 code units (0x0016..0x002b): check currentPackageName().hashCode() ==
        # hashCode("com.google.ambient.streaming") (0x01099be4), set Build.TYPE = "userdebug" if so,
        # and at 0x002a goto/16 +0x83 (to i_end at 0x00ad, skipping ID tags).
        seq = (
            bytes([0x71, 0x00]) + struct.pack("<H", m_cur_pkg) + bytes([0x00, 0x00])
            + bytes([0x0c, 0x02])
            + bytes([0x38, 0x02]) + struct.pack("<h", 0x10)
            + bytes([0x6e, 0x10]) + struct.pack("<H", m_hash) + bytes([0x02, 0x00])
            + bytes([0x0a, 0x02])
            + bytes([0x14, 0x03]) + struct.pack("<I", 0x01099be4)
            + bytes([0x33, 0x32]) + struct.pack("<h", 0x07)
            + bytes([0x1b, 0x02]) + struct.pack("<I", s_userdebug)
            + bytes([0x69, 0x02]) + struct.pack("<H", f_build_type)
            + bytes([0x29, 0x00]) + struct.pack("<h", units - 20)
        )
        assert len(seq) == 44, len(seq)
        body = seq + bytes(end - start - len(seq))
        out = bytearray(dex[:start] + body + dex[end:])
        # Drop ACC_FINAL (0x10) on Build.TYPE in class_data_item so sput-object from
        # AndroidKeyStoreKeyPairGeneratorSpi does not throw IllegalAccessError at runtime.
        b_data = next(d for c, d in x.class_defs() if c == "Landroid/os/Build;")
        p = b_data
        n_sf, p = gbos_kamera_jpeg._uleb(out, p)
        for _ in range(3):
            _, p = gbos_kamera_jpeg._uleb(out, p)
        f_idx = 0
        for _ in range(n_sf):
            diff, p = gbos_kamera_jpeg._uleb(out, p)
            f_idx += diff
            p_flags = p
            flags, p = gbos_kamera_jpeg._uleb(out, p)
            if f_idx == f_build_type:
                assert p - p_flags == 1 and flags == 0x19, (p - p_flags, hex(flags))
                out[p_flags] = flags & ~0x10  # 0x09 (PUBLIC | STATIC)
                break
        else:
            raise AssertionError("Build.TYPE not found in Build class_data_item")
        patched = fix_dex_header(bytes(out))
        print(f"[+] framework.jar!{entry}: AndroidKeyStoreKeyPairGeneratorSpi.addAttestationParameters "
              f"skips Knox-broken ID attestation tags + enables peer UNVERIFIED boot state at {start:#x}")
        return {entry: patched}
    raise SystemExit(f"framework.jar: {method_full} not found")


def patch_ro_system_features(framework):
    """mica compiles android.software.device_admin, android.hardware.location.gps,
    android.software.managed_users, android.hardware.wifi.aware and android.hardware.wifi.rtt
    into framework.jar as "always absent" (RoSystemFeatures). SystemConfig then skips the
    tablet's vendor XMLs for them, and every app's hasSystemFeature() answers false before it asks
    the system: Play services kept Find My Device off and the GNSS receiver, Wi-Fi Aware and RTT
    were hidden. maybeHasFeature() is made to return null ("not fixed at build time") for every
    feature, so the feature XMLs decide, as reconcile_features() writes them."""
    cls, name, proto = ("Lcom/android/internal/pm/RoSystemFeatures;", "maybeHasFeature",
                        "(Ljava/lang/String;I)Ljava/lang/Boolean;")
    src = os.path.join(WORK, "denetim", "pristine-framework.jar")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "wb") as f:
        f.write(pristine_file("system/framework/framework.jar"))
    try:
        with zipfile.ZipFile(src) as z:
            found = [(e, z.read(e)) for e in z.namelist()
                     if re.fullmatch(r"classes\d*\.dex", e) and cls.encode()[1:-1] in z.read(e)]
            hits = [(e, dex, dex_method_code(dex, cls, name, proto)) for e, dex in found]
            hits = [h for h in hits if h[2]]
            assert len(hits) == 1, f"framework.jar: {cls}{name} found in {len(hits)} dex files"
            entry, dex, (off, regs, units) = hits[0]
            tries = struct.unpack_from("<H", dex, off - 16 + 6)[0]  # code_item.tries_size
            assert regs >= 1 and units >= 2 and tries == 0, \
                f"{name}: {regs} registers, {units} code units, {tries} try blocks"
            # const/4 v0, #0 ; return-object v0 ; nop for every remaining code unit. The ART
            # verifier checks every instruction, reachable or not, so no fragment of the old code
            # may stay behind (a half if-nez decoded as move/from16 v17 killed zygote).
            body = bytes.fromhex("12001100") + bytes(2 * (units - 2))
            dex = fix_dex_header(dex[:off] + body + dex[off + 2 * units:])
            replacements = {entry: dex}
            keystore = patch_keystore_attestation(z)
            assert not set(keystore) & set(replacements), "two patches touch the same dex of framework.jar"
            replacements.update(keystore)
            # Camera: JPEG next to a video encoder is produced by the system (yamalar/kamera_jpeg.py)
            scratch = os.path.join(WORK, "denetim", "kamera-jpeg-build")
            os.makedirs(scratch, exist_ok=True)
            camera = gbos_kamera_jpeg.framework_replacements(z, scratch)
            assert not set(camera) & set(replacements), "two patches touch the same dex of framework.jar"
            replacements.update(camera)
            shutil.rmtree(scratch, ignore_errors=True)
        rewrite_jar_entries(src, os.path.join(framework, "framework.jar"), replacements)
    finally:
        os.remove(src)
    invalidate_system_boot_image(framework)
    print(f"[+] framework.jar!{entry}: RoSystemFeatures.maybeHasFeature returns null at {off:#x}")


def invalidate_system_boot_image(framework):
    """The primary boot image on /system was compiled from the unpatched framework.jar; ART
    would reject it at zygote start. Without it odrefresh compiles the boot image on /data on the
    first boot (as on every device whose ART module was updated). The /system/framework/oat
    artifacts were compiled against that boot image too, and odrefresh only checks that they
    exist, so they go as well and odrefresh compiles system_server's jars on /data. App odex
    files stay: ART falls back to their vdex until background dexopt recompiles them."""
    removed = 0
    # <isa>/boot*.vdex are symlinks to ../boot*.vdex and would dangle; glob lists broken links.
    targets = glob.glob(os.path.join(framework, "*", "boot*.art*")) + \
        glob.glob(os.path.join(framework, "*", "boot*.oat*")) + \
        glob.glob(os.path.join(framework, "*", "boot*.vdex*")) + \
        glob.glob(os.path.join(framework, "boot*.vdex*")) + \
        glob.glob(os.path.join(framework, "oat", "*", "*"))
    emptied = set()
    for p in targets:
        if re.search(r"\.(art|oat|odex|vdex)(\.fsv_meta)?$", p):
            os.remove(p)
            emptied.add(os.path.dirname(p))
            removed += 1
    emptied.add(os.path.join(framework, "oat"))
    for d in sorted(emptied - {framework}, key=len, reverse=True):
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
    print(f"[+] Removed {removed} boot image / framework oat files compiled from the old framework.jar")


def patch_system():
    # Ensure Samsung root mountpoint directories exist in System-as-Root (/)
    cache_dir = os.path.join(SYS_DIR, "cache")
    if os.path.islink(cache_dir) or os.path.isfile(cache_dir):
        os.unlink(cache_dir)
    for mp in ["cache", "carrier", "dqmdbg", "efs", "misc", "omr", "optics", "prism", "spu"]:
        os.makedirs(os.path.join(SYS_DIR, mp), exist_ok=True)

    patch_tethering_apex()
    install_32bit_runtime()
    patch_binder_and_hidlbase_threadpool()
    patch_framework()
    write_component_overrides()

    # Keep mica's own bpfloader (matching Rust .dylib.so ABI in /system/lib64) and patch libbpf_android.so
    # so vendorBpfLoader() re-executes /system/bin/netbpfload (instead of unpatched /apex/com.android.tethering/bin/netbpfload)
    with open(os.path.join(SYS_DIR, "system/bin/bpfloader"), "wb") as f:
        f.write(pristine_file("system/bin/bpfloader"))
    os.chmod(os.path.join(SYS_DIR, "system/bin/bpfloader"), 0o755)
    shutil.copytree(os.path.join(LINEAGE_PARTS, "system/etc/bpf"), os.path.join(SYS_DIR, "system/etc/bpf"), dirs_exist_ok=True)

    libbpf_and_path = os.path.join(SYS_DIR, "system/lib64/libbpf_android.so")
    lb_data = pristine_file("system/lib64/libbpf_android.so")
    old_teth_bin = b"/apex/com.android.tethering/bin/netbpfload\x00"
    new_sys_bin = b"/system/bin/netbpfload".ljust(len(old_teth_bin), b"\x00")
    assert old_teth_bin in lb_data, "Could not find /apex/com.android.tethering/bin/netbpfload in libbpf_android.so"
    with open(libbpf_and_path, "wb") as f:
        f.write(lb_data.replace(old_teth_bin, new_sys_bin))
    os.chmod(libbpf_and_path, 0o644)

    # Write /system/etc/init/netbpfload.rc with netbpfload_419 service
    rc_path = os.path.join(SYS_DIR, "system/etc/init/netbpfload.rc")
    rc_content = """# 2026 3 37 1 0 # 26q3 sdk/api level 37.1 - Android 17 QPR1
on load-bpf-programs
    write /proc/sys/kernel/kptr_restrict 1
    exec_start netbpfload_419
    wait_for_prop bpf.progs_loaded 1
    write /proc/sys/kernel/kptr_restrict 2
    exec_start netd1shot
    start netd

service netbpfload_419 /system/bin/netbpfload
    disabled
    capabilities CHOWN SYS_ADMIN NET_ADMIN SYSLOG
    group root graphics network_stack net_admin net_bw_acct net_bw_stats net_raw system
    user root
    file /dev/kmsg w
    rlimit memlock 1073741824 1073741824
    oneshot

service bpfloader /system/bin/false
    disabled
    user root
    oneshot
    updatable
"""
    with open(rc_path, "w") as f:
        f.write(rc_content)

    # Patch /system/build.prop idempotently
    bp_path = os.path.join(SYS_DIR, "system/build.prop")
    with open(bp_path, "r") as f:
        bp = f.read()
    bp = bp.replace("hwservicemanager.always_sets_disabled=true", "hwservicemanager.always_sets_disabled=false")
    bp = bp.replace("apexd.config.erofs_file_backed_mount.use_dio=true", "apexd.config.erofs_file_backed_mount.use_dio=false")
    bp = bp.replace("ro.debuggable=0", "ro.debuggable=1")
    bp = bp.replace("ro.secure=1", "ro.secure=0")
    bp = bp.replace("ro.adb.secure=1", "ro.adb.secure=0")
    bp = bp.replace("trusty.security_vm.enabled=1", "trusty.security_vm.enabled=0")
    bp = bp.replace("ro.boot.hypervisor.vm.supported=1", "ro.boot.hypervisor.vm.supported=0")
    bp = comment_out_prop(bp, "service.adb.listen_addrs=tcp:localhost:5555")
    # 16 GiB zram backed by a 16 GiB writeback file; mmd sets both up from these properties.
    bp = set_prop(bp, "mmd.zram.size", "290%")
    bp = set_prop(bp, "mmd.zram.writeback.device_size", "17179869184")
    for old_line in ["\nro.debuggable=1\nro.adb.secure=0\nservice.adb.root=1\npersist.sys.usb.config=adb\nro.bpf.kver_override=5.10.239\nro.face.sense_service=true\n"]:
        bp = bp.replace(old_line, "\n")
    marker = "# --- GOOGLEBOOKOS_T870_PATCH ---"
    bp = bp.split(marker)[0].rstrip("\n") + "\n"  # same result on pristine and patched input
    bp += (
        f"{marker}\n"
        "ro.debuggable=1\nro.secure=0\nro.adb.secure=0\nservice.adb.root=1\n"
        "persist.sys.usb.config=adb\nro.bpf.kver_override=5.10.239\nro.face.sense_service=true\n"
        # Find Hub on Googlebooks (GMS 26.40+): with the desktop restriction on, GMS wants this
        # finder capability beside its flag before it registers the FMD module. Set both properties:
        # plat_property_contexts labels persist.bluetooth.finder.supported as bluetooth_finder_prop
        # (readable by gmscore_app under SELinux), while ro.bluetooth.finder.supported falls into
        # default_prop; dxiv.g() checks both. The flag side is applied on the device (see DEVIR 3.1).
        "ro.bluetooth.finder.supported=true\n"
        "persist.bluetooth.finder.supported=true\n"
        "ro.product.cpu.abilist=arm64-v8a\nro.product.cpu.abilist64=arm64-v8a\nro.product.cpu.abilist32=\n"
        "ro.vendor.product.cpu.abilist=arm64-v8a\nro.vendor.product.cpu.abilist64=arm64-v8a\nro.vendor.product.cpu.abilist32=\n"
        "ro.odm.product.cpu.abilist=arm64-v8a\nro.odm.product.cpu.abilist64=arm64-v8a\nro.odm.product.cpu.abilist32=\n"
        # No ro.bootanim.set_orientation_*: the panel's landscape pose is the natural orientation
        # now (yamalar/dogal_yon.py), so the boot animation needs no rotation.
        "dalvik.vm.dex2oat64.enabled=true\n"
        # Chrome/WebView ship their own ANGLE, which reports only ES 2.0 on Adreno 650's
        # Vulkan 1.1 driver; the GPU process then dies creating an ES 3.0 context.
        "debug.angle.feature_overrides_enabled="
        "exposeNonConformantExtensionsAndVersions:exposeES32ForTesting\n"
    )
    with open(bp_path, "w") as f:
        f.write(bp)

    # Remove mica VM/GSC/EC init scripts and VINTF
    for rel in [
        "system/etc/init/gscd.rc",
        "system/etc/init/trusty_security_vm.rc",
        "system/etc/vintf/manifest/manifest_gscd.xml",
        "system/etc/init/gscutil.rc",
        "system/etc/init/gscutil-executor.rc",
        "system/etc/init/check_tpm_clear.rc",
        "system/etc/init/timberslide.rc",
        "system/etc/init/ec_crash_collector.rc",
    ]:
        p = os.path.join(SYS_DIR, rel)
        if os.path.exists(p):
            os.remove(p)

    # Copy vibrator framework VINTF manifest
    shutil.copy2(
        os.path.join(LINEAGE_PARTS, "system/etc/vintf/manifest/manifest_services_android.frameworks.vibrator.xml"),
        os.path.join(SYS_DIR, "system/etc/vintf/manifest/manifest_services_android.frameworks.vibrator.xml"),
    )

    # Add IFace/default V4 to /system/etc/vintf/manifest.xml
    vintf_path = os.path.join(SYS_DIR, "system/etc/vintf/manifest.xml")
    with open(vintf_path, "r") as f:
        vintf = f.read()
    if "android.hardware.biometrics.face" not in vintf:
        iface_entry = """    <hal format="aidl">
        <name>android.hardware.biometrics.face</name>
        <version>4</version>
        <fqname>IFace/default</fqname>
    </hal>
</manifest>"""
        vintf = vintf.replace("</manifest>", iface_entry)
        with open(vintf_path, "w") as f:
            f.write(vintf)

    # Copy libcodec2_hidl_client@1.1.so and @1.2.so
    for lib in ["libcodec2_hidl_client@1.1.so", "libcodec2_hidl_client@1.2.so"]:
        shutil.copy2(os.path.join(LINEAGE_PARTS, "system/lib64", lib), os.path.join(SYS_DIR, "system/lib64", lib))

    # LineageOS's input device configurations (the ones AOSP ships for known keyboards, remotes and
    # game pads; mica has none)
    for idc in glob.glob(os.path.join(LINEAGE_PARTS, "system/usr/idc/**/*"), recursive=True):
        if os.path.isfile(idc):
            shutil.copy2(idc, os.path.join(SYS_DIR, "system/usr/idc", os.path.basename(idc)))

    # Install FaceUnlock.apk and native libs (yuz-tanima/build.sh, rebuilt when its sources changed;
    # FaceUnlock/ and the compilers come from yuz-tanima/getir.sh, run here when one is missing; on
    # the tablet the kit carries the outputs and FaceUnlock's files, nothing is downloaded)
    if not os.environ.get("GBOS_KIT") and not all(
            os.path.exists(os.path.join(SRC_FACE, p)) for p in FACE_FETCHED):
        subprocess.run([os.path.join(SRC_FACE, "getir.sh")], check=True)
    hazir_ikili.derle(SRC_FACE, os.path.join(SRC_FACE, "out", "STAMP"),
                      [os.path.join(SRC_FACE, o) for o in FACE_OUTPUTS], "yuz-tanima", kitte_kaynak=False)
    fu_dir = os.path.join(SYS_DIR, "system/priv-app/FaceUnlock")
    fu_lib_dir = os.path.join(fu_dir, "lib/arm64")
    os.makedirs(fu_lib_dir, exist_ok=True)
    shutil.copy2(os.path.join(SRC_FACE, "out/FaceUnlock.apk"), os.path.join(fu_dir, "FaceUnlock.apk"))
    for so in glob.glob(os.path.join(SRC_FACE, "FaceUnlock/lib/*.so")):
        shutil.copy2(so, os.path.join(fu_lib_dir, os.path.basename(so)))

    # Permissions & feature XMLs for FaceUnlock
    shutil.copy2(
        os.path.join(SRC_FACE, "FaceUnlock/permissions/privapp_whitelist_co.aospa.sense.xml"),
        os.path.join(SYS_DIR, "system/etc/permissions/privapp-permissions-sense.xml"),
    )
    os.makedirs(os.path.join(SYS_DIR, "system/etc/default-permissions"), exist_ok=True)
    shutil.copy2(
        os.path.join(SRC_FACE, "FaceUnlock/permissions/default_permissions_co.aospa.sense.xml"),
        os.path.join(SYS_DIR, "system/etc/default-permissions/default-permissions-sense.xml"),
    )
    shutil.copy2(
        os.path.join(SRC_FACE, "FaceUnlock/permissions/hiddenapi-whitelist-co.aospa.sense.xml"),
        os.path.join(SYS_DIR, "system/etc/sysconfig/hiddenapi-package-whitelist-sense.xml"),
    )
    with open(os.path.join(SYS_DIR, "system/etc/permissions/android.hardware.biometrics.face.xml"), "w") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n<permissions>\n    <feature name="android.hardware.biometrics.face" />\n</permissions>\n')

    # Install sense-face-hal.jar and init rc
    shutil.copy2(
        os.path.join(SRC_FACE, "out/sense-face-hal.jar"),
        os.path.join(SYS_DIR, "system/framework/sense-face-hal.jar"),
    )
    with open(os.path.join(SYS_DIR, "system/etc/init/sense-face-hal.rc"), "w") as f:
        f.write("""service sense_face_hal /system/bin/app_process64 /system/bin --nice-name=sense-face-hal co.aospa.sense.hal.SenseFaceHalMain
    class hal
    user system
    group system camera input
    setenv CLASSPATH /system/framework/sense-face-hal.jar
    seclabel u:r:system_server:s0

on property:sys.boot_completed=1
    start sense_face_hal
""")

    # Replaced by VENDOR_BINDER_OVERRIDE (the kernel refuses the bind mount at boot).
    obsolete_rc = os.path.join(SYS_DIR, "system/etc/init/gbos-vendor-binder.rc")
    if os.path.exists(obsolete_rc):
        os.remove(obsolete_rc)

    # SELinux policy (plat/system_ext CIL, service contexts, enforcing or permissive mode) is
    # written by yamalar/selinux.py, called from __main__ after the partitions are patched.

    # /metadata is 26 MB and also holds vold's metadata-encryption keys, password_slots, aconfig
    # flag storage and staged installs; a full /metadata breaks those (and an OTA's first boot).
    # The log therefore keeps at most (bootlog_rotations + 1) x bootlog_kib (4 MiB). Earlier images kept
    # 9 x 4 MiB, filled /metadata to 98 % and made logcat stop at ENOSPC; post-fs removes those
    # surplus rotations on the first boot of this image.
    bootlog_kib, bootlog_rotations = 1024, 3
    surplus = "".join(f"    rm /metadata/gbos-bootlog/logcat.txt.{n}\n"
                      for n in range(bootlog_rotations + 1, 9))
    with open(os.path.join(SYS_DIR, "system/etc/init/gbos-bootlog.rc"), "w") as f:
        f.write(f"""# Googlebook OS on SM-T870: persistent bring-up log in /metadata/gbos-bootlog
service gbos_bootlog /system/bin/logcat -b main,system,events,crash -v threadtime -f /metadata/gbos-bootlog/logcat.txt -r {bootlog_kib} -n {bootlog_rotations} *:V sscrpcd:S QC-time-services:S libperfmgr:S
    user root
    group root log readproc
    seclabel u:r:gbos_bootlog:s0
    disabled
    oneshot

on post-fs && property:ro.gbos.bootlog=1
    mkdir /metadata/gbos-bootlog 0770 root system
{surplus}    start gbos_bootlog
""")
    print("[+] Patched /system partition")

def patch_system_ext():
    remove_unsupported_apps(SYSEXT_DIR, "sysext")
    # Remove mica trusty VM keymint & rpmb rc files
    for rc in [
        "etc/init/android.hardware.security.keymint-service.desktop_trusty_system_vm.rc",
        "etc/init/desktop-trusty_security_vm_launcher.rc",
        "etc/init/rpmb_dev.desktop.system.rc",
    ]:
        p = os.path.join(SYSEXT_DIR, rc)
        if os.path.exists(p):
            os.remove(p)

    # Replace system_ext VINTF manifest.xml with tablet's (hidl.manager + hidl.token only)
    shutil.copy2(
        os.path.join(LINEAGE_PARTS, "system_ext/etc/vintf/manifest.xml"),
        os.path.join(SYSEXT_DIR, "etc/vintf/manifest.xml"),
    )
    shutil.copy2(
        os.path.join(LINEAGE_PARTS, "system_ext/etc/vintf/manifest/android.hidl.allocator@1.0-service.xml"),
        os.path.join(SYSEXT_DIR, "etc/vintf/manifest/android.hidl.allocator@1.0-service.xml"),
    )

    # Copy hwservicemanager, hidl.allocator, wificond, and 64-bit + 32-bit hidl.memory impl
    os.makedirs(os.path.join(SYSEXT_DIR, "bin/hw"), exist_ok=True)
    os.makedirs(os.path.join(SYSEXT_DIR, "lib64/hw"), exist_ok=True)
    os.makedirs(os.path.join(SYSEXT_DIR, "lib/hw"), exist_ok=True)
    for rel in ["bin/hw/android.hidl.allocator@1.0-service", "bin/hwservicemanager", "bin/wificond",
                "etc/init/android.hidl.allocator@1.0-service.rc", "etc/init/hwservicemanager.rc",
                "etc/init/wificond.rc"]:
        shutil.copy2(os.path.join(LINEAGE_PARTS, "system_ext", rel), os.path.join(SYSEXT_DIR, rel))
    for stale in ["bin/adb_root", "etc/init/adb_root.rc"]:
        p = os.path.join(SYSEXT_DIR, stale)
        if os.path.exists(p):
            os.remove(p)
    shutil.copy2(os.path.join(LINEAGE_PARTS, "system_ext/lib64/hw/android.hidl.memory@1.0-impl.so"), os.path.join(SYSEXT_DIR, "lib64/hw/android.hidl.memory@1.0-impl.so"))
    shutil.copy2(
        os.path.join(STAGE_32, "sysext_lib/lib/hw/android.hidl.memory@1.0-impl.so"),
        os.path.join(SYSEXT_DIR, "lib/hw/android.hidl.memory@1.0-impl.so"),
    )

    # /dev/ion in system_ext_file_contexts
    sefc = os.path.join(SYSEXT_DIR, "etc/selinux/system_ext_file_contexts")
    with open(sefc, "r") as f:
        fc = f.read()
    if "\n/dev/ion " not in "\n" + fc:
        with open(sefc, "w") as f:
            f.write("/dev/ion    u:object_r:ion_device:s0\n" + fc)

    # Patch system_ext_sepolicy.cil from pristine copy

    # Patch system_ext_property_contexts from pristine copy (17 missing device/qcom/lineage properties)
    se_pc_path = os.path.join(SYSEXT_DIR, "etc/selinux/system_ext_property_contexts")
    se_pc = pristine_file("etc/selinux/system_ext_property_contexts", "system_ext_a.erofs").decode()
    se_pc_add = """
# --- GOOGLEBOOKOS_T870_PATCH ---
persist.vendor.ai-director.                u:object_r:vendor_persist_camera_prop:s0
persist.vendor.sys.location.               u:object_r:vendor_sys_location_prop:s0
hw.fm.                                     u:object_r:exported3_system_prop:s0
bluetooth.hci.disabled_commands            u:object_r:bluetooth_config_prop:s0
vendor.camera.aux.packageexcludelist       u:object_r:camera2_extensions_prop:s0
ro.config.force_multi_window               u:object_r:exported_config_prop:s0 exact bool
persist.vendor.nfc.config_file_name        u:object_r:vendor_persist_nfc_prop:s0 exact string
persist.vendor.nfc.fw_file_name            u:object_r:vendor_persist_nfc_prop:s0 exact string
persist.vendor.nfc.nxp.lx_debug_mask       u:object_r:vendor_persist_nfc_prop:s0 exact string
ro.recovery.batteryless                    u:object_r:recovery_config_prop:s0 exact bool
ro.recovery.supported_fs                   u:object_r:recovery_config_prop:s0 exact string
ro.minui.blacklist_input_devices           u:object_r:recovery_config_prop:s0
ro.minui.default_touch_rotation            u:object_r:recovery_config_prop:s0
persist.sys.xtra-daemon.enabled            u:object_r:xtra_control_prop:s0 exact bool
persist.sysui.disable_mirroring_confirmation_dialog u:object_r:hdmi_config_prop:s0
ro.usb.uvc.disable_video_encode_flag       u:object_r:usb_uvc_config_prop:s0
vendor.usb.uvc.payload_transfer_size       u:object_r:usb_uvc_config_prop:s0
"""
    with open(se_pc_path, "w") as f:
        f.write(se_pc.rstrip("\n") + "\n" + se_pc_add)

    # Patch /system_ext/etc/build.prop
    se_bp_path = os.path.join(SYSEXT_DIR, "etc/build.prop")
    with open(se_bp_path, "r") as f:
        se_bp = f.read()
    se_bp = se_bp.replace("ro.audio.ihaladaptervendorextension_enabled=true", "ro.audio.ihaladaptervendorextension_enabled=false")
    se_bp = se_bp.replace("ro.telephony.sim_slots.count=2", "ro.telephony.sim_slots.count=0")
    se_bp = se_bp.replace("telephony.active_modems.max_count=2", "telephony.active_modems.max_count=0")
    with open(se_bp_path, "w") as f:
        f.write(se_bp)
    print("[+] Patched /system_ext partition")

# Googlebook features that only work on Dell XPS hardware (factory/repair tools that wait for the
# embedded controller, firmware, VPD and I2C executors of mica's vendor partition) and the AVF
# Linux terminal (needs KVM; this kernel runs at EL1 under the Qualcomm hypervisor).
# A removed app takes its own privapp-permissions and preinstalled-packages files with it.
# Dell SupportAssist (com.dell.supportassist.alos) has no Dell hardware to look after here; its
# TelemetryService ran into a 200 s ANR and was killed after every boot (23 Oct 2026, Kadir: remove).
# Dell Diagnostics stays: the battery card lives there.
REMOVED_APPS = {
    "sysext": ["priv-app/RepairMode-Desktop", "app/DesktopRepairModeResources"],
    "prod": ["priv-app/DellRepairStub", "priv-app/RepairLauncherPrebuilt", "priv-app/RepairPrebuilt",
             "priv-app/TaskManagerPrebuilt", "overlay/RepairModeDesktopOverlay-Common.apk",
             "overlay/TerminalAppDesktopOverlay",
             "etc/permissions/privapp-permissions-com.dell.luxsharerma.xml",
             "etc/sysconfig/preinstalled-packages-com.dell.luxsharerma.xml",
             "priv-app/DellSupportAssistStub",
             "etc/permissions/privapp-permissions-com.dell.supportassist.alos.xml",
             "etc/sysconfig/preinstalled-packages-com.dell.supportassist.alos.xml"],
}
# The com.android.virt APEX stays (its framework jars are on the boot/system server classpath);
# its terminal app is replaced by the Terminal's compatibility package (yamalar/aosp_terminal.py).
DISABLED_COMPONENTS = {
    # App Cast is enabled since 25 Oct 2026: the services.jar peer-attestation patch plus the phone's
    # Root Flags lnd.b hook made the phone register AppCastService for this tablet, and casting works
    # (app list and a Chrome session opened on the tablet). The launcher activity stays visible; see
    # DEVIR 3.1 and telefon-merkezi/appcast-20261025-*.png.
    # DesktopContextualTools (Quick Look): LocaleChangedReceiver_Receiver disables ProcessTextGateway
    # whenever Locale.getDefault().getLanguage() is not "en" (esp.a -> fja.bh). Keep the receiver off
    # so ProcessTextGateway stays enabled on non-English locales.
    "com.google.android.desktop.contextualtools": [
        "com.google.android.desktop.contextualtools.entrypoints.LocaleChangedReceiver_Receiver",
    ],
}
ENABLED_COMPONENTS = {
    "com.google.android.desktop.contextualtools": [
        "com.google.android.desktop.contextualtools.entrypoints.ProcessTextGateway",
    ],
    "com.google.android.desktop.alai": [
        "com.google.android.desktop.alai.loam.app.settings.activity.LoamSettingsActivityAlias",
        "com.google.android.desktop.alai.loam.app.widget.SmartCollectionWidgetProvider",
        "com.google.android.desktop.alai.gateway.gemini.ShareTargetActivity",
        "com.google.android.desktop.alai.gateway.notebooklm.ShareTargetActivity",
        "com.google.android.desktop.alai.gateway.notebooklm.DocsUIShareTargetActivityAlias",
    ],
    "com.google.android.apps.bard": [
        "com.google.android.apps.bard.shellapp.RememberThisShareTargetActivity",
    ],
    "com.google.android.googlequicksearchbox": [
        "com.google.android.apps.search.assistant.surfaces.voice.robin.launcher.ProcessTextGatewayActivity",
        "com.google.android.apps.search.assistant.surfaces.voice.robin.launcher.NotebooksShortcutGatewayActivity",
        "com.google.android.apps.search.assistant.surfaces.voice.ui.interpreter.shortcut.InterpreterAddShortcutActivity",
        "com.google.android.apps.search.assistant.verticals.snapshot.widgets.weather.WeatherWidget_Receiver",
        "com.google.android.apps.search.assistant.verticals.snapshot.widgets.weather.FreeformWeatherWidget_Receiver",
        "com.google.android.apps.search.assistant.verticals.familyshare.FamilyShareActivity",
    ],
    "com.google.android.as": [
        "com.google.android.apps.miphone.aiai.translate.settings.packageupdatedreceiver.AutoTranslatePackageUpdatedReceiver",
        "com.google.android.apps.miphone.aiai.safecomms.ui.settingsactivity.MessageArmourSettingsActivity",
        "com.google.android.apps.miphone.aiai.safecomms.ui.settingsactivity.MessageArmourAapmIntegrationActivity",
        "com.google.android.apps.miphone.aiai.safecomms.ui.overlayactivity.MessageArmourTrampolineActivity",
        "com.google.android.apps.miphone.aiai.safecomms.ui.overlayactivity.MessageArmourOverlayActivity",
        "com.google.android.apps.miphone.aiai.safecomms.featurestatusservice.FeatureStatusService",
        "com.google.android.apps.miphone.aiai.besties.bestiespicker.ui.BestiesPickerActivity",
        "com.google.android.apps.miphone.aiai.adaptiveaudio.settings.ui.AdaptiveAudioSettingsActivity",
    ],
    "com.google.pixel.livewallpaper": [
        "com.breel.wallpapers20.conveyor.wallpaper.ConveyorWallpaper",
        "com.google.pixel.livewallpaper.wallpapers.HalfDomeWallpaperService",
        "com.google.pixel.wallpapers22.lightfieldflower.flamingoflower.FlamingoFlowerWallpaperService",
        "com.google.pixel.livewallpaper.tactile.wallpapers.center.TactileWallpaperCenter",
        "com.google.pixel.livewallpaper.wallpapers.WhitehavenWallpaperService",
        "com.google.pixel.livewallpaper.soundviz.wallpaper.SoundVizWallpaper",
        "com.google.pixel.livewallpaper.dioramas.utah.wallpapers.UtahWallpaper",
        "com.google.pixel.wallpapers21.parallaxflower.multiflower.bloomingbotanicals.BloomingBotanicalsWallpaperService",
        "com.google.pixel.livewallpaper.cities.wallpapers.variations.SanFranciscoWallpaper",
        "com.google.pixel.livewallpaper.imprint.wallpapers.ImprintWallpaper",
        "com.google.pixel.livewallpaper.dioramas.switzerland.wallpapers.SwitzerlandWallpaper",
        "com.google.pixel.livewallpaper.tactile.wallpapers.offsetbloom.TactileWallpaperOffsetBloom",
        "com.google.pixel.livewallpaper.dioramas.fiji.wallpapers.FijiWallpaper",
        "com.google.pixel.livewallpaper.miniman.wallpaper.MinimanWallpaper",
        "com.google.pixel.livewallpaper.wallpapers.KentWallpaperService",
        "com.google.pixel.wallpapers22.lightfieldflower.persianbuttercup.PersianButtercupWallpaperService",
        "com.google.pixel.livewallpaper.tactile.wallpapers.offsetburst.TactileWallpaperOffsetBurst",
        "com.google.pixel.livewallpaper.canyons.wallpapers.CanyonsWallpaper",
        "com.google.pixel.livewallpaper.celestialBodies.wallpapers.variations.MoonWallpaperService",
        "com.google.pixel.livewallpaper.windy.wallpapers.WindyWallpaper",
        "com.google.pixel.livewallpaper.wallpapers.PantheonWallpaperService",
        "com.google.pixel.wallpapers22.lightfieldflower.makidahlia.MakiDahliaWallpaperService",
        "com.google.pixel.livewallpaper.dioramas.vesuvius.VesuviusWallpaperService",
        "com.google.pixel.livewallpaper.celestialbodies.MarsWallpaperService",
        "com.google.pixel.livewallpaper.wallpapers.MoabWallpaperService",
        "com.google.pixel.wallpapers21.parallaxflower.multiflower.brightblooms.BrightBloomsWallpaperService",
        "com.google.pixel.livewallpaper.wallpapers.TogoWallpaperService",
        "com.google.pixel.livewallpaper.dioramas.lagos.LagosWallpaperService",
        "com.google.pixel.livewallpaper.dioramas.uyuni.UyuniWallpaperService",
        "com.google.pixel.livewallpaper.celestialBodies.wallpapers.variations.PlutoWallpaperService",
        "com.google.pixel.livewallpaper.delight.wallpapers.DelightWallpaper",
        "com.google.pixel.livewallpaper.dioramas.monumentvalley.MonumentValleyWallpaperService",
        "com.google.pixel.livewallpaper.pulley.wallpaper.PulleyWallpaper",
        "com.google.pixel.livewallpaper.dioramas.honolulu.HonoluluWallpaperService",
        "com.google.pixel.livewallpaper.dioramas.mexico.wallpapers.MexicoWallpaper",
        "com.google.pixel.livewallpaper.surfandturf.wallpapers.SurfAndTurfWallpaper",
        "com.google.pixel.livewallpaper.celestialbodies.EarthOrbitWallpaperService",
        "com.google.pixel.wallpapers22.lightfieldflower.boatorchid.BoatOrchidWallpaperService",
        "com.google.pixel.livewallpaper.cities.wallpapers.variations.NewYorkWallpaper",
        "com.google.pixel.livewallpaper.celestialBodies.wallpapers.EarthWallpaperService",
        "com.google.pixel.livewallpaper.wallpapers.SunsetOnBatteryWallpaperService",
    ],
}


def remove_unsupported_apps(tree_dir, key):
    for rel in REMOVED_APPS[key]:
        p = os.path.join(tree_dir, rel)
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)


# The tablet's own hardware truth: the feature XMLs of the LineageOS vendor partition it runs,
# taken from the OTA zip by lineage-dosya-cikar.py (see the stage's KAYNAK file):
#   lineage-dosya-cikar.py <zip> vendor /etc/permissions/ --cikti $WORK/lineage/vendor_features
STAGE_VENDOR_FEATURES = os.path.join(WORK, "lineage/vendor_features")
# Where mica declares features, per partition: (tree, pristine image, path inside the image).
FEATURE_DIRS = [
    ("SYS_DIR", "system_a.erofs", "system/etc/permissions"),
    ("SYS_DIR", "system_a.erofs", "system/etc/sysconfig"),
    ("SYSEXT_DIR", "system_ext_a.erofs", "etc/permissions"),
    ("SYSEXT_DIR", "system_ext_a.erofs", "etc/sysconfig"),
    ("PROD_DIR", "product_a.erofs", "etc/permissions"),
    ("PROD_DIR", "product_a.erofs", "etc/sysconfig"),
]
# mica is built for Dell laptops and hides some features that the tablet has. A hardware feature
# the tablet's vendor declares is made available again by rule (apps, Play services and the
# framework gate whole functions on it: android.hardware.location.gps hid the working GNSS
# receiver). A software feature is a policy choice: each one the vendor declares but mica hides
# needs a decision here, and a new one stops the build until it gets one.
SOFTWARE_FEATURE_DECISIONS = {
    # Play services keeps its whole Find My Device module (settings, ring, lock, erase, device
    # admin receiver) disabled without it: Find Hub listed the tablet but could not turn it on.
    "android.software.device_admin": True,
    # Work profiles: mica keeps them off on consumer devices; no function here depends on them.
    "android.software.managed_users": False,
}
# Hardware features mica declares that the tablet's vendor does not, each one deliberate.
MICA_ONLY_HARDWARE = {
    # Googlebook OS is the desktop form factor; the desktop shell and apps key on it.
    "android.hardware.type.pc",
}
UNAVAILABLE_LINE = re.compile(r'^([ \t]*)<unavailable-feature\s+name="([^"]+)"\s*/>[ \t]*$', re.M)


def pristine_listdir(rel, image):
    """Regular files directly in directory `rel` of a pristine mica image ([] if there is none)."""
    r = subprocess.run([os.path.join(EROFS_BIN, "dump.erofs"), "--ls", f"--path=/{rel}",
                        os.path.join(MICA_IMAGES, image)], capture_output=True, text=True)
    if r.returncode != 0:
        return []
    rows = (line.split(None, 2) for line in r.stdout.splitlines())
    return sorted(e[2] for e in rows if len(e) == 3 and e[0].isdigit() and e[1] == "1")


def declared_features(text, where):
    """Names of the <feature> elements of a permissions XML (comments are not declarations).
    Like Android's parser it ignores the XML declaration (Qualcomm ships version="2.0")."""
    try:
        root = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", text))
    except ET.ParseError as e:
        raise SystemExit(f"{where}: not a permissions XML ({e})")
    return {e.get("name") for e in root.iter("feature")}


def vendor_features():
    files = sorted(glob.glob(os.path.join(STAGE_VENDOR_FEATURES, "*.xml")))
    if not files:
        raise SystemExit(f"{STAGE_VENDOR_FEATURES}: no vendor feature XMLs; extract them with "
                         "lineage-dosya-cikar.py <zip> vendor /etc/permissions/ --cikti <stage>")
    names = set()
    for path in files:
        with open(path) as f:
            names |= declared_features(f.read(), path)
    return names


def reconcile_features():
    """Rewrites every mica feature XML that hides a feature the tablet has, from its pristine
    copy (idempotent), and stops on a difference that has no rule or decision."""
    vendor = vendor_features()
    undecided, mica_only, changed = [], [], []
    for tree_var, image, rel_dir in FEATURE_DIRS:
        for name in pristine_listdir(rel_dir, image):
            if not name.endswith(".xml"):
                continue
            rel = f"{rel_dir}/{name}"
            text = pristine_file(rel, image).decode()
            mica_only += [(rel, f) for f in sorted(declared_features(text, rel))
                          if f.startswith("android.hardware.") and f not in vendor
                          and f not in MICA_ONLY_HARDWARE]

            def keep_or_drop(m):
                feature = m.group(2)
                if feature not in vendor:
                    return m.group(0)
                if feature.startswith("android.hardware."):
                    available = True
                elif feature in SOFTWARE_FEATURE_DECISIONS:
                    available = SOFTWARE_FEATURE_DECISIONS[feature]
                else:
                    undecided.append((rel, feature))
                    return m.group(0)
                if not available:
                    return m.group(0)
                changed.append(feature)
                return (f"{m.group(1)}<!-- Googlebook OS on SM-T870: {feature} is available, "
                        f"the tablet's vendor declares it -->")

            new = UNAVAILABLE_LINE.sub(keep_or_drop, text)
            if new != text or UNAVAILABLE_LINE.search(text):  # also undoes a reverted decision
                path = os.path.join(globals()[tree_var], rel)
                with open(path, "w") as f:
                    f.write(new)
                os.chmod(path, 0o644)
    if undecided or mica_only:
        raise SystemExit(
            "feature differences without a decision (SOFTWARE_FEATURE_DECISIONS / "
            "MICA_ONLY_HARDWARE in apply_patches.py):\n"
            + "".join(f"  {rel}: hides {f}, the tablet's vendor declares it\n" for rel, f in undecided)
            + "".join(f"  {rel}: declares {f}, the tablet's vendor does not\n" for rel, f in mica_only))
    print(f"[+] Feature XMLs reconciled with the tablet's vendor: available again {sorted(changed)}")


def write_component_overrides():
    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             "<!-- Googlebook OS on SM-T870: component overrides for desktop features and locales -->",
             "<config>"]
    all_pkgs = sorted(set(DISABLED_COMPONENTS) | set(ENABLED_COMPONENTS))
    for pkg in all_pkgs:
        lines.append(f'    <component-override package="{pkg}">')
        for c in DISABLED_COMPONENTS.get(pkg, ()):
            lines.append(f'        <component class="{c}" enabled="false" />')
        for c in ENABLED_COMPONENTS.get(pkg, ()):
            lines.append(f'        <component class="{c}" enabled="true" />')
        lines.append("    </component-override>")
    lines.append("</config>\n")
    path = os.path.join(SYS_DIR, "system/etc/sysconfig/gbos-disabled-components.xml")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    os.chmod(path, 0o644)


# Dex containers (tree, path, pristine image, path on the tablet) apply_patches.py rewrites besides
# the framework jars; dex-dogrula.py runs the tablet's ART verifier over them too.
CAMERA_APK = ("prod", "app/AL_Camera/AL_Camera.apk", "product_a.erofs", "/product/app/AL_Camera/AL_Camera.apk")
PATCHED_APKS = [CAMERA_APK]
JPEG_QUIRK = "Landroidx/camera/camera2/compat/quirk/JpegHalCorruptImageQuirk;"


def camerax_jpeg_quirk_sites(methods):
    """[(method, index of the List.contains call)] where CameraX's quirk loader decides
    JpegHalCorruptImageQuirk: sget-object JpegHalCorruptImageQuirk.a (device list) ... sget-object
    Build.DEVICE ... invoke-interface List.contains ; move-result vX ; const-class JpegHalCorruptImageQuirk."""
    sites = []
    for method, insns in methods.items():
        for i, (_off, text) in enumerate(insns):
            if not text.startswith("const-class ") or f", {JPEG_QUIRK} //" not in text:
                continue
            if i < 2 or not re.match(r"move-result v\d+$", insns[i - 1][1]):
                continue
            if not re.match(r"invoke-interface \{v\d+, v\d+\}, Ljava/util/List;\.contains:", insns[i - 2][1]):
                continue
            window = " | ".join(t for _o, t in insns[max(0, i - 12):i - 2])
            if f"sget-object v" in window and f"{JPEG_QUIRK}.a:Ljava/util/List;" in window \
                    and "Landroid/os/Build;.DEVICE:Ljava/lang/String;" in window:
                sites.append((method, i - 2))
    return sites


def _insn_bytes(dex, insns, i):
    """Raw bytes of instruction i of a dexdump'ed method (its length from the next offset)."""
    off = insns[i][0]
    nxt = insns[i + 1][0] if i + 1 < len(insns) else None
    assert nxt is not None, "last instruction"
    return dex[off:nxt]


def patch_camerax_jpeg_quirk(dex):
    """CameraX (inside the Googlebook camera app) asks the HAL for preview + H.264 encoder + JPEG
    on the back camera. The LineageOS/Samsung CamX HAL's "Video With Liveshot" usecase then holds
    every video buffer and fills none, so recording freezes (acilis-loglari/kamera-video-20261022/
    KOK-NEDEN.md); preview + encoder + YUV works at every size, measured with cihaz-kabul/KameraAkis.
    CameraX has the fix built in for devices whose JPEG HAL is broken: JpegHalCorruptImageQuirk
    (SoftwareJpegEncodingPreferredQuirk) makes ImageCapture take YUV_420_888 from the camera and
    encode the JPEG itself. Its device list is compiled in (heroqltetmo, heroqltevzw,
    k61v1_basic_ref) and the dex has no string for Build.DEVICE "desktop". The 20 code units that
    compute List.contains(Build.DEVICE.toLowerCase()) are replaced, same size, by
    LENS_FACING == LENS_FACING_BACK read from the camera's characteristics (the way the loader's
    other quirks decide per camera): the back camera gets the quirk, the front camera keeps HAL
    JPEG. Only the back camera (FULL level) has a guaranteed preview + video + YUV(maximum)
    combination; on the LIMITED front camera a YUV still of full size leaves the app no valid
    Preview + ImageCapture + VideoCapture set and it refuses to switch ("Camera 1 does not have
    valid capabilities"), and the front camera never opened the freezing JPEG set anyway.
    Returns (patched dex, (method, offset)) or (None, None) when this dex has no loader."""
    if JPEG_QUIRK.encode()[1:-1] not in dex:
        return None, None
    methods = dex_methods(dexdump(dex))
    sites = camerax_jpeg_quirk_sites(methods)
    if not sites:
        return None, None
    assert len(sites) == 1, f"CameraX quirk loader found {len(sites)} times: {sites}"
    method, i = sites[0]
    insns = methods[method]
    # the sequence starts at the device-list sget-object, 9 instructions / 16 code units before
    # the contains call, and ends with its move-result: 20 code units in all
    j = i - 7
    assert re.match(rf"sget-object v\d+, {re.escape(JPEG_QUIRK)}\.a:Ljava/util/List;", insns[j][1]), insns[j]
    start, end = insns[j][0], insns[i + 2][0]
    assert end - start == 40, f"{method}: {end - start} bytes between the device list and the result"
    reg = int(re.match(r"move-result v(\d+)$", insns[i + 1][1]).group(1))
    tmp = int(re.match(r"sget-object v(\d+), Landroid/os/Build;\.DEVICE:", insns[j + 1][1]).group(1))
    assert reg <= 15 and tmp <= 15 and reg != tmp, (reg, tmp)
    # the same method reads other characteristics for other quirks: copy their encodings
    def find(pattern):
        for k, (_o, t) in enumerate(insns):
            if re.match(pattern, t):
                return k
        raise AssertionError(f"{method}: no instruction matches {pattern}")
    k = find(r"sget-object v\d+, Landroid/hardware/camera2/CameraCharacteristics;\.LENS_FACING:")
    lens_field = _insn_bytes(dex, insns, k)[2:4]
    k = find(r"invoke-interface \{v\d+, v\d+\}, L[^;]+;\.[a-zA-Z_$]+:\(Landroid/hardware/camera2/CameraCharacteristics\$Key;\)Ljava/lang/Object;")
    chars = int(re.match(r"invoke-interface \{v(\d+),", insns[k][1]).group(1))
    get_method = _insn_bytes(dex, insns, k)[2:4]
    k = find(r"check-cast v\d+, Ljava/lang/Integer;")
    integer_type = _insn_bytes(dex, insns, k)[2:4]
    k = find(r"invoke-virtual \{v\d+\}, Ljava/lang/Integer;\.intValue:\(\)I")
    int_value = _insn_bytes(dex, insns, k)[2:4]
    assert chars <= 15, chars
    body = (
        bytes([0x62, reg]) + lens_field                      # sget-object vR, LENS_FACING
        + bytes([0x72, 0x20]) + get_method + bytes([(reg << 4) | chars, 0x00])  # invoke-interface {vC, vR}, get(Key)
        + bytes([0x0c, reg])                                 # move-result-object vR
        + bytes([0x1f, reg]) + integer_type                  # check-cast vR, Integer
        + bytes([0x38, reg, 0x0b, 0x00])                     # if-eqz vR, +11 -> const/4 vR, #0
        + bytes([0x6e, 0x10]) + int_value + bytes([reg, 0x00])  # invoke-virtual {vR}, Integer.intValue
        + bytes([0x0a, reg])                                 # move-result vR
        + bytes([0x12, (1 << 4) | tmp])                      # const/4 vT, #1 (LENS_FACING_BACK)
        + bytes([0x33, (tmp << 4) | reg, 0x04, 0x00])        # if-ne vR, vT, +4 -> const/4 vR, #0
        + bytes([0x12, (1 << 4) | reg])                      # const/4 vR, #1
        + bytes([0x28, 0x02])                                # goto +2
        + bytes([0x12, reg])                                 # const/4 vR, #0
    )
    assert len(body) == 40, len(body)
    return fix_dex_header(dex[:start] + body + dex[end:]), (method, start)


def patch_camera_app():
    """The camera app of the image carries the JpegHalCorruptImageQuirk patch and the project's
    signature (sign_apk). Consequence: Play's updates of com.google.android.desktop.camera no
    longer install (signature mismatch); the app is updated with each Googlebook OS release
    instead, and an update Play already installed on /data is dropped at the next boot. Always
    rebuilt from the pristine APK, so the result is the same on a pristine and a patched tree."""
    tree, rel, image, _dev = CAMERA_APK
    assert tree == "prod"
    src = os.path.join(WORK, "denetim", "pristine-" + os.path.basename(rel))
    unsigned = src + ".unsigned"
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "wb") as f:
        f.write(pristine_file(rel, image))
    try:
        replacements, site = {}, None
        with zipfile.ZipFile(src) as z:
            for entry in sorted(e for e in z.namelist() if re.fullmatch(r"classes\d*\.dex", e)):
                patched, s = patch_camerax_jpeg_quirk(z.read(entry))
                if patched is not None:
                    assert site is None, f"{rel}: quirk loader in more than one dex ({entry})"
                    replacements[entry], site = patched, (entry,) + s
        assert site, f"{rel}: CameraX JpegHalCorruptImageQuirk loader not found (new CameraX?)"
        rewrite_jar_entries(src, unsigned, replacements)
        dst = os.path.join(PROD_DIR, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        sign_apk(unsigned, dst)
    finally:
        for p in (src, unsigned):
            if os.path.exists(p):
                os.remove(p)
    entry, method, off = site
    print(f"[+] {rel}!{entry}: CameraX JpegHalCorruptImageQuirk on for LENS_FACING_BACK at {off:#x} "
          f"({method.split(':')[0]}); re-signed with {APP_KEYSTORE}")


def patch_product():
    remove_unsupported_apps(PROD_DIR, "prod")
    # mica's Qualcomm display helper only sets vendor.display.disable_rounded_corner on lahaina
    # and lito; on this kona tablet it does nothing, and its domain's rules live in mica's vendor
    # policy, which the tablet does not run (selinux-port, 22 Oct 2026). Without its service the
    # domain needs no rules at all.
    for rel in ("bin/init.qti.display.sh", "etc/init/init.qti.display.rc"):
        p = os.path.join(PROD_DIR, rel)
        if os.path.lexists(p):
            os.remove(p)
    # The Googlebook desktop apps (Tips, Repair mode, Task manager, desktop serviceability) bind
    # to vendor.google.desktop.serviceability, a plain Java priv-app that mica ships on its vendor
    # partition (the tablet keeps the Samsung/LineageOS vendor). Without it they crash with
    # "Failed to bind to ...VendorServiceabilityService", and the bind permission it defines
    # does not exist. The unmodified, Google-signed APK is installed as a product priv-app.
    vs = "vendor.google.desktop.serviceability"
    vs_dir = os.path.join(PROD_DIR, "priv-app", vs)
    os.makedirs(vs_dir, exist_ok=True)
    with open(os.path.join(vs_dir, f"{vs}.apk"), "wb") as f:
        f.write(pristine_file(f"priv-app/{vs}/{vs}.apk", "vendor_a.erofs"))
    os.chmod(os.path.join(vs_dir, f"{vs}.apk"), 0o644)

    # Update /product/etc/build.prop idempotently
    bp_path = os.path.join(PROD_DIR, "etc/build.prop")
    with open(bp_path, "r") as f:
        bp = f.read()
    bp = bp.replace("remote_provisioning.strongbox.rkp_only=1", "remote_provisioning.strongbox.rkp_only=0")
    bp = bp.replace("remote_provisioning.tee.rkp_only=1", "remote_provisioning.tee.rkp_only=0")
    bp = comment_out_prop(bp, "ro.setupwizard.require_network=any")
    # traced listens on this vsock port to relay traces out of the Terminal VM (Microdroid). The
    # tablet's 4.19 kernel has no AF_VSOCK, so traced died at start ("Failed to create
    # vsock://-1:2010", errno 97) and init restarted it every 4 minutes; no VM runs here
    # (terminal/ is a chroot).
    bp = comment_out_prop(bp, "traced.relay_producer_port=vsock://-1:2010")
    for codec in ["aac", "aptx", "aptx_hd", "ldac", "opus", "lhdcv5"]:
        bp = comment_out_prop(bp, f"bluetooth.a2dp.source.{codec}_priority.config=-1")
    for old_line in ["\nro.bpf.kver_override=5.10.239\nro.adb.secure=0\nservice.adb.root=1\npersist.sys.usb.config=adb\n"]:
        bp = bp.replace(old_line, "\n")
    marker = "# --- GOOGLEBOOKOS_T870_PATCH ---"
    bp = bp.split(marker)[0].rstrip("\n") + "\n"  # same result on pristine and patched input
    # Apps (HWUI) and SurfaceFlinger's client composition draw through Vulkan, as on Pixels: mica
    # leaves both on Skia GL, and on the Adreno 650 Vulkan halved the GPU time of both (WhatsApp's
    # animated chat wallpaper 32 -> 17 ms, SurfaceFlinger 19 -> 11.5 ms per frame) and took
    # Settings' scrolling from 24 % to 0.5 % janky frames, with no renderer error over 18 apps
    # (cihaz-kabul/cizim-kabul.sh, kanit/20261023-gl-cizim vs 20261023-vk-cizim).
    bp += (
        f"{marker}\n"
        "ro.hwui.use_vulkan=true\n"
        "debug.renderengine.backend=skiavkthreaded\n"
        "ro.zygote=zygote64\n"
        # No ro.product.property_source_order: Android init's default order
        # (product,odm,vendor,system_ext,system) gives the stock Googlebook OS identity from
        # /product/etc/build.prop (Dell / mica / XPS Googlebook), which Satin (GSA Circle to Search
        # allowlist ddqo.d + ddqp) and Googlebook system components expect. Keystore ID attestation
        # reads ro.product.vendor.* directly (Build.getVendorDeviceIdProperty).
        "ro.gbos.bootlog=1\nro.logd.kernel=true\nro.logd.auditd.dmesg=false\n"
        "ro.bpf.kver_override=5.10.239\nro.adb.secure=0\nservice.adb.root=1\npersist.sys.usb.config=adb\n"
    )
    with open(bp_path, "w") as f:
        f.write(bp)

    # Patch product_property_contexts from pristine copy (14 Samsung properties + bootanim orientation)
    p_pc_path = os.path.join(PROD_DIR, "etc/selinux/product_property_contexts")
    p_pc = pristine_file("etc/selinux/product_property_contexts", "product_a.erofs").decode()
    p_pc_add = """
# --- GOOGLEBOOKOS_T870_PATCH ---
ro.bootanim.set_orientation_                     u:object_r:bootanim_config_prop:s0
service.camera.                                  u:object_r:sec_camera_prop:s0
persist.sys.omcnw_path                           u:object_r:exported_config_prop:s0
persist.sys.omcnw_path2                          u:object_r:exported_config_prop:s0
mdc.                                             u:object_r:exported_config_prop:s0
ro.csc.                                          u:object_r:exported_config_prop:s0
ro.omc.region                                    u:object_r:exported_config_prop:s0
persist.ril.                                     u:object_r:radio_prop:s0
ril.NwNmId                                       u:object_r:telephony_config_prop:s0
ril.NwNmId2                                      u:object_r:telephony_config_prop:s0
ro.multisim.                                     u:object_r:radio_prop:s0
ro.simbased.changetype                           u:object_r:telephony_config_prop:s0
persist.sns.camera_light                         u:object_r:sns_camera_light_prop:s0
ro.factory.sensor.                               u:object_r:exported_system_prop:s0
dev.sdhms.thermal.hv                             u:object_r:exported_system_prop:s0
"""
    with open(p_pc_path, "w") as f:
        f.write(p_pc.rstrip("\n") + "\n" + p_pc_add)

    # Copy lineage compatibility matrix
    shutil.copy2(
        os.path.join(LINEAGE_PARTS, "product/etc/vintf/compatibility_matrix.lineage.xml"),
        os.path.join(PROD_DIR, "etc/vintf/compatibility_matrix.lineage.xml"),
    )

    os.makedirs(os.path.join(PROD_DIR, "etc/security"), exist_ok=True)
    shutil.copy2(adb_keys(), os.path.join(PROD_DIR, "etc/security/adb_keys"))

    # Copy FaceUnlockOverlay.apk and gts7lwifi hardware overlays
    ov_dir = os.path.join(PROD_DIR, "overlay")
    os.makedirs(ov_dir, exist_ok=True)
    shutil.copy2(os.path.join(SRC_FACE, "out/FaceUnlockOverlay.apk"), os.path.join(ov_dir, "FaceUnlockOverlay.apk"))
    for apk in [
        "framework-res__lineage_gts7lwifi__auto_generated_rro_product.apk",
        "SettingsProvider__lineage_gts7lwifi__auto_generated_rro_product.apk",
        "StorageManager__lineage_gts7lwifi__auto_generated_rro_product.apk",
        "SystemUI__lineage_gts7lwifi__auto_generated_rro_product.apk",
    ]:
        shutil.copy2(os.path.join(LINEAGE_PARTS, "product/overlay", apk), os.path.join(ov_dir, apk))
    # The camera app stays as Google ships it: the video-with-JPEG freeze is fixed for every app
    # in framework.jar (yamalar/kamera_jpeg.py), and the app patch (patch_camera_app: CameraX
    # quirk + project signature) would block its Play updates. Kadir, 23 Oct 2026: Play updates
    # matter more. patch_camera_app() is kept for the day the framework path is not enough.
    print("[+] Patched /product partition")

if __name__ == "__main__":
    ensure_trees()
    patch_system()
    patch_system_ext()
    patch_product()
    mica_servisleri.install(SYS_DIR, SYSEXT_DIR, PROD_DIR)  # VPD HAL, no GSC (mica-servisleri/)
    gbos_terminal.install(SYS_DIR, SYSEXT_DIR, PROD_DIR)  # Terminal + Debian environment (terminal/)
    # com.android.virtualization.terminal: the Terminal's compatibility package (terminal/kopru)
    gbos_aosp_terminal.install(SYS_DIR, WORK, pristine_file, _apex_payload_layout, _extract_with_meta)
    gbos_pil_sagligi.install(SYS_DIR, SYSEXT_DIR, PROD_DIR)  # health HAL, Samsung battery nodes (pil-sagligi/)
    # landscape natural orientation: sensor and camera HALs (dogal-yon/hal), then display, touch,
    # pen, boot animation and fingerprint location (yamalar/dogal_yon.py)
    gbos_dogal_yon.install_all(SYS_DIR, SYSEXT_DIR, PROD_DIR)
    reconcile_features()  # mica's hidden features vs the tablet's vendor declarations
    gbos_guncelleyici.install(SYS_DIR)  # updater, recovery package, rollback (guncelleyici/)
    gbos_tablet_modu.install(SYS_DIR)  # quick settings tile for the cover's tablet mode (tablet-modu/)
    # the updater kit, so a wiped tablet still prepares the next release (yamalar/kit_imaj.py)
    gbos_kit_imaj.install(PROD_DIR, RELEASE_INDEPENDENT_INPUTS)
    # every app the finished trees lack against the pristine images: its leftover Play update is
    # removed after the update (written last, after every patch that removes an app)
    gbos_guncelleyici.install_removed(SYS_DIR, SYSEXT_DIR, PROD_DIR, pristine_file, REMOVED_APPS)
    gbos_selinux.uygula(SYS_DIR, SYSEXT_DIR, pristine_file)
    gbos_selinux.dogrula(WORK)
    gbos_pil_sagligi.politika(WORK)
    gbos_dogal_yon_hal.politika(WORK)
    print("ALL_PATCHES_APPLIED_OK")
