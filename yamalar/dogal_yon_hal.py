#!/usr/bin/env python3
"""Sensor and camera layers of the SM-T870's landscape natural orientation (dogal-yon/hal/BENIOKU.md).

yamalar/dogal_yon.py turns the display, touch, pen, boot animation and fingerprint location;
the sensor axes and the camera orientation come from the vendor HALs, which report relative to
the portrait panel. This module puts the two vendor HALs, rebuilt with a natural-orientation
transform (dogal-yon/hal/build.sh), into the product tree and replaces the vendor services with
them, the way pil-sagligi replaces the health HAL:

    bin/hw/<name>                          launcher (0755), labelled with the vendor HAL's exec
                                           type, so init runs it in the vendor HAL's own domain
    lib64/gbos-dogal-yon/<name>.so         the HAL (0644; system_lib_file like every /product
                                           library, loaded through a namespace cloned from sphal)
    etc/init/<name>.rc                     stops the vendor service before its class starts,
                                           runs this one with the same options
    etc/selinux/product_file_contexts      marked block with the two exec labels

for <name> in android.hardware.sensors-service.samsung-multihal-gbos (vendor.sensors-hal-
multihal) and android.hardware.camera.provider-service.samsung-gbos (vendor.camera.provider).
install() returns the verified layers; dogal_yon.install_all() passes them on as
sensor_layer_ready / camera_layer_ready.

Command line (from T870-araclar):
    python3 -I yamalar/dogal_yon_hal.py build            build (if stale), print the hashes
    python3 -I yamalar/dogal_yon_hal.py install SYS SYSEXT PROD
    python3 -I yamalar/dogal_yon_hal.py politika [--work DIR]
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
SRC = os.path.join(TOOLS_DIR, "dogal-yon", "hal")
OUT = os.path.join(SRC, "out")
STAMP = os.path.join(OUT, "STAMP")
LIBDIR = "lib64/gbos-dogal-yon"


class Hal:
    def __init__(self, layer, name, service, vendor_service, vendor_binary, vendor_rc, domain):
        self.layer = layer                    # dogal_yon's name for it
        self.name = name
        self.service = service
        self.vendor_service = vendor_service
        self.vendor_binary = vendor_binary
        self.vendor_rc = vendor_rc            # file name in /vendor/etc/init
        self.domain = domain
        self.exec_type = domain + "_exec"
        self.rc = os.path.join(SRC, "etc", f"{name}.rc")
        self.files = [
            # (source, path in the product tree, mode)
            (os.path.join(OUT, "bin", "hw", name), f"bin/hw/{name}", 0o755),
            (os.path.join(OUT, LIBDIR, f"{name}.so"), f"{LIBDIR}/{name}.so", 0o644),
            (self.rc, f"etc/init/{name}.rc", 0o644),
        ]


HALS = (
    Hal("sensor", "android.hardware.sensors-service.samsung-multihal-gbos",
        "gbos.sensors-hal-multihal", "vendor.sensors-hal-multihal",
        "/vendor/bin/hw/android.hardware.sensors-service.samsung-multihal",
        "android.hardware.sensors-service.samsung-multihal.rc", "hal_sensors_default"),
    Hal("kamera", "android.hardware.camera.provider-service.samsung-gbos",
        "gbos.camera.provider", "vendor.camera.provider",
        "/vendor/bin/hw/android.hardware.camera.provider-service.samsung",
        "android.hardware.camera.provider-service.samsung.rc", "hal_camera_default"),
)
LAYERS = tuple(h.layer for h in HALS)

PRODUCT_FILE_CONTEXTS = "etc/selinux/product_file_contexts"
PLAT_FILE_CONTEXTS = "system/etc/selinux/plat_file_contexts"
FC_BEGIN = "# --- GOOGLEBOOKOS_T870_PATCH: dogal-yon-hal ---\n"
FC_END = "# --- GOOGLEBOOKOS_T870_PATCH: dogal-yon-hal end ---\n"
FC_LINES = "".join(f"/product/bin/hw/{re.escape(h.name)} u:object_r:{h.exec_type}:s0\n" for h in HALS)
# /product/lib64 is system_lib_file in mica's plat_file_contexts; the HAL domains load it
# (beklenen.txt). install() checks the rule is still there instead of adding its own label.
LIB_LABEL_RULE = re.compile(r"^/\(product\|system/product\)/lib\(64\)\?\(/\.\*\)\?\s+u:object_r:system_lib_file:s0$", re.M)
# The tablet's LineageOS vendor partition as lineage-hazirla.py reads it out of the zip.
LINEAGE_VENDOR = os.path.join(os.environ.get("GBOS_WORK", "/var/tmp/mica_work"), "lineage", "bolumler", "vendor")
VENDOR_POLICY = os.path.join(LINEAGE_VENDOR, "etc", "selinux", "vendor_sepolicy.cil")
BEKLENEN = os.path.join(SRC, "beklenen.txt")


class DogalYonHalHatasi(RuntimeError):
    pass


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def current_stamp():
    return hazir_ikili.stamp(SRC)


def outputs():
    return [src for h in HALS for src, _, _ in h.files[:2]]


def build():
    """Builds out/ unless it already holds the build of the current sources; in the tablet's kit
    (GBOS_KIT) a stale out/ is an error (yamalar/hazir_ikili.py)."""
    hazir_ikili.derle(SRC, STAMP, outputs(), "dogal-yon/hal")


def _vendor_dir(tmp):
    """The vendor files denetim.py needs: read out of the LineageOS zip on the computer
    (araclar/vendor-dosyalari.py); in the tablet's kit the copy guncelleyici/kit/kit-uret.py took
    from that same zip (vendor-kopya/: the tablet's /vendor is that LineageOS build, mica updates
    never change it, and the Linux environment may not read /vendor); else /vendor itself."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("gbos_vendor_dosyalari",
                                                  os.path.join(SRC, "araclar", "vendor-dosyalari.py"))
    vd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vd)
    if os.path.isfile(vd.ZIP):
        return vd.stage()
    kopya = os.path.join(SRC, "vendor-kopya")
    if os.path.isdir(kopya):
        missing = [p for p in vd.FILES if not os.path.isfile(os.path.join(kopya, os.path.basename(p)))]
        if missing:
            raise DogalYonHalHatasi(f"{kopya}: missing {missing}")
        return kopya
    if os.path.isdir("/vendor/lib64"):
        out = os.path.join(tmp, "vendor")
        os.makedirs(out)
        for path in vd.FILES:
            if not os.path.isfile("/vendor" + path):
                raise DogalYonHalHatasi(f"/vendor{path} missing on this device")
            os.symlink("/vendor" + path, os.path.join(out, os.path.basename(path)))
        return out
    raise DogalYonHalHatasi(f"neither the LineageOS zip ({vd.ZIP}) nor /vendor is available")


