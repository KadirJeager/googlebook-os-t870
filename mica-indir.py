#!/usr/bin/env python3
"""
Finds the current Googlebook (mica) release in Google's recovery image listing and extracts
its logical partitions without downloading or storing the whole 19 GB disk image.

  mica-indir.py                 # download the latest STABLE release if it is newer than ours
  mica-indir.py --duzen         # only read the partition layout (downloads ~100 MB), print it
  mica-indir.py --zorla         # download even if the release is already present

recovery.zip holds one deflated raw disk image. The zip directory is read with HTTP range
requests, the compressed entry is streamed and inflated in order, the GPT and the super
partition's LP metadata are parsed as soon as they have streamed past, and only the bytes of
the wanted logical partitions are written. Every image is then checked with fsck.erofs.

Output: mica-imaj/<buildidentifier>/<partition>.erofs, layout.json, kaynak.json;
mica-imaj/guncel points at the newest complete release (what apply_patches.py reads).
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
import urllib.request
import zlib

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import mica_disk  # noqa: E402

LISTING = "https://dl.google.com/dl/edgedl/device/recovery/production_recovery.json"
IMAGES = os.path.join(TOOLS, "mica-imaj")
CURRENT = os.path.join(IMAGES, "guncel")
WANTED = ["system_a", "system_ext_a", "product_a", "vendor_a"]
FSCK_EROFS = os.path.join(os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin")), "fsck.erofs")
CHUNK = 8 << 20
PREFETCH = 4


def log(msg):
    print(msg, flush=True)


def http_range(url, start, end):
    """Bytes [start, end] (inclusive) of url, retried."""
    last = None
    for attempt in range(10):
        try:
            req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if len(data) == end - start + 1:
                return data
            last = IOError(f"short read {len(data)} != {end - start + 1}")
        except Exception as e:  # network errors are retried
            last = e
        time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"range {start}-{end} failed: {last}")


def http_size(url):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return int(resp.headers["Content-Length"])


def latest_release(channel):
    with urllib.request.urlopen(LISTING, timeout=60) as resp:
        items = json.load(resp)
    found = [x for x in items if x.get("device") == "mica" and x.get("channel") == channel]
    if not found:
        raise RuntimeError(f"no mica {channel} release in {LISTING}")
    return max(found, key=lambda x: x["buildidentifier"])


def zip_entry(url):
    """(data_offset, compressed_size, uncompressed_size) of the single deflated entry."""
    size = http_size(url)
    tail_len = min(size, 65536 + 22)
    tail = http_range(url, size - tail_len, size - 1)
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError("zip end of central directory not found")
    entries, cd_size, cd_off = struct.unpack_from("<HII", tail, eocd + 10)
    if cd_off == 0xFFFFFFFF or entries == 0xFFFF:
        loc = tail.rfind(b"PK\x06\x07", 0, eocd)
        if loc < 0:
            raise ValueError("zip64 locator not found")
        eocd64_off = struct.unpack_from("<Q", tail, loc + 8)[0]
        rec = http_range(url, eocd64_off, eocd64_off + 55)
        if rec[:4] != b"PK\x06\x06":
            raise ValueError("zip64 end record not found")
        entries, cd_size, cd_off = struct.unpack_from("<QQQ", rec, 32)
    if entries != 1:
        raise ValueError(f"expected one zip entry, found {entries}")
    cd = http_range(url, cd_off, cd_off + cd_size - 1)
    if cd[:4] != b"PK\x01\x02":
        raise ValueError("zip central directory entry not found")
    method = struct.unpack_from("<H", cd, 10)[0]
    comp, uncomp = struct.unpack_from("<II", cd, 20)
    name_len, extra_len = struct.unpack_from("<HH", cd, 28)
    local_off = struct.unpack_from("<I", cd, 42)[0]
    extra = cd[46 + name_len:46 + name_len + extra_len]
    i = 0
    while i + 4 <= len(extra):
        tag, ln = struct.unpack_from("<HH", extra, i)
        if tag == 0x0001:
            vals = list(struct.unpack_from(f"<{ln // 8}Q", extra, i + 4))
            if uncomp == 0xFFFFFFFF:
                uncomp = vals.pop(0)
            if comp == 0xFFFFFFFF:
                comp = vals.pop(0)
            if local_off == 0xFFFFFFFF:
                local_off = vals.pop(0)
        i += 4 + ln
    if method != 8:
        raise ValueError(f"unsupported zip compression method {method}")
    lh = http_range(url, local_off, local_off + 29)
    if lh[:4] != b"PK\x03\x04":
        raise ValueError("zip local header not found")
    n, m = struct.unpack_from("<HH", lh, 26)
    return local_off + 30 + n + m, comp, uncomp


class Extractor:
    """Consumes the inflated disk image in order and routes the wanted byte ranges."""

    def __init__(self, out_dir, wanted, layout_only):
        self.out_dir = out_dir
        self.wanted = wanted
        self.layout_only = layout_only
        self.pos = 0
        self.head = bytearray()
        self.gpt = None
        self.super_head = bytearray()
        self.lp = None
        self.targets = []  # (abs_start, abs_end, file, file_offset)
        self.files = {}
        self.end = None

    def done(self):
        if self.layout_only:
            return self.lp is not None
        return self.end is not None and self.pos >= self.end

    def feed(self, data):
        start, self.pos = self.pos, self.pos + len(data)
        if self.gpt is None:
            self.head += data[:max(0, (1 << 20) - start)]
            if len(self.head) >= 2 * mica_disk.SECTOR and \
                    len(self.head) >= mica_disk.gpt_bytes_needed(self.head):
                self.gpt = mica_disk.parse_gpt(bytes(self.head))
                if "super" not in self.gpt:
                    raise ValueError("no super partition in GPT")
        if self.gpt is not None and self.lp is None:
            s0 = self.gpt["super"][0]
            lo, hi = max(start, s0), self.pos
            if hi > lo:
                self.super_head += data[lo - start:hi - start]
            fixed = mica_disk.LP_RESERVED_BYTES + mica_disk.LP_GEOMETRY_SIZE
            if len(self.super_head) >= fixed and \
                    len(self.super_head) >= mica_disk.lp_bytes_needed(self.super_head):
                self.lp = mica_disk.parse_lp(bytes(self.super_head), s0)
                self._plan()
                # A partition may begin right after the metadata, inside bytes that streamed past
                # before the metadata was complete; they are still in super_head.
                self._write(bytes(self.super_head[:max(0, start - s0)]), s0)
        self._write(data, start)

    def _write(self, data, start):
        end = start + len(data)
        for a, b, name, file_off in self.targets:
            lo, hi = max(a, start), min(b, end)
            if hi > lo:
                f = self.files[name]
                f.seek(file_off + (lo - a))
                f.write(data[lo - start:hi - start])

    def _plan(self):
        if self.layout_only:
            return
        os.makedirs(self.out_dir, exist_ok=True)
        for name in self.wanted:
            if name not in self.lp:
                raise ValueError(f"{name} not in super metadata")
            path = os.path.join(self.out_dir, name + ".erofs.part")
            f = open(path, "w+b")
            self.files[name] = f
            off = 0
            for a, b, kind in self.lp[name]:
                if kind == "zero":
                    f.truncate(off + (b - a))  # sparse zeros
                else:
                    if a < self.gpt["super"][0]:
                        raise ValueError(f"{name} starts before the super partition")
                    self.targets.append((a, b, name, off))
                off += b - a
            f.truncate(off)
        self.end = max(b for a, b, _, _ in self.targets)

    def close(self):
        for f in self.files.values():
            f.close()


def stream(url, data_off, comp_size, extractor):
    inflater = zlib.decompressobj(-15)
    ranges = [(p, min(p + CHUNK, data_off + comp_size) - 1)
              for p in range(data_off, data_off + comp_size, CHUNK)]
    started = time.time()
    last_log = started
    with concurrent.futures.ThreadPoolExecutor(max_workers=PREFETCH) as ex:
        pending = {}
        nxt = 0
        for idx in range(len(ranges)):
            while nxt < len(ranges) and nxt < idx + PREFETCH:
                pending[nxt] = ex.submit(http_range, url, *ranges[nxt])
                nxt += 1
            chunk = pending.pop(idx).result()
            view = memoryview(chunk)
            for i in range(0, len(view), 1 << 18):
                out = inflater.decompress(view[i:i + (1 << 18)])
                if out:
                    extractor.feed(out)
                if extractor.done():
                    for fut in pending.values():
                        fut.cancel()
                    return
            now = time.time()
            if now - last_log >= 30:
                done = ranges[idx][1] - data_off + 1
                total = extractor.end or comp_size
                log(f"  {done / 2**20:.0f} MiB indirildi, açılan {extractor.pos / 2**20:.0f} / "
                    f"{total / 2**20:.0f} MiB ({done / 2**20 / (now - started):.1f} MiB/s)")
                last_log = now
    raise RuntimeError("stream ended before all partitions were extracted")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kanal", default="STABLE")
    ap.add_argument("--duzen", action="store_true", help="only print the partition layout")
    ap.add_argument("--zorla", action="store_true", help="download even if already present")
    ap.add_argument("--bolum", action="append", help=f"partition to extract (default {WANTED})")
    args = ap.parse_args()

    rel = latest_release(args.kanal)
    build = rel["buildidentifier"]
    out_dir = os.path.join(IMAGES, build)
    log(f"Son mica {args.kanal} sürümü: {build} ({rel['dateupdated']}) {rel['url']}")
    complete = os.path.exists(os.path.join(out_dir, "kaynak.json"))
    if complete and not args.zorla and not args.duzen:
        log("Bu sürüm zaten indirilmiş; yapılacak bir şey yok.")
        return 0

    data_off, comp, uncomp = zip_entry(rel["url"])
    log(f"zip: veri {data_off}, sıkıştırılmış {comp}, açık {uncomp}")
    wanted = args.bolum or WANTED
    ex = Extractor(out_dir, wanted, args.duzen)
    try:
        stream(rel["url"], data_off, comp, ex)
    finally:
        ex.close()
    layout = {"gpt": ex.gpt, "super": {k: v for k, v in ex.lp.items()}}
    if args.duzen:
        print(json.dumps(layout, indent=1))
        return 0

    manifest = {"release": rel, "zip": {"data_offset": data_off, "compressed": comp,
                                        "uncompressed": uncomp}, "images": {}}
    for name in wanted:
        part = os.path.join(out_dir, name + ".erofs.part")
        res = subprocess.run([FSCK_EROFS, part], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"fsck.erofs {name}: {res.stderr.strip() or res.stdout.strip()}")
        final = os.path.join(out_dir, name + ".erofs")
        os.replace(part, final)
        h = hashlib.sha256()
        with open(final, "rb") as f:
            for blk in iter(lambda: f.read(1 << 20), b""):
                h.update(blk)
        manifest["images"][name] = {"bytes": os.path.getsize(final), "sha256": h.hexdigest(),
                                    "extents": ex.lp[name]}
        log(f"  {name}.erofs doğrulandı ({os.path.getsize(final)} bayt)")
    with open(os.path.join(out_dir, "layout.json"), "w") as f:
        json.dump(layout, f, indent=1)
    with open(os.path.join(out_dir, "kaynak.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    tmp = CURRENT + ".tmp"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(build, tmp)
    os.replace(tmp, CURRENT)
    log(f"Tamam: {out_dir} (mica-imaj/guncel -> {build})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
