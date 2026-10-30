#!/usr/bin/env python3
import os
import sys
import time
import zlib
import urllib.request
import concurrent.futures

URL = "https://dl.google.com/device/recovery/mica-user/16471258/recovery.zip"
OUT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # mica-imaj/
os.makedirs(OUT_DIR, exist_ok=True)

TARGETS = [
    ("boot_gpt_head.bin",   0,          176160768),
    ("super_meta.bin",      176160768,  177209344),
    ("system_a.erofs",      177209344,  1258655744),
    ("vendor_a.erofs",      5647630336, 6033580032),
    ("product_a.erofs",     6034554880, 8004562944),
    ("system_ext_a.erofs",  8004829184, 8187715584),
    ("system_dlkm_a.erofs", 8188338176, 8196820992),
    ("vendor_dlkm_a.erofs", 8197779456, 8214519808),
]
MAX_U_OFFSET = TARGETS[-1][2]
TOTAL_COMP_SIZE = 7601764157
DATA_START = 71
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB per HTTP Range request
PREFETCH = 4

def fetch_range(start, end):
    for attempt in range(10):
        try:
            req = urllib.request.Request(
                URL,
                headers={
                    "Range": f"bytes={start}-{end}",
                    "User-Agent": "Mozilla/5.0",
                },
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = resp.read()
                if len(data) == (end - start + 1):
                    return data
                raise IOError(f"Short read: {len(data)} != {end - start + 1}")
        except Exception as e:
            print(f"[WARN] Range {start}-{end} attempt {attempt+1} failed: {e}", flush=True)
            time.sleep(2)
    raise RuntimeError(f"Failed to fetch range {start}-{end} after 10 attempts")

def main():
    decomp = zlib.decompressobj(-15)
    c_pos = DATA_START
    u_pos = 0
    open_files = {}
    t_start = time.time()
    last_log = t_start

    ranges = []
    pos = DATA_START
    while pos < TOTAL_COMP_SIZE:
        end = min(pos + CHUNK_SIZE - 1, TOTAL_COMP_SIZE - 1)
        ranges.append((pos, end))
        pos = end + 1

    print(f"[INFO] Starting pipelined extraction of mica-user to {OUT_DIR} ({len(ranges)} chunks)", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=PREFETCH) as ex:
        futures = {}
        next_idx = 0
        while next_idx < min(PREFETCH, len(ranges)):
            s, e = ranges[next_idx]
            futures[next_idx] = ex.submit(fetch_range, s, e)
            next_idx += 1

        cur_idx = 0
        while cur_idx < len(ranges) and u_pos < MAX_U_OFFSET:
            comp_chunk = futures.pop(cur_idx).result()
            s, e = ranges[cur_idx]
            c_pos = e + 1
            cur_idx += 1

            if next_idx < len(ranges):
                ns, ne = ranges[next_idx]
                futures[next_idx] = ex.submit(fetch_range, ns, ne)
                next_idx += 1

            mv = memoryview(comp_chunk)
            step = 262144
            for i in range(0, len(mv), step):
                raw = decomp.decompress(mv[i:i+step])
                if not raw:
                    continue
                r_start = u_pos
                r_end = u_pos + len(raw)
                u_pos = r_end

                for name, t_start_off, t_end_off in TARGETS:
                    if r_end <= t_start_off or r_start >= t_end_off:
                        if name in open_files and r_start >= t_end_off:
                            open_files[name].close()
                            del open_files[name]
                            print(f"[DONE] Completed {name} ({t_end_off - t_start_off} bytes)", flush=True)
                        continue
                    if name not in open_files:
                        path = os.path.join(OUT_DIR, name)
                        open_files[name] = open(path, "wb")
                        print(f"[START] Writing {name} ...", flush=True)
                    ov_start = max(r_start, t_start_off)
                    ov_end = min(r_end, t_end_off)
                    open_files[name].write(raw[ov_start - r_start : ov_end - r_start])

                if u_pos >= MAX_U_OFFSET:
                    break

            now = time.time()
            if now - last_log >= 20:
                elapsed = now - t_start
                mb_c = (c_pos - DATA_START) / 1048576
                mb_u = u_pos / 1048576
                speed = mb_c / elapsed if elapsed > 0 else 0
                print(f"[PROGRESS] comp={mb_c:.1f} MiB ({speed:.2f} MiB/s), uncomp={mb_u:.1f} / {MAX_U_OFFSET/1048576:.1f} MiB", flush=True)
                last_log = now

        for fut in futures.values():
            fut.cancel()

    for name, f in list(open_files.items()):
        f.close()
        print(f"[DONE] Closed {name}", flush=True)
    print("[SUCCESS] All mica-user partitions extracted!", flush=True)

if __name__ == "__main__":
    main()