def uyum_denetle():
    """araclar/denetim.py on out/ against the LLNDK libraries of the mica release being patched
    (araclar/sistem-kutuphaneleri.py: mica-imaj/guncel) and the tablet's vendor libraries: every
    import resolves, nothing else is needed. out/ names no release (stamp.sh), so this runs on every
    install, also in the tablet's kit (GNU readelf there, hazir_ikili.readelf)."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="gbos-dogal-yon-uyum-", dir=os.environ.get("TMPDIR", "/var/tmp")) as tmp:
        vendor = _vendor_dir(tmp)
        readelf = hazir_ikili.readelf()
        lib, flat = os.path.join(tmp, "lib"), os.path.join(tmp, "out")
        os.makedirs(flat)
        subprocess.run([sys.executable, "-I", os.path.join(SRC, "araclar", "sistem-kutuphaneleri.py"), lib],
                       check=True, capture_output=True)
        for h in HALS:
            for src, rel, _ in h.files[:2]:
                os.symlink(src, os.path.join(flat, os.path.basename(rel)))
        sys.path.insert(0, TOOLS_DIR)
        from yamalar import dogal_yon
        r = subprocess.run([sys.executable, "-I", os.path.join(SRC, "araclar", "denetim.py"), readelf, flat,
                            vendor, lib, str(dogal_yon.PANEL_ROTATION)], capture_output=True, text=True)
    if r.returncode != 0:
        raise DogalYonHalHatasi("dogal-yon hal: out/ does not fit this release or vendor\n"
                                + r.stdout + r.stderr)
    return r.stdout.strip()


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


def service_problems(hal):
    """Our service runs like the vendor's: same class, user, groups, capabilities, I/O priority,
    task profiles and limits; the vendor service is stopped before its class starts and again
    whenever it is started. The only option it does not copy is the vendor's `interface` line:
    init refuses a second service for the same interface (service_parser.cpp ParseInterface),
    and servicemanager's on-demand start of the vendor service is caught by the second trigger."""
    with open(os.path.join(LINEAGE_VENDOR, "etc", "init", hal.vendor_rc)) as f:
        vendor, _ = parse_rc(f.read())
    with open(hal.rc) as f:
        ours, actions = parse_rc(f.read())
    problems = []
    if hal.vendor_service not in vendor:
        return [f"{hal.vendor_rc} no longer defines {hal.vendor_service}"]
    vargv, vopts = vendor[hal.vendor_service]
    if vargv != [hal.vendor_binary]:
        problems.append(f"{hal.vendor_service} runs {vargv}")
    if list(ours) != [hal.service]:
        return problems + [f"{hal.rc} defines {list(ours)}, expected only {hal.service}"]
    argv, opts = ours[hal.service]
    if argv != [f"/product/bin/hw/{hal.name}"]:
        problems.append(f"{hal.service} runs {argv}")
    want = sorted(o for o in vopts if not o.startswith("interface "))
    if sorted(opts) != want:
        problems.append(f"{hal.service} options {sorted(opts)} differ from {hal.vendor_service}'s {want}")
    if f"stop {hal.vendor_service}" not in actions.get("early-init", []):
        problems.append(f"{hal.rc} does not stop {hal.vendor_service} on early-init")
    if actions.get(f"property:init.svc.{hal.vendor_service}=running") != [
            f"stop {hal.vendor_service}", f"restart {hal.service}"]:
        problems.append(f"{hal.rc} does not stop {hal.vendor_service} again when it starts")
    return problems


