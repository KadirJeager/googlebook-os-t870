"""d) lineage-dosya-cikar.py reproduces the vendor stage apply_patches.py builds the 32-bit
gbos-vendor-override from (lineage/vendor_stage: libbinder.so, libhidlbase.so of the LineageOS
vendor partition the tablet runs; codec2 KOK-NEDEN.md). If the OTA rebuild (transfer list +
brotli) or the file read went wrong, the override would carry a library the tablet does not have.
"""
import os
import subprocess
import sys

from conftest import (ARACLAR, GERCEK_WORK, LINEAGE_ZIP, arac_gerekli, disk_payi, gerekli, oku,
                      sha256)

STAGE = os.path.join(GERCEK_WORK, "lineage", "vendor_stage")
DOSYALAR = ["/lib/libbinder.so", "/lib/libhidlbase.so"]


def test_vendor_kitapliklari_asamayla_ayni(gecici):
    """The command line as documented, run on the real zip: SHA256SUMS identical to the stage's,
    the files byte-identical, KAYNAK naming the same zip and hash, and the rebuilt partition image
    removed again."""
    gerekli(LINEAGE_ZIP, os.path.join(STAGE, "SHA256SUMS"), os.path.join(STAGE, "KAYNAK"))
    arac_gerekli("brotli")
    disk_payi(1)
    cikti = os.path.join(gecici, "cikti")
    r = subprocess.run([sys.executable, "-I", os.path.join(ARACLAR, "lineage-dosya-cikar.py"),
                        LINEAGE_ZIP, "vendor", *DOSYALAR, "--cikti", cikti],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1000:]
    assert oku(os.path.join(cikti, "SHA256SUMS")) == oku(os.path.join(STAGE, "SHA256SUMS"))
    assert oku(os.path.join(cikti, "KAYNAK")) == oku(os.path.join(STAGE, "KAYNAK"))
    for path in DOSYALAR:
        name = os.path.basename(path)
        assert sha256(os.path.join(cikti, name)) == sha256(os.path.join(STAGE, name)), name
        assert os.stat(os.path.join(cikti, name)).st_mode & 0o777 == 0o644
    assert sorted(os.listdir(cikti)) == sorted(["KAYNAK", "SHA256SUMS",
                                                *(os.path.basename(p) for p in DOSYALAR)])
    # Every printed line is the SHA256SUMS line: what the user sees matches what is stored.
    assert r.stdout.strip().splitlines() == oku(os.path.join(cikti, "SHA256SUMS")).strip().splitlines()


def _adlar(kok):
    return sorted(n for n in os.listdir(kok) if os.path.isfile(os.path.join(kok, n))
                  and not os.path.islink(os.path.join(kok, n)))


def test_dizin_listesi_erofs(gecici):
    """list_files (a path ending in /) on the pristine mica vendor image: the same regular files
    fsck.erofs extracts, no subdirectories."""
    from conftest import EROFS_BIN, MICA, _extract_dir, arac_modulu
    image = os.path.join(MICA, "vendor_a.erofs")
    gerekli(image, os.path.join(EROFS_BIN, "fsck.erofs"))
    ldc = arac_modulu("lineage-dosya-cikar.py", "lineage_dosya_cikar")
    hedef = os.path.join(gecici, "x")
    assert _extract_dir(image, "/etc/permissions", hedef)
    assert ldc.list_files(image, "/etc/permissions") == _adlar(hedef)


def test_dizin_listesi_ext4(gecici):
    """list_files on an ext4 image (the LineageOS vendor partition is ext4): files only, names
    with dots and dashes, a subdirectory and a symlink left out."""
    from conftest import arac_modulu, yaz
    arac_gerekli("mke2fs", "debugfs")
    ldc = arac_modulu("lineage-dosya-cikar.py", "lineage_dosya_cikar")
    kaynak = os.path.join(gecici, "kok")
    for ad in ("android.hardware.wifi.aware.xml", "handheld_core_hardware.xml", "a-b_c.xml"):
        yaz(os.path.join(kaynak, "etc/permissions", ad), "<permissions/>\n")
    os.makedirs(os.path.join(kaynak, "etc/permissions/alt"))
    os.symlink("a-b_c.xml", os.path.join(kaynak, "etc/permissions/bag.xml"))
    image = os.path.join(gecici, "v.img")
    subprocess.run(["mke2fs", "-q", "-t", "ext4", "-d", kaynak, image, "8M"], check=True)
    assert ldc.list_files(image, "/etc/permissions") == [
        "a-b_c.xml", "android.hardware.wifi.aware.xml", "handheld_core_hardware.xml"]
