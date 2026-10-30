"""yamalar/guncelleyici.py against the real pipeline inputs:

- the package names it reads from binary manifests equal aapt2's, for every APK
  apply_patches.REMOVED_APPS removes (pristine mica images)
- install() lays out every file with its mode, yuvalar.conf equals tablete-yaz.py's slots and
  header, kaldirilan-paketler names the removed apps (Dell SupportAssist among them), the
  file_contexts block is added once, and a second run gives the same tree

    python3 -I -m pytest guncelleyici/testler -q
"""
import hashlib
import os
import shutil
import stat
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))
AAPT2 = os.path.join(os.environ.get("ANDROID_BUILD_TOOLS") or os.path.join(
    os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK") or os.path.expanduser("~/Android/Sdk"),
    "build-tools", "37.0.0"), "aapt2")
WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")


@pytest.fixture(scope="module")
def ap():
    sys.path.insert(0, TOOLS)
    import apply_patches
    if not os.path.exists(os.path.join(apply_patches.MICA_IMAGES, "system_a.erofs")):
        pytest.skip("mica-imaj/guncel yok")
    return apply_patches


@pytest.fixture(scope="module")
def mod():
    sys.path.insert(0, TOOLS)
    from yamalar import guncelleyici
    return guncelleyici


def test_paket_adlari_aapt2_ile_ayni(ap, mod, tmp_path):
    seen = 0
    for key, paths in ap.REMOVED_APPS.items():
        image = mod.IMAGES[key]
        for rel in paths:
            if rel.endswith(".xml"):
                continue
            apks = [rel] if rel.endswith(".apk") else \
                [f"{rel}/{n}" for n in mod.pristine_ls(rel, image) if n.endswith(".apk")]
            for apk in apks:
                data = ap.pristine_file(apk, image)
                path = tmp_path / os.path.basename(apk)
                path.write_bytes(data)
                want = subprocess.run([AAPT2, "dump", "packagename", str(path)], check=True,
                                      capture_output=True, text=True).stdout.strip()
                assert mod.apk_package(data) == want, apk
                seen += 1
    assert seen >= 7


def tree_digest(root):
    h = hashlib.sha256()
    for d, dirs, files in sorted(os.walk(root)):
        dirs.sort()
        for n in sorted(files):
            p = os.path.join(d, n)
            h.update(os.path.relpath(p, root).encode())
            h.update(oct(stat.S_IMODE(os.stat(p).st_mode)).encode())
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def test_kurulum_ve_yineleme(ap, mod, tmp_path):
    fc = os.path.join(WORK, "sys", "system", "etc", "selinux", "plat_file_contexts")
    if not os.path.exists(fc):
        pytest.skip(f"{fc} yok")
    if not os.path.exists(os.path.join(mod.GUNCELLEYICI, "out", "arm64", "system")):
        pytest.skip("guncelleyici/build.sh çalıştırılmamış")
    sys_dir = tmp_path / "sys"
    (sys_dir / "system/etc/selinux").mkdir(parents=True)
    shutil.copyfile(fc, sys_dir / "system/etc/selinux/plat_file_contexts")
    shutil.copyfile(os.path.join(os.path.dirname(fc), "plat_property_contexts"),
                    sys_dir / "system/etc/selinux/plat_property_contexts")
    mod.install(str(sys_dir))
    first = tree_digest(sys_dir)
    mod.install(str(sys_dir))
    assert tree_digest(sys_dir) == first

    system = sys_dir / "system"
    for rel in mod.BUILT + list(mod.SOURCES):
        assert (system / rel).is_file(), rel
    assert stat.S_IMODE((system / "bin/gbos-guncelleyici-is").stat().st_mode) == 0o755
    assert stat.S_IMODE((system / "bin/gbos-guncelleyicid").stat().st_mode) == 0o755
    assert stat.S_IMODE((system / "etc/init/gbos-guncelleyici.rc").stat().st_mode) == 0o644

    import importlib.util
    spec = importlib.util.spec_from_file_location("ty", os.path.join(TOOLS, "tablete-yaz.py"))
    ty = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ty)
    conf = (system / "etc/gbos-guncelleyici/yuvalar.conf").read_text().splitlines()
    assert f"super_baslik_sha256 {ty.GBOS_HEADER_SHA256}" in conf
    for name, (first_mib, size) in ty.SLOTS.items():
        assert f"bolum {name} {first_mib} {size}" in conf
    # an install may start from googlebookos-1 (where it is recorded): every slot of it lies
    # inside googlebookos-2's
    if "googlebookos-1" in ty.BASLIK.SHA:
        assert f"onceki_baslik {ty.BASLIK.SHA['googlebookos-1']}" in conf
    else:
        assert not [l for l in conf if l.startswith("onceki_baslik")]
    header = (system / "etc/gbos-guncelleyici/super-baslik.bin").read_bytes()
    assert hashlib.sha256(header).hexdigest() == ty.GBOS_HEADER_SHA256

    text = (system / "etc/selinux/plat_file_contexts").read_text()
    assert text.count("# --- GBOS_GUNCELLEYICI BEGIN") == 1
    assert "/system/bin/gbos-guncelleyici-is    u:object_r:gbos_guncelleyici_exec:s0" in text
    props = (system / "etc/selinux/plat_property_contexts").read_text()
    assert props.count("# --- GBOS_GUNCELLEYICI BEGIN") == 1
    assert "persist.vold.dont_commit_checkpoint u:object_r:gbos_vold_commit_prop:s0" in props


def test_kaldirilan_paketler_agac_farkindan(ap, mod, tmp_path):
    """kaldirilan-paketler is every package of the pristine images' app directories that the
    finished trees lack: the work tree's, against the pristine mica images."""
    trees = {k: os.path.join(WORK, d) for k, d in (("sys", "sys"), ("sysext", "sysext"), ("prod", "prod"))}
    for t in trees.values():
        if not os.path.isdir(t):
            pytest.skip(f"{t} yok")
    if not os.path.isdir(mod.MICA_IMAGES):
        pytest.skip(f"{mod.MICA_IMAGES} yok")
    sys_dir = tmp_path / "sys"
    shutil.copytree(os.path.join(trees["sys"], "system", "app"), sys_dir / "system" / "app", symlinks=True)
    shutil.copytree(os.path.join(trees["sys"], "system", "priv-app"), sys_dir / "system" / "priv-app",
                    symlinks=True)
    mod.install_removed(str(sys_dir), trees["sysext"], trees["prod"], ap.pristine_file, ap.REMOVED_APPS)
    pk = (sys_dir / "system/etc/gbos-guncelleyici/kaldirilan-paketler").read_text().splitlines()
    pk = [p for p in pk if not p.startswith("#")]
    assert "com.dell.supportassist.alos" in pk
    assert pk == sorted(pk)
    still = mod.tree_packages({"sys": str(sys_dir), "sysext": trees["sysext"], "prod": trees["prod"]})
    assert not set(pk) & still
