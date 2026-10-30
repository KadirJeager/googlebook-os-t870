"""REMOVED_APPS against the pristine mica images: every entry still names something the image
ships (a mica update that renames or drops it shows up here instead of silently doing nothing),
and a removed app leaves none of its own privapp-permissions or preinstalled-packages files
behind (23 Oct 2026: DellRepairStub had been removed while its two files stayed)."""
import os
import re
import subprocess

import pytest

from conftest import EROFS_BIN, MICA, gerekli

AAPT2 = os.path.expanduser("~/Android/Sdk/build-tools/37.0.0/aapt2")
IMAJ = {"prod": "product_a.erofs", "sysext": "system_ext_a.erofs"}
AYAR_DIZINLERI = ("etc/permissions", "etc/sysconfig")


def _ls(image, rel):
    """Names in a directory of a pristine image, or None when the path is missing."""
    r = subprocess.run([os.path.join(EROFS_BIN, "dump.erofs"), "--ls", f"--path=/{rel}",
                        os.path.join(MICA, image)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True)
    if r.returncode != 0:
        return None
    return [m.group(1) for m in re.finditer(r"^\s*\d+\s+\d+\s+(\S+)$", r.stdout, re.M)
            if m.group(1) not in (".", "..")]


def _var_mi(image, rel):
    parent, name = os.path.split(rel)
    names = _ls(image, parent)
    return names is not None and name in names


def _paket_adi(ap, image, apk_rel, gecici):
    path = os.path.join(gecici, os.path.basename(apk_rel))
    with open(path, "wb") as f:
        f.write(ap.pristine_file(apk_rel, image))
    out = subprocess.run([AAPT2, "dump", "packagename", path], check=True,
                         stdout=subprocess.PIPE, text=True).stdout.strip()
    assert re.fullmatch(r"[\w.]+", out), out
    return out


@pytest.mark.parametrize("key", sorted(IMAJ))
def test_girdiler_imajda_var(ap, key):
    gerekli(*(os.path.join(MICA, i) for i in IMAJ.values()), os.path.join(EROFS_BIN, "dump.erofs"))
    eksik = [rel for rel in ap.REMOVED_APPS[key] if not _var_mi(IMAJ[key], rel)]
    assert not eksik, f"{key}: pristine image no longer ships {eksik}"


@pytest.mark.parametrize("key", sorted(IMAJ))
def test_kaldirilanin_ayarlari_kalmaz(ap, key, gecici):
    gerekli(*(os.path.join(MICA, i) for i in IMAJ.values()), os.path.join(EROFS_BIN, "dump.erofs"),
            AAPT2)
    image = IMAJ[key]
    removed = set(ap.REMOVED_APPS[key])
    kalan = []
    for rel in removed:
        if not re.fullmatch(r"(priv-)?app/[^/]+", rel):
            continue
        apks = [n for n in _ls(image, rel) or [] if n.endswith(".apk")]
        assert apks, f"{rel}: no APK in the pristine image"
        pkg = _paket_adi(ap, image, f"{rel}/{apks[0]}", gecici)
        for d in AYAR_DIZINLERI:
            for name in _ls(image, d) or []:
                if name.endswith(f"-{pkg}.xml") and f"{d}/{name}" not in removed:
                    kalan.append(f"{d}/{name} ({rel})")
    assert not kalan, f"{key}: files of removed apps stay in the image: {kalan}"


def test_supportassist_kaldirilir(ap):
    """Kadir's decision of 23 Oct 2026; Dell Diagnostics (battery card) is kept."""
    assert "priv-app/DellSupportAssistStub" in ap.REMOVED_APPS["prod"]
    assert not any("DellDiagnosticsStub" in rel or "com.dell.diagnostics" in rel
                   for rel in ap.REMOVED_APPS["prod"])
