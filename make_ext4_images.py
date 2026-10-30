#!/usr/bin/env python3
import os
import re
import stat
import subprocess
import time
import uuid

WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")


def slot_blocks(name):
    """4 KiB blocks of the partition's slot in super: every image fills its slot exactly
    (tablete-yaz.py SLOTS, for the super header the images are made for)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tablete_yaz", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tablete-yaz.py"))
    ty = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ty)
    return ty.SLOTS[name][1] * (1 << 20) // 4096


# File timestamps inside the image. PackageManager keeps parsed system packages in
# /data/system/package_cache and treats an entry as current while the package directory's
# mtime is older than the cache file. With a fixed old timestamp a reflashed image would keep
# serving stale manifests, so every build stamps its own time (SOURCE_DATE_EPOCH pins it for
# reproducible builds).
IMAGE_TIMESTAMP = int(os.environ.get("SOURCE_DATE_EPOCH", time.time()))
# Same inputs + same timestamp => byte-identical image: fixed filesystem UUID and directory
# hash seed (mke2fs would pick random ones) and a fixed clock for every e2fsprogs tool.
E2FS_ENV = dict(os.environ, E2FSPROGS_FAKE_TIME=str(IMAGE_TIMESTAMP),
                E2FSCK_TIME=str(IMAGE_TIMESTAMP))


def fs_uuid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"googlebookos-t870/{name}/{IMAGE_TIMESTAMP}"))

SPECIAL_SELINUX = {
    "/cache": "u:object_r:cache_file:s0",
    "/carrier": "u:object_r:carrier_file:s0",
    "/dqmdbg": "u:object_r:dqmdbg_file:s0",
    "/efs": "u:object_r:efs_file:s0",
    "/misc": "u:object_r:misc_file:s0",
    "/omr": "u:object_r:omr_file:s0",
    "/optics": "u:object_r:vendor_file:s0",
    "/prism": "u:object_r:system_file:s0",
    "/spu": "u:object_r:spu_file:s0",
    "/system/bin/linker": "u:object_r:system_linker_exec:s0",
    "/system/bin/bootstrap/linker": "u:object_r:system_linker_exec:s0",
    "/system/bin/netbpfload": "u:object_r:bpfloader_exec:s0",
    "/system/bin/bpfloader": "u:object_r:bpfloader_platform_exec:s0",
    "/system_ext/bin/hwservicemanager": "u:object_r:hwservicemanager_exec:s0",
    "/system_ext/bin/wificond": "u:object_r:wificond_exec:s0",
    "/system_ext/bin/hw/android.hidl.allocator@1.0-service": "u:object_r:hal_allocator_default_exec:s0",
}

# Files the patches add have no entry in the *.meta capture. They are labelled by the device's own
# file_contexts, merged in the order init loads them (plat, system_ext, product); libselinux in
# e2fsdroid then applies its usual precedence (literal entries and later entries win), exactly as
# restorecon would on the tablet. Each module adds its labels to these files (e.g.
# yamalar/mica_servisleri.py, yamalar/terminal.py), never to this script.
DEVICE_FILE_CONTEXTS = [
    f"{WORK}/sys/system/etc/selinux/plat_file_contexts",
    f"{WORK}/sysext/etc/selinux/system_ext_file_contexts",
    f"{WORK}/prod/etc/selinux/product_file_contexts",
]
# Labels a new file must never end up with: they mean no file_contexts entry matched it.
FALLBACK_LABELS = {"u:object_r:rootfs:s0", "u:object_r:unlabeled:s0"}

SPECIAL_FS_CONFIG = {
    "/cache": (1000, 2001, 0o770),
    "/carrier": (0, 0, 0o755),
    "/dqmdbg": (0, 0, 0o755),
    "/efs": (0, 0, 0o755),
    "/misc": (0, 0, 0o755),
    "/omr": (0, 0, 0o755),
    "/optics": (0, 0, 0o755),
    "/prism": (0, 0, 0o755),
    "/spu": (0, 0, 0o755),
}

def meta_root(meta_path):
    """Directory a *.meta capture was recorded from (ROOT line, or the first entry's path)."""
    with open(meta_path, "r", errors="replace") as f:
        return f.readline().rstrip("\n").split("\t")[1]


def parse_meta(meta_path, extract_root, mount_prefix):
    # Paths in the capture are absolute paths of the tree it was recorded from, which need not
    # be where the tree lives now.
    extract_root = meta_root(meta_path)
    uid_gid = {}
    modes = {}
    selinux = {}
    caps = {}
    with open(meta_path, "r", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            kind, path = parts[0], parts[1]
            if not path.startswith(extract_root):
                continue
            rel = path[len(extract_root):]
            if not rel:
                rel = "/"
            if mount_prefix != "/":
                full = mount_prefix + ("" if rel == "/" else rel)
            else:
                full = rel
            if kind == "CHOWN" and len(parts) >= 4:
                uid_gid[full] = (int(parts[2]), int(parts[3]))
            elif kind == "CHMOD" and len(parts) >= 3:
                modes[full] = int(parts[2], 8) & 0o7777
            elif kind == "XATTR" and len(parts) >= 4:
                xname, xval = parts[2], parts[3]
                if xname == "security.selinux":
                    raw = bytes.fromhex(xval).rstrip(b"\x00").decode("ascii", errors="replace")
                    selinux[full] = raw
                elif xname == "security.capability":
                    raw = bytes.fromhex(xval)
                    if len(raw) == 20:
                        low = int.from_bytes(raw[4:8], "little")
                        high = int.from_bytes(raw[12:16], "little")
                        caps[full] = (high << 32) | low
    return uid_gid, modes, selinux, caps

def device_file_contexts():
    out = []
    for path in DEVICE_FILE_CONTEXTS:
        with open(path) as f:
            out.append(f"# --- {os.path.basename(path)} ---")
            out.extend(line.rstrip("\n") for line in f)
    return out


def image_labels(img_path, mount_point, vpaths):
    """security.selinux of each path as e2fsdroid wrote it into the image (read back with debugfs)."""
    if not vpaths:
        return {}
    prefix = "" if mount_point == "/" else mount_point
    cmds = f"{img_path}.labels.cmd"
    with open(cmds, "w") as f:
        for v in vpaths:
            f.write(f'ea_get -V "{v[len(prefix):] or "/"}" security.selinux\n')
    try:
        out = subprocess.run(["debugfs", "-f", cmds, img_path], capture_output=True, text=True,
                             check=True).stdout
    finally:
        os.remove(cmds)
    labels, current = {}, iter(vpaths)
    vpath = None
    for line in out.splitlines():
        if line.startswith("debugfs: ea_get"):
            vpath = next(current)
        elif vpath is not None and line.strip():
            labels[vpath] = line.strip().rstrip("\x00")
            vpath = None
    return labels


def build_ext4(name, extract_root, meta_path, mount_point, label):
    print(f"=== Building {name}.img (mount={mount_point}, label={label}) ===")
    uid_gid, modes, selinux, caps = parse_meta(meta_path, extract_root, mount_point)

    fsc_lines = []
    fc_lines = []

    if mount_point == "/":
        fc_lines.append("/.* u:object_r:rootfs:s0")
        fsc_lines.append("/ 0 0 0755")
    else:
        fc_lines.append(f"{mount_point}(/.*)? u:object_r:system_file:s0")
        fsc_lines.append("/ 0 0 0755")

    fc_lines.extend(device_file_contexts())
    new_paths = []

    inode_count = 16
    total_bytes = 0

    for root, dirs, files in os.walk(extract_root, followlinks=False):
        entries = [("", root)]
        for d in dirs:
            entries.append((d, os.path.join(root, d)))
        for fl in files:
            entries.append((fl, os.path.join(root, fl)))
        if root != extract_root:
            entries = entries[1:]  # root itself was already processed by parent

        for _, full_host in entries:
            inode_count += 1
            st = os.lstat(full_host)
            if stat.S_ISREG(st.st_mode):
                total_bytes += ((st.st_size + 4095) // 4096) * 4096
            else:
                total_bytes += 4096

            rel = full_host[len(extract_root):]
            if not rel:
                rel = "/"
            if mount_point != "/":
                vpath = mount_point + ("" if rel == "/" else rel)
            else:
                vpath = rel

            if vpath in SPECIAL_FS_CONFIG:
                u, g, m = SPECIAL_FS_CONFIG[vpath]
            else:
                if vpath in uid_gid:
                    u, g = uid_gid[vpath]
                else:
                    if "/bin/" in vpath or vpath.endswith("/bin") or vpath.endswith("/bin/hw"):
                        u, g = 0, 2000
                    else:
                        u, g = 0, 0

                if vpath in modes:
                    m = modes[vpath]
                elif stat.S_ISLNK(st.st_mode):
                    m = 0o777
                elif stat.S_ISDIR(st.st_mode):
                    m = 0o755
                else:
                    if "/bin/" in vpath:
                        m = 0o755
                    else:
                        m = 0o644

            if vpath in SPECIAL_SELINUX:
                sc = SPECIAL_SELINUX[vpath]
            elif vpath in selinux:
                sc = selinux[vpath]
            else:
                sc = None  # added by a patch: labelled by the device's file_contexts
                new_paths.append(vpath)

            cap_str = ""
            if vpath in caps and caps[vpath] != 0:
                cap_str = f" capabilities=0x{caps[vpath]:x}"

            if not (mount_point == "/" and vpath == "/"):
                fsc_lines.append(f"{vpath} {u} {g} {m:04o}{cap_str}")
            if sc is not None:
                fc_lines.append(f"{re.escape(vpath)} {sc}")

    fsc_path = f"{WORK}/{name}_fs_config"
    fc_path = f"{WORK}/{name}_file_contexts"
    with open(fsc_path, "w") as f:
        f.write("\n".join(fsc_lines) + "\n")
    with open(fc_path, "w") as f:
        f.write("\n".join(fc_lines) + "\n")

    inodes_to_alloc = ((inode_count + 2048 + 1023) // 1024) * 1024
    total_blocks = slot_blocks(name)

    img_path = f"{WORK}/{name}.img"
    if os.path.exists(img_path):
        os.remove(img_path)

    print(f"  inodes={inodes_to_alloc}, total_blocks={total_blocks} ({total_blocks*4096/1024/1024:.1f} MB), used_est={total_bytes/1024/1024:.1f} MB")
    subprocess.run([
        "mke2fs", "-F",
        "-U", fs_uuid(name),
        "-E", f"hash_seed={fs_uuid(name + '/hash')}",
        "-L", label,
        "-I", "256",
        "-N", str(inodes_to_alloc),
        "-M", mount_point,
        "-m", "0",
        "-t", "ext4",
        "-b", "4096",
        "-O", "^has_journal,^metadata_csum,^64bit",
        img_path,
        str(total_blocks)
    ], check=True, env=E2FS_ENV)

    subprocess.run([
        "e2fsdroid", "-e",
        "-T", str(IMAGE_TIMESTAMP),
        "-f", extract_root,
        "-C", fsc_path,
        "-S", fc_path,
        "-a", mount_point,
        img_path
    ], check=True, env=E2FS_ENV)

    subprocess.run(["e2fsck", "-fy", img_path], check=False, env=E2FS_ENV)

    labels = image_labels(img_path, mount_point, new_paths)
    report = f"{WORK}/{name}_new_file_labels.txt"
    with open(report, "w") as f:
        for v in new_paths:
            f.write(f"{v} {labels.get(v, '?')}\n")
    bad = [v for v in new_paths if labels.get(v) in FALLBACK_LABELS or v not in labels]
    counts = {}
    for v in new_paths:
        counts[labels.get(v, "?")] = counts.get(labels.get(v, "?"), 0) + 1
    print(f"  {len(new_paths)} files added by patches, labels: "
          + ", ".join(f"{k.split(':')[2] if k.count(':') >= 2 else k}={n}"
                      for k, n in sorted(counts.items(), key=lambda kv: -kv[1])) + f" ({report})")
    if bad:
        raise SystemExit(f"{name}: no file_contexts entry labels {len(bad)} added files, e.g. "
                         f"{bad[:5]}; add their labels to the module that installs them")
    sz = os.path.getsize(img_path)
    print(f"[+] Built {img_path}: {sz} bytes ({sz/1024/1024:.2f} MB, {sz//512} sectors)")
    return sz

_PARTS = {
    "system": (f"{WORK}/sys", f"{WORK}/sys.meta", "/", "/"),
    "system_ext": (f"{WORK}/sysext", f"{WORK}/sysext.meta", "/system_ext", "system_ext"),
    "product": (f"{WORK}/prod", f"{WORK}/prod.meta", "/product", "product"),
}


def main(argv):
    total = 0
    for part in argv or list(_PARTS):
        total += build_ext4(part, *_PARTS[part])
    print(f"TOTAL SIZE: {total} bytes ({total/1024/1024:.2f} MB, {total//512} sectors)")


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
