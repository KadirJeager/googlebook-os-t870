"""e) patch_binder_and_hidlbase_threadpool: the byte patterns of the threadpool shrink check (and
the 32-bit isVndservicemanagerEnabled patch) against the real libraries, and the working tree.

The patterns are raw instruction bytes. If a library changes (new mica release, new LineageOS
stage) a pattern may vanish or appear twice; apply_byte_patches refuses that, but only when it runs.
This test runs the function itself on the real inputs into a scratch tree and checks each pattern
and the output, then that the working tree carries exactly these bytes (the system libbinder and
the vendor-flavour gbos-vendor-override copies the codec2 fix depends on)."""
import os

import pytest

from conftest import GERCEK_WORK, gerekli, oku


@pytest.fixture
def yamali(ap_asamalar, monkeypatch, gecici):
    """Runs the function with SYS_DIR in a scratch dir; records every apply_byte_patches call
    (the real function still does the work)."""
    ap = ap_asamalar
    gerekli(os.path.join(GERCEK_WORK, "sys"))
    sys_dir = os.path.join(gecici, "sys")
    for d in ("system/lib", "system/lib64"):
        os.makedirs(os.path.join(sys_dir, d))
    monkeypatch.setattr(ap, "SYS_DIR", sys_dir)
    kayit = []
    gercek = ap.apply_byte_patches

    def izle(data, patches, what):
        out = gercek(data, patches, what)
        kayit.append((what, data, list(patches), out))
        return out

    monkeypatch.setattr(ap, "apply_byte_patches", izle)
    ap.patch_binder_and_hidlbase_threadpool()
    yazilan = {}
    for dp, _dn, fn in os.walk(sys_dir):
        for n in fn:
            p = os.path.join(dp, n)
            yazilan[os.path.relpath(p, sys_dir)] = oku(p, "rb")
    return ap, kayit, yazilan


def test_kaliplar_bir_kez_ve_yamali(yamali):
    """In every source library each original pattern occurs exactly once; in the output it is
    gone, the patched pattern is there one more time than in the source, the length is unchanged
    and nothing but the patched bytes differs."""
    _ap, kayit, _ = yamali
    assert kayit, "no patch applied"
    for what, data, patches, out in kayit:
        assert len(out) == len(data), what
        changed = 0
        for old, new in patches:
            assert len(old) == len(new), what
            assert data.count(old) == 1, f"{what}: {old.hex()} occurs {data.count(old)} times"
            assert out.count(old) == 0, f"{what}: {old.hex()} still present"
            assert out.count(new) == data.count(new) + 1, f"{what}: {new.hex()} not written"
            changed += sum(a != b for a, b in zip(old, new))
        assert sum(a != b for a, b in zip(data, out)) == changed, f"{what}: other bytes changed"


def test_calisan_agac_yamali_ciktiyla_ayni(yamali):
    """Each library the function writes is byte-identical in the working tree, among them
    system/lib/libbinder.so and system/lib/gbos-vendor-override/*; the override directory holds
    nothing else (a stale extra library there would be loaded first by every vendor process)."""
    ap, kayit, yazilan = yamali
    assert len(yazilan) == len(kayit)
    override = ap.VENDOR_BINDER_OVERRIDE
    assert "system/lib/libbinder.so" in yazilan
    assert {f"{override}/libbinder.so", f"{override}/libhidlbase.so"} <= set(yazilan)
    for rel, data in sorted(yazilan.items()):
        calisan = os.path.join(GERCEK_WORK, "sys", rel)
        gerekli(calisan)
        assert oku(calisan, "rb") == data, f"working tree {rel} differs from the patched output"
    calisan_override = os.path.join(GERCEK_WORK, "sys", override)
    assert sorted(os.listdir(calisan_override)) == sorted(
        os.path.basename(r) for r in yazilan if r.startswith(override + "/"))
