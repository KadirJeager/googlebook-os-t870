#!/usr/bin/env python3
"""
Builds and signs the recovery install package of the Googlebook OS updater (kurulum.zip) and
checks a package the way LineageOS recovery does.

  paket.py olustur [--cikti kurulum.zip]          # build + sign + verify against our certificate
  paket.py dogrula PAKET --otacerts otacerts.zip   # recovery's check (otautil/verifier.cpp)

The package carries META-INF/com/google/android/update-binary (kurtarma/update-binary, a shell
script, with the slot table of the accepted super header filled in from tablete-yaz.py, the one
place the slots are defined), a placeholder updater-script and META-INF/com/android/metadata
(ota-type=BLOCK; LineageOS 24 recovery reads it, AOSP's refuses a package without it). The
images and their block maps are prepared in /cache/gbos by the tablet (see BENIOKU.md). Its
content never depends on the release, so it is built and signed once here and shipped in the
system image (yamalar/guncelleyici.py). The private key stays on this computer.

Signature format (whole-file signature, as written by AOSP signapk -w): the zip comment holds a
detached CMS/PKCS#7 SignedData without signed attributes over every byte of the archive except
the comment and its 2-byte length, followed by a 6-byte footer
(signature start from the end, 0xff 0xff, comment size). The zip itself is reproducible: stored
entries, fixed timestamps, fixed order.
"""
import argparse
import io
import os
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))
KEY_DIR = os.environ.get("GBOS_ANAHTAR", os.path.expanduser("~/.android-certs/googlebookos-t870"))
CERT = os.path.join(KEY_DIR, "ota.x509.pem")
UPDATE_BINARY = os.path.join(HERE, "update-binary")
EPOCH = (2008, 1, 1, 0, 0, 0)
EOCD_MAGIC = b"PK\x05\x06"
FOOTER_SIZE = 6
EOCD_HEADER_SIZE = 22


SLOT_PLACEHOLDER = b"@@SLOT_TABLE@@"
METADATA = b"ota-type=BLOCK\n"


