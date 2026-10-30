"""libcapture_meta.so / libprovide_meta.so with the real erofs tools (no stand-ins):

- a tree with more entries than the old fixed table held (512) and files carrying two xattrs
  (SELinux label + file capabilities, like crosvm/virtmgr/virtualizationservice in the virt APEX)
  is packed by mkfs.erofs with libprovide_meta.so, extracted again by fsck.erofs with
  libcapture_meta.so, and every owner, mode and xattr must come back unchanged
- a file in the tree without captured metadata, a malformed row and an unreadable META_LOG stop
  mkfs.erofs (exit 70) instead of producing an image with the build user's uid or no labels
- the pristine virt and tethering APEX payloads of the current mica release survive
  extract -> pack -> extract with every xattr (skipped when the release is not on this computer)

    python3 -I -m pytest guncelleyici/testler -q
"""
import io
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GUNCELLEYICI = os.path.dirname(HERE)
TOOLS = os.path.dirname(GUNCELLEYICI)
EROFS_BIN = os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin"))
MKFS, FSCK, DUMP = (os.path.join(EROFS_BIN, t) for t in ("mkfs.erofs", "fsck.erofs", "dump.erofs"))
MICA = os.path.join(TOOLS, "mica-imaj", "guncel")
SELINUX = "security.selinux"
CAPABILITY = "security.capability"


def need(path):
    if not os.path.exists(path):
        pytest.skip(f"{path} yok")


@pytest.fixture(scope="module")
def libs(tmp_path_factory):
    need(MKFS)
    sys.path.insert(0, os.path.join(GUNCELLEYICI, "yerel"))
    import derle
    out = str(tmp_path_factory.mktemp("lib"))
    for lib in derle.LIBS:
        derle.build(lib, out, "cc")
    return out


def read_meta(path):
    """{relative path: {"owner": (uid, gid), "mode": perm bits, "xattrs": {name: bytes}}}"""
    out = {}
    with open(path) as f:
        kind, root = f.readline().rstrip("\n").split("\t")
        assert kind == "ROOT"
        for line in f:
            row = line.rstrip("\n").split("\t")
            rel = os.path.relpath(row[1], root)
            e = out.setdefault(rel, {"owner": None, "mode": None, "xattrs": {}})
            if row[0] == "CHOWN":
                e["owner"] = (int(row[2]), int(row[3]))
            elif row[0] == "CHMOD":
                e["mode"] = int(row[2], 8) & 0o7777
            elif row[0] == "XATTR":
                e["xattrs"][row[2]] = bytes.fromhex(row[3])
    return out


def capture(libs, image, dest, meta):
    """apply_patches._extract_with_meta, the way the pipeline extracts a pristine image."""
    with open(meta, "w") as f:
        f.write(f"ROOT\t{dest}\n")
    env = dict(os.environ, LD_PRELOAD=os.path.join(libs, "libcapture_meta.so"), META_LOG=meta)
    subprocess.run([FSCK, f"--extract={dest}", "--preserve", "--xattrs", image], env=env,
                   check=True, stdout=subprocess.DEVNULL)


def pack(libs, tree, meta, image, old_root=None, check=True):
    """apply_patches.patch_tethering_apex's mkfs.erofs call."""
    env = dict(os.environ, LD_PRELOAD=os.path.join(libs, "libprovide_meta.so"), META_LOG=meta)
    if old_root:
        env.update(META_OLD_PREFIX=old_root, META_NEW_PREFIX=tree)
    return subprocess.run([MKFS, "-zlz4hc", "-b", "4096", "-T", "0", image, tree], env=env,
                          check=check, capture_output=True, text=True)


