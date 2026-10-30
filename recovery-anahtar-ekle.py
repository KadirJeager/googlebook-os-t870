#!/usr/bin/env python3
"""
Adds the Googlebook OS updater's certificate to the tablet's LineageOS recovery, so that
update packages signed with our key install without recovery's "Install anyway?" prompt while
recovery keeps verifying signatures (packages signed by anyone else are still rejected).

  recovery-anahtar-ekle.py                       # -> $GBOS_WORK/denetim/recovery/recovery-gbos.img
  recovery-anahtar-ekle.py --girdi X --cikti Y --sertifika Z

Only one file changes: system/etc/security/otacerts.zip in the recovery ramdisk gets one more
entry (our certificate, name ending in x509.pem as recovery requires); LineageOS's certificates
stay. The cpio entry is replaced in place, every other ramdisk entry is kept byte for byte,
the boot image is rebuilt with the original mkbootimg arguments and the AVB hash footer is put
back with the same key (AOSP testkey_rsa4096, as LineageOS signed it), salt, rollback index and
properties. The result is verified before it is written: kernel, dtb and recovery_dtbo
identical, ramdisk identical except otacerts.zip, otacerts.zip = original entries + ours,
footer valid. Output is reproducible.
"""
import argparse
import gzip
import hashlib
import io
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
# Without --girdi: recovery.img of the LineageOS zip the work directory was built from (lineage-zip.py)
DEFAULT_OUT = os.path.join(WORK, "denetim", "recovery", "recovery-gbos.img")
# The certificate of the builder's own package key (anahtar-olustur.sh)
DEFAULT_CERT = os.path.join(os.environ.get("GBOS_ANAHTAR", os.path.expanduser("~/.android-certs/googlebookos-t870")),
                            "ota.x509.pem")
AVB_KEY = os.path.join(TOOLS, "guncelleyici", "kurtarma", "avb", "testkey_rsa4096.pem")
OTACERTS = "system/etc/security/otacerts.zip"
OUR_ENTRY = "gbos/gbos-guncelleyici.x509.pem"
ZIP_DATE = (2009, 1, 1, 0, 0, 0)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def cpio_entries(data):
    """Parses a newc cpio archive into [(header_fields, name, data_offset, data_size), ...]."""
    out, off = [], 0
    while True:
        if data[off:off + 6] != b"070701":
            raise ValueError(f"not a newc cpio header at {off}")
        fields = [int(data[off + 6 + 8 * i:off + 14 + 8 * i], 16) for i in range(13)]
        namesize, filesize = fields[11], fields[6]
        name = data[off + 110:off + 110 + namesize - 1].decode()
        data_off = (off + 110 + namesize + 3) & ~3
        out.append((off, fields, name, data_off, filesize))
        off = (data_off + filesize + 3) & ~3
        if name == "TRAILER!!!":
            return out


def replace_cpio_file(data, target, new_content):
    entries = cpio_entries(data)
    hits = [e for e in entries if e[2] == target]
    if len(hits) != 1:
        raise ValueError(f"{target}: expected one cpio entry, found {len(hits)}")
    hdr_off, fields, name, data_off, size = hits[0]
    end = (data_off + size + 3) & ~3
    fields = list(fields)
    fields[6] = len(new_content)
    header = b"070701" + b"".join(b"%08X" % v for v in fields)
    namebytes = data[hdr_off + 110:data_off]  # name + NUL + padding, unchanged
    body = new_content + b"\0" * ((4 - len(new_content) % 4) % 4)
    return data[:hdr_off] + header + namebytes + body + data[end:]


def new_otacerts(orig_zip, cert):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(orig_zip)) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            if info.filename == OUR_ENTRY:
                continue
            zout.writestr(info, zin.read(info.filename))
        ni = zipfile.ZipInfo(OUR_ENTRY, date_time=ZIP_DATE)
        ni.compress_type = zipfile.ZIP_STORED
        ni.external_attr = 0o644 << 16
        zout.writestr(ni, cert)
    return out.getvalue()


def avb_info(img):
    info = run(["avbtool", "info_image", "--image", img])
    def get(key):
        m = re.search(rf"^\s*{re.escape(key)}:\s*(.+)$", info, re.M)
        if not m:
            raise ValueError(f"avb: {key} missing")
        return m.group(1).strip()
    props = re.findall(r"^\s*Prop: (\S+) -> '(.*)'$", info, re.M)
    return {"partition_size": int(get("Image size").split()[0]),
            "partition_name": get("Partition Name"), "salt": get("Salt"),
            "rollback_index": int(get("Rollback Index")), "algorithm": get("Algorithm"),
            "pubkey_sha1": get("Public key (sha1)"), "props": props}


def unpack(img, outdir):
    args = run(["unpack_bootimg", "--boot_img", img, "--out", outdir, "--format=mkbootimg"])
    return shlex.split(args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--girdi", help="LineageOS recovery image (default: the one in the LineageOS zip)")
    ap.add_argument("--cikti", default=DEFAULT_OUT)
    ap.add_argument("--sertifika", default=DEFAULT_CERT)
    args = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(args.cikti)), exist_ok=True)
    if args.girdi:
        build(args)
        return
    with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(args.cikti))) as tmp:
        args.girdi = os.path.join(tmp, "recovery.img")
        run([sys.executable, "-I", os.path.join(TOOLS, "lineage-zip.py"), "cikar", "recovery.img", args.girdi])
        build(args)


