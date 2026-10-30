#!/usr/bin/env python3
"""Ports the binder service labels that the Googlebook system side expects from mica's vendor.

    mica-servis-aktar.py [--mica DIR] [--lineage DIR] [--cikti DIR]

On mica, vendor_service_contexts labels the desktop services that system_server registers
(desktop_net, IDesktopServiceabilityService, IVideoChatManager ...) and the vendor services the
framework and Googlebook apps look up (IGoogleBattery, IDesktopPowerChargeControl ...). The
tablet keeps the LineageOS vendor, so those names fall to default_android_service: system_server
may not register them, and isDeclared()/waitForService() on them fail with EX_SECURITY instead
of reporting "not declared", which a Java caller sees as a SecurityException.

Every name in mica's vendor_service_contexts that neither mica's system partitions nor the
LineageOS vendor/odm label is ported, so the platform sees exactly mica's label for it. (All of
them are reachable from the platform: system_app, shell and dumpstate may find any
service_manager_type, and the Dell serviceability app, a system_app here, looks up mica-only
executors such as IEcExecutor.) For them this writes
  politika/system_ext/mica-servisleri.service_contexts  the name -> label lines, and
  politika/system_ext/50-mica-servisleri.cil               the type, its platform attributes and every
                                             service_manager rule mica's vendor policy has on it
                                             with a platform source (versioned names such as
                                             system_server_202604 become system_server, so the
                                             rules do not depend on the vendor's policy version).
Rules whose source is a mica vendor domain are dropped: those processes do not exist here. The
output is deterministic; rerun it when mica-imaj/guncel or the LineageOS build changes.
"""
import argparse
import collections
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SELINUX = os.path.dirname(HERE)
TOOLS = os.path.dirname(SELINUX)
DUMP_EROFS = os.path.join(os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin")), "dump.erofs")

RULE_RE = re.compile(r"^\((allow|dontaudit|auditallow) (\S+) (\S+) \(service_manager \(([^)]*)\)\)\)$", re.M)
ATTRSET_RE = re.compile(r"^\(typeattributeset (\S+) \(([^()]*)\)\)$", re.M)
DECL_RE = re.compile(r"^\((?:type|typeattribute) (\S+)\)$", re.M)
VERSIONED_RE = re.compile(r"_(\d{6}|\d+_0)$")


def erofs_cat(image, path):
    return subprocess.run([DUMP_EROFS, "--cat", f"--path={path}", image],
                          check=True, capture_output=True).stdout.decode()


def service_contexts(text):
    out = collections.OrderedDict()
    for line in text.splitlines():
        f = line.split("#", 1)[0].split()
        if len(f) >= 2 and f[0] != "*":
            out.setdefault(f[0], f[1])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mica", default=os.path.join(TOOLS, "mica-imaj", "guncel"))
    ap.add_argument("--lineage", default=os.path.join(os.environ.get("GBOS_WORK", "/var/tmp/mica_work"),
                                                      "lineage", "bolumler"),
                    help="LineageOS partitions as lineage-hazirla.py writes them")
    ap.add_argument("--cikti", default=os.path.join(SELINUX, "politika", "system_ext"))
    args = ap.parse_args()

    img = lambda name: os.path.join(args.mica, name)
    vendor_cil = erofs_cat(img("vendor_a.erofs"), "/etc/selinux/vendor_sepolicy.cil")
    vendor_sc = service_contexts(erofs_cat(img("vendor_a.erofs"), "/etc/selinux/vendor_service_contexts"))
    platform_cil = "\n".join([
        erofs_cat(img("system_a.erofs"), "/system/etc/selinux/plat_sepolicy.cil"),
        erofs_cat(img("system_ext_a.erofs"), "/etc/selinux/system_ext_sepolicy.cil"),
        erofs_cat(img("product_a.erofs"), "/etc/selinux/product_sepolicy.cil"),
    ])
    labelled = set()
    for image, path in [("system_a.erofs", "/system/etc/selinux/plat_service_contexts"),
                        ("system_ext_a.erofs", "/etc/selinux/system_ext_service_contexts"),
                        ("product_a.erofs", "/etc/selinux/product_service_contexts")]:
        labelled |= set(service_contexts(erofs_cat(img(image), path)))
    for part, name in [("vendor", "vendor_service_contexts"), ("odm", "odm_service_contexts")]:
        with open(os.path.join(args.lineage, part, "etc", "selinux", name)) as f:
            labelled |= set(service_contexts(f.read()))

    platform_names = set(DECL_RE.findall(platform_cil))

    def platform(name):
        """The platform type/attribute a vendor-policy name refers to, or None if vendor-only."""
        base = VERSIONED_RE.sub("", name)
        if base != name:
            return base if base in platform_names else None
        return name if name in platform_names else None

    rules = collections.defaultdict(list)  # type -> [(kind, platform source, perms)]
    for kind, src, tgt, perms in RULE_RE.findall(vendor_cil):
        p = platform(src)
        if p:
            rules[tgt].append((kind, p, " ".join(perms.split())))
    attrs = collections.defaultdict(set)  # type -> platform attributes mica's vendor gives it
    for attr, members in ATTRSET_RE.findall(vendor_cil):
        if attr in platform_names:
            for t in members.split():
                attrs[t].add(attr)

    ported = collections.OrderedDict()  # type -> [names]
    for name, ctx in vendor_sc.items():
        t = ctx.split(":")[2]
        if name in labelled:
            continue
        ported.setdefault(t, []).append(name)

    os.makedirs(args.cikti, exist_ok=True)
    head = ("generated by selinux/araclar/mica-servis-aktar.py from mica-imaj/guncel "
            "(vendor_sepolicy.cil, vendor_service_contexts); do not edit by hand")
    with open(os.path.join(args.cikti, "mica-servisleri.service_contexts"), "w") as f:
        f.write(f"# {head}\n")
        for t, names in ported.items():
            for name in names:
                f.write(f"{name} u:object_r:{t}:s0\n")
    with open(os.path.join(args.cikti, "50-mica-servisleri.cil"), "w") as f:
        f.write(f";; {head}\n")
        for t, names in ported.items():
            f.write(f"\n;; {', '.join(names)}\n")
            if t not in platform_names:
                f.write(f"(type {t})\n(roletype object_r {t})\n")
                for a in sorted(attrs[t]):
                    f.write(f"(typeattributeset {a} ({t}))\n")
            seen = set()
            for kind, src, perms in rules[t]:
                line = f"({kind} {src} {t} (service_manager ({perms})))"
                if line not in seen:
                    seen.add(line)
                    f.write(line + "\n")
    n_names = sum(len(v) for v in ported.values())
    print(f"{len(ported)} types, {n_names} service names -> {args.cikti}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
