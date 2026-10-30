"""c) /system/etc/ld.config.32 as install_32bit_runtime writes it, and whether the LineageOS
32-bit vendor code can load under it.

Root cause it protects (acilis-loglari/codec2-20261022/KOK-NEDEN.md): a flat configuration
gave the 32-bit vendor HALs a single libbinder; vndservicemanager then saw SYST headers, codec2's
AIDL allocator lookup failed, its fallback path called mremap and the vendor seccomp policy killed
the encoder. The [vendor] namespace must be isolated, take only LLNDK libraries from the system
namespace (libbinder_ndk yes, libbinder no) and search the patched vendor-flavour override first.
"""
import collections
import os
import re
import struct
import subprocess

import pytest

from conftest import GERCEK_WORK, PLAT_FC, arac_gerekli, gerekli, oku, yaz

LD_CONFIG = "system/etc/ld.config.32"
EM_ARM = 40


def ayristir(metin):
    """ld.config.txt format (bionic linker_config.cpp): `dir.<section> = <path>` lines before the
    first section, then `[section]` with `key = value` properties."""
    dirs = collections.defaultdict(list)
    sections = {}
    current = None
    for line in metin.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"\[(\w+)\]", line)
        if m:
            current = sections.setdefault(m.group(1), {})
            continue
        key, sep, value = line.partition("=")
        assert sep, f"bad line {line!r}"
        key, value = key.strip(), value.strip()
        if current is None:
            assert key.startswith("dir."), line
            dirs[key[4:]].append(value)
        else:
            assert key not in current, f"duplicate {key}"
            current[key] = value
    return dirs, sections


@pytest.fixture(scope="session")
def uretilen(ap_asamalar, pristine_fc):
    """ld.config.32 written by apply_patches.install_32bit_runtime() into the scratch work dir
    (LineageOS 32-bit stage as input, pristine plat_file_contexts for its label)."""
    ap = ap_asamalar
    sys_dir = os.path.join(ap.WORK, "sys")
    assert ap.SYS_DIR == sys_dir
    yaz(os.path.join(sys_dir, PLAT_FC), pristine_fc)
    ap.install_32bit_runtime()
    return oku(os.path.join(sys_dir, LD_CONFIG))


def test_vendor_ad_alani_yalitilmis(ap, uretilen):
    """[vendor]: isolated default namespace, override directory first in both search and
    permitted paths, a link to `system` that shares the LLNDK libraries libbinder_ndk.so and
    libc.so but neither libbinder.so nor libhidlbase.so (the vendor keeps its own copies)."""
    dirs, sec = ayristir(uretilen)
    assert sorted(dirs["vendor"]) == ["/odm/bin/", "/vendor/bin/"]
    assert "/system/bin/" in dirs["system"]
    v = sec["vendor"]
    assert v["namespace.default.isolated"] == "true"
    override = "/" + ap.VENDOR_BINDER_OVERRIDE
    assert v["namespace.default.search.paths"].split(":")[0] == override
    assert v["namespace.default.permitted.paths"].split(":")[0] == override
    assert v["additional.namespaces"] == "system"
    assert v["namespace.default.links"] == "system"
    llndk = v["namespace.default.link.system.shared_libs"].split(":")
    assert len(llndk) == len(set(llndk))
    assert {"libbinder_ndk.so", "libc.so"} <= set(llndk)
    assert not {"libbinder.so", "libhidlbase.so"} & set(llndk)
    # Bionic of mica's 64-bit-only runtime is not there: the system namespace must reach
    # /system/lib/bootstrap, where install_32bit_runtime puts the 32-bit libc.
    assert "/system/lib/bootstrap" in v["namespace.system.search.paths"].split(":")
    assert sec["system"]["namespace.default.isolated"] == "false"


def test_calisan_agacla_ayni(uretilen):
    """The working tree carries exactly the file the current install_32bit_runtime writes."""
    calisan = os.path.join(GERCEK_WORK, "sys", LD_CONFIG)
    gerekli(calisan)
    assert oku(calisan) == uretilen


