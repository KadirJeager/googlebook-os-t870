"""The kit in the image and the preparations it drives, with real processes and files:

- yamalar/kit_imaj.py: the archive is reproducible, holds kit/, the carried inputs, ev/ without the
  app signing key or caches, KIT, and the links as links; the recovery's own toybox (zcat | tar,
  what gbos-guncelleyici-is unpack_kit runs) unpacks it to the same files
- a bridge image carries no kit
- motor.py: which super header the images are made for, when the running release is prepared
  again for the kit, hazir.conf's new fields, the set files taken from the tree, and the tablet's
  own app key (real keytool, the parameters apply_patches.sign_apk uses)
"""
import hashlib
import importlib.util
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))
MOTOR = os.path.join(TOOLS, "guncelleyici", "motor", "motor.py")
OLDEST = "googlebookos-1"  # recorded only on the checkout whose tablet first ran Googlebook OS


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def bin_dir():
    out = subprocess.run([os.path.join(HERE, "araclar.sh")], check=True, capture_output=True,
                         text=True).stdout.strip().splitlines()[-1]
    return out


def make_kit(root):
    """A small kit in kit-uret.py uret's layout."""
    files = {
        "kit/apply_patches.py": b"print('x')\n",
        "kit/guncelleyici/motor/motor.py": b"#!/usr/bin/env python3\n",
        "kit/kernel/build.sh": b"#!/bin/sh\necho\n",
        "tasinan/lineage/lib32_stage/system_lib/lib/libc.so": b"\x7fELF" + bytes(100),
        "tasinan/lineage/vendor_stage/a": b"a",
        "ev/Android/Sdk/build-tools/37.0.0/d8": b"#!/bin/sh\n",
        "ev/.android-certs/googlebookos-t870/uygulama.p12": b"GIZLI",
        "ev/.cache/gbos-selinux/bin/secilc": b"cache",
        "ev/.local/bin/x": b"cache",
        "KIT": b"commit abc\nicerik 1234\ndosya 3\ntarih 2026-10-25T20:00:00+03:00\n",
    }
    for rel, data in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    os.chmod(os.path.join(root, "kit/kernel/build.sh"), 0o755)
    os.symlink("../mica-imaj", os.path.join(root, "kit", "mica-imaj"))
    os.symlink("libc.so", os.path.join(root, "tasinan/lineage/lib32_stage/system_lib/lib/libc2.so"))
    os.makedirs(os.path.join(root, "kit", "yamalar", "__pycache__"))
    with open(os.path.join(root, "kit", "yamalar", "__pycache__", "a.pyc"), "wb") as f:
        f.write(b"x")


ITEMS = ["libcapture_meta.so", "lineage/lib32_stage", "lineage/vendor_stage"]


@pytest.fixture
def kit_imaj(monkeypatch):
    monkeypatch.delenv("GBOS_SUPER_BASLIK", raising=False)
    sys.path.insert(0, TOOLS)
    try:
        return load("kit_imaj_t", os.path.join(TOOLS, "yamalar", "kit_imaj.py"))
    finally:
        sys.path.remove(TOOLS)


def test_arsiv_tekrarlanabilir_ve_dogru_icerik(tmp_path, kit_imaj, bin_dir):
    root = tmp_path / "kit"
    make_kit(str(root))
    a = kit_imaj.archive(str(root), [i for i in ITEMS if not i.endswith(".so")], 1761400000)
    b = kit_imaj.archive(str(root), [i for i in ITEMS if not i.endswith(".so")], 1761400000)
    assert a == b
    # unpacked the way the Android side does it: toybox 0.8.13 (the tablet's) zcat and tar
    out = tmp_path / "acilan"
    out.mkdir()
    (tmp_path / "kit.tar.gz").write_bytes(a)
    subprocess.run([os.path.join(bin_dir, "sh"), "-c",
                    f"toybox zcat {tmp_path}/kit.tar.gz | toybox tar -x -C {out}"],
                   check=True, env={"PATH": bin_dir})
    names = sorted(os.path.relpath(os.path.join(d, n), out)
                   for d, dirs, files in os.walk(out) for n in files + dirs)
    assert "KIT" in names and "kit/apply_patches.py" in names
    assert "ev/Android/Sdk/build-tools/37.0.0/d8" in names
    assert not any(n.startswith(("ev/.android-certs", "ev/.cache", "ev/.local")) for n in names)
    assert "kit/mica-imaj" not in names
    assert not any("__pycache__" in n for n in names)
    assert os.readlink(out / "tasinan/lineage/lib32_stage/system_lib/lib/libc2.so") == "libc.so"
    assert os.access(out / "kit/kernel/build.sh", os.X_OK)
    for rel in ("kit/apply_patches.py", "tasinan/lineage/vendor_stage/a", "KIT"):
        assert (out / rel).read_bytes() == (root / rel).read_bytes()


def test_install_ve_kopru(tmp_path, kit_imaj, monkeypatch):
    root = tmp_path / "kit"
    make_kit(str(root))
    prod = tmp_path / "prod"
    kit_imaj.install(str(prod), ITEMS, root=str(root))
    assert (prod / "etc/gbos-kit/KIT").read_bytes() == (root / "KIT").read_bytes()
    assert (prod / "etc/gbos-kit/kit.tar.gz").stat().st_size > 0
    if OLDEST not in load("sb_kopru", os.path.join(TOOLS, "super-baslik", "uret.py")).SHA:
        pytest.skip(f"{OLDEST} is not recorded in this checkout: no bridge image")
    monkeypatch.setenv("GBOS_SUPER_BASLIK", OLDEST)
    kit_imaj.install(str(prod), ITEMS, root=str(root))
    assert not (prod / "etc/gbos-kit").exists()


