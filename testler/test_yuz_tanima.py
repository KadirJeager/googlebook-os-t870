"""Face unlock from source (yuz-tanima/): the patches over Evolution-X's FaceUnlock, the checkout the
build reads, and a clean build reproducing what the image carries.

Real inputs: yuz-tanima/FaceUnlock and tools/ (getir.sh), the pristine mica system image, the
project's sense key ($GBOS_ANAHTAR) and the working tree's installed copies (read only)."""
import hashlib
import os
import re
import shutil
import subprocess

from conftest import ARACLAR, GERCEK_WORK, MICA, arac_gerekli, disk_payi, gerekli, oku, sha256

YUZ = os.path.join(ARACLAR, "yuz-tanima")
FACE = os.path.join(YUZ, "FaceUnlock")
ANAHTAR = os.environ.get("GBOS_ANAHTAR", os.path.expanduser("~/.android-certs/googlebookos-t870"))


def _yamalar():
    d = os.path.join(YUZ, "yamalar")
    return [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".patch")]


def test_yamalar_lisans_ve_kisisel_veri():
    """The patches keep every upstream copyright and license line, and carry no address but the
    GitHub noreply one (they are published with the project)."""
    yamalar = _yamalar()
    assert yamalar
    for y in yamalar:
        metin = oku(y)
        silinen = [l for l in metin.splitlines()
                   if l.startswith("-") and not l.startswith("---")
                   and re.search(r"Copyright|Licen[cs]e", l)]
        assert not silinen, f"{os.path.basename(y)} removes license lines: {silinen[:3]}"
        # an address, not Kotlin's this@Label: a lower-case host with a known top-level domain
        adresler = set(re.findall(r"[\w.+-]+@(?:[a-z0-9-]+\.)+(?:com|net|org|io|dev|tr)\b", metin))
        assert adresler <= {"KadirJeager@users.noreply.github.com"}, adresler


def test_kaynak_agaci_sabitle_ayni():
    """The FaceUnlock checkout the build reads is exactly upstream + yamalar/ (the tree hash getir.sh
    pins): a change made there and not exported to a patch fails here, not on someone else's
    clean build."""
    gerekli(os.path.join(FACE, "AndroidManifest.xml"))
    beklenen = re.search(r"^FACEUNLOCK_TREE_SHA256=(\w+)$", oku(os.path.join(YUZ, "getir.sh")),
                         re.M).group(1)
    satirlar = []
    for kok, dizinler, dosyalar in os.walk(FACE):
        dizinler[:] = [d for d in dizinler if not (kok == FACE and d == ".git")]
        for ad in dosyalar:
            yol = os.path.join(kok, ad)
            satirlar.append((("./" + os.path.relpath(yol, FACE)).encode(), sha256(yol)))
    # same as getir.sh: sha256sum over the sorted file list, then sha256 of that listing
    liste = b"".join(f"{h}  ".encode() + ad + b"\n" for ad, h in sorted(satirlar))
    assert hashlib.sha256(liste).hexdigest() == beklenen


def test_temiz_derleme_imajdakiyle_ayni(gecici):
    """build.sh in a fresh directory (no out/, no earlier APK in the image tree to borrow from)
    gives byte-identical HAL, app and overlay to the ones the working tree installs."""
    gerekli(os.path.join(FACE, "AndroidManifest.xml"), os.path.join(YUZ, "tools", "kotlinc"),
            os.path.join(YUZ, "tools", "dex-tools-v2.4"), os.path.join(MICA, "system_a.erofs"),
            os.path.join(ANAHTAR, "sense.keystore"))
    arac_gerekli("javac", "jar", "unzip")
    disk_payi(2)
    kok = os.path.join(gecici, "yuz-tanima")
    os.makedirs(kok)
    # build.sh ends with stamp.sh, which hashes getir.sh and yamalar/ too
    for ad in ("build.sh", "sinif-yolu.sh", "stamp.sh", "getir.sh"):
        shutil.copy2(os.path.join(YUZ, ad), kok)
    for ad in ("hal_src", "aosp", "yamalar"):
        shutil.copytree(os.path.join(YUZ, ad), os.path.join(kok, ad))
    for ad in ("FaceUnlock", "tools"):
        os.symlink(os.path.join(YUZ, ad), os.path.join(kok, ad))
    env = dict(os.environ, MICA_IMAGES=MICA, SYS_TREE=os.path.join(gecici, "yok"))
    r = subprocess.run([os.path.join(kok, "build.sh")], capture_output=True, text=True, env=env,
                       timeout=1200)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    kurulu = {
        "FaceUnlock.apk": "sys/system/priv-app/FaceUnlock/FaceUnlock.apk",
        "sense-face-hal.jar": "sys/system/framework/sense-face-hal.jar",
        "FaceUnlockOverlay.apk": "prod/overlay/FaceUnlockOverlay.apk",
    }
    for cikti, yol in kurulu.items():
        gerekli(os.path.join(GERCEK_WORK, yol))
        assert sha256(os.path.join(kok, "out", cikti)) == sha256(os.path.join(GERCEK_WORK, yol)), cikti