def make_tree(root, meta, files=700):
    """A tree of `files` files in nested directories plus a symlink; returns the metadata written
    to `meta` for it (every row type, two xattrs on some files)."""
    rows = [f"ROOT\t{root}"]
    want = {}

    def add(rel, owner, mode, xattrs):
        path = root if rel == "." else os.path.join(root, rel)
        rows.append(f"CHOWN\t{path}\t{owner[0]}\t{owner[1]}")
        if mode is not None:
            rows.append(f"CHMOD\t{path}\t{mode:o}")
        for name, value in xattrs.items():
            rows.append(f"XATTR\t{path}\t{name}\t{value.hex()}")
        want[rel] = {"owner": owner, "mode": mode, "xattrs": xattrs}

    os.makedirs(root)
    add(".", (0, 0), 0o755, {SELINUX: b"u:object_r:system_file:s0\0"})
    for d in range(7):
        rel = f"d{d}"
        os.makedirs(os.path.join(root, rel))
        add(rel, (0, 2000), 0o751, {SELINUX: b"u:object_r:system_lib_file:s0\0"})
        for i in range(files // 7):
            frel = f"{rel}/f{i}"
            with open(os.path.join(root, frel), "wb") as f:
                f.write(f"{frel}\n".encode() * (i % 5 + 1))
            xattrs = {SELINUX: f"u:object_r:label_{d}_{i % 3}:s0\0".encode()}
            if i % 10 == 0:
                # VFS_CAP_REVISION_2, CAP_NET_ADMIN | CAP_SYS_ADMIN: the shape crosvm's has
                xattrs[CAPABILITY] = bytes.fromhex("0000000200100000000000000000000000000000")
            add(frel, (1000 + d, 2000 + i % 3), 0o644 if i % 2 else 0o755, xattrs)
    os.symlink("d0/f0", os.path.join(root, "baglanti"))
    add("baglanti", (0, 0), None, {SELINUX: b"u:object_r:system_file:s0\0"})
    with open(meta, "w") as f:
        f.write("\n".join(rows) + "\n")
    return want


def test_cok_xattr_ve_buyuk_agac_geri_gelir(libs, tmp_path):
    tree, meta, img = str(tmp_path / "agac"), str(tmp_path / "agac.meta"), str(tmp_path / "a.img")
    want = make_tree(tree, meta)
    assert len(want) > 512
    pack(libs, tree, meta, img)
    got = read_meta_after(libs, img, tmp_path)
    assert set(got) == set(want)
    two = 0
    for rel, w in want.items():
        g = got[rel]
        assert g["owner"] == w["owner"], rel
        if w["mode"] is not None:
            assert g["mode"] == w["mode"], rel
        assert g["xattrs"] == w["xattrs"], rel
        two += len(w["xattrs"]) == 2
    assert two >= 70


def read_meta_after(libs, img, tmp_path):
    out, meta = str(tmp_path / "geri"), str(tmp_path / "geri.meta")
    capture(libs, img, out, meta)
    return read_meta(meta)


def test_onek_tasinir(libs, tmp_path):
    """META_OLD_PREFIX/META_NEW_PREFIX: metadata captured under one root applies to a copy."""
    orig, meta, img = str(tmp_path / "ilk"), str(tmp_path / "ilk.meta"), str(tmp_path / "b.img")
    want = make_tree(orig, meta, files=21)
    copy = str(tmp_path / "kopya")
    shutil.copytree(orig, copy, symlinks=True)
    pack(libs, copy, meta, img, old_root=orig)
    got = read_meta_after(libs, img, tmp_path)
    assert {k: v["xattrs"] for k, v in got.items()} == {k: v["xattrs"] for k, v in want.items()}


def test_metasiz_dosya_durdurur(libs, tmp_path):
    tree, meta, img = str(tmp_path / "agac"), str(tmp_path / "agac.meta"), str(tmp_path / "c.img")
    make_tree(tree, meta, files=14)
    with open(os.path.join(tree, "d3", "yeni"), "w") as f:
        f.write("kaydı yok\n")
    res = pack(libs, tree, meta, img, old_root=tree, check=False)
    assert res.returncode == 70, res.stderr
    assert "d3/yeni" in res.stderr


@pytest.mark.parametrize("satir", ["CHOWN\t/x\tbir\t0", "XATTR\t/x\tsecurity.selinux\t7g",
                                   "SIZE\t/x\t1"])
def test_bozuk_satir_durdurur(libs, tmp_path, satir):
    tree, meta, img = str(tmp_path / "agac"), str(tmp_path / "agac.meta"), str(tmp_path / "d.img")
    make_tree(tree, meta, files=7)
    with open(meta, "a") as f:
        f.write(satir + "\n")
    res = pack(libs, tree, meta, img, check=False)
    assert res.returncode == 70, res.stderr


def test_okunamayan_kayit_durdurur(libs, tmp_path):
    tree = str(tmp_path / "agac")
    make_tree(tree, str(tmp_path / "agac.meta"), files=7)
    res = pack(libs, tree, str(tmp_path / "yok.meta"), str(tmp_path / "e.img"), check=False)
    assert res.returncode == 70


def apex_payload(rel):
    """The EROFS payload of a pristine APEX of the current mica release."""
    need(os.path.join(MICA, "system_a.erofs"))
    apex = subprocess.run([DUMP, "--cat", f"--path=/{rel}", os.path.join(MICA, "system_a.erofs")],
                          check=True, stdout=subprocess.PIPE).stdout
    with zipfile.ZipFile(io.BytesIO(apex)) as z:
        payload = z.read("apex_payload.img")
    import struct
    image_size = struct.unpack(">4sIIQQQ", payload[-64:-28])[3]
    return payload[:image_size]


@pytest.mark.parametrize("apex", ["system/apex/com.google.android.virt.apex",
                                  "system/apex/com.google.android.tethering.apex"])
def test_gercek_apex_yukleri(libs, tmp_path, apex):
    """virt carries files with a label and capabilities: both must survive the round trip."""
    src = str(tmp_path / "kaynak.img")
    with open(src, "wb") as f:
        f.write(apex_payload(apex))
    first, first_meta = str(tmp_path / "ilk"), str(tmp_path / "ilk.meta")
    capture(libs, src, first, first_meta)
    want = read_meta(first_meta)
    work = str(tmp_path / "is")
    shutil.copytree(first, work, symlinks=True)
    os.chmod(work, 0o755)
    img = str(tmp_path / "yeni.img")
    pack(libs, work, first_meta, img, old_root=first)
    got = read_meta_after(libs, img, tmp_path)
    assert got == want
    if "virt" in apex:
        caps = [rel for rel, e in want.items() if CAPABILITY in e["xattrs"]]
        assert any(rel.endswith("crosvm") for rel in caps), caps
        assert all(SELINUX in want[rel]["xattrs"] for rel in caps)
