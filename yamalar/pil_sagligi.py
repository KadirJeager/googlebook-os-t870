#!/usr/bin/env python3
"""Battery health of the SM-T870: the tablet's health HAL with the Samsung driver's nodes configured.

apply_patches.py calls
    pil_sagligi.install(SYS_DIR, SYSEXT_DIR, PROD_DIR)
on the system, system_ext and product trees. The result is the same on pristine and on already
patched trees. What it does and why is in pil-sagligi/BENIOKU.md; in short:

The LineageOS vendor health HAL (vendor.health-samsung) runs AOSP's default configuration, which
only knows the standard sysfs names; the Samsung battery driver reports state of health as
fg_asoc and the cycle counter as battery_cycle, so Settings > Battery health shows 0 % and 0
cycles. pil-sagligi/out/android.hardware.health-service.samsung-gbos is the same HAL built from
the same sources with those two paths configured. install() writes into the product tree
    bin/hw/android.hardware.health-service.samsung-gbos      the HAL (0755)
    etc/init/android.hardware.health-service.samsung-gbos.rc stops vendor.health-samsung before
                                                             its class starts, runs this one
    etc/selinux/product_file_contexts                        marked block: the binary is
                                                             hal_health_default_exec, so init
                                                             starts it in the vendor HAL's own
                                                             domain hal_health_default
The binary is rebuilt with pil-sagligi/build.sh when out/STAMP no longer matches its sources, and
checked against the device libraries of the release being patched (yamalar/hazir_ikili.py).

Command line (from T870-araclar):
    python3 yamalar/pil_sagligi.py build             build (if stale) and print the binary's hash
    python3 yamalar/pil_sagligi.py install SYS SYSEXT PROD
    python3 yamalar/pil_sagligi.py politika [--work DIR]   compile the work tree's policy as init
                                                     does and check pil-sagligi/beklenen.txt
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hazir_ikili  # noqa: E402

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(TOOLS_DIR, "pil-sagligi")
OUT = os.path.join(SRC, "out")
NAME = "android.hardware.health-service.samsung-gbos"
BINARY = os.path.join(OUT, NAME)
STAMP = os.path.join(OUT, "STAMP")
RC = os.path.join(SRC, "etc", f"{NAME}.rc")

FILES = [
    # (source, path in the product tree, mode)
    (BINARY, f"bin/hw/{NAME}", 0o755),
    (RC, f"etc/init/{NAME}.rc", 0o644),
]
SERVICE = "gbos.health-samsung"
VENDOR_SERVICE = "vendor.health-samsung"

PRODUCT_FILE_CONTEXTS = "etc/selinux/product_file_contexts"
FC_BEGIN = "# --- GOOGLEBOOKOS_T870_PATCH: pil-sagligi ---\n"
FC_END = "# --- GOOGLEBOOKOS_T870_PATCH: pil-sagligi end ---\n"
EXEC_TYPE = "hal_health_default_exec"
DOMAIN = "hal_health_default"
FC_LINES = f"/product/bin/hw/{re.escape(NAME)} u:object_r:{EXEC_TYPE}:s0\n"
# The tablet's LineageOS vendor partition as lineage-hazirla.py reads it out of the zip.
LINEAGE_VENDOR = os.path.join(os.environ.get("GBOS_WORK", "/var/tmp/mica_work"), "lineage", "bolumler", "vendor")
# The label's type and the transition init takes into the vendor HAL's domain come from the
# tablet's LineageOS vendor policy (init_daemon_domain(hal_health_default)); install() refuses
# to label the binary with a type that policy does not have.
VENDOR_POLICY = os.path.join(LINEAGE_VENDOR, "etc", "selinux", "vendor_sepolicy.cil")
# The vendor service's rc and VINTF fragment, which ours mirrors.
VENDOR_RC = os.path.join(LINEAGE_VENDOR, "etc", "init", "android.hardware.health-service.samsung.rc")
VENDOR_VINTF = os.path.join(LINEAGE_VENDOR, "etc", "vintf", "manifest", "android.hardware.health-service.samsung.xml")
BEKLENEN = os.path.join(SRC, "beklenen.txt")


def build():
    """Builds the binary unless out/ already holds the build of the current sources
    (yamalar/hazir_ikili.py)."""
    hazir_ikili.derle(SRC, STAMP, [BINARY], "pil-sagligi")


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def parse_rc(text):
    """({service name: (argv, [options])}, {trigger: [commands]}) of an init rc text."""
    services, actions, current = {}, {}, None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        words = line.split()
        if not raw[:1].isspace():
            current = None
            if words[0] == "service":
                current = services.setdefault(words[1], (words[2:], []))[1]
            elif words[0] == "on":
                current = actions.setdefault(" ".join(words[1:]), [])
        elif current is not None:
            current.append(" ".join(words))
    return services, actions


def vendor_policy_problems():
    with open(VENDOR_POLICY) as f:
        cil = f.read()
    problems = []
    if f"(type {EXEC_TYPE})" not in cil:
        problems.append(f"{EXEC_TYPE} is not declared")
    if not re.search(rf"\(typetransition init_\d+ {EXEC_TYPE} process {DOMAIN}\)", cil):
        problems.append(f"no init transition {EXEC_TYPE} -> {DOMAIN}")
    return problems


def service_problems():
    """Our service must run exactly like the vendor's: same class, user, groups, capabilities
    and files, and the vendor's must still be the one we stop."""
    with open(VENDOR_RC) as f:
        vendor, _ = parse_rc(f.read())
    with open(RC) as f:
        ours, actions = parse_rc(f.read())
    problems = []
    if VENDOR_SERVICE not in vendor:
        problems.append(f"{VENDOR_RC} no longer defines {VENDOR_SERVICE}")
    elif SERVICE not in ours:
        problems.append(f"{RC} does not define {SERVICE}")
    else:
        if ours[SERVICE][0] != [f"/product/bin/hw/{NAME}"]:
            problems.append(f"{SERVICE} runs {ours[SERVICE][0]}")
        if sorted(ours[SERVICE][1]) != sorted(vendor[VENDOR_SERVICE][1]):
            problems.append(f"{SERVICE} options {ours[SERVICE][1]} differ from {VENDOR_SERVICE}'s "
                            f"{vendor[VENDOR_SERVICE][1]}")
        if vendor[VENDOR_SERVICE][0] != ["/vendor/bin/hw/android.hardware.health-service.samsung"]:
            problems.append(f"{VENDOR_SERVICE} runs {vendor[VENDOR_SERVICE][0]}")
    if list(ours) != [SERVICE]:
        problems.append(f"{RC} defines {list(ours)}, expected only {SERVICE}")
    if f"stop {VENDOR_SERVICE}" not in actions.get("early-init", []):
        problems.append(f"{RC} does not stop {VENDOR_SERVICE} on early-init")
    if actions.get(f"property:init.svc.{VENDOR_SERVICE}=running") != [
            f"stop {VENDOR_SERVICE}", f"restart {SERVICE}"]:
        problems.append(f"{RC} does not stop {VENDOR_SERVICE} again when it starts")
    return problems


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


