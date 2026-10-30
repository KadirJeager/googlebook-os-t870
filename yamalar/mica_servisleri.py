#!/usr/bin/env python3
"""Tablet-side answers to the Dell/Googlebook hardware services mica's system waits for.

apply_patches.py calls
    mica_servisleri.install(SYS_DIR, SYSEXT_DIR, PROD_DIR)
on the system, system_ext and product trees. The result is the same on pristine and on already
patched trees. What it does and why is in mica-servisleri/README.md; in short:

VPD   com.android.desktop.serviceability (and through it My Googlebook, Setup Wizard and the
      factory apps) blocks in ServiceManager.waitForService() on
      vendor.google.desktop.vpd_executor.IVpdExecutor/default, which mica serves from its vendor
      partition. mica-servisleri/out/gbos-vpd-executor implements it for this tablet:
        system/bin/gbos-vpd-executor                       the lazy AIDL service (0755)
        system/etc/init/gbos-vpd-executor.rc               init service + /data/misc/gbos_vpd
        system/etc/vintf/manifest/gbos-vpd-executor.xml    framework manifest declaration
        system/etc/selinux/plat_file_contexts              labels of the binary and its data
      The binary is rebuilt with mica-servisleri/build.sh when out/STAMP no longer matches its
      sources, and checked against the release's device libraries (yamalar/hazir_ikili.py).
GSC   /system/bin/udsattestation waits forever for android.system.desktop.security.gsc.IGsc/default.
      The tablet has no Google Security Chip and gscd is not installed (apply_patches.py removes
      gscd.rc), so the service is removed: system/etc/init/udsattestation.rc.

Command line (from T870-araclar):
    python3 yamalar/mica_servisleri.py build             build (if stale) and print the binary's hash
    python3 yamalar/mica_servisleri.py install SYS SYSEXT PROD
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hazir_ikili  # noqa: E402

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(TOOLS_DIR, "mica-servisleri")
OUT = os.path.join(SRC, "out")
BINARY = os.path.join(OUT, "gbos-vpd-executor")
STAMP = os.path.join(OUT, "STAMP")

VPD_FILES = [
    # (source, path in the system tree, mode)
    (BINARY, "system/bin/gbos-vpd-executor", 0o755),
    (os.path.join(SRC, "vpd/etc/gbos-vpd-executor.rc"), "system/etc/init/gbos-vpd-executor.rc", 0o644),
    (os.path.join(SRC, "vpd/etc/gbos-vpd-executor.xml"),
     "system/etc/vintf/manifest/gbos-vpd-executor.xml", 0o644),
]
PLAT_FILE_CONTEXTS = "system/etc/selinux/plat_file_contexts"
FC_BEGIN = "# --- GOOGLEBOOKOS_T870_PATCH: mica-servisleri ---\n"
FC_END = "# --- GOOGLEBOOKOS_T870_PATCH: mica-servisleri end ---\n"
FC_LINES = (
    "/system/bin/gbos-vpd-executor u:object_r:gbos_vpd_executor_exec:s0\n"
    "/data/misc/gbos_vpd(/.*)? u:object_r:gbos_vpd_data_file:s0\n"
)
# Types the FC_LINES use; a context naming a type the loaded policy lacks cannot be applied on
# the device, so install() refuses to write them unless the policy sources declare them
# (selinux/politika, compiled by yamalar/selinux.py; owned by the SELinux port).
POLICY_DIR = os.path.join(TOOLS_DIR, "selinux", "politika")
FC_TYPES = ("gbos_vpd_executor_exec", "gbos_vpd_data_file")
REMOVED = [
    # Starts udsattestation, which waits forever for the GSC this tablet does not have.
    "system/etc/init/udsattestation.rc",
]
SERVICE = "vendor.google.desktop.vpd_executor.IVpdExecutor/default"


def build():
    """Builds the binary unless out/ already holds the build of the current sources
    (yamalar/hazir_ikili.py)."""
    hazir_ikili.derle(SRC, STAMP, [BINARY], "mica-servisleri")


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _install_file(src, dst, mode):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    shutil.copyfile(src, tmp)
    os.chmod(tmp, mode)
    os.replace(tmp, dst)


def _patch_file_contexts(path):
    with open(path) as f:
        fc = f.read()
    begin = fc.find(FC_BEGIN)
    if begin >= 0:
        end = fc.index(FC_END, begin) + len(FC_END)
        fc = fc[:begin] + fc[end:]
    fc = fc.rstrip("\n") + "\n" + FC_BEGIN + FC_LINES + FC_END
    with open(path, "w") as f:
        f.write(fc)


def undeclared_policy_types():
    declared = set()
    for dp, dn, fn in os.walk(POLICY_DIR):
        for name in fn:
            if name.endswith(".cil"):
                with open(os.path.join(dp, name)) as f:
                    body = f.read()
                declared.update(t for t in FC_TYPES if f"(type {t})" in body)
    return [t for t in FC_TYPES if t not in declared]


def install(sys_dir, sysext_dir, prod_dir):
    for tree in (sys_dir, sysext_dir, prod_dir):
        if not os.path.isdir(tree):
            raise FileNotFoundError(tree)
    missing = undeclared_policy_types()
    if missing:
        raise RuntimeError(f"mica servisleri: {', '.join(missing)} not declared in {POLICY_DIR}")
    build()
    hazir_ikili.uyum_denetle(BINARY, "mica-servisleri")
    for src, rel, mode in VPD_FILES:
        _install_file(src, os.path.join(sys_dir, rel), mode)
    _patch_file_contexts(os.path.join(sys_dir, PLAT_FILE_CONTEXTS))
    for rel in REMOVED:
        p = os.path.join(sys_dir, rel)
        if os.path.lexists(p):
            os.remove(p)
    check(sys_dir, sysext_dir, prod_dir)
    print("[+] mica servisleri: IVpdExecutor (gbos-vpd-executor), udsattestation removed")


def check(sys_dir, sysext_dir, prod_dir):
    """Raises unless the trees hold exactly what install() puts there."""
    errors = []
    for src, rel, mode in VPD_FILES:
        p = os.path.join(sys_dir, rel)
        if not os.path.isfile(p):
            errors.append(f"missing {rel}")
            continue
        if sha256(p) != sha256(src):
            errors.append(f"{rel} differs from {os.path.relpath(src, TOOLS_DIR)}")
        if os.stat(p).st_mode & 0o7777 != mode:
            errors.append(f"{rel} mode {oct(os.stat(p).st_mode & 0o7777)}, expected {oct(mode)}")
    with open(os.path.join(sys_dir, PLAT_FILE_CONTEXTS)) as f:
        fc = f.read()
    if fc.count(FC_BEGIN) != 1 or FC_BEGIN + FC_LINES + FC_END not in fc:
        errors.append(f"{PLAT_FILE_CONTEXTS} lacks the mica-servisleri block")
    for rel in REMOVED:
        if os.path.lexists(os.path.join(sys_dir, rel)):
            errors.append(f"{rel} still present")
    # Exactly one place may declare and start the service.
    declared = []
    for tree, base in ((sys_dir, "system/etc"), (sysext_dir, "etc"), (prod_dir, "etc")):
        for sub in ("init", "vintf/manifest"):
            d = os.path.join(tree, base, sub)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    with open(p, errors="replace") as f:
                        body = f.read()
                    if "vendor.google.desktop.vpd_executor" in body:
                        declared.append(os.path.relpath(p, tree))
    expected = sorted(rel for _, rel, _ in VPD_FILES if rel.endswith((".rc", ".xml")))
    if sorted(declared) != expected:
        errors.append(f"{SERVICE} declared by {declared}, expected {expected}")
    if errors:
        raise RuntimeError("mica servisleri: " + "; ".join(errors))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    p = sub.add_parser("install")
    p.add_argument("sys_dir")
    p.add_argument("sysext_dir")
    p.add_argument("prod_dir")
    args = ap.parse_args()
    if args.cmd == "build":
        build()
        print(sha256(BINARY), BINARY)
    else:
        install(args.sys_dir, args.sysext_dir, args.prod_dir)


if __name__ == "__main__":
    sys.exit(main())
