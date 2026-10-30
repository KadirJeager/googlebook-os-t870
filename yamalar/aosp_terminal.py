#!/usr/bin/env python3
"""com.android.virtualization.terminal for apps written for Android's own Linux terminal.

The image's com.google.android.virt APEX ships Android's Linux terminal (priv-app
VmTerminalAppGoogle, package com.android.virtualization.terminal). It runs Debian in a KVM
virtual machine, which this tablet cannot run (its kernel runs at EL1 under the Qualcomm
hypervisor); the Googlebook Terminal (terminal/, com.gbos.terminal) gives the same Linux in a
chroot. Apps written for Android's terminal (Antigravity's "Terminal VM Setup") address that
package by name: VIEW android-terminal://start?cmd=&dest_path=&background= with
setPackage("com.android.virtualization.terminal"), SHARE_DIRECTORY, and the provider authority
com.android.virtualization.terminal.provider (getVmStatus, vm_status change notifications). An
intent filter in com.gbos.terminal cannot receive an intent addressed to another package, and an
authority belongs to one package; so the APEX's terminal APK is replaced, at the same path, by
the compatibility package terminal/kopru (built by terminal/build.sh into
terminal/out/<abi>/kopru/VmTerminalApp.apk): same package name, same MANAGE_TERMINAL permission
(signature|preinstalled), the same intent filters and provider authority, forwarding to
com.gbos.terminal, which checks each request, asks the user and reports the real Linux state.
The rest of the APEX (virtualizationservice, the boot and system server jars) is byte for byte
the pristine one.

The APEX payload is rebuilt the way apply_patches.patch_tethering_apex() rebuilds the tethering
APEX: extracted with its metadata (owners, modes, SELinux labels and capabilities, through
apply_patches' _extract_with_meta, passed in), changed, packed again with mkfs.erofs into the
original payload area; apexd checks the signed vbmeta, not the hashtree, of a pre-installed APEX
(that comment and the tablet's running tethering APEX are the evidence). The packing goes
through a POSIX tar stream (mkfs.erofs --tar=f) that carries each file's owner, mode and every
xattr: this APEX has files with both a SELinux label and file capabilities (crosvm, virtmgr,
virtualizationservice), and libprovide_meta.so, which the tethering rebuild uses, keeps only
one xattr per file (it would drop their labels).

    from yamalar import aosp_terminal
    aosp_terminal.install(SYS_DIR, WORK, pristine_file, _apex_payload_layout, _extract_with_meta)

Running it again gives the same APEX (deterministic mkfs: fixed time, the pristine UUID).
Intermediate trees and images in WORK are removed afterwards.
"""
import glob
import io
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terminal  # noqa: E402

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERMINAL = os.path.join(TOOLS_DIR, "terminal")
VIRT_APEX = "system/apex/com.google.android.virt.apex"
PACKAGE = "com.android.virtualization.terminal"
EROFS_BIN = os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin"))


def bridge_apk(abi="arm64"):
    terminal.build(abi)
    path = os.path.join(TERMINAL, "out", abi, "kopru", "VmTerminalApp.apk")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{path} is missing (run terminal/build.sh)")
    return path


def _axml_strings(data, off):
    """Strings of the binary XML string pool chunk at `off` (ResStringPool_header)."""
    _type, hsize, _size, count, _styles, flags, start, _sstart = struct.unpack_from("<HHIIIIII", data, off)
    utf8 = bool(flags & 0x100)
    out = []
    for i in range(count):
        pos = off + start + struct.unpack_from("<I", data, off + hsize + 4 * i)[0]
        if utf8:
            n = data[pos]
            pos += 2 if n & 0x80 else 1           # UTF-16 length, skipped
            n = data[pos]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | data[pos + 1]
                pos += 1
            pos += 1
            out.append(data[pos:pos + n].decode("utf-8"))
        else:
            n = struct.unpack_from("<H", data, pos)[0]
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, pos + 2)[0]
                pos += 2
            pos += 2
            out.append(data[pos:pos + 2 * n].decode("utf-16-le"))
    return out