def install(sys_dir, sysext_dir, prod_dir):
    for tree in (sys_dir, sysext_dir, prod_dir):
        if not os.path.isdir(tree):
            raise FileNotFoundError(tree)
    problems = vendor_policy_problems() + service_problems()
    if problems:
        raise RuntimeError("pil sagligi: " + "; ".join(problems))
    build()
    hazir_ikili.uyum_denetle(BINARY, "pil-sagligi")
    for src, rel, mode in FILES:
        _install_file(src, os.path.join(prod_dir, rel), mode)
    _patch_file_contexts(os.path.join(prod_dir, PRODUCT_FILE_CONTEXTS))
    check(sys_dir, sysext_dir, prod_dir)
    print(f"[+] pil sagligi: {SERVICE} ({NAME}) replaces {VENDOR_SERVICE}")


def check(sys_dir, sysext_dir, prod_dir):
    """Raises unless the trees hold exactly what install() puts there."""
    errors = []
    for src, rel, mode in FILES:
        p = os.path.join(prod_dir, rel)
        if not os.path.isfile(p):
            errors.append(f"missing product/{rel}")
            continue
        if sha256(p) != sha256(src):
            errors.append(f"product/{rel} differs from {os.path.relpath(src, TOOLS_DIR)}")
        if os.stat(p).st_mode & 0o7777 != mode:
            errors.append(f"product/{rel} mode {oct(os.stat(p).st_mode & 0o7777)}, expected {oct(mode)}")
    with open(os.path.join(prod_dir, PRODUCT_FILE_CONTEXTS)) as f:
        fc = f.read()
    if fc.count(FC_BEGIN) != 1 or FC_BEGIN + FC_LINES + FC_END not in fc:
        errors.append(f"product/{PRODUCT_FILE_CONTEXTS} lacks the pil-sagligi block")
    # Exactly one rc of the image may define, stop or start a health HAL service.
    found = []
    for tree, base in ((sys_dir, "system/etc/init"), (sysext_dir, "etc/init"), (prod_dir, "etc/init")):
        d = os.path.join(tree, base)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                with open(p, errors="replace") as f:
                    body = f.read()
                if SERVICE in body or VENDOR_SERVICE in body or "health-service" in body:
                    found.append(os.path.relpath(p, tree))
    if found != [f"etc/init/{NAME}.rc"]:
        errors.append(f"health HAL services in {found}, expected only etc/init/{NAME}.rc")
    if errors:
        raise RuntimeError("pil sagligi: " + "; ".join(errors))


