"""k) The battery health module (pil-sagligi/, yamalar/pil_sagligi.py): the configured health HAL
links against the tablet's own libraries, serves the interface the vendor HAL serves, replaces
the vendor service the way init allows, and lands in the product image with the vendor HAL's
SELinux label and domain.

Real inputs: out/ of pil-sagligi (built by its build.sh), the pristine mica images, the LineageOS
zip (vendor rc, VINTF fragment and binary are re-extracted from it), the working tree's product
file_contexts, prod.meta and policy files (read only), and AOSP's init sources (network)."""
import base64
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from conftest import (ARACLAR, EROFS_BIN, GERCEK_WORK, LINEAGE_ZIP, MICA, arac_gerekli, disk_payi,
                      gerekli, oku, reflink, sha256, sil)

sys.path.insert(0, ARACLAR)
from yamalar import hazir_ikili, pil_sagligi  # noqa: E402

MODUL = pil_sagligi.SRC
NDK = os.environ.get("ANDROID_NDK", os.path.expanduser("~/Android/Sdk/ndk/30.0.16248370"))
READELF = os.path.join(NDK, "toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf")
AOSP_INIT = "https://android.googlesource.com/platform/system/core/+/refs/tags/android-17.0.0_r1/init/"
VENDOR_BIN = "android.hardware.health-service.samsung"


