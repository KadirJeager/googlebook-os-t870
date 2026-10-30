"""plat_file_contexts blocks: apply_patches.add_plat_file_contexts (a) and the module blocks of
yamalar/mica_servisleri.py and yamalar/terminal.py (b).

The working tree's plat_file_contexts is never reset to the pristine copy: every run of
apply_patches.py edits the file it finds. A writer that is not idempotent or depends on call order
therefore makes the tree drift from "pristine + patches", which yama-dogrula.py reports as a
difference and an OTA rebuild would lose."""
import itertools
import os
import subprocess

import pytest

from conftest import (GERCEK_WORK, PLAT_FC, ap_girdileri, arac_gerekli, disk_payi, gerekli, oku,
                      sil, yaz)

# What older apply_patches.py versions appended (constant of patch_framework in the 21 Oct 22:46
# build; the 01:30 known-good image carries exactly this tail, see test_eski_bicim_tasinir).
ESKI_EK = "\n/system/bin/netbpfload u:object_r:bpfloader_exec:s0\n"
BILINEN_IYI = os.path.join(GERCEK_WORK, "denetim", "bilinen-iyi-272-0130", "system.img.zst")


def calistir(ap, monkeypatch, dizin, metin, *cagrilar):
    """Writes `metin` as plat_file_contexts under `dizin`, runs add_plat_file_contexts once per
    argument tuple and returns the resulting file."""
    monkeypatch.setattr(ap, "SYS_DIR", dizin)
    yol = os.path.join(dizin, PLAT_FC)
    yaz(yol, metin)
    for girdiler in cagrilar:
        ap.add_plat_file_contexts(*girdiler)
    return oku(yol)


@pytest.fixture
def beklenen(ap, monkeypatch, gecici, pristine_fc):
    """Reference result: pristine file, one call per entry in source order."""
    return calistir(ap, monkeypatch, os.path.join(gecici, "ref"), pristine_fc,
                    *[(e,) for e in ap_girdileri()])


# ---------------------------------------------------------------- a) add_plat_file_contexts

def test_tek_sirali_blok(ap, beklenen, pristine_fc):
    """The labels land in one block at the end, sorted, exactly once; the pristine part is left
    byte for byte as shipped (no stray blank line, nothing reordered)."""
    girdiler = ap_girdileri()
    blok = [ap.FC_BLOCK_BEGIN, *sorted(set(girdiler)), ap.FC_BLOCK_END]
    assert beklenen == pristine_fc.rstrip("\n") + "\n" + "\n".join(blok) + "\n"
    assert beklenen.count(ap.FC_BLOCK_BEGIN) == 1 and beklenen.count(ap.FC_BLOCK_END) == 1
    for e in girdiler:
        assert beklenen.split("\n").count(e) == 1, e


def test_cagri_sirasindan_bagimsiz(ap, monkeypatch, gecici, pristine_fc, beklenen):
    """Same file whatever the order of the calls, one call with every entry, or an entry passed
    twice: install_32bit_runtime and patch_framework may be reordered without changing the image."""
    girdiler = ap_girdileri()
    denemeler = [[(e,) for e in p] for p in itertools.permutations(girdiler)]
    denemeler.append([tuple(girdiler)])
    denemeler.append([(e,) for e in girdiler + girdiler[:1]])
    for i, cagrilar in enumerate(denemeler):
        got = calistir(ap, monkeypatch, os.path.join(gecici, str(i)), pristine_fc, *cagrilar)
        assert got == beklenen, f"call order {cagrilar} gives a different file"


def test_tekrar_calistirma_ayni(ap, monkeypatch, gecici, beklenen):
    """Running every call again on an already patched file changes nothing."""
    got = calistir(ap, monkeypatch, gecici, beklenen, *[(e,) for e in ap_girdileri()])
    assert got == beklenen