def politika(work=None):
    """Compiles the work tree's SELinux policy the way init does on the tablet (with its
    LineageOS vendor and odm policy) and checks pil-sagligi/beklenen.txt and the transition.
    Returns the number of checked lines; raises RuntimeError on a mismatch."""
    sys.path.insert(0, TOOLS_DIR)
    from yamalar import selinux as gbos_selinux
    import tempfile
    work = work or os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
    secilc, checker = gbos_selinux._tools()
    tuples, want, where = [], [], []
    with open(BEKLENEN) as f:
        for n, line in enumerate(f, 1):
            fields = line.split("#", 1)[0].split()
            if not fields:
                continue
            deny = fields[0].startswith("-")
            fields[0] = fields[0].lstrip("-")
            if len(fields) != 4:
                raise RuntimeError(f"{BEKLENEN}:{n}: expected 4 fields")
            tuples.append(fields)
            want.append(not deny)
            where.append(n)
    with tempfile.TemporaryDirectory(prefix="gbos-pil-", dir=os.environ.get("TMPDIR", "/var/tmp")) as tmp:
        lin = gbos_selinux._lineage_layout(work)
        inputs = gbos_selinux.init_inputs(os.path.join(work, "sys", "system"), os.path.join(work, "sysext"),
                                          os.path.join(work, "prod"), lin["vendor"], lin["odm"])
        policy = gbos_selinux.compile_policy(secilc, inputs, os.path.join(tmp, "gbos.pol"))
        verdicts = gbos_selinux.run_checker(checker, policy, tuples)
        cil = subprocess.run([checker, "--cil", policy], check=True, capture_output=True, text=True).stdout
    errors = [f"beklenen.txt:{n}: {' '.join(t)} -> {v}, expected {'ALLOW' if w else 'DENY'}"
              for t, w, n, v in zip(tuples, want, where, verdicts) if (v == "ALLOW") != w]
    if f"(typetransition init {EXEC_TYPE} process {DOMAIN})" not in cil:
        errors.append(f"compiled policy has no typetransition init {EXEC_TYPE} process {DOMAIN}")
    if errors:
        raise RuntimeError("pil sagligi politika: " + "; ".join(errors))
    return len(tuples) + 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    p = sub.add_parser("install")
    p.add_argument("sys_dir")
    p.add_argument("sysext_dir")
    p.add_argument("prod_dir")
    p = sub.add_parser("politika")
    p.add_argument("--work")
    args = ap.parse_args()
    if args.cmd == "build":
        build()
        print(sha256(BINARY), BINARY)
    elif args.cmd == "install":
        install(args.sys_dir, args.sysext_dir, args.prod_dir)
    else:
        print(f"[+] pil sagligi politika: {politika(args.work)} checks hold")


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main())