def _readelf(*args):
    return subprocess.run([READELF, *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture(scope="module")
def ikili():
    """The built HAL; it must be the build of the current inputs (pil-sagligi/build.sh)."""
    gerekli(pil_sagligi.BINARY, pil_sagligi.STAMP, READELF)
    assert oku(pil_sagligi.STAMP).strip() == hazir_ikili.stamp(pil_sagligi.SRC), \
        "out/ is stale: run pil-sagligi/build.sh"
    return pil_sagligi.BINARY


@pytest.fixture(scope="module")
def lineage_vendor(gecici_kok):
    """The vendor health HAL's binary, rc and VINTF fragment, re-extracted from the LineageOS zip."""
    gerekli(LINEAGE_ZIP)
    arac_gerekli("brotli")
    disk_payi(0.5)  # the vendor image (0.4 GB) is rebuilt next to the output and removed
    out = os.path.join(gecici_kok, "pil-lineage-vendor")
    r = subprocess.run([sys.executable, "-I", os.path.join(ARACLAR, "lineage-dosya-cikar.py"),
                        LINEAGE_ZIP, "vendor", f"/bin/hw/{VENDOR_BIN}", f"/etc/init/{VENDOR_BIN}.rc",
                        f"/etc/vintf/manifest/{VENDOR_BIN}.xml", "--cikti", out],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    return out


def test_ikili_cihazin_kutuphaneleriyle_baglanir(ikili, gecici):
    """AArch64 PIE for /system/bin/linker64; DT_NEEDED only LLNDK libraries (visible in
    linkerconfig's product section); every imported symbol, with its version, exported by mica's
    own libbinder_ndk/liblog/bionic; nothing exported by the binary (no interposition on the
    process's shared libutils/libbase)."""
    gerekli(os.path.join(MICA, "system_a.erofs"), os.path.join(EROFS_BIN, "dump.erofs"))
    hdr = _readelf("-h", ikili)
    assert re.search(r"Class:\s+ELF64", hdr) and re.search(r"Machine:\s+AArch64", hdr)
    assert re.search(r"Type:\s+DYN", hdr)
    assert "Requesting program interpreter: /system/bin/linker64" in _readelf("-l", ikili)
    needed = sorted(re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", _readelf("-d", ikili)))
    assert needed == ["libbinder_ndk.so", "libc.so", "libdl.so", "liblog.so"]
    llndk = oku(os.path.join(GERCEK_WORK, "lineage", "llndk.txt")).split() \
        if os.path.exists(os.path.join(GERCEK_WORK, "lineage", "llndk.txt")) else None
    if llndk is not None:
        assert set(needed) <= set(llndk)
    lib = os.path.join(gecici, "lib")
    subprocess.run([sys.executable, "-I", os.path.join(MODUL, "araclar", "cihaz-kutuphaneleri.py"), lib],
                   check=True)
    r = subprocess.run([sys.executable, "-I", os.path.join(MODUL, "araclar", "sembol-denetimi.py"),
                        READELF, ikili, lib], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "exported by the binary: 0" in r.stdout


def test_vendor_ile_ayni_arayuz_ve_yapilandirma(ikili, lineage_vendor):
    """Same AIDL interface as the vendor HAL (android.hardware.health V5, the frozen hash both
    embed), the two configured sysfs nodes and nothing else of the sort, and the vendor rc / VINTF
    fragment the patch reads ($WORK/lineage/bolumler) equal what the zip ships and what the port
    was checked against (lineage.lock)."""
    vendor = os.path.join(lineage_vendor, VENDOR_BIN)
    assert "android.hardware.health-V5-ndk.so" in _readelf("-d", vendor)
    assert re.search(r"AIDL_VERSION=5\b", oku(os.path.join(MODUL, "build.sh")))
    data, vdata = open(ikili, "rb").read(), open(vendor, "rb").read()
    v5 = b"33ada6818419126ab45a0ca13d57ffa3cc59a2dd"  # aidl_api/android.hardware.health/5/.hash
    assert v5 + b"\0" in data and v5 + b"\0" in vdata
    assert b"/sys/class/power_supply/battery/fg_asoc\0" in data
    assert b"/sys/class/power_supply/battery/battery_cycle\0" in data
    kaynak = oku(os.path.join(MODUL, "lineage.lock"))
    assert f"sha256={sha256(LINEAGE_ZIP)}" in kaynak
    for name, ours in ((f"{VENDOR_BIN}.rc", pil_sagligi.VENDOR_RC), (f"{VENDOR_BIN}.xml", pil_sagligi.VENDOR_VINTF)):
        gerekli(ours)
        assert sha256(os.path.join(lineage_vendor, name)) == sha256(ours)
        assert sha256(ours) in kaynak, name
    assert sha256(vendor) in kaynak
    assert "<fqname>IHealth/default</fqname>" in oku(os.path.join(lineage_vendor, f"{VENDOR_BIN}.xml"))


def test_servis_tanimi_ve_vendor_politikasi():
    """The service runs exactly like vendor.health-samsung (class, user, groups, capabilities,
    files), the vendor service is stopped before its class starts and again if it is started, and
    the tablet's vendor policy has the exec type and init's transition into hal_health_default."""
    assert pil_sagligi.service_problems() == []
    assert pil_sagligi.vendor_policy_problems() == []


def test_install_iki_kez_ayni(ikili, gecici):
    """install() on a copy of the real product file_contexts: the pristine part stays byte for
    byte, the block is there once, a second run changes nothing, files and modes are as declared;
    the system and system_ext trees (real, read only) define no other health service."""
    gerekli(os.path.join(GERCEK_WORK, "sys", "system", "etc", "init"), os.path.join(GERCEK_WORK, "sysext"),
            os.path.join(GERCEK_WORK, "prod", pil_sagligi.PRODUCT_FILE_CONTEXTS))
    prod = os.path.join(gecici, "prod")
    fc_rel = pil_sagligi.PRODUCT_FILE_CONTEXTS
    reflink(os.path.join(GERCEK_WORK, "prod", fc_rel), os.path.join(prod, fc_rel))
    asil = oku(os.path.join(prod, fc_rel))
    if pil_sagligi.FC_BEGIN in asil:  # the working tree may already carry the block
        b = asil.index(pil_sagligi.FC_BEGIN)
        asil = asil[:b] + asil[asil.index(pil_sagligi.FC_END) + len(pil_sagligi.FC_END):]
    sys_dir, sysext_dir = os.path.join(GERCEK_WORK, "sys"), os.path.join(GERCEK_WORK, "sysext")
    pil_sagligi.install(sys_dir, sysext_dir, prod)
    bir = {rel: sha256(os.path.join(prod, rel)) for rel in (fc_rel, *(r for _, r, _ in pil_sagligi.FILES))}
    pil_sagligi.install(sys_dir, sysext_dir, prod)
    iki = {rel: sha256(os.path.join(prod, rel)) for rel in bir}
    assert bir == iki
    fc = oku(os.path.join(prod, fc_rel))
    assert fc == asil.rstrip("\n") + "\n" + pil_sagligi.FC_BEGIN + pil_sagligi.FC_LINES + pil_sagligi.FC_END
    for src, rel, mode in pil_sagligi.FILES:
        assert sha256(os.path.join(prod, rel)) == sha256(src)
        assert os.stat(os.path.join(prod, rel)).st_mode & 0o7777 == mode


def test_politika_gecis_ve_erisimler():
    """The working tree's policy, compiled as init compiles it on the tablet with its LineageOS
    vendor/odm policy, allows everything pil-sagligi/beklenen.txt lists and has init's
    transition hal_health_default_exec -> hal_health_default."""
    gerekli(os.path.join(GERCEK_WORK, "sys", "system", "etc", "selinux", "plat_sepolicy.cil"))
    assert pil_sagligi.politika(GERCEK_WORK) >= 20


def test_product_imajinda_etiket_ve_sahiplik(ikili, gecici):
    """make_ext4_images.py product, run as the pipeline runs it on a small work tree (install()
    output + the real prod.meta and the real plat/system_ext file_contexts): the binary is
    hal_health_default_exec, root:shell 0755; the rc is system_file 0644."""
    arac_gerekli("mke2fs", "e2fsdroid", "e2fsck", "debugfs")
    disk_payi(0.5)  # sparse 3.2 GiB image; only the small tree and fs metadata are written
    gerekli(os.path.join(GERCEK_WORK, "prod.meta"),
            os.path.join(GERCEK_WORK, "sys/system/etc/selinux/plat_file_contexts"),
            os.path.join(GERCEK_WORK, "sysext/etc/selinux/system_ext_file_contexts"),
            os.path.join(GERCEK_WORK, "prod", pil_sagligi.PRODUCT_FILE_CONTEXTS))
    work = os.path.join(gecici, "work")
    for rel in ("prod.meta", "sys/system/etc/selinux/plat_file_contexts",
                "sysext/etc/selinux/system_ext_file_contexts",
                os.path.join("prod", pil_sagligi.PRODUCT_FILE_CONTEXTS)):
        reflink(os.path.join(GERCEK_WORK, rel), os.path.join(work, rel))
    os.makedirs(os.path.join(work, "sys", "system", "etc", "init"))
    os.makedirs(os.path.join(work, "sysext", "etc", "init"))
    pil_sagligi.install(os.path.join(work, "sys"), os.path.join(work, "sysext"), os.path.join(work, "prod"))
    img = os.path.join(work, "product.img")
    try:
        r = subprocess.run([sys.executable, "-I", os.path.join(ARACLAR, "make_ext4_images.py"), "product"],
                           env=dict(os.environ, GBOS_WORK=work), cwd=work, capture_output=True, text=True)
        assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
        want = {f"/bin/hw/{pil_sagligi.NAME}": ("u:object_r:hal_health_default_exec:s0", 0o100755, 0, 2000),
                f"/etc/init/{pil_sagligi.NAME}.rc": ("u:object_r:system_file:s0", 0o100644, 0, 0)}
        for path, (label, mode, uid, gid) in want.items():
            out = subprocess.run(["debugfs", "-R", f'ea_get -V "{path}" security.selinux', img],
                                 capture_output=True, text=True, check=True).stdout
            assert out.strip().rstrip("\x00") == label, path
            st = subprocess.run(["debugfs", "-R", f'stat "{path}"', img], capture_output=True,
                                text=True, check=True).stdout
            m = re.search(r"Mode:\s+(\d+).*?User:\s+(\d+)\s+Group:\s+(\d+)", st, re.S)
            assert m and int(m.group(1), 8) == mode & 0o7777 and (int(m.group(2)), int(m.group(3))) == (uid, gid), st[:300]
    finally:
        sil(img)


@pytest.fixture(scope="module")
def aosp_init():
    def get(name):
        try:
            with urllib.request.urlopen(AOSP_INIT + name + "?format=TEXT", timeout=30) as r:
                return base64.b64decode(r.read()).decode()
        except urllib.error.HTTPError as e:
            if e.code in (429,) or e.code >= 500:
                pytest.skip(f"atlandı: gitiles {e.code}")
            pytest.fail(f"{AOSP_INIT}{name}: HTTP {e.code}")
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            pytest.skip(f"atlandı: ağ yok ({e})")
    return get


def test_init_kurallari_android17(aosp_init):
    """Why the rc stops the vendor service instead of overriding it, pinned in AOSP's init
    (android-17.0.0_r1): product rc files are parsed after vendor's; vendor and odm files run in
    the vendor subcontext and an override across that boundary is refused; stop on a service that
    has not started keeps class_start from starting it."""
    init_cpp = aosp_init("init.cpp")
    order = [init_cpp.index(f'ParseConfig("/{p}/etc/init")') for p in ("vendor", "odm", "product")]
    assert order == sorted(order)
    sub = aosp_init("subcontext.cpp")
    assert re.search(r'new Subcontext\(std::vector<std::string>\{"/vendor", "/odm"\}', sub)
    parser = aosp_init("service_parser.cpp")
    assert "across the treble boundary" in parser and "service_->subcontext()" in parser
    service = aosp_init("service.cpp")
    assert "If the service has not yet started, prevent it from auto-starting with its class." in service
    assert re.search(r"StartIfNotDisabled\(\) \{\s+if \(!\(flags_ & SVC_DISABLED\)\)", service)
