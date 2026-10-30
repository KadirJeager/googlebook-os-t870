#!/usr/bin/env python3
"""
Super headers (LP metadata, the first MiB of the tablet's super partition) of Googlebook OS.

  uret.py yaz DOSYA       write the current header (googlebookos-2) uncompressed
  uret.py denetle         check the stored headers against SHA256SUMS and print each one's slots
  uret.py yakala [--dosya F]
                          take this tablet's LineageOS header (adb, from recovery or a root
                          shell; or the first MiB of super saved in F) as lineage.bin.xz and
                          record it and the Googlebook OS header made from it in SHA256SUMS

Headers (SHA256SUMS lists the ones this checkout knows, "<sha256>  <name>"):
  lineage.bin.xz          the tablet's own LineageOS header, byte-exact first MiB of super. Its
                          system/system_ext/product extents in the lower half of super are the
                          way back to LineageOS (never written over). The one in the repository is
                          the author's (LineageOS 24.0 gts7lwifi); another tablet records its own
                          with `yakala` before the first install.
  googlebookos-2          made here from lineage (relocate): vendor and odm stay where LineageOS
                          put them (shared), system, system_ext and product move to the space
                          after LineageOS's last extent, MiB aligned: system 1432 MiB, system_ext
                          348 MiB, product up to the end of super. Only those three extents and
                          the checksums of metadata slot 0 change.
  googlebookos-1.bin.xz   the author's first Googlebook OS header (21 Oct 2026): the same layout
                          with product 3096 MiB. Installers older than GBOS_KUR_BICIM=2 know only
                          it (guncelleyici/motor/motor.py bridge images); a checkout that recorded
                          another tablet's header drops it from SHA256SUMS.

Only metadata slot 0 (its primary and backup copies) describes the running layout; the tablet is
not A/B and slot 1 still holds Samsung's factory layout, which is left as it is. The format is
AOSP liblp's (system/core/fs_mgr/liblp/include/liblp/metadata_format.h): each copy is a
LpMetadataHeader whose header_checksum and tables_checksum (SHA-256) cover the header and the
partition/extent/group/block device tables.
"""
import hashlib
import lzma
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MIB = 1 << 20
SECTOR = 512
LP_RESERVED_BYTES = 4096
LP_GEOMETRY_SIZE = 4096
LP_HEADER_MAGIC = 0x414C5030
LP_TARGET_TYPE_LINEAR = 0

SUMS = os.path.join(HERE, "SHA256SUMS")
CURRENT = "googlebookos-2"
SECTORS_PER_MIB = MIB // SECTOR
# Googlebook OS partitions in the order they follow LineageOS's last extent; None grows to the end
# of super. tablete-yaz.py, make_ext4_images.py and the updater take the slots from the header.
LAYOUT = [("system", 1432), ("system_ext", 348), ("product", None)]
LINEAGE_PARTITIONS = {"system", "system_ext", "product", "vendor", "odm"}


def read_sums(path=SUMS):
    """{name: sha256} of the headers this checkout knows (none before `yakala`)."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                sha, name = line.split()
                out[name] = sha
    return out


SHA = read_sums()


def stored(name):
    with open(os.path.join(HERE, f"{name}.bin.xz"), "rb") as f:
        data = lzma.decompress(f.read())
    if len(data) != MIB:
        raise ValueError(f"{name}: {len(data)} bytes, expected the first MiB of super")
    return data


def geometry(head):
    g = head[LP_RESERVED_BYTES:LP_RESERVED_BYTES + LP_GEOMETRY_SIZE]
    max_size, slots = struct.unpack_from("<II", g, 40)
    return max_size, slots


def copies(head, slot):
    """Byte offsets of metadata slot `slot`'s primary and backup copies (liblp
    GetPrimaryMetadataOffset / GetBackupMetadataOffset)."""
    max_size, slots = geometry(head)
    base = LP_RESERVED_BYTES + 2 * LP_GEOMETRY_SIZE
    return [base + slot * max_size, base + max_size * slots + slot * max_size]


def _tables(h):
    header_size, tables_size = struct.unpack_from("<I", h, 8)[0], struct.unpack_from("<I", h, 44)[0]
    return header_size, tables_size


def check_copy(h):
    """Raises unless the copy's magic and both checksums are right."""
    magic, major = struct.unpack_from("<IH", h, 0)
    if magic != LP_HEADER_MAGIC or major != 10:
        raise ValueError("not an LP metadata header (v10)")
    header_size, tables_size = _tables(h)
    raw = bytearray(h[:header_size])
    want = bytes(raw[12:44])
    raw[12:44] = bytes(32)
    if hashlib.sha256(raw).digest() != want:
        raise ValueError("header checksum")
    if hashlib.sha256(h[header_size:header_size + tables_size]).digest() != bytes(h[48:80]):
        raise ValueError("tables checksum")


