"""Landscape natural orientation (yamalar/dogal_yon.py): the display property, the product idc
files of the two digitisers, the boot animation and the side fingerprint location stay
consistent with one panel rotation, and the AOSP code they rely on still behaves that way.

Real inputs: the LineageOS vendor stage $WORK/lineage/vendor_dogal_yon (sec_e-pen.idc,
build.prop), the working tree's build.prop files and plat_property_contexts (read only, copied to
scratch), the tablet's vendor property contexts ($WORK/lineage/bolumler) and AOSP
android-17.0.0_r1 sources (network)."""
import base64
import os
import re
import shutil
import socket
import sys
import urllib.error
import urllib.request

import pytest

from conftest import ARACLAR, GERCEK_WORK, gerekli, oku, yaz

sys.path.insert(0, ARACLAR)
from yamalar import dogal_yon  # noqa: E402

STAGE = os.path.join(GERCEK_WORK, "lineage", "vendor_dogal_yon")
AOSP = "https://android.googlesource.com/platform/{repo}/+/refs/tags/android-17.0.0_r1/{path}?format=TEXT"
VENDOR_PC = os.path.join(GERCEK_WORK, "lineage", "bolumler", "vendor", "etc", "selinux", "vendor_property_contexts")
PLAT_PC = os.path.join(GERCEK_WORK, "sys", "system", "etc", "selinux", "plat_property_contexts")


def _aosp(repo, path):
    try:
        with urllib.request.urlopen(AOSP.format(repo=repo, path=path), timeout=60) as r:
            return base64.b64decode(r.read()).decode()
    except (urllib.error.URLError, socket.timeout) as e:
        pytest.skip(f"atlandı: AOSP kaynağı okunamadı: {e}")


@pytest.fixture(scope="module")
def stage():
    gerekli(os.path.join(STAGE, "sec_e-pen.idc"), os.path.join(STAGE, "build.prop"),
            os.path.join(STAGE, "KAYNAK"))
    return dogal_yon.vendor_stage(STAGE)


# ------------------------------------------------------------------ geometry

def test_panel_koseleri_dogal_ekrana_duser():
    w, h = dogal_yon.PANEL_SIZE
    for r in (0, 90, 180, 270):
        nw, nh = (h, w) if r % 180 else (w, h)
        pts = {dogal_yon.rotate_natural_point(x, y, r) for x in (0, w) for y in (0, h)}
        assert pts == {(0, 0), (nw, 0), (0, nh), (nw, nh)}, r