def package_name(apk):
    """The package attribute of the APK's binary AndroidManifest.xml (AOSP ResourceTypes.h:
    RES_XML_TYPE, RES_STRING_POOL_TYPE, RES_XML_START_ELEMENT_TYPE). Read here instead of with
    aapt2: the tablet's kit runs where only Debian's aapt2 2.19 exists, and it does not open the
    APKs of newer releases (24 Oct 2026, .276 VmTerminalAppGoogle.apk)."""
    with zipfile.ZipFile(apk) as z:
        data = z.read("AndroidManifest.xml")
    if struct.unpack_from("<H", data, 0)[0] != 0x0003:
        raise ValueError(f"{apk}: AndroidManifest.xml is not binary XML")
    strings, off = None, struct.unpack_from("<H", data, 2)[0]
    while off < len(data):
        ctype, _hsize, size = struct.unpack_from("<HHI", data, off)
        if ctype == 0x0001:
            strings = _axml_strings(data, off)
        elif ctype == 0x0102 and strings is not None:
            name = struct.unpack_from("<I", data, off + 20)[0]
            if strings[name] != "manifest":
                raise ValueError(f"{apk}: first element is {strings[name]}, not manifest")
            attr_start, attr_size, attr_count = struct.unpack_from("<HHH", data, off + 24)
            for i in range(attr_count):
                a = off + 16 + attr_start + i * attr_size
                ns, aname, raw = struct.unpack_from("<III", data, a)
                dtype, value = struct.unpack_from("<BI", data, a + 15)
                if strings[aname] == "package" and ns == 0xFFFFFFFF:
                    return strings[raw] if raw != 0xFFFFFFFF else strings[value]
            raise ValueError(f"{apk}: manifest has no package attribute")
        off += size
    raise ValueError(f"{apk}: no manifest element")


def terminal_apk_in(tree):
    """The one APK of Android's terminal in an extracted APEX tree (priv-app/VmTerminalApp*/)."""
    found = sorted(glob.glob(os.path.join(tree, "priv-app", "VmTerminalApp*", "*.apk")))
    if len(found) != 1:
        raise SystemExit(f"{tree}: expected one VmTerminalApp*/ APK under priv-app, found {found}")
    directory = os.path.dirname(found[0])
    extra = sorted(set(os.listdir(directory)) - {os.path.basename(found[0])})
    if extra:
        # oat/ or other files next to the APK would belong to the replaced app.
        raise SystemExit(f"{directory}: unexpected files next to the APK: {extra}")
    return found[0]


def _remove(*paths):
    for p in paths:
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        elif os.path.lexists(p):
            os.remove(p)


def read_meta(meta_path):
    """{path relative to the capture root: {"owner": (uid, gid), "mode": int, "xattrs": {name:
    bytes}}} from a capture of apply_patches._extract_with_meta (ROOT, CHOWN, CHMOD, XATTR rows)."""
    entries = {}
    with open(meta_path) as f:
        kind, root = f.readline().rstrip("\n").split("\t")
        assert kind == "ROOT", f"{meta_path}: no ROOT line"
        for line in f:
            row = line.rstrip("\n").split("\t")
            path = row[1]
            if path != root and not path.startswith(root + "/"):
                raise SystemExit(f"{meta_path}: {path} is outside {root}")
            rel = os.path.relpath(path, root)
            e = entries.setdefault(rel, {"owner": None, "mode": None, "xattrs": {}})
            if row[0] == "CHOWN":
                e["owner"] = (int(row[2]), int(row[3]))
            elif row[0] == "CHMOD":
                e["mode"] = int(row[2], 8) & 0o7777
            elif row[0] == "XATTR":
                e["xattrs"][row[2]] = bytes.fromhex(row[3])
            else:
                raise SystemExit(f"{meta_path}: unknown row {row[0]}")
    return entries