def slot_table():
    """'<header sha> <name> <first MiB> <MiB> ...' for every Googlebook OS super header, from
    tablete-yaz.py (HEADER_SLOTS; hashes from super-baslik/uret.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("tablete_yaz", os.path.join(TOOLS, "tablete-yaz.py"))
    ty = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ty)
    return " ".join(f"{ty.BASLIK.SHA[header]} {name} {first} {size}"
                    for header, slots in sorted(ty.HEADER_SLOTS.items())
                    for name, (first, size) in sorted(slots.items()))


def update_binary(table=None):
    """update-binary with the slot table filled in (the tablet's, or `table` in the tests)."""
    with open(UPDATE_BINARY, "rb") as f:
        binary = f.read()
    if binary.count(SLOT_PLACEHOLDER) != 1:
        raise RuntimeError("update-binary must hold the slot table placeholder once")
    return binary.replace(SLOT_PLACEHOLDER, (slot_table() if table is None else table).encode())


def build_zip():
    """Unsigned archive with an empty comment."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data, mode in (
            ("META-INF/com/android/metadata", METADATA, 0o644),
            ("META-INF/com/google/android/update-binary", update_binary(), 0o755),
            ("META-INF/com/google/android/updater-script",
             b"# Googlebook OS T870 guncelleyici: the work is done by update-binary.\n", 0o644),
        ):
            info = zipfile.ZipInfo(name, date_time=EPOCH)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o100000 | mode) << 16
            z.writestr(info, data)
    data = buf.getvalue()
    assert data[-EOCD_HEADER_SIZE:-EOCD_HEADER_SIZE + 4] == EOCD_MAGIC and data[-2:] == b"\0\0"
    return data


def sign(unsigned, key_pem, cert_pem):
    """Appends the whole-file signature to an archive that has an empty zip comment."""
    signed_part = unsigned[:-2]  # everything but the comment length
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "imzalanan")
        with open(src, "wb") as f:
            f.write(signed_part)
        sig = subprocess.run(
            ["openssl", "cms", "-sign", "-binary", "-noattr", "-md", "sha256", "-outform", "DER",
             "-signer", cert_pem, "-inkey", key_pem, "-in", src],
            check=True, stdout=subprocess.PIPE).stdout
    if EOCD_MAGIC in sig:
        raise RuntimeError("imza EOCD imini içeriyor; recovery bu paketi reddeder")
    comment_size = len(sig) + FOOTER_SIZE
    if comment_size > 0xFFFF:
        raise RuntimeError("imza zip yorumuna sığmıyor")
    footer = struct.pack("<H", comment_size) + b"\xff\xff" + struct.pack("<H", comment_size)
    return signed_part + struct.pack("<H", comment_size) + sig + footer


# --- verification, a port of LineageOS recovery's verify_file() and read_pkcs7() -------------

class Der:
    """Minimal DER reader with the navigation recovery's asn1_decoder offers."""

    def __init__(self, data, pos=0, end=None):
        self.data, self.pos, self.end = data, pos, len(data) if end is None else end

    def _header(self):
        if self.pos + 2 > self.end:
            raise ValueError("DER: truncated")
        tag = self.data[self.pos]
        length = self.data[self.pos + 1]
        hdr = 2
        if length & 0x80:
            n = length & 0x7F
            if n == 0 or n > 4:
                raise ValueError("DER: unsupported length")
            length = int.from_bytes(self.data[self.pos + 2:self.pos + 2 + n], "big")
            hdr += n
        start = self.pos + hdr
        if start + length > self.end:
            raise ValueError("DER: element runs past its parent")
        return tag, start, start + length

    def get(self, want):
        tag, start, end = self._header()
        if tag != want:
            raise ValueError(f"DER: expected tag {want:#x}, found {tag:#x}")
        return Der(self.data, start, end)

    def skip(self):
        _, _, end = self._header()
        self.pos = end

    def skip_constructed(self):
        while self.pos < self.end and self.data[self.pos] & 0xA0 == 0xA0:
            self.skip()

    def octets(self):
        d = self.get(0x04)
        return self.data[d.pos:d.end]


def signature_from_pkcs7(der):
    seq = Der(der).get(0x30)
    seq.skip()                                  # contentType
    signed = seq.get(0xA0).get(0x30)            # [0] SignedData
    for _ in range(3):                          # version, digestAlgorithms, encapContentInfo
        signed.skip()
    signed.skip_constructed()                   # [0] certificates, [1] crls
    signer = signed.get(0x31).get(0x30)         # signerInfos / first SignerInfo
    for _ in range(4):                          # version, sid, digestAlgorithm, signatureAlgorithm
        signer.skip()
    return signer.octets()


def verify(package, cert_pems):
    """True when recovery would accept the whole-file signature with one of the certificates."""
    if len(package) < FOOTER_SIZE:
        return False, "imza altbilgisi yok"
    footer = package[-FOOTER_SIZE:]
    if footer[2:4] != b"\xff\xff":
        return False, "altbilgi hatalı"
    comment_size = footer[4] | footer[5] << 8
    signature_start = footer[0] | footer[1] << 8
    if signature_start > comment_size or signature_start <= FOOTER_SIZE:
        return False, "imza başlangıcı geçersiz"
    eocd_size = comment_size + EOCD_HEADER_SIZE
    if len(package) < eocd_size:
        return False, "EOCD sığmıyor"
    eocd = package[-eocd_size:]
    if eocd[:4] != EOCD_MAGIC:
        return False, "EOCD imi yerinde değil"
    if EOCD_MAGIC in eocd[4:]:
        return False, "EOCD imi imzadan sonra yineleniyor"
    signed_len = len(package) - eocd_size + EOCD_HEADER_SIZE - 2
    sig_der = eocd[eocd_size - signature_start:eocd_size - FOOTER_SIZE]
    try:
        raw_sig = signature_from_pkcs7(sig_der)
    except ValueError as e:
        return False, f"PKCS#7 okunamadı: {e}"
    with tempfile.TemporaryDirectory() as tmp:
        data_path = os.path.join(tmp, "veri")
        sig_path = os.path.join(tmp, "imza")
        with open(data_path, "wb") as f:
            f.write(package[:signed_len])
        with open(sig_path, "wb") as f:
            f.write(raw_sig)
        for i, pem in enumerate(cert_pems):
            pub = os.path.join(tmp, f"anahtar{i}.pem")
            cert = os.path.join(tmp, f"sertifika{i}.pem")
            with open(cert, "wb") as f:
                f.write(pem)
            with open(pub, "wb") as f:
                f.write(subprocess.run(["openssl", "x509", "-in", cert, "-pubkey", "-noout"],
                                       check=True, stdout=subprocess.PIPE).stdout)
            res = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature", sig_path,
                                  data_path], capture_output=True, text=True)
            if res.returncode == 0:
                return True, f"{i}. sertifikayla doğrulandı"
    return False, "hiçbir sertifikayla doğrulanmadı"