def vendor_policy_problems():
    with open(VENDOR_POLICY) as f:
        cil = f.read()
    problems = []
    for h in HALS:
        if f"(type {h.exec_type})" not in cil:
            problems.append(f"{h.exec_type} is not declared")
        if not re.search(rf"\(typetransition init_\d+ {h.exec_type} process {h.domain}\)", cil):
            problems.append(f"no init transition {h.exec_type} -> {h.domain}")
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
    """Installs both layers and returns {layer: True} once check() has verified them."""
    for tree in (sys_dir, sysext_dir, prod_dir):
        if not os.path.isdir(tree):
            raise FileNotFoundError(tree)
    problems = vendor_policy_problems()
    for h in HALS:
        problems += service_problems(h)
    if problems:
        raise DogalYonHalHatasi("dogal-yon hal: " + "; ".join(problems))
    build()
    uyum_denetle()
    for h in HALS:
        for src, rel, mode in h.files:
            _install_file(src, os.path.join(prod_dir, rel), mode)
    os.chmod(os.path.join(prod_dir, LIBDIR), 0o755)
    _patch_file_contexts(os.path.join(prod_dir, PRODUCT_FILE_CONTEXTS))
    check(sys_dir, sysext_dir, prod_dir)
    for h in HALS:
        print(f"[+] dogal-yon hal: {h.service} ({h.name}) replaces {h.vendor_service}")
    return {h.layer: True for h in HALS}