def _elf(path):
    with open(path, "rb") as f:
        h = f.read(20)
    if len(h) < 20 or h[:4] != b"\x7fELF":
        return None
    return h[4], struct.unpack_from("<H", h, 18)[0]  # EI_CLASS, e_machine


def _host(path, lineage_vendor):
    """Host directory holding the device directory `path` (vendor/odm from the LineageOS zip,
    /system and /system_ext from the working tree)."""
    for prefix, root in (("/vendor/", "vendor"), ("/odm/", "odm")):
        if path.startswith(prefix):
            top, _, rest = path[len(prefix):].partition("/")
            base = lineage_vendor.get(f"{root}/{top}")
            return os.path.join(base, rest) if base else None
    if path.startswith("/system_ext/"):
        return os.path.join(GERCEK_WORK, "sysext", path[len("/system_ext/"):])
    return os.path.join(GERCEK_WORK, "sys", path.lstrip("/"))


def _adlar(dirs):
    names = set()
    for d in dirs:
        if d and os.path.isdir(d):
            names.update(n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n)))
    return names


def test_vendor_needed_cozuluyor(uretilen, lineage_vendor):
    """Every DT_NEEDED of the LineageOS vendor's 32-bit ARM code (all of /vendor/lib and the
    32-bit programs in /vendor/bin) resolves under [vendor]: from its search paths, or through the
    link to `system` for an LLNDK name that the system namespace really provides. Hexagon DSP
    libraries (/vendor/lib/rfsa/adsp) are not ARM and are not loaded by this linker."""
    arac_gerekli("llvm-readelf")
    _, sec = ayristir(uretilen)
    v = sec["vendor"]
    llndk = set(v["namespace.default.link.system.shared_libs"].split(":"))
    saglayan = _adlar(_host(p, lineage_vendor) for p in v["namespace.default.search.paths"].split(":"))
    sistem = _adlar(_host(p, lineage_vendor) for p in v["namespace.system.search.paths"].split(":"))
    assert sistem, "no system namespace directory found in the working tree"

    kaynaklar, dsp_disi = [], []
    for key in ("vendor/lib", "vendor/bin"):
        base = lineage_vendor[key]
        for dp, _dn, fn in os.walk(base):
            for n in fn:
                p = os.path.join(dp, n)
                if os.path.islink(p):
                    continue
                e = _elf(p)
                if e == (1, EM_ARM):
                    kaynaklar.append(p)
                elif e and key == "vendor/lib":
                    dsp_disi.append(os.path.relpath(p, base))
    assert len(kaynaklar) > 100, f"only {len(kaynaklar)} 32-bit ARM ELF files found"
    # Only the DSP side may be non-ARM in /vendor/lib.
    assert all(r.startswith("rfsa/adsp/") for r in dsp_disi), dsp_disi

    r = subprocess.run(["llvm-readelf", "-d", *kaynaklar], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    needed, cur = {}, None
    for line in r.stdout.splitlines():
        if line.startswith("File: "):
            cur = line[6:]
            needed[cur] = []
        m = re.search(r"\(NEEDED\)\s+Shared library: \[(.+)\]", line)
        if m:
            needed[cur].append(m.group(1))
    assert sorted(needed) == sorted(kaynaklar), "llvm-readelf did not report every file"
    assert all(needed[p] for p in kaynaklar if p.endswith(".so")), "a library without NEEDED"

    cozulmeyen, sistemde_yok = collections.defaultdict(list), collections.defaultdict(list)
    for p, libs in needed.items():
        for lib in libs:
            if lib in saglayan:
                continue
            if lib in llndk:
                if lib not in sistem:
                    sistemde_yok[lib].append(p)
                continue
            cozulmeyen[lib].append(p)
    assert not cozulmeyen, {k: v[:3] for k, v in cozulmeyen.items()}
    assert not sistemde_yok, {k: v[:3] for k, v in sistemde_yok.items()}