def build(args):
    avb = avb_info(args.girdi)
    key_sha1 = hashlib.sha1(subprocess.run(
        ["avbtool", "extract_public_key", "--key", AVB_KEY, "--output", "/dev/stdout"],
        check=True, capture_output=True).stdout).hexdigest()
    if key_sha1 != avb["pubkey_sha1"]:
        sys.exit(f"AVB key {key_sha1} != footer key {avb['pubkey_sha1']}")
    with open(args.sertifika, "rb") as f:
        cert = f.read()
    run(["openssl", "x509", "-in", args.sertifika, "-noout"])  # must be a valid certificate

    os.makedirs(os.path.dirname(os.path.abspath(args.cikti)), exist_ok=True)
    with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(args.cikti))) as tmp:
        orig_dir, new_dir = os.path.join(tmp, "orig"), os.path.join(tmp, "new")
        mkargs = unpack(args.girdi, orig_dir)
        with open(os.path.join(orig_dir, "ramdisk"), "rb") as f:
            ramdisk_gz = f.read()
        if ramdisk_gz[:2] != b"\x1f\x8b":
            sys.exit("ramdisk is not gzip")
        cpio = gzip.decompress(ramdisk_gz)
        entries = cpio_entries(cpio)
        ota = [e for e in entries if e[2] == OTACERTS][0]
        orig_ota = cpio[ota[3]:ota[3] + ota[4]]
        ota_new = new_otacerts(orig_ota, cert)
        cpio_new = replace_cpio_file(cpio, OTACERTS, ota_new)
        new_ramdisk = os.path.join(tmp, "ramdisk.new")
        with open(new_ramdisk, "wb") as f:
            with gzip.GzipFile(fileobj=f, mode="wb", compresslevel=9, mtime=0) as gz:
                gz.write(cpio_new)

        i = mkargs.index("--ramdisk")
        mkargs[i + 1] = new_ramdisk
        unsigned = os.path.join(tmp, "recovery.unsigned")
        run(["mkbootimg", *mkargs, "--output", unsigned])
        cmd = ["avbtool", "add_hash_footer", "--image", unsigned,
               "--partition_size", str(avb["partition_size"]),
               "--partition_name", avb["partition_name"], "--salt", avb["salt"],
               "--rollback_index", str(avb["rollback_index"]),
               "--algorithm", avb["algorithm"], "--key", AVB_KEY]
        for k, v in avb["props"]:
            cmd += ["--prop", f"{k}:{v}"]
        run(cmd)

        # Verify the result before handing it out.
        run(["avbtool", "verify_image", "--image", unsigned, "--key", AVB_KEY])
        new_avb = avb_info(unsigned)
        for k in ("partition_size", "partition_name", "salt", "rollback_index", "algorithm",
                  "pubkey_sha1", "props"):
            if new_avb[k] != avb[k]:
                sys.exit(f"footer {k} changed: {avb[k]} -> {new_avb[k]}")
        new_mkargs = unpack(unsigned, new_dir)
        strip = lambda a: [x for x in a if not x.startswith(tmp)]
        if strip(new_mkargs) != strip(mkargs):
            sys.exit("mkbootimg arguments differ after repacking")
        for part in ("kernel", "dtb", "recovery_dtbo"):
            with open(os.path.join(orig_dir, part), "rb") as a, open(os.path.join(new_dir, part), "rb") as b:
                if a.read() != b.read():
                    sys.exit(f"{part} changed")
        with open(os.path.join(new_dir, "ramdisk"), "rb") as f:
            got = gzip.decompress(f.read())
        a = {e[2]: cpio[e[3]:e[3] + e[4]] for e in entries}
        a_meta = {e[2]: e[1][:6] + e[1][7:] for e in entries}
        got_entries = cpio_entries(got)
        b = {e[2]: got[e[3]:e[3] + e[4]] for e in got_entries}
        b_meta = {e[2]: e[1][:6] + e[1][7:] for e in got_entries}
        changed = sorted(n for n in a if a[n] != b.get(n)) + sorted(set(b) - set(a))
        if changed != [OTACERTS] or a_meta != b_meta:
            sys.exit(f"ramdisk differs beyond {OTACERTS}: {changed}")
        with zipfile.ZipFile(io.BytesIO(b[OTACERTS])) as z, zipfile.ZipFile(io.BytesIO(orig_ota)) as zo:
            names = z.namelist()
            if names != zo.namelist() + [OUR_ENTRY] or any(z.read(n) != zo.read(n) for n in zo.namelist()):
                sys.exit(f"otacerts.zip content unexpected: {names}")
            if z.read(OUR_ENTRY) != cert:
                sys.exit("our certificate not stored intact")
        shutil.copyfile(unsigned, args.cikti)

    h = hashlib.sha256(open(args.cikti, "rb").read()).hexdigest()
    print(f"Tamam: {args.cikti} ({os.path.getsize(args.cikti)} bayt) sha256 {h}")
    print(f"otacerts.zip: LineageOS sertifikaları + {OUR_ENTRY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