def layer_problems(sys_dir, sysext_dir, prod_dir):
    """{layer: [problems]} for the trees as install() leaves them."""
    out = {h.layer: [] for h in HALS}
    fc_path = os.path.join(prod_dir, PRODUCT_FILE_CONTEXTS)
    fc = open(fc_path).read() if os.path.isfile(fc_path) else ""
    fc_ok = fc.count(FC_BEGIN) == 1 and FC_BEGIN + FC_LINES + FC_END in fc
    plat_path = os.path.join(sys_dir, PLAT_FILE_CONTEXTS)
    plat = open(plat_path).read() if os.path.isfile(plat_path) else ""
    rc_bodies = []
    for tree, base in ((sys_dir, "system/etc/init"), (sysext_dir, "etc/init"), (prod_dir, "etc/init")):
        d = os.path.join(tree, base)
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    with open(p, errors="replace") as f:
                        rc_bodies.append((os.path.relpath(p, tree), f.read()))
    for h in HALS:
        errors = out[h.layer]
        for src, rel, mode in h.files:
            p = os.path.join(prod_dir, rel)
            if not os.path.isfile(p):
                errors.append(f"missing product/{rel}")
                continue
            if not os.path.isfile(src) or sha256(p) != sha256(src):
                errors.append(f"product/{rel} differs from {os.path.relpath(src, TOOLS_DIR)}")
            if os.stat(p).st_mode & 0o7777 != mode:
                errors.append(f"product/{rel} mode {oct(os.stat(p).st_mode & 0o7777)}, expected {oct(mode)}")
        if not fc_ok:
            errors.append(f"product/{PRODUCT_FILE_CONTEXTS} lacks the dogal-yon-hal block")
        if not LIB_LABEL_RULE.search(plat):
            errors.append(f"{PLAT_FILE_CONTEXTS} no longer labels /product/lib64 system_lib_file")
        found = [rel for rel, body in rc_bodies if h.service in body or h.vendor_service in body]
        if found != [f"etc/init/{h.name}.rc"]:
            errors.append(f"{h.layer} services in {found}, expected only etc/init/{h.name}.rc")
    return out


def check(sys_dir, sysext_dir, prod_dir):
    problems = [p for ps in layer_problems(sys_dir, sysext_dir, prod_dir).values() for p in ps]
    if problems:
        raise DogalYonHalHatasi("dogal-yon hal: " + "; ".join(problems))


def politika(work=None):
    """Compiles the work tree's SELinux policy the way init does on the tablet (with its
    LineageOS vendor and odm policy) and checks dogal-yon/hal/beklenen.txt and the transitions.
    Returns the number of checked lines; raises on a mismatch."""
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
                raise DogalYonHalHatasi(f"{BEKLENEN}:{n}: expected 4 fields")
            tuples.append(fields)
            want.append(not deny)
            where.append(n)
    with tempfile.TemporaryDirectory(prefix="gbos-dogal-yon-", dir=os.environ.get("TMPDIR", "/var/tmp")) as tmp:
        lin = gbos_selinux._lineage_layout(work)
        inputs = gbos_selinux.init_inputs(os.path.join(work, "sys", "system"), os.path.join(work, "sysext"),
                                          os.path.join(work, "prod"), lin["vendor"], lin["odm"])
        policy = gbos_selinux.compile_policy(secilc, inputs, os.path.join(tmp, "gbos.pol"))
        verdicts = gbos_selinux.run_checker(checker, policy, tuples)
        cil = subprocess.run([checker, "--cil", policy], check=True, capture_output=True, text=True).stdout
    errors = [f"beklenen.txt:{n}: {' '.join(t)} -> {v}, expected {'ALLOW' if w else 'DENY'}"
              for t, w, n, v in zip(tuples, want, where, verdicts) if (v == "ALLOW") != w]
    for h in HALS:
        if f"(typetransition init {h.exec_type} process {h.domain})" not in cil:
            errors.append(f"compiled policy has no typetransition init {h.exec_type} process {h.domain}")
    if errors:
        raise DogalYonHalHatasi("dogal-yon hal politika: " + "; ".join(errors))
    return len(tuples) + len(HALS)


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
        for h in HALS:
            for src, rel, _ in h.files[:2]:
                print(sha256(src), rel)
    elif args.cmd == "install":
        install(args.sys_dir, args.sysext_dir, args.prod_dir)
    else:
        print(f"[+] dogal-yon hal politika: {politika(args.work)} checks hold")


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main())
