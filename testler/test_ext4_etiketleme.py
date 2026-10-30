"""g) make_ext4_images.py labels files the patches add (no *.meta entry) with the device's own
file_contexts and stops when none matches.

Why: an added file that ends up rootfs/unlabeled, or with the plat label the patches override
(/system/lib/bootstrap is system_bootstrap_lib_file in mica; the 32-bit vendor HALs map their libc
from there and enforcing denies that), breaks services only on the tablet. The tool is run as the
pipeline runs it (`make_ext4_images.py system`, GBOS_WORK = a small scratch work dir made of real
files from the working tree, the real sys.meta and the real system/system_ext/product
file_contexts); the labels are read back from the built image with debugfs ea_get. The tool runs
its builds at import time, so it is run as a program, not imported."""
import os
import subprocess
import sys

import pytest

from conftest import (ARACLAR, GERCEK_WORK, PLAT_FC, ap_girdileri, arac_gerekli, disk_payi,
                      gerekli, oku, reflink, sil)

# Real files of the working tree copied into the small tree.
META_DOSYALARI = ["system/build.prop", PLAT_FC]  # recorded in sys.meta: keep the captured label
YENI_DOSYALAR = [  # added by the patches: labelled by file_contexts
    "system/bin/gbos-linuxd",
    "system/bin/gbos-linux",
    "system/bin/gbos-vpd-executor",
    "system/etc/ld.config.32",
    "system/lib/bootstrap/libc.so",
]
DEVICE_FC = ["sysext/etc/selinux/system_ext_file_contexts", "prod/etc/selinux/product_file_contexts"]
ESLESMEYEN = "gbos-test-eslesmeyen"  # a new top-level file no file_contexts entry covers


def _fc_satiri(metin, spec):
    """Context of the file_contexts line whose path specification is exactly `spec`."""
    found = [line.split()[-1] for line in metin.splitlines()
             if line.split() and line.split()[0] == spec]
    assert len(found) == 1, f"{spec}: {len(found)} lines"
    return found[0]


def beklenen_etiketler(ap, pristine_fc):
    """Expected label of each added file, taken from where it is declared: the terminal module's
    file_contexts, mica-servisleri's FC_LINES, apply_patches.py's own block entry, the pristine
    plat_file_contexts (ld.config.*); gbos-linux has no entry of its own: /system(/.*)?."""
    terminal_fc = oku(os.path.join(ap.gbos_terminal.TERMINAL, "selinux", "file_contexts"))
    bootstrap = [e for e in ap_girdileri() if e.startswith("/system/lib/bootstrap")]
    assert len(bootstrap) == 1, bootstrap
    return {
        "/system/bin/gbos-linuxd": _fc_satiri(terminal_fc, "/system/bin/gbos-linuxd"),
        "/system/bin/gbos-vpd-executor": _fc_satiri(ap.mica_servisleri.FC_LINES,
                                                    "/system/bin/gbos-vpd-executor"),
        "/system/etc/ld.config.32": _fc_satiri(pristine_fc, r"/system/etc/ld\.config.*"),
        "/system/lib/bootstrap/libc.so": bootstrap[0].split()[-1],
        "/system/lib/bootstrap": bootstrap[0].split()[-1],
        "/system/bin/gbos-linux": _fc_satiri(pristine_fc, "/system(/.*)?"),
    }


def _meta_etiketi(meta, path):
    root = oku(meta, "r").split("\n", 1)[0].split("\t")[1]
    with open(meta, errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if parts[0] == "XATTR" and parts[1] == root + path and parts[2] == "security.selinux":
                return bytes.fromhex(parts[3]).rstrip(b"\0").decode()
    raise AssertionError(f"{path} has no label in {meta}")


def _image_label(img, path):
    r = subprocess.run(["debugfs", "-R", f'ea_get -V "{path}" security.selinux', img],
                       capture_output=True, text=True, check=True)
    lines = [ln.strip().rstrip("\x00") for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"{path}: {r.stdout!r} {r.stderr!r}"
    return lines[0]


def _kucuk_agac(work, ekstra=()):
    gerekli(os.path.join(GERCEK_WORK, "sys.meta"),
            *[os.path.join(GERCEK_WORK, "sys", rel) for rel in META_DOSYALARI + YENI_DOSYALAR],
            *[os.path.join(GERCEK_WORK, rel) for rel in DEVICE_FC])
    for rel in META_DOSYALARI + YENI_DOSYALAR + list(ekstra):
        src = os.path.join(GERCEK_WORK, "sys", rel if rel != ESLESMEYEN else "system/build.prop")
        reflink(src, os.path.join(work, "sys", rel))
    for rel in DEVICE_FC:
        reflink(os.path.join(GERCEK_WORK, rel), os.path.join(work, rel))
    reflink(os.path.join(GERCEK_WORK, "sys.meta"), os.path.join(work, "sys.meta"))


def _insa(work):
    env = dict(os.environ, GBOS_WORK=work)
    return subprocess.run([sys.executable, "-I", os.path.join(ARACLAR, "make_ext4_images.py"),
                           "system"], env=env, cwd=work, capture_output=True, text=True)


@pytest.fixture
def ext4_araclari():
    arac_gerekli("mke2fs", "e2fsdroid", "e2fsck", "debugfs")
    disk_payi(0.5)  # the image is a sparse 1.4 GiB file; only the small tree and fs metadata are written


def test_yeni_dosyalar_dogru_etiketlenir(ap, pristine_fc, gecici, ext4_araclari):
    """Added files get the label their module declares (gbos_linuxd_exec, gbos_vpd_executor_exec,
    system_linker_config_file, system_lib_file for /system/lib/bootstrap, not the pristine
    system_bootstrap_lib_file), files in sys.meta keep their captured label, and the report
    <name>_new_file_labels.txt says the same as the image."""
    work = os.path.join(gecici, "work")
    _kucuk_agac(work)
    img = os.path.join(work, "system.img")
    try:
        r = _insa(work)
        assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
        want = beklenen_etiketler(ap, pristine_fc)
        bootstrap_plat = _fc_satiri(pristine_fc, "/system/lib(64)?/bootstrap(/.*)?")
        assert want["/system/lib/bootstrap/libc.so"] != bootstrap_plat
        for path, label in want.items():
            assert _image_label(img, path) == label, path
        for rel in META_DOSYALARI:
            path = "/" + rel
            assert _image_label(img, path) == _meta_etiketi(os.path.join(work, "sys.meta"), path)
        rapor = dict(line.split(" ", 1) for line in
                     oku(os.path.join(work, "system_new_file_labels.txt")).splitlines())
        for path, label in want.items():
            assert rapor.get(path) == label, path
        for rel in META_DOSYALARI:
            assert "/" + rel not in rapor
    finally:
        sil(img)


def test_eslesmeyen_dosyada_durur(gecici, ext4_araclari):
    """A new file that only the tool's own "/.* rootfs" fallback matches stops the build with a
    non-zero exit naming the file; the report shows the fallback label."""
    work = os.path.join(gecici, "work")
    _kucuk_agac(work, ekstra=[ESLESMEYEN])
    try:
        r = _insa(work)
        assert r.returncode != 0
        assert "no file_contexts entry labels" in r.stderr and f"/{ESLESMEYEN}" in r.stderr, r.stderr[-800:]
        rapor = oku(os.path.join(work, "system_new_file_labels.txt"))
        assert f"/{ESLESMEYEN} u:object_r:rootfs:s0" in rapor.splitlines()
    finally:
        sil(os.path.join(work, "system.img"))