def test_eski_bicim_tasinir(ap, monkeypatch, gecici, pristine_fc, beklenen):
    """A tree last patched by an older apply_patches.py ("\\n/system/bin/netbpfload …\\n" appended
    after a blank line) is migrated into the block, its blank line removed, so the result equals a
    run on the pristine file. Input: plat_file_contexts of the 01:30 known-good system image."""
    gerekli(BILINEN_IYI)
    arac_gerekli("zstd", "debugfs")
    disk_payi(1.5)  # the 01:30 system.img is 1432 MiB, removed as soon as it is read
    img = os.path.join(gecici, "system.img")
    try:
        subprocess.run(["zstd", "-dq", BILINEN_IYI, "-o", img], check=True)
        eski = subprocess.run(["debugfs", "-R", f"cat /{PLAT_FC}", img], check=True,
                              capture_output=True, text=True).stdout
    finally:
        sil(img)
    if eski != pristine_fc + ESKI_EK:
        pytest.skip("atlandı: 01:30 imajının plat_file_contexts'i 'güncel mica + eski ek' değil "
                    f"(son satırlar: {eski.splitlines()[-2:]})")
    got = calistir(ap, monkeypatch, os.path.join(gecici, "t"), eski,
                   *[(e,) for e in ap_girdileri()])
    assert got == beklenen


def test_blok_onundeki_bos_satir(ap, monkeypatch, gecici, beklenen):
    """An intermediate version wrote a blank line before its block (add_plat_file_contexts'
    docstring); that line goes away with the old block, also combined with the old tail."""
    satirlar = beklenen.split("\n")
    i = satirlar.index(ap.FC_BLOCK_BEGIN)
    bos_satirli = "\n".join(satirlar[:i] + [""] + satirlar[i:])
    ikisi = "\n".join(satirlar[:i]) + ESKI_EK + "\n" + "\n".join(satirlar[i:])
    for n, metin in enumerate((bos_satirli, ikisi)):
        got = calistir(ap, monkeypatch, os.path.join(gecici, str(n)), metin,
                       *[(e,) for e in ap_girdileri()])
        assert got == beklenen


def test_modul_bloklari_onunde_bos_satir(ap, monkeypatch, gecici):
    """The same blank line in the layout the pipeline really has, the apply_patches.py block
    followed by the module blocks (working tree file): there the block is not at the end, so no
    trailing-blank cleanup hides a left-over line; the result must equal a run without it."""
    calisan = os.path.join(GERCEK_WORK, "sys", PLAT_FC)
    gerekli(calisan)
    satirlar = oku(calisan).split("\n")
    if ap.FC_BLOCK_BEGIN not in satirlar or ap.mica_servisleri.FC_BEGIN not in oku(calisan):
        pytest.skip("atlandı: çalışan ağaçta apply_patches.py ve modül blokları yok")
    i = satirlar.index(ap.FC_BLOCK_BEGIN)
    cagrilar = [(e,) for e in ap_girdileri()]
    temiz = calistir(ap, monkeypatch, os.path.join(gecici, "temiz"), "\n".join(satirlar), *cagrilar)
    bos = calistir(ap, monkeypatch, os.path.join(gecici, "bos"),
                   "\n".join(satirlar[:i] + [""] + satirlar[i:]), *cagrilar)
    assert bos == temiz


def _modul_bloklari(ap, metin):
    """The mica-servisleri and GBOS_TERMINAL blocks of a file, exactly as written."""
    ms = ap.mica_servisleri
    out = {}
    b = metin.index(ms.FC_BEGIN)
    out["mica-servisleri"] = metin[b:metin.index(ms.FC_END, b) + len(ms.FC_END)]
    m = ap.gbos_terminal.FC_BLOCK.search(metin)
    assert m, "GBOS_TERMINAL block missing"
    out["terminal"] = m.group(0).lstrip("\n")
    return out


def test_modul_bloklari_varken_idempotent(ap, monkeypatch, gecici):
    """On the working tree's file, which carries the mica-servisleri and GBOS_TERMINAL blocks,
    a second run gives the first run's result, and both module blocks stay intact and single."""
    calisan = os.path.join(GERCEK_WORK, "sys", PLAT_FC)
    gerekli(calisan)
    giris = oku(calisan)
    if ap.mica_servisleri.FC_BEGIN not in giris or "GBOS_TERMINAL BEGIN" not in giris:
        pytest.skip("atlandı: çalışan ağacın plat_file_contexts'inde modül blokları yok")
    cagrilar = [(e,) for e in ap_girdileri()]
    bir = calistir(ap, monkeypatch, os.path.join(gecici, "1"), giris, *cagrilar)
    iki = calistir(ap, monkeypatch, os.path.join(gecici, "2"), bir, *cagrilar)
    assert iki == bir
    once, sonra = _modul_bloklari(ap, giris), _modul_bloklari(ap, iki)
    assert sonra == once
    assert iki.count(ap.mica_servisleri.FC_BEGIN) == 1
    assert iki.count("# --- GBOS_TERMINAL BEGIN") == 1
    assert iki.count(ap.FC_BLOCK_BEGIN) == 1


