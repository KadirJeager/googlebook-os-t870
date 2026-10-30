"""f) tablete-yaz.py writes images into the tablet's super partition by offset (SLOTS) after
comparing the super header with the known Googlebook OS headers (super-baslik/uret.py). The slot
table HEADER_SLOTS is read from each header, and the Googlebook OS header itself is made from the
tablet's LineageOS header (uret.relocate); a wrong offset or size would overwrite another logical
partition (LineageOS's half of super is the way back). The LP metadata is parsed
with mica_disk.py, the pipeline's own parser, and checked with AOSP lpdump when it is installed."""
import os
import re
import shutil
import subprocess

import pytest

from conftest import ARACLAR, GERCEK_WORK, MIB, arac_gerekli, arac_modulu, gerekli

SURUM_DUSUR = os.path.join(GERCEK_WORK, "denetim", "surum-dusur")
BILINEN_IYI = os.path.join(GERCEK_WORK, "denetim", "bilinen-iyi-272-0130")


@pytest.fixture(scope="module")
def ty():
    gerekli(os.path.join(ARACLAR, "tablete-yaz.py"))
    return arac_modulu("tablete-yaz.py", "tablete_yaz")


def _baslik_gerekli(ty, name):
    """googlebookos-1 exists only where it was installed (the author's tablet, SHA256SUMS); a
    checkout that recorded another tablet knows googlebookos-2 alone."""
    if name not in ty.HEADER_SLOTS:
        pytest.skip(f"atlandı: {name} bu checkout'ta kayıtlı değil (super-baslik/SHA256SUMS)")


def _lp(ty, name):
    import mica_disk
    return mica_disk.parse_lp(ty.BASLIK.header(name), 0)


@pytest.mark.parametrize("header", ["googlebookos-1", "googlebookos-2"])
def test_yuvalar_lp_ile_ayni(ty, header):
    """Each slot is the single linear extent of the same-named partition in that header's LP
    metadata (first MiB and size in MiB)."""
    _baslik_gerekli(ty, header)
    lp = _lp(ty, header)
    for name, (first, size) in ty.HEADER_SLOTS[header].items():
        assert name in lp, f"{name} not in the LP metadata ({sorted(lp)})"
        assert lp[name] == [(first * MIB, (first + size) * MIB, "linear")], (name, lp[name])


def test_basliklar_ozetleriyle_ve_lpdump_ile(ty, tmp_path):
    """Every stored header is the first MiB of super with its recorded hash (the rebuilt
    googlebookos-2 too), and AOSP's own reader accepts googlebookos-2's checksums."""
    import hashlib
    for name in ty.BASLIK.SHA:
        data = ty.BASLIK.header(name)
        assert len(data) == MIB and hashlib.sha256(data).hexdigest() == ty.BASLIK.SHA[name]
    assert ty.GBOS_HEADER_SHA256 == ty.BASLIK.SHA[ty.HEADER]
    if not shutil.which("lpdump"):
        pytest.skip("atlandı: lpdump yok")
    path = tmp_path / "googlebookos-2.bin"
    path.write_bytes(ty.BASLIK.header("googlebookos-2"))
    out = subprocess.run(["lpdump", "-a", str(path)], capture_output=True, text=True, check=True).stdout
    assert "super: 12740608 .. 19865600: product (7124992 sectors)" in out


def test_yeni_baslik_yalniz_product_buyutur(ty):
    """googlebookos-2 differs from googlebookos-1 only in product, which ends at the end of super
    (unallocated before); every other partition keeps its extent."""
    _baslik_gerekli(ty, "googlebookos-1")
    old, new = _lp(ty, "googlebookos-1"), _lp(ty, "googlebookos-2")
    assert set(old) == set(new)
    for name in old:
        if name != "product":
            assert old[name] == new[name], name
    (s0, e0, _), = old["product"]
    (s1, e1, _), = new["product"]
    assert s0 == s1 and e1 > e0 and e1 == 9700 * MIB


def test_eski_yuvalar_yenilerin_icinde(ty):
    """An image made for an older header mounts under the newer one: same start, not larger."""
    _baslik_gerekli(ty, "googlebookos-1")
    old, new = ty.HEADER_SLOTS["googlebookos-1"], ty.HEADER_SLOTS["googlebookos-2"]
    for name, (first, size) in old.items():
        assert new[name][0] == first and new[name][1] >= size, name