def test_90_panelin_sag_kenarini_uste_tasir():
    # ROTATION_90 today = the new natural orientation: the panel's right edge is the top edge.
    w, h = dogal_yon.PANEL_SIZE
    for y in (0, h // 2, h):
        assert dogal_yon.rotate_natural_point(w, y)[1] == 0
    # the panel's top-left corner ends up bottom-left
    assert dogal_yon.rotate_natural_point(0, 0) == (0, w)


def test_parmak_izi_konumu_gecersiz_girdiyi_reddeder():
    for bad in ("", "1|2", "a|b|c", "1|2|3|4|5"):
        with pytest.raises(dogal_yon.DogalYonHatasi):
            dogal_yon.rotate_fingerprint_location(bad)


def test_parmak_izi_konumu_gercek_vendor_degerinden(stage):
    _pen, vbp = stage
    loc = dogal_yon.read_prop(vbp, dogal_yon.FINGERPRINT_PROP)
    assert loc, "vendor build.prop has no fingerprint location"
    out = dogal_yon.rotate_fingerprint_location(loc)
    for old, new in zip(loc.split(";"), out.split(";")):
        o, n = old.split("|"), new.split("|")
        assert n[2:] == o[2:], "radius and display must stay"
        # side sensor beyond the panel's right edge -> clamped to y=0 on the new top edge so
        # SystemUI's SideFpsSensorInteractor (sensorLocationY != 0 -> vertical) treats it as
        # horizontal on the top edge at the same distance along the edge (x = old y = 295)
        if int(o[0]) >= dogal_yon.PANEL_SIZE[0]:
            assert int(n[1]) == 0 and int(n[0]) == int(o[1])
        # rotating the remaining three quarter turns returns the sensor to the right edge (x = w)
        back = new
        for i in range(3):
            back = dogal_yon.rotate_fingerprint_location(
                back, 90, (dogal_yon.PANEL_SIZE[1], dogal_yon.PANEL_SIZE[0]) if i % 2 == 0 else dogal_yon.PANEL_SIZE)
        b = back.split("|")
        assert int(b[0]) == dogal_yon.PANEL_SIZE[0] and int(b[1]) == int(o[1]) and b[2:] == o[2:]


# ------------------------------------------------------------------ idc and props

def test_idc_dosyalari(stage):
    pen, _ = stage
    files = dogal_yon.idc_files(pen)
    assert set(files) == {d + ".idc" for d in dogal_yon.TOUCH_DEVICES}
    line = f"touch.orientation = ORIENTATION_{dogal_yon.PANEL_ROTATION}"
    for name, text in files.items():
        assert [l for l in text.splitlines() if re.match(r"touch\.orientation\s*=", l)] == [line], name
    for vline in (l for l in pen.splitlines() if l.strip()):
        assert vline in files["sec_e-pen.idc"].splitlines(), vline
    for keep in dogal_yon.UNTOUCHED_DEVICES:
        assert keep + ".idc" not in files


def test_kalem_dokunmatik_degilse_reddedilir():
    with pytest.raises(dogal_yon.DogalYonHatasi):
        dogal_yon.idc_files("touch.orientationAware = 1\n")


def test_ozellik_baglamlari():
    gerekli(PLAT_PC, VENDOR_PC)
    plat = oku(PLAT_PC)
    m = re.search(rf"^{re.escape(dogal_yon.DISPLAY_PROP)}\s+\S+\s+exact\s+enum\s+(.*)$", plat, re.M)
    assert m and dogal_yon.ORIENTATION_VALUE in m.group(1).split()
    # init loads /product/etc/build.prop in its own context; the fingerprint property has a type
    prefixes = [l.split()[0] for t in (plat, oku(VENDOR_PC)) for l in t.splitlines()
                if l.strip() and not l.lstrip().startswith("#")]
    assert any(dogal_yon.FINGERPRINT_PROP.startswith(p) for p in prefixes)


def _agaclar(gecici):
    """Scratch copies of the working tree's build.prop files."""
    src_sys = os.path.join(GERCEK_WORK, "sys", "system", "build.prop")
    src_prod = os.path.join(GERCEK_WORK, "prod", "etc", "build.prop")
    gerekli(src_sys, src_prod)
    sys_dir, prod_dir = os.path.join(gecici, "sys"), os.path.join(gecici, "prod")
    for src, dst in ((src_sys, os.path.join(sys_dir, "system", "build.prop")),
                     (src_prod, os.path.join(prod_dir, "etc", "build.prop"))):
        os.makedirs(os.path.dirname(dst))
        shutil.copyfile(src, dst)
    return sys_dir, prod_dir


def _bootanim_sil(path):
    text = oku(path)
    yaz(path, "\n".join(l for l in text.splitlines()
                        if not l.startswith(dogal_yon.BOOTANIM_PREFIX)) + "\n")


def test_eksik_katmanla_yazmaz(stage, gecici):
    sys_dir, prod_dir = _agaclar(gecici)
    before = oku(os.path.join(prod_dir, "etc", "build.prop"))
    for flags in ((False, False), (True, False), (False, True)):
        with pytest.raises(dogal_yon.DogalYonHatasi):
            dogal_yon.install(sys_dir, prod_dir, *flags, stage=STAGE, log=lambda *_: None)
    assert oku(os.path.join(prod_dir, "etc", "build.prop")) == before
    assert not os.path.exists(os.path.join(prod_dir, "usr"))


def test_bootanim_satiri_kalirsa_dogrulama_durur(stage, gecici):
    sys_dir, prod_dir = _agaclar(gecici)
    sys_bp = os.path.join(sys_dir, "system", "build.prop")
    if not dogal_yon.bootanim_lines(oku(sys_bp)):
        yaz(sys_bp, oku(sys_bp) + dogal_yon.BOOTANIM_PREFIX + "logical_0=ORIENTATION_90\n")
    with pytest.raises(dogal_yon.DogalYonHatasi, match="boot animation"):
        dogal_yon.install(sys_dir, prod_dir, True, True, stage=STAGE, log=lambda *_: None)


def test_kurulum_tutarli_ve_tekrarlanabilir(stage, gecici):
    sys_dir, prod_dir = _agaclar(gecici)
    _bootanim_sil(os.path.join(sys_dir, "system", "build.prop"))
    _bootanim_sil(os.path.join(prod_dir, "etc", "build.prop"))
    dogal_yon.install(sys_dir, prod_dir, True, True, stage=STAGE, log=lambda *_: None)
    first = oku(os.path.join(prod_dir, "etc", "build.prop"))
    dogal_yon.install(sys_dir, prod_dir, True, True, stage=STAGE, log=lambda *_: None)
    assert oku(os.path.join(prod_dir, "etc", "build.prop")) == first
    assert first.count(dogal_yon.BLOCK_BEGIN) == 1
    assert dogal_yon.dogrula(sys_dir, prod_dir, STAGE)
    # a hand edit of a generated idc file is caught
    p = os.path.join(prod_dir, "usr", "idc", "sec_touchscreen.idc")
    yaz(p, "touch.orientation = ORIENTATION_0\n")
    with pytest.raises(dogal_yon.DogalYonHatasi, match="sec_touchscreen"):
        dogal_yon.dogrula(sys_dir, prod_dir, STAGE)


# ------------------------------------------------------------------ the AOSP behaviour relied on

def test_aosp_surfaceflinger_birincil_ekran_yonu():
    sf = _aosp("frameworks/native", "services/surfaceflinger/SurfaceFlinger.cpp")
    assert "primary_display_orientation(Values::ORIENTATION_0)" in sf
    assert "info->installOrientation = display->getPhysicalOrientation();" in sf
    dd = _aosp("frameworks/native", "services/surfaceflinger/DisplayDevice.cpp")
    assert "const auto transformOrientation = orientation + mPhysicalOrientation;" in dd
    assert "InputFlinger do not know about this" in dd


def test_aosp_dokunma_yonu_ve_idc_arama_sirasi():
    tim = _aosp("frameworks/native", "services/inputflinger/reader/mapper/TouchInputMapper.cpp")
    assert 'config.getString("touch.orientation")' in tim
    assert f'*orientationString == "{dogal_yon.ORIENTATION_VALUE}"' in tim
    dev = _aosp("frameworks/native", "libs/input/InputDevice.cpp")
    order = [dev.index(p) for p in ('"/product/usr/"', '"/vendor/usr/"')]
    assert order == sorted(order)


def test_aosp_bootanim_ve_build_prop_sirasi():
    ba = _aosp("frameworks/base", "cmds/bootanimation/BootAnimation.cpp")
    assert '"ro.bootanim.set_orientation_logical_0"' in ba
    ps = _aosp("system/core", "init/property_service.cpp")
    # product is loaded last, in init's own context, so it may override vendor values
    i_vendor = ps.index('load_properties_from_partition("odm"')
    i_product = ps.index('load_properties_from_partition("product"')
    assert i_vendor < i_product
    assert "Overriding previous property" in ps
