#!/usr/bin/env python3
"""SELinux policy additions of Googlebook OS on the SM-T870, and their verification.

apply_patches.py calls (importing it as "from yamalar import selinux as gbos_selinux", so that it
never shadows the system's libselinux Python module of the same name)
    gbos_selinux.uygula(SYS_DIR, SYSEXT_DIR, pristine_file)   # after patch_product()
    gbos_selinux.dogrula(WORK)                                 # raises if the result is unusable
uygula() rebuilds, from the pristine mica copies, everything this module owns:
  system/etc/selinux/plat_sepolicy.cil           + selinux/politika/plat/*.cil
  system_ext/etc/selinux/system_ext_sepolicy.cil + selinux/politika/system_ext/*.cil
  system_ext/etc/selinux/system_ext_service_contexts
                                                 + selinux/politika/system_ext/*.service_contexts
  system/etc/init/gbos-selinux.rc                written only in permissive mode, else removed

The single switch is the mode: SELINUX_KIP below, overridden per build by the environment
variable GBOS_SELINUX_KIP=enforcing|permissive. Permissive adds politika/plat-serbest/*.cil
(init may setenforce) and gbos-selinux.rc, which turns enforcing off in early-init; enforcing
leaves both out, so the kernel stays in the mode the policy load put it in (enforcing).

dogrula() compiles the policy the way init does on the device (system/core/init/selinux.cpp:
secilc -m -M true -G -N -c 30, plat + mapping + system_ext + product + the tablet's LineageOS
vendor/odm policy from $WORK/lineage/bolumler), then checks selinux/beklenen.txt and the ported service
labels, and replays every denial of the recorded boot logs against three policies: ours, mica's own and LineageOS 24's own. A denial ours still refuses
although mica allows it, or that LineageOS allowed a vendor domain, is an error unless
selinux/kabul.txt accepts it with a reason; the others are listed for information.

The denials come from selinux/reddedilenler.txt (one canonical line per denial, written by
`topla` from the device logs: selinux/kanit, acilis-loglari, cihaz-kabul/kanit/*/avc-ham.txt)
and from those logs themselves where they exist. The logs stay with the developer; the
collected file carries no path, process name or inode, so the replay is the same without them.

Command line (from T870-araclar):
    python3 yamalar/selinux.py prova      pristine mica + these additions in a temporary tree
    python3 yamalar/selinux.py dogrula [--work DIR]   the tree apply_patches.py produced
    python3 yamalar/selinux.py topla      rewrite selinux/reddedilenler.txt from the logs
"""
import argparse
import collections
import fnmatch
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

SELINUX_KIP = "enforcing"
KIPLER = ("enforcing", "permissive")

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELINUX_DIR = os.path.join(TOOLS_DIR, "selinux")
POLITIKA = os.path.join(SELINUX_DIR, "politika")
# The LineageOS partitions' etc/selinux, read out of the tablet's LineageOS zip by
# lineage-hazirla.py: $WORK/lineage/bolumler/<partition>/etc/selinux.
LINEAGE_PARTS = ("system", "system_ext", "product", "vendor", "odm")
KABUL = os.path.join(SELINUX_DIR, "kabul.txt")
BEKLENEN = os.path.join(SELINUX_DIR, "beklenen.txt")
REDDEDILENLER = os.path.join(SELINUX_DIR, "reddedilenler.txt")
LOG_GLOBS = [os.path.join(SELINUX_DIR, "kanit", "*.txt"),
               os.path.join(TOOLS_DIR, "acilis-loglari", "**", "*"),
               # every acceptance run's raw denials (cihaz-kabul/kabul.sh writes avc-ham.txt)
               os.path.join(TOOLS_DIR, "cihaz-kabul", "kanit", "*", "avc-ham.txt")]
KANIT_GLOBS = [REDDEDILENLER] + LOG_GLOBS


def log_files():
    return sorted({p for g in LOG_GLOBS for p in glob.glob(g, recursive=True) if os.path.isfile(p)})


