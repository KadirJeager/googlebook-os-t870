"""yamalar/aosp_terminal.py against the pristine com.google.android.virt APEX: the rebuilt APEX
carries the compatibility package (terminal/kopru) in place of Android's terminal app, at the
same path with the same owner, mode and SELinux label, and every other file, its metadata and
every other zip entry exactly as Google ships them; the zip is consistent (CRCs) and the result
is the same on a second run. Also: apply_patches.py must not disable components of the package
once it installs the compatibility package (they would be the bridge's)."""
import ast
import hashlib
import os
import shutil
import zipfile

import pytest

from conftest import ARACLAR, EROFS_BIN, GERCEK_WORK, MICA, disk_payi, gerekli, oku

from yamalar import aosp_terminal

VIRT = aosp_terminal.VIRT_APEX


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _meta_rows(path, root):
    """The capture of a tree (CHOWN/CHMOD/XATTR rows) with its root made relative."""
    rows = set()
    with open(path) as f:
        f.readline()  # ROOT line
        for line in f:
            rows.add(line.rstrip("\n").replace(root, "<root>"))
    return rows


@pytest.fixture(scope="module")
def kur(ap, gecici_kok):
    gerekli(os.path.join(MICA, "system_a.erofs"), os.path.join(EROFS_BIN, "mkfs.erofs"),
            os.path.join(EROFS_BIN, "fsck.erofs"),
            os.path.join(GERCEK_WORK, "libcapture_meta.so"))
    try:
        aosp_terminal.bridge_apk()
    except FileNotFoundError as e:
        pytest.skip(f"atlandı: gerçek girdi yok: {e}")
    disk_payi(1)
    dst = os.path.join(ap.WORK, "libcapture_meta.so")
    if not os.path.exists(dst):
        shutil.copy2(os.path.join(GERCEK_WORK, "libcapture_meta.so"), dst)
    out = {}
    for n in (1, 2):
        sys_dir = os.path.join(gecici_kok, f"aosp_sys{n}")
        os.makedirs(os.path.join(sys_dir, os.path.dirname(VIRT)))
        aosp_terminal.install(sys_dir, ap.WORK, ap.pristine_file, ap._apex_payload_layout,
                              ap._extract_with_meta)
        out[n] = os.path.join(sys_dir, VIRT)
    for leftover in ("virt_payload.img", "virt_clean", "virt.tar", "virt.meta", "virt_new_payload.img"):
        assert not os.path.lexists(os.path.join(ap.WORK, leftover)), f"left behind: {leftover}"
    return out


def _payload_tree(ap, apex_bytes, dest, name):
    lay = ap._apex_payload_layout(apex_bytes)
    img = os.path.join(dest, name + ".img")
    with open(img, "wb") as f:
        f.write(apex_bytes[lay["data_off"]:lay["data_off"] + lay["image_size"]])
    tree, meta = os.path.join(dest, name), os.path.join(dest, name + ".meta")
    ap._extract_with_meta(img, tree, meta)
    os.remove(img)
    return tree, meta


def test_kopru_yerinde_geri_kalan_ayni(ap, kur, gecici):
    pristine = ap.pristine_file(VIRT)
    with open(kur[1], "rb") as f:
        rebuilt = f.read()
    old, old_meta = _payload_tree(ap, pristine, gecici, "old")
    new, new_meta = _payload_tree(ap, rebuilt, gecici, "new")
    target_old = aosp_terminal.terminal_apk_in(old)
    target_new = aosp_terminal.terminal_apk_in(new)
    rel = os.path.relpath(target_old, old)
    assert os.path.relpath(target_new, new) == rel, "the APK moved"
    assert aosp_terminal.package_name(target_old) == aosp_terminal.PACKAGE
    assert _sha(oku(target_new, "rb")) == _sha(oku(aosp_terminal.bridge_apk(), "rb"))

    def files(root):
        out = {}
        for d, dirs, names in os.walk(root):
            for n in dirs + names:
                p = os.path.join(d, n)
                r = os.path.relpath(p, root)
                if os.path.islink(p):
                    out[r] = "link:" + os.readlink(p)
                elif os.path.isfile(p):
                    out[r] = _sha(oku(p, "rb"))
                else:
                    out[r] = "dir"
        return out
    a, b = files(old), files(new)
    assert set(a) == set(b), f"file set differs: {sorted(set(a) ^ set(b))}"
    changed = sorted(r for r in a if a[r] != b[r])
    assert changed == [rel], changed
    # Owners, modes and SELinux labels: identical, the replaced file included.
    assert _meta_rows(old_meta, ap.meta_root(old_meta)) == _meta_rows(new_meta, ap.meta_root(new_meta))


def test_zip_butun_ve_diger_girdiler_ayni(ap, kur):
    pristine = ap.pristine_file(VIRT)
    import io
    with zipfile.ZipFile(io.BytesIO(pristine)) as zp, zipfile.ZipFile(kur[1]) as zn:
        assert zn.testzip() is None
        assert [i.filename for i in zp.infolist()] == [i.filename for i in zn.infolist()]
        for info in zp.infolist():
            if info.filename != "apex_payload.img":
                assert zp.read(info.filename) == zn.read(info.filename), info.filename
        # The hashtree, vbmeta and AVB footer after the filesystem area are untouched.
        lay = ap._apex_payload_layout(pristine)
        assert zp.read("apex_payload.img")[lay["image_size"]:] == zn.read("apex_payload.img")[lay["image_size"]:]
    assert len(oku(kur[1], "rb")) == len(pristine)


def test_ikinci_kosu_ayni(kur):
    assert _sha(oku(kur[1], "rb")) == _sha(oku(kur[2], "rb"))


def test_apply_patches_kopru_bilesenlerini_kapatmaz(ap):
    """Once apply_patches.py calls aosp_terminal.install, DISABLED_COMPONENTS must not list
    com.android.virtualization.terminal: its classes are the bridge's now."""
    tree = ast.parse(oku(os.path.join(ARACLAR, "apply_patches.py")))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "install"
             and isinstance(n.func.value, ast.Name) and "aosp_terminal" in n.func.value.id]
    if not calls:
        pytest.skip("atlandı: apply_patches.py henüz aosp_terminal.install çağırmıyor")
    assert aosp_terminal.PACKAGE not in ap.DISABLED_COMPONENTS