def tar_tree(root, meta, replace, out):
    """Writes `root` as a PAX tar to `out` with the captured owner, mode and xattrs of every
    entry (mtime 0); `replace` maps relative paths to the bytes that go there instead."""
    with tarfile.open(out, "w", format=tarfile.PAX_FORMAT, encoding="utf-8",
                      errors="surrogateescape") as tar:
        paths = ["."]
        for d, dirs, names in os.walk(root):
            dirs.sort()
            for n in sorted(dirs + names):
                paths.append(os.path.relpath(os.path.join(d, n), root))
        for rel in paths:
            full = os.path.join(root, rel)
            st = os.lstat(full)
            m = meta.get(rel)
            if m is None or m["owner"] is None:
                raise SystemExit(f"{rel}: no captured owner")
            info = tarfile.TarInfo(rel)
            info.uid, info.gid = m["owner"]
            info.mtime = 0
            info.pax_headers = {"SCHILY.xattr." + k: v.decode("utf-8", "surrogateescape")
                                for k, v in sorted(m["xattrs"].items())}
            if os.path.islink(full):
                info.type = tarfile.SYMTYPE
                info.linkname = os.readlink(full)
                info.mode = 0o777
                tar.addfile(info)
                continue
            if m["mode"] is None:
                raise SystemExit(f"{rel}: no captured mode")
            info.mode = m["mode"]
            if os.path.isdir(full):
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif os.path.isfile(full):
                if st.st_nlink > 1:
                    raise SystemExit(f"{rel}: hard links are not expected in this APEX")
                if rel in replace:
                    data = replace[rel]
                else:
                    with open(full, "rb") as f:
                        data = f.read()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                raise SystemExit(f"{rel}: unexpected file type")


def install(sys_dir, work, pristine_file, apex_payload_layout, extract_with_meta, abi="arm64",
            apex_rel=VIRT_APEX):
    """Writes sys_dir/<apex_rel> (system/apex/com.google.android.virt.apex): the pristine APEX
    with the compatibility package in place of Android's terminal app. apex_rel names another
    virt APEX (the x86_64 test emulator's com.android.virt.apex) for tests."""
    bridge = bridge_apk(abi)
    if package_name(bridge) != PACKAGE:
        raise SystemExit(f"{bridge} is not {PACKAGE}")
    with open(bridge, "rb") as f:
        bridge_bytes = f.read()
    apex = pristine_file(apex_rel)
    lay = apex_payload_layout(apex)
    payload = apex[lay["data_off"]:lay["data_off"] + lay["size"]]

    img = os.path.join(work, "virt_payload.img")
    clean = os.path.join(work, "virt_clean")
    meta = os.path.join(work, "virt.meta")
    tar = os.path.join(work, "virt.tar")
    new_img = os.path.join(work, "virt_new_payload.img")
    try:
        with open(img, "wb") as f:
            f.write(payload[:lay["image_size"]])
        extract_with_meta(img, clean, meta)
        target = terminal_apk_in(clean)
        if package_name(target) != PACKAGE:
            raise SystemExit(f"{target} is not {PACKAGE}: the APEX layout changed")
        # Same path: the captured owner, mode and SELinux label of the file apply unchanged.
        tar_tree(clean, read_meta(meta), {os.path.relpath(target, clean): bridge_bytes}, tar)
        _remove(new_img)
        subprocess.run([os.path.join(EROFS_BIN, "mkfs.erofs"), "-zlz4hc", "-b", "4096", "-T", "0",
                        "-U", lay["uuid"], "--tar=f", "--quiet", new_img, tar],
                       stdout=subprocess.DEVNULL, check=True)
        with open(new_img, "rb") as f:
            erofs = f.read()
    finally:
        _remove(img, clean, meta, tar, new_img)
    area = lay["image_size"]
    if len(erofs) > area:
        raise SystemExit(f"new virt payload too large: {len(erofs)} > {area}")
    # The original hashtree, vbmeta and AVB footer stay after the filesystem area.
    full = erofs.ljust(area, b"\0") + payload[area:]
    assert len(full) == lay["size"]
    raw = bytearray(apex)
    crc = struct.pack("<I", zlib.crc32(full) & 0xffffffff)
    raw[lay["data_off"]:lay["data_off"] + lay["size"]] = full
    raw[lay["local_crc"]:lay["local_crc"] + 4] = crc
    raw[lay["cd_crc"]:lay["cd_crc"] + 4] = crc
    dst = os.path.join(sys_dir, apex_rel)
    with open(dst, "wb") as f:
        f.write(raw)
    os.chmod(dst, 0o644)
    with zipfile.ZipFile(dst) as z:
        bad = z.testzip()
        if bad:
            raise SystemExit(f"{dst}: CRC mismatch in {bad}")
    print(f"[+] com.google.android.virt.apex: {PACKAGE} is the Googlebook Terminal's "
          f"compatibility package (EROFS={len(erofs)} bytes)")