# ---------------------------------------------------------------- b) module blocks

def _moduller_hazir(ap):
    """mica_servisleri.install() would rebuild a stale binary into mica-servisleri/out and
    terminal.install() needs terminal/out; the tests never write outside testler/."""
    ms = ap.mica_servisleri
    gerekli(ms.BINARY, ms.STAMP)
    if oku(ms.STAMP).strip() != ap.mica_servisleri.hazir_ikili.stamp(ms.SRC):
        pytest.skip("atlandı: mica-servisleri/out bayat; install() derleme yapardı "
                    "(mica-servisleri/build.sh)")
    built = os.path.join(ap.gbos_terminal.TERMINAL, "out", "arm64", "system")
    gerekli(*[os.path.join(built, rel) for rel in ap.gbos_terminal.BUILT])


def _agac(kok, pristine_fc):
    sys_dir, sysext, prod = (os.path.join(kok, n) for n in ("sys", "sysext", "prod"))
    yaz(os.path.join(sys_dir, PLAT_FC), pristine_fc)
    os.makedirs(sysext)
    os.makedirs(prod)
    return sys_dir, sysext, prod


def _zincir(ap, monkeypatch, sys_dir, sysext, prod):
    """What apply_patches.py's __main__ does to plat_file_contexts, in its order."""
    monkeypatch.setattr(ap, "SYS_DIR", sys_dir)
    for e in ap_girdileri():
        ap.add_plat_file_contexts(e)
    ap.mica_servisleri.install(sys_dir, sysext, prod)
    ap.gbos_terminal.install(sys_dir, sysext, prod)
    ap.gbos_guncelleyici.install_file_contexts(sys_dir)
    return oku(os.path.join(sys_dir, PLAT_FC))


@pytest.mark.parametrize("modul", ["mica_servisleri", "terminal"])
def test_modul_iki_kez_ayni_dosya(ap, gecici, pristine_fc, modul):
    """Each module alone: install() twice on a pristine file gives the same file as once, and the
    block appears once (no stacked blocks, no growing blank lines)."""
    _moduller_hazir(ap)
    sys_dir, sysext, prod = _agac(gecici, pristine_fc)
    mod = ap.mica_servisleri if modul == "mica_servisleri" else ap.gbos_terminal
    mod.install(sys_dir, sysext, prod)
    bir = oku(os.path.join(sys_dir, PLAT_FC))
    mod.install(sys_dir, sysext, prod)
    iki = oku(os.path.join(sys_dir, PLAT_FC))
    assert iki == bir
    assert bir.startswith(pristine_fc.rstrip("\n") + "\n")
    isaret = ap.mica_servisleri.FC_BEGIN if modul == "mica_servisleri" else "# --- GBOS_TERMINAL BEGIN"
    assert bir.count(isaret) == 1


def test_zincir_pristine_ve_tekrar_ayni(ap, monkeypatch, gecici, pristine_fc):
    """apply_patches.py's whole sequence (own labels, mica-servisleri, terminal) on the pristine
    file and again on its own output gives the same file."""
    _moduller_hazir(ap)
    sys_dir, sysext, prod = _agac(gecici, pristine_fc)
    bir = _zincir(ap, monkeypatch, sys_dir, sysext, prod)
    iki = _zincir(ap, monkeypatch, sys_dir, sysext, prod)
    assert iki == bir
    for isaret in (ap.FC_BLOCK_BEGIN, ap.mica_servisleri.FC_BEGIN, "# --- GBOS_TERMINAL BEGIN",
                   "# --- GBOS_GUNCELLEYICI BEGIN"):
        assert bir.count(isaret) == 1, isaret


def test_calisan_agac_zincirle_ayni(ap, monkeypatch, gecici, pristine_fc):
    """The working tree's plat_file_contexts is exactly what the current tools produce from the
    pristine file (yama-dogrula.py's 0-difference for this file): the shipped image carries no
    label the scripts would not reproduce, and misses none."""
    _moduller_hazir(ap)
    calisan = os.path.join(GERCEK_WORK, "sys", PLAT_FC)
    gerekli(calisan)
    sys_dir, sysext, prod = _agac(gecici, pristine_fc)
    assert _zincir(ap, monkeypatch, sys_dir, sysext, prod) == oku(calisan)
