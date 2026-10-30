"""j) yamalar/kamera_jpeg.py: the framework.jar camera patch (system-side JPEG next to a video
encoder) against the real pristine framework.jar, and the working tree.

The module merges gbos.CameraBridge into classes2.dex with d8, puts the hidden-API flags back,
rewrites five binder calls of ICameraDeviceUserWrapper and adds classes7.dex (gbos.CameraJpegShim).
Each step is checked on the real jar: the hiddenapi round trip reproduces the pristine section byte
for byte, every pristine member keeps its flag after the merge, the five call sites decode to
invoke-static CameraBridge.<name> with the wrapper as first register inside the method's try block
and the rest of each method is untouched, the shim dex holds the shim classes only, and the
working tree's framework.jar carries exactly these two entries (yama-dogrula's 0 differences)."""
import io
import os
import re
import zipfile

import pytest

from conftest import GERCEK_WORK, gerekli, oku

SDK_JAR = os.path.expanduser("~/Android/Sdk/platforms/android-37.0/android.jar")


@pytest.fixture(scope="module")
def kj():
    import importlib
    return importlib.import_module("yamalar.kamera_jpeg")


@pytest.fixture(scope="module")
def pristine_jar(ap):
    gerekli(os.path.join(ap.MICA_IMAGES, "system_a.erofs"))
    return ap.pristine_file("system/framework/framework.jar")


@pytest.fixture(scope="module")
def uretim(kj, pristine_jar, gecici_kok):
    """framework_replacements on the pristine jar, once per module."""
    gerekli(kj.D8, kj.DEXDUMP, kj.ANDROID_JAR)
    scratch = os.path.join(gecici_kok, "kamera-jpeg")
    os.makedirs(scratch, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(pristine_jar)) as z:
        pristine_dex = z.read(kj.WRAPPER_DEX)
        rep = kj.framework_replacements(z, scratch, log=lambda *a: None)
    return pristine_dex, rep, scratch


def test_hiddenapi_gidis_donus(kj, pristine_jar):
    """Decoding the pristine hiddenapi section and encoding it again gives the same bytes."""
    with zipfile.ZipFile(io.BytesIO(pristine_jar)) as z:
        dex = z.read(kj.WRAPPER_DEX)
    assert kj.hiddenapi_roundtrip_ok(dex)
    flags = kj.hiddenapi_decode(dex)
    assert flags[(kj.WRAPPER, ("M", "endConfigure", "(ILandroid/hardware/camera2/impl/CameraMetadataNative;J)[I"))] == 2
    assert flags[(kj.WRAPPER, ("M", "createStream", "(Landroid/hardware/camera2/params/OutputConfiguration;)I"))] == 3


def test_birlesik_dex_bayraklar_ve_tablolar(kj, uretim):
    """After the merge every pristine member keeps its flag, the bridge's members are blocked,
    the method id table stays under the limit and the map has the hiddenapi section last but one."""
    pristine_dex, rep, _ = uretim
    merged = rep[kj.WRAPPER_DEX]
    before = kj.hiddenapi_decode(pristine_dex)
    after = kj.hiddenapi_decode(merged)
    assert all(after[k] == v for k, v in before.items())
    bridge = {k: v for k, v in after.items() if k[0] == kj.BRIDGE}
    assert {m[1][1] for m in bridge} >= set(kj.HOOKS)
    assert all(v == kj.BRIDGE_FLAG for v in bridge.values())
    x = kj.Dex(merged)
    assert x.n_method <= 0xFFFF and x.n_method > kj.Dex(pristine_dex).n_method
    types = [m[0] for m in x.map_items()]
    assert types[-2:] == [kj.MAP_HIDDENAPI, kj.MAP_LIST]
    assert x.map_off + 4 + 12 * len(types) == len(merged)


def test_bes_cagri_kopruye(kj, uretim):
    """Each hooked method: one invoke-static CameraBridge.<name> whose first register is the
    wrapper's `this`, the interface call gone, the catch blocks and the rest of the code unchanged."""
    pristine_dex, rep, scratch = uretim
    before = kj._wrapper_methods(kj._dexdump(pristine_dex, scratch))
    after = kj._wrapper_methods(kj._dexdump(rep[kj.WRAPPER_DEX], scratch))
    for hook in kj.HOOKS:
        kb = [k for k in before if k.startswith(hook + ":")]
        ka = [k for k in after if k.startswith(hook + ":")]
        assert len(kb) == 1 and ka == kb, hook
        # d8 lays the merged dex out anew, so instructions are compared by their address inside
        # the method (code units), not by file offset
        b = [(u, t) for _o, t, u in before[kb[0]]]
        a = [(u, t) for _o, t, u in after[ka[0]]]
        texts_b = [t for _o, t in b]
        texts_a = [t for _o, t in a]
        assert not any(f"{kj.REMOTE}.{hook}:" in t for t in texts_a), hook
        calls = [t for t in texts_a if t.startswith("invoke-static ") and f"{kj.BRIDGE}.{hook}:" in t]
        assert len(calls) == 1, (hook, texts_a)
        iget = next(t for t in texts_b if "mRemoteDevice:" in t)
        this_reg = re.match(r"iget-object v\d+, v(\d+),", iget).group(1)
        regs = re.findall(r"v(\d+)", calls[0].split("}")[0])
        orig = next(t for t in texts_b if f"{kj.REMOTE}.{hook}:" in t)
        orig_regs = re.findall(r"v(\d+)", orig.split("}")[0])
        assert regs == [this_reg] + orig_regs[1:], (hook, calls[0])
        # everything but the rewritten iget/invoke is identical (by offset), the handlers stay
        bo, ao = dict(b), dict(a)
        iget_off = next(o for o, t in b if "mRemoteDevice:" in t)
        call_off = next(o for o, t in b if f"{kj.REMOTE}.{hook}:" in t)
        for off in sorted(set(bo) | set(ao)):
            if off == call_off:
                assert ao[off].startswith("invoke-static "), (hook, ao[off])
            elif off == iget_off and call_off == iget_off + 2:
                assert ao[off].startswith("nop"), (hook, ao[off])
            elif off == iget_off + 1 and call_off == iget_off + 2:
                assert off not in bo and ao[off].startswith("nop"), (hook, ao.get(off))
            else:
                assert bo.get(off) == ao.get(off), (hook, off, bo.get(off), ao.get(off))
        assert "throwAsPublicException" in " ".join(texts_a) or hook == "disconnect"


def test_shim_dex(kj, uretim):
    """classes7.dex holds the shim classes and nothing else, and references the hidden camera
    classes it needs by their real descriptors."""
    _p, rep, _s = uretim
    x = kj.Dex(rep[kj.SHIM_DEX])
    defined = {c for c, _ in x.class_defs()}
    assert kj.SHIM in defined and all(c.startswith("Lgbos/CameraJpegShim") for c in defined)
    assert kj.WRAPPER in x.types and kj.REMOTE in x.types
    assert "Landroid/media/ImageWriter;" in x.types and "Landroid/graphics/YuvImage;" in x.types


def test_calisan_agac_ayni(kj, uretim):
    """The working tree's framework.jar has exactly these two entries (byte-identical, stored)."""
    _p, rep, _s = uretim
    jar = os.path.join(GERCEK_WORK, "sys/system/framework/framework.jar")
    gerekli(jar)
    with zipfile.ZipFile(jar) as z:
        for name, data in rep.items():
            info = z.getinfo(name)
            assert info.compress_type == zipfile.ZIP_STORED, name
            assert z.read(name) == data, f"{name} differs from today's output"