@pytest.mark.parametrize("header", ["googlebookos-1", "googlebookos-2"])
def test_lineage_yarisina_dokunmaz(ty, header):
    """The slots tablete-yaz.py writes overlap no extent of the LineageOS header's partitions,
    and vendor/odm (shared by both systems) sit at the same place in both headers."""
    _baslik_gerekli(ty, header)
    gbos, lineage = _lp(ty, header), _lp(ty, "lineage")
    for name, (first, size) in ty.HEADER_SLOTS[header].items():
        a, b = first * MIB, (first + size) * MIB
        for other, extents in lineage.items():
            for s, e, kind in extents:
                if kind == "linear":
                    assert b <= s or e <= a, f"{name} slot overlaps LineageOS {other}"
    for shared in ("vendor", "odm"):
        assert gbos[shared] == lineage[shared], shared


def test_ext4_blok_sayilari_yuvaya_esit(ty, monkeypatch):
    """make_ext4_images.py sizes every image to its slot for the header the images are made for
    (GBOS_SUPER_BASLIK picks a bridge header)."""
    m = arac_modulu("make_ext4_images.py", "make_ext4_images")  # builds nothing when imported
    for header, slots in ty.HEADER_SLOTS.items():
        monkeypatch.setenv("GBOS_SUPER_BASLIK", header)
        for name, (_first, size) in slots.items():
            assert m.slot_blocks(name) * 4096 == size * MIB, (header, name)


def _imajlar():
    out = []
    if os.path.isdir(SURUM_DUSUR):
        out += [os.path.join(SURUM_DUSUR, n) for n in sorted(os.listdir(SURUM_DUSUR))
                if n.endswith(".img")]
    if os.path.isdir(BILINEN_IYI):
        out += [os.path.join(BILINEN_IYI, n) for n in sorted(os.listdir(BILINEN_IYI))
                if n.endswith(".img.zst")]
    return out or [None]


@pytest.mark.parametrize("imaj", _imajlar(), ids=lambda p: os.path.relpath(p, GERCEK_WORK) if p else "yok")
def test_hazir_imaj_boyutu_yuvaya_esit(ty, imaj):
    """The prepared image sets (denetim/surum-dusur, and the 01:30 known-good rollback set by its
    zstd frame content size) are exactly slot-sized; read only (stat / zstd -l)."""
    if imaj is None:
        pytest.skip(f"atlandı: gerçek girdi yok: {SURUM_DUSUR} ve {BILINEN_IYI}")
    name = os.path.basename(imaj).split(".")[0]
    assert name in ty.SLOTS, f"{imaj}: no slot named {name}"
    # these sets were made for googlebookos-1
    _baslik_gerekli(ty, "googlebookos-1")
    want = ty.HEADER_SLOTS["googlebookos-1"][name][1] * MIB
    if imaj.endswith(".zst"):
        arac_gerekli("zstd")
        out = subprocess.run(["zstd", "-lv", imaj], capture_output=True, text=True, check=True).stdout
        m = re.search(r"Decompressed Size:.*\((\d+) B\)", out)
        if not m:
            pytest.skip(f"atlandı: {imaj} zstd çerçevesi açılmış boyutu taşımıyor")
        size = int(m.group(1))
    else:
        size = os.path.getsize(imaj)
    assert size == want, f"{imaj}: {size} bytes, slot {want}"


def _uzat(baslik, head, name, mib):
    """`head` with partition `name`'s single extent `mib` MiB longer (slot 0, both copies,
    checksums recomputed): a LineageOS build whose partition is that much larger."""
    import hashlib
    import struct
    out = bytearray(head)
    for at in baslik.copies(head, 0):
        header_size, tables_size = baslik._tables(out[at:])
        t = at + header_size
        poff, pnum, psize = struct.unpack_from("<III", out, at + 80)
        eoff, _n, esize = struct.unpack_from("<III", out, at + 92)
        for i in range(pnum):
            p = t + poff + i * psize
            if bytes(out[p:p + 36]).split(b"\0")[0].decode() == name:
                first = struct.unpack_from("<I", out, p + 40)[0]
                e = t + eoff + first * esize
                struct.pack_into("<Q", out, e, struct.unpack_from("<Q", out, e)[0] + mib * 2048)
        out[at + 48:at + 80] = hashlib.sha256(bytes(out[t:t + tables_size])).digest()
        out[at + 12:at + 44] = bytes(32)
        out[at + 12:at + 44] = hashlib.sha256(bytes(out[at:at + header_size])).digest()
    return bytes(out)