def super_sectors(h):
    """Size of super in sectors, from the block device table (one device)."""
    header_size, _ = _tables(h)
    off, num, esize = struct.unpack_from("<III", h, 80 + 12 * 3)
    if num != 1:
        raise ValueError("one block device expected")
    dev = h[header_size + off:header_size + off + esize]
    size = struct.unpack_from("<Q", dev, 16)[0]  # after first_logical_sector, alignment, offset
    if size % SECTOR:
        raise ValueError("super size not a whole number of sectors")
    return size // SECTOR


def partitions(h):
    """[(name, [(sector count, target type, first sector, source)])] of one metadata copy."""
    header_size, _ = _tables(h)
    t = header_size
    poff, pnum, psize = struct.unpack_from("<III", h, 80)
    eoff, _enum, esize = struct.unpack_from("<III", h, 80 + 12)
    out = []
    for i in range(pnum):
        p = t + poff + i * psize
        name = bytes(h[p:p + 36]).split(b"\0")[0].decode()
        _attrs, first, num, _group = struct.unpack_from("<IIII", h, p + 36)
        out.append((name, [struct.unpack_from("<QIQI", h, t + eoff + j * esize)
                           for j in range(first, first + num)]))
    return out


def relocate(head):
    """The Googlebook OS header made from a LineageOS header: LAYOUT's partitions get one linear
    extent each after the last extent of the LineageOS layout, in slot 0's two copies, both
    checksums recomputed. Raises unless `head` is a LineageOS layout with room for them."""
    out = bytearray(head)
    for at in copies(head, 0):
        check_copy(out[at:])
        parts = partitions(out[at:])
        if {n for n, _ in parts} != LINEAGE_PARTITIONS:
            raise ValueError(f"not a LineageOS layout: partitions {sorted(n for n, _ in parts)}")
        header_size, tables_size = _tables(out[at:])
        t = at + header_size
        poff, pnum, psize = struct.unpack_from("<III", out, at + 80)
        eoff, _enum, esize = struct.unpack_from("<III", out, at + 80 + 12)
        end, entry = 0, {}
        for i, (name, extents) in enumerate(parts):
            if len(extents) != 1 or extents[0][1] != LP_TARGET_TYPE_LINEAR or extents[0][3] != 0:
                raise ValueError(f"{name}: one linear extent on super expected")
            end = max(end, extents[0][2] + extents[0][0])
            first = struct.unpack_from("<I", out, t + poff + i * psize + 40)[0]
            entry[name] = t + eoff + first * esize
        start = -(-end // SECTORS_PER_MIB) * SECTORS_PER_MIB
        last = super_sectors(out[at:])
        for name, mib in LAYOUT:
            size = last - start if mib is None else mib * SECTORS_PER_MIB
            if size <= 0 or start + size > last:
                raise ValueError(f"{name}: no room after LineageOS (super ends at sector {last})")
            struct.pack_into("<Q", out, entry[name], size)
            struct.pack_into("<Q", out, entry[name] + 12, start)
            start += size
        out[at + 48:at + 80] = hashlib.sha256(bytes(out[t:t + tables_size])).digest()
        out[at + 12:at + 44] = bytes(32)
        out[at + 12:at + 44] = hashlib.sha256(bytes(out[at:at + header_size])).digest()
        check_copy(out[at:])
    return bytes(out)


def slots(head):
    """{partition: (first MiB, size in MiB)} of LAYOUT's partitions in a Googlebook OS header."""
    at = copies(head, 0)[0]
    check_copy(head[at:])
    out = {}
    for name, extents in partitions(head[at:]):
        if name in dict(LAYOUT):
            (sectors, _type, data, _src), = extents
            if data % SECTORS_PER_MIB or sectors % SECTORS_PER_MIB:
                raise ValueError(f"{name}: slot not MiB aligned")
            out[name] = (data // SECTORS_PER_MIB, sectors // SECTORS_PER_MIB)
    return out


def header(name, sums=None):
    """The first MiB of super for a known header, checked against SHA256SUMS."""
    sums = SHA if sums is None else sums
    if name not in sums:
        if "lineage" not in sums:
            raise SystemExit(f"{SUMS}: no header recorded yet. Record this tablet's LineageOS "
                             "header first: super-baslik/uret.py yakala (tablet in LineageOS "
                             "recovery with ADB on)")
        raise ValueError(f"{name}: not in {SUMS}")
    data = relocate(stored("lineage")) if name == CURRENT else stored(name)
    got = hashlib.sha256(data).hexdigest()
    if got != sums[name]:
        raise ValueError(f"{name}: sha256 {got}, expected {sums[name]}")
    return data


def googlebook_headers():
    """Names of the Googlebook OS headers this checkout knows (googlebookos-*)."""
    return sorted(n for n in SHA if n.startswith("googlebookos-"))


def capture():
    """The first MiB of super, read over adb from recovery or a root shell."""
    sys.path.insert(0, os.path.dirname(HERE))
    from t870_cihaz import seri
    import subprocess
    cmd = "dd if=/dev/block/by-name/super bs=1048576 count=1 2>/dev/null"
    r = subprocess.run(["adb", "-s", seri(), "exec-out", cmd], capture_output=True)
    if len(r.stdout) != MIB:
        r = subprocess.run(["adb", "-s", seri(), "exec-out", f"su -c '{cmd}'"], capture_output=True)
    if len(r.stdout) != MIB:
        raise SystemExit("could not read super over adb: boot LineageOS recovery (Advanced > "
                         "Enable ADB) or allow root for adb, then try again")
    return r.stdout


def record(head):
    """Takes `head` as this tablet's LineageOS header: lineage.bin.xz and SHA256SUMS."""
    gbos = relocate(head)  # raises unless it is a LineageOS layout with room for Googlebook OS
    lineage_sha = hashlib.sha256(head).hexdigest()
    sums = {"lineage": lineage_sha, CURRENT: hashlib.sha256(gbos).hexdigest()}
    if SHA.get("lineage") == lineage_sha and "googlebookos-1" in SHA:
        sums["googlebookos-1"] = SHA["googlebookos-1"]  # the author's tablet: keep its history
    tmp = os.path.join(HERE, "lineage.bin.xz.tmp")
    with open(tmp, "wb") as f:
        f.write(lzma.compress(head, preset=9 | lzma.PRESET_EXTREME))
    os.replace(tmp, os.path.join(HERE, "lineage.bin.xz"))
    with open(SUMS + ".tmp", "w") as f:
        f.write("# Super headers this checkout knows (uret.py): sha256 of the first MiB of super\n")
        for name in sorted(sums):
            f.write(f"{sums[name]}  {name}\n")
    os.replace(SUMS + ".tmp", SUMS)
    return sums, slots(gbos)


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "yaz":
        data = header(CURRENT)
        with open(args[1] + ".tmp", "wb") as f:
            f.write(data)
        os.replace(args[1] + ".tmp", args[1])
        print(f"{args[1]}: {CURRENT} {SHA[CURRENT]}")
        return 0
    if args == ["denetle"]:
        for name in SHA:
            data = header(name)
            extra = "" if name == "lineage" else "  " + " ".join(
                f"{n} {f}+{s}" for n, (f, s) in sorted(slots(data).items()))
            print(f"{name} {SHA[name]}{extra}")
        return 0
    if args and args[0] == "yakala" and (len(args) == 1 or (len(args) == 3 and args[1] == "--dosya")):
        if len(args) == 3:
            with open(args[2], "rb") as f:
                head = f.read(MIB + 1)
        else:
            head = capture()
        if len(head) != MIB:
            raise SystemExit(f"{len(head)} bytes: the first MiB of super expected")
        known = [n for n, sha in SHA.items() if sha == hashlib.sha256(head).hexdigest()]
        if known and known[0] != "lineage":
            raise SystemExit(f"super already holds Googlebook OS ({known[0]}); nothing to record")
        try:
            sums, gbos_slots = record(head)
        except ValueError as e:
            raise SystemExit(f"not a LineageOS super header this tool can use: {e}")
        for name in sorted(sums):
            print(f"{name} {sums[name]}")
        print("Googlebook OS slots (MiB): " + ", ".join(
            f"{n} {f}+{s}" for n, (f, s) in sorted(gbos_slots.items())))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