def test_eksik_tasinan_girdi_durdurur(tmp_path, kit_imaj):
    root = tmp_path / "kit"
    make_kit(str(root))
    with pytest.raises(SystemExit):
        kit_imaj.install(str(tmp_path / "prod"), ITEMS + ["lineage/yok"], root=str(root))


# --- motor.py ---------------------------------------------------------------------------------

@pytest.fixture
def motor(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    os.symlink(TOOLS, work / "kit")
    (work / "KIT").write_text("commit abc\nicerik 1111\n")
    monkeypatch.setenv("GBOS_GUNCELLEYICI", str(work))
    monkeypatch.setenv("GBOS_CIKTI", str(tmp_path / "gelen"))
    for v in ("GBOS_KUR_BICIM", "GBOS_IMAJ_KIT", "GBOS_BASARISIZ_SURUM", "GBOS_BASARISIZ_KIT"):
        monkeypatch.delenv(v, raising=False)
    return load("motor_kit_t", MOTOR)


def test_hedef_baslik(motor, monkeypatch):
    baslik = load("sb_hedef", os.path.join(TOOLS, "super-baslik", "uret.py"))
    if motor.OLDEST_HEADER in baslik.SHA:
        assert motor.target_header() == motor.OLDEST_HEADER  # an installer that cannot change headers
    else:  # a checkout that never had the first header installs the current one from the start
        assert motor.target_header() == baslik.CURRENT
    monkeypatch.setenv("GBOS_KUR_BICIM", "2")
    assert motor.target_header() == "googlebookos-2"


def test_kit_nedeni(motor, monkeypatch):
    assert motor.kit_reason()  # the image carries no kit (or the old installer did not say)
    monkeypatch.setenv("GBOS_IMAJ_KIT", "1111")
    assert not motor.kit_reason()
    monkeypatch.setenv("GBOS_IMAJ_KIT", "0000")
    assert motor.kit_reason()


def test_geri_alinan_surum_ve_kit(motor, monkeypatch):
    build = "16560795_CL3B.260622.276.R2"
    assert not motor.failed_before(build)
    monkeypatch.setenv("GBOS_BASARISIZ_SURUM", build)
    monkeypatch.setenv("GBOS_BASARISIZ_KIT", "1111")
    assert motor.failed_before(build)  # this release with this kit was rolled back
    assert not motor.failed_before("16600000_CL3B.260700.100.R1")  # a newer release is tried
    monkeypatch.setenv("GBOS_BASARISIZ_KIT", "2222")
    assert not motor.failed_before(build)  # so is another kit


def test_kume_dosyalari_ve_hazir_conf(motor, tmp_path, monkeypatch):
    monkeypatch.setenv("GBOS_KUR_BICIM", "2")
    baslik = load("sb", os.path.join(TOOLS, "super-baslik", "uret.py"))
    work = tmp_path / "is"
    etc = work / motor.SET_FILES_DIR
    etc.mkdir(parents=True)
    (etc / "yuvalar.conf").write_text("super_baslik_sha256 x\n")
    (etc / "kurulum.zip").write_bytes(b"PK")
    (etc / "super-baslik.bin").write_bytes(baslik.header("lineage"))
    with pytest.raises(motor.Failure):  # the tree's header is not the one the images are made for
        motor.set_files(str(work), "googlebookos-2")
    (etc / "super-baslik.bin").write_bytes(baslik.header("googlebookos-2"))
    files = motor.set_files(str(work), "googlebookos-2")
    img = tmp_path / "system.img"
    img.write_bytes(b"s")
    parts = {n: {"dosya": str(tmp_path / f"{n}.img"), "sha256": n * 2} for n in motor.PARTITIONS}
    for n in motor.PARTITIONS:
        (tmp_path / f"{n}.img").write_bytes(n.encode())
    motor.hand_over("16560795_CL3B.260622.276.R2", parts, files, "kit", "1111", "googlebookos-2")
    ready = motor.read_ready()
    assert ready["build"] == "16560795_CL3B.260622.276.R2"
    assert (ready["kit"], ready["header"]) == ("1111", "googlebookos-2")
    conf = open(motor.READY).read()
    want = hashlib.sha256(baslik.header("googlebookos-2")).hexdigest()
    assert f"dosya super-baslik.bin {want}\n" in conf and "neden kit\n" in conf
    for n in motor.SET_FILES:
        assert os.path.exists(os.path.join(motor.OUTPUT, n))


def test_tablet_anahtari(motor):
    motor.make_app_key()
    key = os.path.join(motor.KIT_HOME, motor.APP_KEY)
    assert oct(os.stat(key).st_mode & 0o777) == "0o600"
    res = subprocess.run(["keytool", "-list", "-keystore", key, "-storepass", "gbos-uygulama",
                          "-alias", "uygulama"], capture_output=True, text=True)
    assert res.returncode == 0 and "PrivateKeyEntry" in res.stdout, res.stdout + res.stderr
    before = open(key, "rb").read()
    motor.make_app_key()  # an existing key is kept
    assert open(key, "rb").read() == before
