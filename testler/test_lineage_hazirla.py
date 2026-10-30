"""l) lineage-hazirla.py builds every LineageOS input of the work directory from the OTA zip alone
(lib32_stage, vendor_stage, vendor_features, vendor_dogal_yon, bolumler). A computer without the author's
work directory, with another LineageOS nightly, starts from this; if it missed a file or read a
symlink wrongly, the 32-bit runtime of the image would differ from the one the tablet runs.
"""
import os
import subprocess
import sys

import pytest

from conftest import (ARACLAR, GERCEK_WORK, LINEAGE_ZIP, arac_gerekli, arac_modulu, disk_payi,
                      gerekli, yaz)


def test_calisan_girdilerle_ayni():
    """--denetle on the real zip: every file of the five stages byte-identical to the work
    directory's, nothing missing and nothing extra, the temporary tree removed again."""
    gerekli(LINEAGE_ZIP, os.path.join(GERCEK_WORK, "lineage", "lib32_stage", "KAYNAK"))
    arac_gerekli("brotli", "debugfs")
    disk_payi(4)
    once = set(os.listdir(GERCEK_WORK))
    r = subprocess.run([sys.executable, "-I", os.path.join(ARACLAR, "lineage-hazirla.py"),
                        LINEAGE_ZIP, "--work", GERCEK_WORK, "--denetle"],
                       capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    assert "identical" in r.stdout
    assert set(os.listdir(GERCEK_WORK)) == once


def test_bos_ve_eksik_dosya(gecici):
    """An empty file is read as an empty file (LineageOS ships empty odm_file_contexts and
    system_ext bug_map); a missing one is an error and leaves nothing behind."""
    arac_gerekli("mke2fs", "debugfs")
    lh = arac_modulu("lineage-hazirla.py", "lineage_hazirla")
    kok = os.path.join(gecici, "kok")
    yaz(os.path.join(kok, "etc/selinux/bug_map"), "")
    yaz(os.path.join(kok, "etc/selinux/mapping/34.0.cil"), "(x)")
    image = os.path.join(gecici, "s.img")
    subprocess.run(["mke2fs", "-q", "-t", "ext4", "-d", kok, image, "8M"], check=True)
    assert lh.tree(image, "/etc/selinux") == ["bug_map", "mapping/34.0.cil"]
    bos = os.path.join(gecici, "bug_map")
    lh.ldc.read_file(image, "/etc/selinux/bug_map", bos)
    assert os.path.getsize(bos) == 0
    yok = os.path.join(gecici, "yok")
    with pytest.raises(SystemExit):
        lh.ldc.read_file(image, "/etc/selinux/yok", yok)
    assert not os.path.exists(yok)


def test_ext4_dizin_turleri(gecici):
    """ext4_entries tells files, directories and symlinks apart (debugfs prints modes with a
    leading zero for directories, 040755) and returns a symlink's absolute target."""
    arac_gerekli("mke2fs", "debugfs")
    lh = arac_modulu("lineage-hazirla.py", "lineage_hazirla")
    kok = os.path.join(gecici, "kok")
    yaz(os.path.join(kok, "system/lib/liba.so"), "a")
    yaz(os.path.join(kok, "system/lib/bootstrap/libc.so"), "c")
    os.symlink("/apex/com.android.runtime/lib/bionic/libm.so", os.path.join(kok, "system/lib/libm.so"))
    image = os.path.join(gecici, "s.img")
    subprocess.run(["mke2fs", "-q", "-t", "ext4", "-d", kok, image, "8M"], check=True)
    assert sorted(lh.ext4_entries(image, "/system/lib")) == [
        ("bootstrap", "d", None), ("liba.so", "f", None),
        ("libm.so", "l", "/apex/com.android.runtime/lib/bionic/libm.so")]
