"""mica-indir.py's Extractor routes the inflated disk stream into partition files. The GPT and LP
parsers are replaced by a fixed layout so the routing itself is exercised with real bytes.

24 Oct 2026: in 16560795 (.276) system_a begins right after the super metadata, inside the chunk
that completes the metadata; the Extractor refused it ("starts before its metadata was parsed")
although those bytes were still buffered."""
import importlib.util
import os

import pytest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("mica_indir", os.path.join(TOOLS, "mica-indir.py"))
mica_indir = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mica_indir)

SUPER = 4096          # super partition start in the disk image
META = 2048           # bytes of super needed to parse its metadata
DISK = 64 * 1024


def disk():
    return bytes((i * 7 + 3) % 251 for i in range(DISK))


@pytest.fixture
def layout(monkeypatch):
    md = mica_indir.mica_disk

    def plan(extents):
        monkeypatch.setattr(md, "gpt_bytes_needed", lambda head: 1024)
        monkeypatch.setattr(md, "parse_gpt", lambda head: {"super": (SUPER, DISK)})
        monkeypatch.setattr(md, "lp_bytes_needed", lambda head: META)
        monkeypatch.setattr(md, "parse_lp", lambda head, s0: extents)
    return plan


def run(tmp_path, extents, chunk, layout):
    layout(extents)
    data = disk()
    ex = mica_indir.Extractor(str(tmp_path), list(extents), False)
    try:
        for i in range(0, len(data), chunk):
            ex.feed(data[i:i + chunk])
    finally:
        ex.close()
    return data, {n: open(os.path.join(tmp_path, n + ".erofs.part"), "rb").read() for n in extents}


@pytest.mark.parametrize("chunk", [256, 3000, 8192, DISK])
def test_partition_right_after_metadata(tmp_path, layout, chunk):
    # system_a starts at the first byte after the metadata and spans two extents with a hole.
    extents = {
        "system_a": [(SUPER + META, SUPER + META + 5000, "linear"),
                     (0, 3000, "zero"),
                     (SUPER + 20000, SUPER + 26000, "linear")],
        "product_a": [(SUPER + 30000, SUPER + 40000, "linear")],
    }
    data, out = run(tmp_path, extents, chunk, layout)
    assert out["system_a"] == data[SUPER + META:SUPER + META + 5000] + bytes(3000) + \
        data[SUPER + 20000:SUPER + 26000]
    assert out["product_a"] == data[SUPER + 30000:SUPER + 40000]


def test_partition_inside_super_head_before_metadata_end(tmp_path, layout):
    # An extent that begins inside the metadata area is only possible with a broken table, but
    # the bytes are buffered, so it is copied rather than lost.
    extents = {"system_a": [(SUPER + 100, SUPER + 900, "linear")]}
    data, out = run(tmp_path, extents, 4096, layout)
    assert out["system_a"] == data[SUPER + 100:SUPER + 900]


def test_partition_before_super_is_rejected(tmp_path, layout):
    extents = {"system_a": [(SUPER - 10, SUPER + 100, "linear")]}
    with pytest.raises(ValueError, match="before the super partition"):
        run(tmp_path, extents, 8192, layout)