def topla():
    """Writes selinux/reddedilenler.txt: every denial of the device logs plus the ones already
    in the file (a log may leave the developer's disk; its denials stay replayed)."""
    sys.path.insert(0, os.path.join(SELINUX_DIR, "araclar"))
    import avc  # selinux/araclar/avc.py
    old = [REDDEDILENLER] if os.path.isfile(REDDEDILENLER) else []
    lines = avc.canonical_lines(old + log_files())
    tmp = REDDEDILENLER + ".tmp"
    with open(tmp, "w") as f:
        f.write("# SELinux denials recorded on the tablet, replayed by yamalar/selinux.py dogrula.\n"
                "# Written by `python3 yamalar/selinux.py topla`; one line per denial.\n")
        f.writelines(lines)
    os.replace(tmp, REDDEDILENLER)
    return len(lines)
MICA_IMAGES = os.path.join(TOOLS_DIR, "mica-imaj", "guncel")
DUMP_EROFS = os.path.join(os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin")), "dump.erofs")
CACHE_BIN = os.path.join(os.environ.get("GBOS_SELINUX_CACHE", os.path.expanduser("~/.cache/gbos-selinux")), "bin")
MARKER = ";; --- GOOGLEBOOKOS_T870_PATCH: selinux/politika/{} ---\n"
INIT_RC = "system/etc/init/gbos-selinux.rc"

# init's SEPOLICY_VERSION (system/core/init/Android.bp: -DSEPOLICY_VERSION=30)
SEPOLICY_VERSION = "30"


def kip(value=None):
    value = value or os.environ.get("GBOS_SELINUX_KIP") or SELINUX_KIP
    if value not in KIPLER:
        raise ValueError(f"GBOS_SELINUX_KIP must be one of {KIPLER}, not {value!r}")
    return value


def _parts(subdir, pattern):
    paths = sorted(glob.glob(os.path.join(POLITIKA, subdir, pattern)))
    assert paths or subdir == "plat-serbest", f"no {pattern} in {POLITIKA}/{subdir}"
    out = []
    for p in paths:
        with open(p) as f:
            body = f.read()
        out.append(MARKER.format(os.path.relpath(p, POLITIKA)) + body.rstrip("\n") + "\n")
    return out


def plat_cil(pristine, mode):
    parts = _parts("plat", "*.cil")
    if mode == "permissive":
        parts += _parts("plat-serbest", "*.cil")
    return pristine.rstrip("\n") + "\n\n" + "".join(parts)


def system_ext_cil(pristine):
    return pristine.rstrip("\n") + "\n\n" + "".join(_parts("system_ext", "*.cil"))


def system_ext_service_contexts(pristine):
    extra = "".join(p.replace(";;", "#", 1) for p in _parts("system_ext", "*.service_contexts"))
    return pristine.rstrip("\n") + "\n\n" + extra


def init_rc(mode):
    if mode != "permissive":
        return None
    return ("# Googlebook OS on SM-T870: SELinux permissive (yamalar/selinux.py, GBOS_SELINUX_KIP)\n"
            "on early-init\n"
            "    write /sys/fs/selinux/enforce 0\n")


def uygula(sys_dir, sysext_dir, pristine_file, mode=None):
    """Writes the SELinux files of this module into the work tree; returns the mode used."""
    mode = kip(mode)

    def rewrite(root, rel, image, transform):
        text = pristine_file(rel, image).decode()
        with open(os.path.join(root, rel), "w") as f:
            f.write(transform(text))

    rewrite(sys_dir, "system/etc/selinux/plat_sepolicy.cil", "system_a.erofs",
            lambda t: plat_cil(t, mode))
    rewrite(sysext_dir, "etc/selinux/system_ext_sepolicy.cil", "system_ext_a.erofs", system_ext_cil)
    rewrite(sysext_dir, "etc/selinux/system_ext_service_contexts", "system_ext_a.erofs",
            system_ext_service_contexts)

    rc_path = os.path.join(sys_dir, INIT_RC)
    rc = init_rc(mode)
    if rc is None:
        if os.path.exists(rc_path):
            os.remove(rc_path)
    else:
        with open(rc_path, "w") as f:
            f.write(rc)
    print(f"[+] SELinux: {mode} (policy additions from {os.path.relpath(POLITIKA, TOOLS_DIR)})")
    return mode


# ---------------------------------------------------------------------------------------------
# Verification


class DogrulamaHatasi(Exception):
    pass


def _tools():
    # kur.sh returns at once when the tools are current, and (re)builds them otherwise.
    subprocess.run(["bash", os.path.join(SELINUX_DIR, "araclar", "kur.sh")], check=True)
    return os.path.join(CACHE_BIN, "secilc"), os.path.join(CACHE_BIN, "avc_kontrol")


def _first_line(path):
    with open(path) as f:
        return f.readline().strip()


def init_inputs(system, system_ext, product, vendor, odm):
    """secilc argument list in the order init builds it (OpenSplitPolicy in selinux.cpp).

    Each argument is the root of a partition as mounted on the device (system = the root of
    /system, i.e. the directory holding etc/selinux)."""
    vers = _first_line(os.path.join(vendor, "etc/selinux/plat_sepolicy_vers.txt"))
    sel = lambda root, rel: os.path.join(root, "etc/selinux", rel)
    args = [sel(system, "plat_sepolicy.cil"), "-m", "-M", "true", "-G", "-N", "-c", SEPOLICY_VERSION,
            sel(system, f"mapping/{vers}.cil")]
    optional = [sel(system, f"mapping/{vers}.compat.cil"),
                sel(system_ext, "system_ext_sepolicy.cil"), sel(system_ext, f"mapping/{vers}.cil"),
                sel(system_ext, f"mapping/{vers}.compat.cil"),
                sel(product, "product_sepolicy.cil"), sel(product, f"mapping/{vers}.cil"),
                sel(vendor, "plat_pub_versioned.cil"), sel(vendor, "vendor_sepolicy.cil"),
                sel(odm, "odm_sepolicy.cil")]
    args += [p for p in optional if os.path.exists(p)]
    genfs_vers = _first_line(os.path.join(vendor, "etc/selinux/genfs_labels_version.txt"))
    genfs = sel(system, f"plat_sepolicy_genfs_{genfs_vers}.cil")
    if os.path.exists(genfs):
        args.append(genfs)
    return args


def compile_policy(secilc, inputs, out):
    cmd = [secilc] + inputs + ["-o", out, "-f", os.devnull]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise DogrulamaHatasi("secilc failed:\n" + (r.stderr or r.stdout)[-4000:])
    return out


def _lineage_layout(work=None):
    """The LineageOS partition roots of a work tree ($WORK/lineage/bolumler/<partition>, each
    holding that partition's etc/selinux)."""
    work = work or os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
    roots = {part: os.path.join(work, "lineage", "bolumler", part) for part in LINEAGE_PARTS}
    missing = [r for r in roots.values() if not os.path.isdir(os.path.join(r, "etc", "selinux"))]
    if missing:
        raise DogrulamaHatasi(f"{missing[0]}/etc/selinux missing: build the LineageOS inputs with "
                              "lineage-hazirla.py (the zip the tablet was flashed with)")
    return roots


def _erofs_selinux(image, path, dest):
    """Copies the regular files of a directory (and its mapping/) out of a pristine erofs image."""
    for sub in ("", "/mapping"):
        listing = subprocess.run([DUMP_EROFS, "--ls", f"--path={path}{sub}", image],
                                 capture_output=True, text=True)
        if listing.returncode != 0:
            continue
        os.makedirs(dest + sub, exist_ok=True)
        for line in listing.stdout.splitlines():
            f = line.split()
            if len(f) == 3 and f[0].isdigit() and f[1] == "1":
                with open(os.path.join(dest + sub, f[2]), "wb") as out:
                    subprocess.run([DUMP_EROFS, "--cat", f"--path={path}{sub}/{f[2]}", image],
                                   check=True, stdout=out)


def mica_tree(tmp):
    """Pristine mica selinux directories laid out as partition roots."""
    roots = {}
    for part, image, path in [("system", "system_a.erofs", "/system/etc/selinux"),
                              ("system_ext", "system_ext_a.erofs", "/etc/selinux"),
                              ("product", "product_a.erofs", "/etc/selinux"),
                              ("vendor", "vendor_a.erofs", "/etc/selinux"),
                              ("odm", "vendor_a.erofs", "/odm/etc/selinux")]:
        root = os.path.join(tmp, "mica-" + part)
        _erofs_selinux(os.path.join(MICA_IMAGES, image), path, os.path.join(root, "etc", "selinux"))
        roots[part] = root
    return roots


def read_contexts(paths):
    out = []
    for p in paths:
        if os.path.exists(p):
            with open(p, errors="replace") as f:
                for line in f:
                    fields = line.split("#", 1)[0].split()
                    if len(fields) >= 2:
                        out.append((fields[0], fields[1], p))
    return out


def check_service_labels(system, system_ext, product, vendor, odm):
    """Every ported name must have exactly one label across all service_contexts files."""
    files = [os.path.join(system, "etc/selinux/plat_service_contexts"),
             os.path.join(system_ext, "etc/selinux/system_ext_service_contexts"),
             os.path.join(product, "etc/selinux/product_service_contexts"),
             os.path.join(vendor, "etc/selinux/vendor_service_contexts"),
             os.path.join(odm, "etc/selinux/odm_service_contexts")]
    labels = collections.defaultdict(set)
    for name, ctx, _ in read_contexts(files):
        labels[name].add(ctx)
    ported = read_contexts([os.path.join(POLITIKA, "system_ext", "mica-servisleri.service_contexts")])
    errors = []
    for name, ctx, _ in ported:
        if labels.get(name) != {ctx}:
            errors.append(f"{name}: expected {ctx}, the tree has {sorted(labels.get(name, []))}")
    return len(ported), errors


def run_checker(checker, policy, tuples):
    stdin = "".join(" ".join(t) + "\n" for t in tuples)
    out = subprocess.run([checker, policy], input=stdin, capture_output=True, text=True, check=True)
    return [line.split()[0] for line in out.stdout.splitlines()]


def check_expected(checker, policy, mode):
    """selinux/beklenen.txt: '[-]scontext tcontext class perm' (a leading '-' = must be denied);
    lines tagged 'kip=enforcing' or 'kip=permissive' only apply in that mode."""
    tuples, want, where = [], [], []
    with open(BEKLENEN) as f:
        for n, line in enumerate(f, 1):
            body, _, comment = line.partition("#")
            fields = body.split()
            if not fields:
                continue
            tag = re.search(r"\bkip=(\w+)", comment)
            if tag and tag.group(1) != mode:
                continue
            deny = fields[0].startswith("-")
            fields[0] = fields[0].lstrip("-")
            if len(fields) != 4:
                raise DogrulamaHatasi(f"{BEKLENEN}:{n}: expected 4 fields")
            tuples.append(fields)
            want.append(not deny)
            where.append(n)
    verdicts = run_checker(checker, policy, tuples)
    errors = []
    for t, w, n, v in zip(tuples, want, where, verdicts):
        if (v == "ALLOW") != w:
            errors.append(f"beklenen.txt:{n}: {' '.join(t)} -> {v}, expected {'ALLOW' if w else 'DENY'}")
    return len(tuples), errors


class Kabul:
    """selinux/kabul.txt: 'stype ttype class perm  # reason' lines (shell patterns allowed in the
    four fields) accepting a refusal that dogrula would otherwise report as an error."""

    def __init__(self, path=KABUL):
        self.rules = []
        with open(path) as f:
            for n, line in enumerate(f, 1):
                body, _, reason = line.partition("#")
                fields = body.split()
                if not fields:
                    continue
                if len(fields) != 4 or not reason.strip():
                    raise DogrulamaHatasi(f"kabul.txt:{n}: needs 'stype ttype class perm  # reason'")
                self.rules.append(tuple(fields))

    def __contains__(self, key):
        return any(all(fnmatch.fnmatchcase(k, p) for k, p in zip(key, rule)) for rule in self.rules)


def cil_permissive_types(paths):
    out = set()
    for p in paths:
        if p.endswith(".cil"):
            with open(p) as f:
                out.update(re.findall(r"^\(typepermissive (\S+)\)$", f.read(), re.M))
    return out


def evidence_report(checker, policies, coredomain, permissive, service_files):
    """Replays the denials of the recorded boots against our, mica's and LineageOS's policy.

    service_files maps each policy name to its service_contexts files: a service_manager denial
    is asked of every policy with the label that platform gives the service name (mica labels
    names in its vendor that fall to default_android_service here, and the other way round).
    Returns (n_logs, n_vectors, groups) where groups maps (class, stype, ttype, tclass) to the
    refused permissions, ttype being our label. class is one of
      "MICA IZIN VERIYOR"    mica's policy allows it, ours does not
      "LINEAGE IZIN VERIYOR" a vendor domain LineageOS allowed, ours does not
      "lineage platform"     LineageOS's platform allowed a platform domain (userdebug, Lineage
                             features); mica does not
      "ikisi de reddediyor"  neither mica nor LineageOS allows it either
      "serbest alan"         the source is a permissive domain (su ...): logged, never refused"""
    sys.path.insert(0, os.path.join(SELINUX_DIR, "araclar"))
    import avc  # selinux/araclar/avc.py

    logs = sorted({p for g in KANIT_GLOBS for p in glob.glob(g, recursive=True) if os.path.isfile(p)})
    labels = {k: avc.load_service_contexts(files) for k, files in service_files.items()}
    queries = set()  # (scon, tcon as logged, tclass, perm, service name or "")
    for (scon, tcon, tclass, perm), names in avc.parse_logs(logs).items():
        for name in names:
            queries.add((scon, tcon, tclass, perm, name or ""))
    queries = sorted(queries)

    def target(policy, q):
        if not q[4]:
            return q[1]
        exact, prefix = labels[policy]
        return avc.service_label(q[4], exact, prefix) or q[1]

    verdicts = {k: run_checker(checker, pol, [(q[0], target(k, q), q[2], q[3]) for q in queries])
                for k, pol in policies.items()}
    groups = collections.OrderedDict()
    for i, q in enumerate(queries):
        if verdicts["ours"][i] in ("ALLOW", "DENY-DONTAUDIT"):
            continue  # allowed, or refused by an explicit dontaudit decision of our policy
        stype = q[0].split(":")[2]
        if all(verdicts[k][i] == "UNKNOWN" for k in verdicts):
            continue  # no policy knows these names: a corrupted pstore/last_kmsg line
        if stype in permissive:
            cls = "serbest alan"
        elif verdicts["mica"][i] == "ALLOW":
            cls = "MICA IZIN VERIYOR"
        elif verdicts["lineage"][i] == "ALLOW":
            cls = "lineage platform" if stype in coredomain else "LINEAGE IZIN VERIYOR"
        else:
            cls = "ikisi de reddediyor"
        groups.setdefault((cls, stype, target("ours", q).split(":")[2], q[2]), set()).add(q[3])
    return len(logs), len(queries), groups


def cil_attribute_members(paths, attr):
    """Types a CIL source puts into attr with plain (typeattributeset attr (t ...)) lists."""
    rx = re.compile(r"^\(typeattributeset " + re.escape(attr) + r" \(([^()]*)\)\)$", re.M)
    members = set()
    for p in paths:
        if p.endswith(".cil"):
            with open(p) as f:
                for m in rx.finditer(f.read()):
                    members.update(m.group(1).split())
    return members


def dogrula(work=None, mode=None, verbose=True):
    """Compiles and checks the policy of a work tree (sys/, sysext/, prod/ as apply_patches.py
    lays them out). Raises DogrulamaHatasi when it does not compile or a check fails."""
    work = work or os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
    secilc, checker = _tools()
    system = os.path.join(work, "sys", "system")
    system_ext = os.path.join(work, "sysext")
    product = os.path.join(work, "prod")
    if mode is None:
        mode = "permissive" if os.path.exists(os.path.join(work, "sys", INIT_RC)) else "enforcing"
    with tempfile.TemporaryDirectory(prefix="gbos-selinux-", dir=os.environ.get("TMPDIR")) as tmp:
        lin = _lineage_layout(work)
        inputs = init_inputs(system, system_ext, product, lin["vendor"], lin["odm"])
        ours = compile_policy(secilc, inputs, os.path.join(tmp, "gbos.pol"))
        if verbose:
            print(f"[+] secilc ({len(inputs) - 7} files, as init): {os.path.getsize(ours)} bytes")

        errors = []
        n, e = check_expected(checker, ours, mode)
        errors += e
        if verbose:
            print(f"[+] beklenen.txt: {n - len(e)}/{n} as expected ({mode})")
        n, e = check_service_labels(system, system_ext, product, lin["vendor"], lin["odm"])
        errors += e
        if verbose:
            print(f"[+] ported service labels: {n - len(e)}/{n} unique and as generated")

        # The compiler must be the device's: LineageOS's own policy, compiled here, has to equal the
        # precompiled_sepolicy its build put on odm.
        precompiled = os.path.join(lin["odm"], "etc", "selinux", "precompiled_sepolicy")
        if os.path.exists(precompiled):
            lineage_pol = compile_policy(secilc, init_inputs(lin["system"], lin["system_ext"], lin["product"],
                                                             lin["vendor"], lin["odm"]), os.path.join(tmp, "lineage.pol"))
            with open(lineage_pol, "rb") as a, open(precompiled, "rb") as b:
                same = a.read() == b.read()
            if not same:
                errors.append("secilc does not reproduce LineageOS's odm/etc/selinux/precompiled_sepolicy")
            if verbose:
                print(f"[+] toolchain: LineageOS policy compiled here {'==' if same else '!='} its precompiled_sepolicy")

        m = mica_tree(tmp)
        policies = {
            "ours": ours,
            "mica": compile_policy(secilc, init_inputs(m["system"], m["system_ext"], m["product"],
                                                       m["vendor"], m["odm"]), os.path.join(tmp, "mica.pol")),
            "lineage": os.path.join(tmp, "lineage.pol") if os.path.exists(precompiled) else
                       compile_policy(secilc, init_inputs(lin["system"], lin["system_ext"], lin["product"],
                                                          lin["vendor"], lin["odm"]), os.path.join(tmp, "lineage.pol")),
        }
        def contexts(sys_root, ext, prod, vend, odm):
            return [os.path.join(sys_root, "etc/selinux/plat_service_contexts"),
                    os.path.join(ext, "etc/selinux/system_ext_service_contexts"),
                    os.path.join(prod, "etc/selinux/product_service_contexts"),
                    os.path.join(vend, "etc/selinux/vendor_service_contexts"),
                    os.path.join(odm, "etc/selinux/odm_service_contexts")]
        service_files = {
            "ours": contexts(system, system_ext, product, lin["vendor"], lin["odm"]),
            "mica": contexts(m["system"], m["system_ext"], m["product"], m["vendor"], m["odm"]),
            "lineage": contexts(lin["system"], lin["system_ext"], lin["product"], lin["vendor"], lin["odm"]),
        }
        coredomain = cil_attribute_members(inputs, "coredomain")
        permissive = cil_permissive_types(inputs)
        n_logs, n_q, groups = evidence_report(checker, policies, coredomain, permissive, service_files)
        accepted = Kabul()
        must_fix = ("MICA IZIN VERIYOR", "LINEAGE IZIN VERIYOR")
        counts = collections.Counter(k[0] for k in groups)
        if verbose:
            print(f"[+] {n_logs} boot logs, {n_q} distinct access vectors; still refused: "
                  + ", ".join(f"{k}={counts[k]}" for k in must_fix + ("lineage platform", "ikisi de reddediyor", "serbest alan")))
        unreviewed = 0
        for (cls, st, tt, c), perms in groups.items():
            open_perms = sorted(p for p in perms if (st, tt, c, p) not in accepted)
            if cls in must_fix and open_perms:
                errors.append(f"{cls}: {st} {tt}:{c} {{ {' '.join(open_perms)} }}")
            elif cls == "ikisi de reddediyor" and open_perms:
                # Not a regression, but every denial gets a recorded decision in kabul.txt.
                unreviewed += 1
                if verbose:
                    print(f"    INCELENMEDI          {st} {tt}:{c} {{ {' '.join(open_perms)} }}")
            if verbose and (open_perms if cls in must_fix else
                            os.environ.get("GBOS_SELINUX_AYRINTI") and cls != "ikisi de reddediyor"):
                mark = "" if cls not in must_fix or open_perms else " (kabul.txt)"
                print(f"    {cls:20} {st} {tt}:{c} {{ {' '.join(sorted(perms))} }}{mark}")

        if verbose:
            print(f"[+] denials both mica and LineageOS refuse too: {counts['ikisi de reddediyor']} groups, "
                  f"{unreviewed} without a decision in kabul.txt")

        # Static: everything LineageOS's policy let the vendor domains do must still be allowed.
        sys.path.insert(0, os.path.join(SELINUX_DIR, "araclar"))
        import avc
        lost = avc.vendor_domain_losses(checker, policies["lineage"], ours)
        open_lost = {}
        for (st, tt, c), perms in lost.items():
            left = sorted(p for p in perms if (st, tt, c, p) not in accepted)
            if left:
                open_lost[(st, tt, c)] = left
        for (st, tt, c), perms in sorted(open_lost.items()):
            errors.append(f"LINEAGE IZIN VERIYORDU: {st} {tt}:{c} {{ {' '.join(perms)} }}")
        if verbose:
            print(f"[+] vendor domains vs LineageOS: {len(lost)} (source, target, class) with fewer "
                  f"permissions, {len(open_lost)} not accepted in kabul.txt")
    if errors:
        raise DogrulamaHatasi("\n".join(errors))
    return True


def prova(mode=None):
    """uygula() on pristine mica selinux files in a temporary tree, then dogrula() on it."""
    mode = kip(mode)

    def pristine(rel, image="system_a.erofs"):
        return subprocess.run([DUMP_EROFS, "--cat", f"--path=/{rel}", os.path.join(MICA_IMAGES, image)],
                              check=True, stdout=subprocess.PIPE).stdout

    with tempfile.TemporaryDirectory(prefix="gbos-selinux-prova-", dir=os.environ.get("TMPDIR")) as work:
        for part, image, path in [("sys/system", "system_a.erofs", "/system/etc/selinux"),
                                  ("sysext", "system_ext_a.erofs", "/etc/selinux"),
                                  ("prod", "product_a.erofs", "/etc/selinux")]:
            _erofs_selinux(os.path.join(MICA_IMAGES, image), path, os.path.join(work, part, "etc", "selinux"))
        os.makedirs(os.path.join(work, "sys/system/etc/init"))
        uygula(os.path.join(work, "sys"), os.path.join(work, "sysext"), pristine, mode)
        return dogrula(work, mode)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prova", help="pristine mica + additions in a temporary tree, then verify")
    p.add_argument("--kip", choices=KIPLER)
    d = sub.add_parser("dogrula", help="verify a work tree produced by apply_patches.py")
    d.add_argument("--work")
    d.add_argument("--kip", choices=KIPLER, help="default: permissive if gbos-selinux.rc exists")
    sub.add_parser("topla", help="rewrite selinux/reddedilenler.txt from the device logs")
    args = ap.parse_args()
    if args.cmd == "topla":
        print(f"[+] {topla()} denials in {os.path.relpath(REDDEDILENLER, TOOLS_DIR)}")
        return 0
    try:
        if args.cmd == "prova":
            prova(args.kip)
        else:
            dogrula(args.work, args.kip)
    except DogrulamaHatasi as e:
        print(f"[!] SELinux verification failed:\n{e}", file=sys.stderr)
        return 1
    print("[+] SELinux verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
