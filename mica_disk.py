"""
Partition layout of a Googlebook (mica) recovery disk image: the GPT of the raw disk and the
logical partitions inside its `super` partition (Android liblp metadata).

Formats follow AOSP:
  GPT:  UEFI spec (header at LBA 1, 128-byte entries).
  LP:   system/core/fs_mgr/liblp/include/liblp/metadata_format.h (geometry at
        LP_PARTITION_RESERVED_BYTES, primary metadata slot 0 after two geometry copies).
All offsets returned are absolute byte offsets in the raw disk image.
"""
import hashlib
import struct
import zlib

SECTOR = 512
LP_GEOMETRY_MAGIC = 0x616C4467
LP_GEOMETRY_SIZE = 4096
LP_HEADER_MAGIC = 0x414C5030
LP_RESERVED_BYTES = 4096
LP_TARGET_TYPE_LINEAR = 0
LP_TARGET_TYPE_ZERO = 1


def parse_gpt(head):
    """`head` must start at byte 0 of the disk and contain the GPT header and entries.
    Returns {name: (start, end)}."""
    hdr = head[SECTOR:2 * SECTOR]
    if hdr[:8] != b"EFI PART":
        raise ValueError("GPT header not found")
    hsize = struct.unpack_from("<I", hdr, 12)[0]
    hcopy = bytearray(hdr[:hsize])
    hcopy[16:20] = bytes(4)
    if zlib.crc32(hcopy) != struct.unpack_from("<I", hdr, 16)[0]:
        raise ValueError("GPT header CRC mismatch")
    lba, count, size = struct.unpack_from("<QII", hdr, 72)
    entries = head[lba * SECTOR:lba * SECTOR + count * size]
    if len(entries) != count * size:
        raise ValueError("GPT entries not fully contained in the given data")
    if zlib.crc32(entries) != struct.unpack_from("<I", hdr, 88)[0]:
        raise ValueError("GPT entries CRC mismatch")
    parts = {}
    for i in range(count):
        e = entries[i * size:(i + 1) * size]
        if not any(e[:16]):
            continue
        first, last = struct.unpack_from("<QQ", e, 32)
        name = e[56:128].decode("utf-16le").rstrip("\0")
        parts[name] = (first * SECTOR, (last + 1) * SECTOR)
    return parts


def gpt_bytes_needed(head_first_sectors):
    """Bytes from the start of the disk needed to parse the GPT, given at least 2 sectors."""
    hdr = head_first_sectors[SECTOR:2 * SECTOR]
    if hdr[:8] != b"EFI PART":
        raise ValueError("GPT header not found")
    lba, count, size = struct.unpack_from("<QII", hdr, 72)
    return lba * SECTOR + count * size


def lp_bytes_needed(super_head):
    """Bytes from the start of `super` needed to parse its primary metadata, given at least
    LP_RESERVED_BYTES + LP_GEOMETRY_SIZE bytes."""
    geo = _geometry(super_head)
    return LP_RESERVED_BYTES + 2 * LP_GEOMETRY_SIZE + geo["metadata_max_size"]


def _geometry(super_head):
    g = super_head[LP_RESERVED_BYTES:LP_RESERVED_BYTES + LP_GEOMETRY_SIZE]
    magic, struct_size = struct.unpack_from("<II", g, 0)
    if magic != LP_GEOMETRY_MAGIC:
        raise ValueError("LP geometry magic not found")
    raw = bytearray(g[:struct_size])
    expected = bytes(raw[8:40])
    raw[8:40] = bytes(32)
    if hashlib.sha256(raw).digest() != expected:
        raise ValueError("LP geometry checksum mismatch")
    max_size, slots, block_size = struct.unpack_from("<III", g, 40)
    return {"metadata_max_size": max_size, "metadata_slot_count": slots,
            "logical_block_size": block_size}


def parse_lp(super_head, super_start):
    """`super_head` starts at the first byte of the super partition. Returns
    {name: [(abs_start, abs_end, kind), ...]} in logical order; kind is "linear" or "zero"."""
    _geometry(super_head)
    base = LP_RESERVED_BYTES + 2 * LP_GEOMETRY_SIZE  # slot 0 primary metadata
    h = super_head[base:]
    magic, major, minor, header_size = struct.unpack_from("<IHHI", h, 0)
    if magic != LP_HEADER_MAGIC or major != 10:
        raise ValueError(f"unsupported LP header magic={magic:#x} version={major}.{minor}")
    hraw = bytearray(h[:header_size])
    hsum = bytes(hraw[12:44])
    hraw[12:44] = bytes(32)
    if hashlib.sha256(hraw).digest() != hsum:
        raise ValueError("LP header checksum mismatch")
    tables_size = struct.unpack_from("<I", h, 44)[0]
    tables = h[header_size:header_size + tables_size]
    if len(tables) != tables_size or hashlib.sha256(tables).digest() != bytes(h[48:80]):
        raise ValueError("LP tables checksum mismatch")

    def table(index):
        off, num, esize = struct.unpack_from("<III", h, 80 + 12 * index)
        return [tables[off + i * esize:off + (i + 1) * esize] for i in range(num)]

    partitions, extents = table(0), table(1)
    devices = table(3)
    if len(devices) != 1:
        raise ValueError("only a single super block device is supported")
    out = {}
    for p in partitions:
        name = p[:36].split(b"\0")[0].decode()
        _attrs, first, num, _group = struct.unpack_from("<IIII", p, 36)
        parts = []
        for e in extents[first:first + num]:
            sectors, ttype, data, source = struct.unpack_from("<QIQI", e)
            if source != 0:
                raise ValueError(f"{name}: extent on block device {source}")
            if ttype == LP_TARGET_TYPE_LINEAR:
                start = super_start + data * SECTOR
                parts.append((start, start + sectors * SECTOR, "linear"))
            elif ttype == LP_TARGET_TYPE_ZERO:
                parts.append((0, sectors * SECTOR, "zero"))
            else:
                raise ValueError(f"{name}: unknown extent type {ttype}")
        out[name] = parts
    return out
