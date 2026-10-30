#!/usr/bin/env python3
"""
Extracts single files from a partition of a LineageOS block-based OTA zip (<part>.new.dat.br +
<part>.transfer.list), e.g. the 32-bit vendor libraries apply_patches.py patches.

  lineage-dosya-cikar.py ZIP vendor /lib/libbinder.so /lib/libhidlbase.so --cikti DIR
  lineage-dosya-cikar.py ZIP vendor /etc/permissions/ --cikti DIR   # every file of a directory

The partition image is rebuilt the way the OTA's updater writes it (transfer list "new" commands
place the brotli-decompressed data, "zero"/"erase" ranges stay zero) into a sparse file next to
the output, the files are read out with debugfs (ext4) or dump.erofs (erofs), and the image is
removed again. DIR/<name> gets each file, DIR/SHA256SUMS their hashes and DIR/KAYNAK the zip's
name and SHA-256, so the stage can be traced back to the official build.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

BLOCK = 4096
DUMP_EROFS = os.path.join(os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin")), "dump.erofs")


def rangeset(text):
    nums = [int(x) for x in text.split(",")]
    assert nums[0] == len(nums) - 1 and nums[0] % 2 == 0, f"bad range set {text[:40]}"
    return list(zip(nums[1::2], nums[2::2]))


def rebuild(zf, part, image):
    lines = zf.read(f"{part}.transfer.list").decode().splitlines()
    version, total = int(lines[0]), int(lines[1])
    if version < 1 or version > 4:
        raise SystemExit(f"transfer list version {version} is not supported")
    commands = lines[4:] if version >= 2 else lines[2:]
    with open(image, "wb") as out:
        out.truncate(total * BLOCK)
    dec = subprocess.Popen(["brotli", "-d", "-c"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    feeder_error = []

    def feed():
        try:
            with zf.open(f"{part}.new.dat.br") as src:
                shutil.copyfileobj(src, dec.stdin, 1 << 20)
        except Exception as e:  # reported after the reader finishes
            feeder_error.append(e)
        finally:
            dec.stdin.close()

    import threading
    t = threading.Thread(target=feed)
    t.start()
    with open(image, "r+b") as out:
        for cmd in commands:
            op, _, arg = cmd.partition(" ")
            if op != "new":
                if op not in ("zero", "erase", "move", "bsdiff", "imgdiff", "stash", "free"):
                    raise SystemExit(f"unknown transfer command {op}")
                if op in ("move", "bsdiff", "imgdiff"):
                    raise SystemExit(f"{op} needs a source image: not a full OTA")
                continue
            for start, end in rangeset(arg):
                left = (end - start) * BLOCK
                out.seek(start * BLOCK)
                while left:
                    chunk = dec.stdout.read(min(left, 1 << 20))
                    if not chunk:
                        raise SystemExit(f"{part}.new.dat.br ended early")
                    out.write(chunk)
                    left -= len(chunk)
    rest = dec.stdout.read(1)
    t.join()
    if dec.wait() != 0 or feeder_error:
        raise SystemExit(f"brotli failed: {feeder_error}")
    if rest:
        raise SystemExit(f"{part}.new.dat.br holds more data than the transfer list places")


def read_file(image, path, dest):
    with open(image, "rb") as f:
        f.seek(1024 + 0x38)
        ext4 = f.read(2) == b"\x53\xef"
    # Some files are legitimately empty (odm_file_contexts, system_ext bug_map), so a missing file
    # is told by the tools' own report, not by the size: debugfs exits 0 and creates nothing,
    # dump.erofs exits 0 with an <E> line.
    if ext4:
        # no -p: the owner is root and cannot be set; callers set the mode themselves
        r = subprocess.run(["debugfs", "-R", f"dump {path} {dest}", image],
                           capture_output=True, text=True)
        failed = not os.path.exists(dest) or "not found" in r.stderr
    else:
        with open(dest, "wb") as out:
            r = subprocess.run([DUMP_EROFS, "--cat", f"--path={path}", image], stdout=out,
                               stderr=subprocess.PIPE, text=True)
        failed = "<E>" in r.stderr
    if r.returncode != 0 or failed:
        if os.path.exists(dest):
            os.remove(dest)
        raise SystemExit(f"{path}: not found in the image ({r.stderr.strip()[-200:]})")


def list_files(image, path):
    """Names of the regular files directly in directory `path` of the image."""
    with open(image, "rb") as f:
        f.seek(1024 + 0x38)
        ext4 = f.read(2) == b"\x53\xef"
    if ext4:
        r = subprocess.run(["debugfs", "-R", f"ls -p {path}", image], capture_output=True, text=True)
        # /inode/mode/uid/gid/name/size/ per entry; mode 100xxx is a regular file
        names = [e[5] for e in (line.split("/") for line in r.stdout.splitlines())
                 if len(e) > 6 and e[2].startswith("100")]
    else:
        r = subprocess.run([DUMP_EROFS, "--ls", f"--path={path}", image], capture_output=True,
                           text=True)
        # NID TYPE FILENAME rows after the inode header; type 1 is a regular file
        names = [e[2] for e in (line.split(None, 2) for line in r.stdout.splitlines())
                 if len(e) == 3 and e[0].isdigit() and e[1] == "1"]
    if r.returncode != 0 or not names:
        raise SystemExit(f"{path}: no files in the image ({r.stderr.strip()[-200:]})")
    return sorted(names)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip")
    ap.add_argument("bolum", help="partition name in the zip (vendor, system, odm, ...)")
    ap.add_argument("dosyalar", nargs="+", help="absolute paths inside the partition; a path ending in / means every file in it")
    ap.add_argument("--cikti", required=True)
    args = ap.parse_args()
    os.makedirs(args.cikti, exist_ok=True)
    image = os.path.join(args.cikti, f".{args.bolum}.img")
    try:
        with zipfile.ZipFile(args.zip) as zf:
            rebuild(zf, args.bolum, image)
        paths = []
        for path in args.dosyalar:
            if path.endswith("/"):
                paths += [path + name for name in list_files(image, path.rstrip("/"))]
            else:
                paths.append(path)
        sums = []
        for path in paths:
            dest = os.path.join(args.cikti, os.path.basename(path))
            read_file(image, path, dest)
            os.chmod(dest, 0o644)
            sums.append(f"{sha256(dest)}  {os.path.basename(path)}  {args.bolum}{path}")
    finally:
        if os.path.exists(image):
            os.remove(image)
    with open(os.path.join(args.cikti, "SHA256SUMS"), "w") as f:
        f.write("\n".join(sums) + "\n")
    with open(os.path.join(args.cikti, "KAYNAK"), "w") as f:
        f.write(f"{os.path.basename(args.zip)}  sha256={sha256(args.zip)}  bolum={args.bolum}\n")
    print("\n".join(sums))
    return 0


if __name__ == "__main__":
    sys.exit(main())
