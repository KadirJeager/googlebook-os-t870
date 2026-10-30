"""patch_product's Vulkan properties: apps (HWUI) and SurfaceFlinger's client composition draw
through Vulkan (measured 23 Oct 2026, cihaz-kabul/kanit/20261023-gl-cizim vs -vk-cizim). The
writer runs on the real pristine product build.prop in a scratch PROD_DIR: the properties are set
once, nowhere else in the file, and a second run gives the same file."""
import os

from conftest import MICA, gerekli, oku, yaz

ISTENEN = {"ro.hwui.use_vulkan": "true", "debug.renderengine.backend": "skiavkthreaded"}


def _build_prop_yaz(ap, gecici):
    """Runs only patch_product's build.prop step: the pristine file through the same code."""
    src = ap.pristine_file("etc/build.prop", "product_a.erofs").decode()
    yol = os.path.join(gecici, "etc/build.prop")
    yaz(yol, src)
    kod = oku(os.path.join(os.path.dirname(ap.__file__), "apply_patches.py"))
    bas = kod.index('    # Update /product/etc/build.prop idempotently')
    son = kod.index('    # Patch product_property_contexts from pristine copy')
    adim = "def _adim(PROD_DIR):\n" + kod[bas:son]
    ns = dict(vars(ap))
    exec(adim, ns)
    ns["_adim"](gecici)
    return oku(yol)


def _degerler(text, key):
    return [l.split("=", 1)[1] for l in text.splitlines() if l.startswith(key + "=")]


def test_vulkan_ozellikleri(ap, gecici):
    gerekli(os.path.join(MICA, "product_a.erofs"))
    bir = _build_prop_yaz(ap, gecici)
    for key, value in ISTENEN.items():
        assert _degerler(bir, key) == [value], key
    iki = _build_prop_yaz(ap, gecici)
    assert bir == iki


def test_ozellik_baglami(ap):
    """ro.hwui.use_vulkan is a platform property (exported_default_prop, bool); the debug.* one
    falls under the platform's debug_prop prefix: no product_property_contexts line is needed."""
    gerekli(os.path.join(MICA, "system_a.erofs"))
    plat = ap.pristine_file("system/etc/selinux/plat_property_contexts").decode()
    assert any(l.split()[:1] == ["ro.hwui.use_vulkan"] and "bool" in l for l in plat.splitlines())
    assert any(l.split()[:1] == ["debug."] for l in plat.splitlines())