def test_googlebook_basligi_lineage_basligindan(ty):
    """relocate() on the tablet's LineageOS header gives the running googlebookos-2 byte for byte
    (the header written on 25 Oct 2026, f64551d7...), the same as growing googlebookos-1."""
    import hashlib
    data = ty.BASLIK.relocate(ty.BASLIK.stored("lineage"))
    assert hashlib.sha256(data).hexdigest() == ty.BASLIK.SHA["googlebookos-2"]


def test_baska_lineage_yerlesimi(ty, tmp_path):
    """Another LineageOS build (system_ext 200 MiB larger, the last LineageOS extent): the
    Googlebook OS slots start after it, MiB aligned, product shrinks by the same amount and
    ends at the end of super, no LineageOS extent is overlapped, vendor/odm stay, and AOSP's
    reader accepts the result."""
    import mica_disk
    b = ty.BASLIK
    lineage = _uzat(b, b.stored("lineage"), "system_ext", 200)
    gbos = b.relocate(lineage)
    slots, ref = b.slots(gbos), ty.HEADER_SLOTS["googlebookos-2"]
    assert slots["system"] == (ref["system"][0] + 200, 1432)
    assert slots["system_ext"] == (ref["system_ext"][0] + 200, 348)
    assert slots["product"] == (ref["product"][0] + 200, ref["product"][1] - 200)
    lin, new = mica_disk.parse_lp(lineage, 0), mica_disk.parse_lp(gbos, 0)
    for name, (first, size) in slots.items():
        for other, extents in lin.items():
            for s, e, kind in extents:
                assert first * MIB + size * MIB <= s or e <= first * MIB, (name, other)
    assert new["vendor"] == lin["vendor"] and new["odm"] == lin["odm"]
    if shutil.which("lpdump"):
        path = tmp_path / "g.bin"
        path.write_bytes(gbos)
        subprocess.run(["lpdump", str(path)], check=True, capture_output=True)


def test_googlebook_basligi_lineage_diye_kaydedilmez(ty):
    """A header that already is a Googlebook OS one (no room left after its last extent) or
    that has other partitions is refused: yakala run on the wrong tablet state writes nothing."""
    b = ty.BASLIK
    with pytest.raises(ValueError):
        b.relocate(b.header("googlebookos-2"))
    with pytest.raises(ValueError):
        b.relocate(_uzat(b, b.stored("lineage"), "system_ext", 5000))  # no room for the slots


def test_kayit_baska_tablet(ty, tmp_path, monkeypatch):
    """record() for another tablet's header: lineage.bin.xz is that header byte for byte,
    SHA256SUMS lists it and the Googlebook OS header made from it, and drops googlebookos-1
    (made from the author's LineageOS header); header() then checks against the new sums."""
    import hashlib
    import lzma
    b = ty.BASLIK
    for n in ("lineage.bin.xz", "SHA256SUMS"):
        shutil.copy(os.path.join(b.HERE, n), tmp_path / n)
    monkeypatch.setattr(b, "HERE", str(tmp_path))
    monkeypatch.setattr(b, "SUMS", str(tmp_path / "SHA256SUMS"))
    head = _uzat(b, b.stored("lineage"), "system_ext", 64)
    sums, slots = b.record(head)
    assert lzma.decompress((tmp_path / "lineage.bin.xz").read_bytes()) == head
    assert b.read_sums(str(tmp_path / "SHA256SUMS")) == sums
    assert set(sums) == {"lineage", "googlebookos-2"}
    assert sums["googlebookos-2"] == hashlib.sha256(b.relocate(head)).hexdigest()
    assert b.header("googlebookos-2", sums) == b.relocate(head)
