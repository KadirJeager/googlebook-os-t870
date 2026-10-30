"""l) "Tablet modu" (tablet-modu/, yamalar/tablet_modu.py, kernel patch 0002): the quick settings
app, its init rule, its policy and the kernel switch all name the same sysfs node; the app is the
signed build of the current sources, installs idempotently and its tile stays hidden until the
running kernel has the switch.

Real inputs: tablet-modu/out (built by tablet-modu/build.sh), the kernel patch in
kernel/kaynak-yamalari, the SDK's apksigner and the updater's APK (same app key)."""
import filecmp
import os
import re
import subprocess
import sys
import tempfile

from conftest import ARACLAR, gerekli

sys.path.insert(0, ARACLAR)
from yamalar import guncelleyici, tablet_modu  # noqa: E402

MODUL = tablet_modu.TABLET_MODU
APK = tablet_modu.FILES[0][0]
RC = tablet_modu.FILES[1][0]
JAVA = os.path.join(MODUL, "uygulama", "src", "com", "gbos", "tabletmodu", "TabletModu.java")
MANIFEST = os.path.join(MODUL, "uygulama", "AndroidManifest.xml")
CIL = os.path.join(MODUL, "selinux", "71-gbos-tablet-modu.cil")
PLAT_LINK = os.path.join(ARACLAR, "selinux", "politika", "plat", "71-gbos-tablet-modu.cil")
PATCH = os.path.join(ARACLAR, "kernel", "kaynak-yamalari",
                     "0002-input-pogo-let-userspace-force-tablet-mode-with-an-o.patch")
APKSIGNER = os.path.join(os.environ.get("ANDROID_HOME", os.path.expanduser("~/Android/Sdk")),
                         "build-tools", "37.0.0", "apksigner")
UPDATER_APK = os.path.join(ARACLAR, "guncelleyici", "out", "arm64", "system", "priv-app",
                           "GbosGuncelleyici", "GbosGuncelleyici.apk")
NODE = "/sys/devices/virtual/sec/sec_keypad/tablet_mode"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_every_part_names_the_same_node():
    gerekli(JAVA, RC, CIL, PATCH)
    java = re.search(r'DUGUM = "([^"]+)"', _read(JAVA)).group(1)
    rc = re.search(r"chmod 0666 (\S+)", _read(RC)).group(1)
    cil = re.search(r'\(genfscon sysfs "([^"]+)"', _read(CIL)).group(1)
    assert java == rc == NODE
    assert "/sys" + cil == NODE
    patch = _read(PATCH)
    # the attribute sits in the group the driver creates on sec_device_create(..., "sec_keypad")
    assert "static DEVICE_ATTR(tablet_mode, 0644, tablet_mode_show, tablet_mode_store);" in patch
    assert "+\t&dev_attr_tablet_mode.attr," in patch


def test_policy_is_linked_into_plat():
    assert os.path.islink(PLAT_LINK)
    assert os.path.realpath(PLAT_LINK) == os.path.realpath(CIL)


def test_apk_is_the_app_signed_with_the_updater_key():
    gerekli(APK, APKSIGNER, UPDATER_APK)
    import zipfile
    with zipfile.ZipFile(APK) as z:
        assert guncelleyici.manifest_package(z.read("AndroidManifest.xml")) == "com.gbos.tabletmodu"
        assert "classes.dex" in z.namelist()

    def cert(apk):
        out = subprocess.run([APKSIGNER, "verify", "--print-certs", apk], check=True,
                             capture_output=True, text=True).stdout
        return re.search(r"certificate SHA-256 digest: (\w+)", out).group(1)

    assert cert(APK) == cert(UPDATER_APK)


def test_apk_is_built_from_the_current_sources():
    """The APK's dex holds every class of uygulama/src; a stale out/ shows as a missing class."""
    gerekli(APK)
    import zipfile
    with zipfile.ZipFile(APK) as z:
        dex = z.read("classes.dex")
    src = os.path.join(MODUL, "uygulama", "src")
    for root, _dirs, names in os.walk(src):
        for n in names:
            if n.endswith(".java"):
                cls = os.path.relpath(os.path.join(root, n[:-5]), src)
                assert f"L{cls};".encode() in dex, f"{cls} yok: tablet-modu/build.sh yeniden koşulmalı"
    newest_src = max(os.path.getmtime(os.path.join(r, n))
                     for r, _d, ns in os.walk(os.path.join(MODUL, "uygulama")) for n in ns)
    assert os.path.getmtime(APK) >= newest_src, "out/ kaynaklardan eski: tablet-modu/build.sh"


def test_tile_hidden_until_the_kernel_has_the_switch():
    m = _read(MANIFEST)
    service = re.search(r'<service\s+android:name="\.TabletModuKutucugu"(.*?)</service>', m, re.S).group(1)
    assert 'android:enabled="false"' in service
    assert 'android:permission="android.permission.BIND_QUICK_SETTINGS_TILE"' in service
    receiver = re.search(r'<receiver\s+android:name="\.AcilisAlicisi"(.*?)</receiver>', m, re.S).group(1)
    assert "android.intent.action.LOCKED_BOOT_COMPLETED" in receiver
    assert 'android:directBootAware="true"' in m


def test_install_is_idempotent_and_copies_the_sources():
    gerekli(APK, RC)
    with tempfile.TemporaryDirectory() as sys_dir:
        tablet_modu.install(sys_dir)
        first = {}
        for src, rel, mode in tablet_modu.FILES:
            dst = os.path.join(sys_dir, "system", rel)
            assert filecmp.cmp(src, dst, shallow=False)
            assert os.stat(dst).st_mode & 0o777 == mode
            first[rel] = os.stat(dst).st_size
        tablet_modu.install(sys_dir)
        for _src, rel, _mode in tablet_modu.FILES:
            assert os.stat(os.path.join(sys_dir, "system", rel)).st_size == first[rel]
        installed = sorted(os.path.relpath(os.path.join(r, n), sys_dir)
                           for r, _d, ns in os.walk(sys_dir) for n in ns)
        assert installed == sorted(os.path.join("system", rel) for _s, rel, _m in tablet_modu.FILES)