def check_certificate(pem):
    """Recovery's LoadCertificateFromBuffer() rules for an RSA key; one bad entry makes recovery
    drop every key, so every certificate that goes into otacerts.zip is checked."""
    text = subprocess.run(["openssl", "x509", "-noout", "-text"], input=pem, check=True,
                          stdout=subprocess.PIPE).stdout.decode()
    sigalg = text.split("Signature Algorithm:", 1)[1].split("\n", 1)[0].strip()
    if sigalg not in ("sha256WithRSAEncryption", "sha1WithRSAEncryption", "md5WithRSAEncryption",
                      "ecdsa-with-SHA256"):
        raise ValueError(f"recovery bu imza algoritmasını tanımıyor: {sigalg}")
    if "rsaEncryption" in text:
        if not ("Public-Key: (2048 bit)" in text or "Public-Key: (4096 bit)" in text):
            raise ValueError("RSA anahtarı 2048 ya da 4096 bit olmalı")
        if "Exponent: 65537" not in text and "Exponent: 3 " not in text:
            raise ValueError("RSA üssü 3 ya da 65537 olmalı")
    return sigalg


def otacerts(path):
    """Certificates recovery loads from otacerts.zip: entries whose name ends in x509.pem."""
    with zipfile.ZipFile(path) as z:
        return [z.read(n) for n in sorted(z.namelist()) if n.endswith("x509.pem")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="komut", required=True)
    o = sub.add_parser("olustur")
    o.add_argument("--cikti", default=os.path.join(os.path.dirname(HERE), "out", "kurulum.zip"))
    d = sub.add_parser("dogrula")
    d.add_argument("paket")
    d.add_argument("--otacerts", required=True)
    args = ap.parse_args()

    if args.komut == "olustur":
        key = os.path.join(KEY_DIR, "ota.key.pem")
        if not os.path.exists(key):
            sys.exit(f"anahtar yok: {key} (önce T870-araclar/anahtar-olustur.sh)")
        with open(CERT, "rb") as f:
            cert = f.read()
        if check_certificate(cert) != "sha256WithRSAEncryption":
            sys.exit("sertifika sha256WithRSAEncryption ile imzalı olmalı (paket SHA-256 ile imzalanıyor)")
        package = sign(build_zip(), key, CERT)
        ok, why = verify(package, [cert])
        if not ok:
            sys.exit(f"yeni paket doğrulanamadı: {why}")
        os.makedirs(os.path.dirname(os.path.abspath(args.cikti)), exist_ok=True)
        tmp = args.cikti + ".tmp"
        with open(tmp, "wb") as f:
            f.write(package)
        os.replace(tmp, args.cikti)
        print(f"{args.cikti}: {len(package)} bayt, {why}")
        return 0

    with open(args.paket, "rb") as f:
        package = f.read()
    certs = otacerts(args.otacerts)
    for c in certs:
        check_certificate(c)
    ok, why = verify(package, certs)
    print(("GEÇERLİ: " if ok else "GEÇERSİZ: ") + why)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
