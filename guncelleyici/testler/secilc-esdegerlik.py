#!/usr/bin/env python3
"""
Cross-device acceptance test of the SELinux tools yamalar/selinux.py runs (secilc, avc_kontrol from
selinux/araclar/kur.sh, AOSP external/selinux android17-release) on the tablet's Linux environment
(aarch64 Debian) against the computer's (x86_64):

  python3 -I guncelleyici/testler/secilc-esdegerlik.py --kanit DIR [--mica-work /var/tmp/mica_work]

On the tablet kur.sh builds both tools from scratch into a temporary cache. Then, on both machines,
with the argument lists init builds (yamalar/selinux.init_inputs):
  - LineageOS's policy ($GBOS_WORK/lineage/bolumler, lineage-hazirla.py) is compiled; the result must equal the
    odm/precompiled_sepolicy LineageOS's build produced (selinux.py's own toolchain check);
  - with --mica-work, the policy of that work tree (sys/, sysext/, prod/ + LineageOS vendor/odm,
    as selinux.dogrula compiles it) is compiled; the tablet's must equal the computer's;
  - avc_kontrol answers selinux/beklenen.txt's tuples and dumps each policy as CIL; the answers and
    the dumps must be the same on both machines.
Debian's own secilc 3.8.1 (apt-get download, not installed) compiles the same inputs for comparison;
the pipeline uses AOSP's, the result tells whether Debian's could replace it.
Writes DIR/secilc.json. Exits non-zero when a required check fails.
"""
import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, KIT)
from t870_cihaz import seri  # noqa: E402
TABLET_DIR = "/var/tmp/gbos-secilc-esdegerlik"
LOCK = "/var/tmp/gbos-dogal-yon/TABLET-ANA-OTURUMDA"

# Runs on both machines inside the staged directory; prints one JSON object.
SIDE = r'''
import hashlib, json, os, subprocess, sys
stage, secilc, checker = sys.argv[1:4]
debian = sys.argv[4] if len(sys.argv) > 4 else None
with open(os.path.join(stage, "girdi.json")) as f:
    spec = json.load(f)
os.chdir(stage)
def sha(b): return hashlib.sha256(b).hexdigest()
out = {}
for name, args in spec["policies"].items():
    pol = os.path.join(stage, name + ".pol")
    r = subprocess.run([secilc, *args, "-o", pol, "-f", os.devnull], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"secilc {name}: {r.stderr[-2000:]}")
    with open(pol, "rb") as f:
        out[name] = sha(f.read())
    cil = subprocess.run([checker, "--cil", pol], capture_output=True, check=True).stdout
    out[name + ".cil"] = sha(cil)
    ans = subprocess.run([checker, pol], input=spec["tuples"], capture_output=True, text=True, check=True).stdout
    out[name + ".avc"] = sha(ans.encode())
    out[name + ".avc_ozet"] = {v: ans.split().count(v) for v in sorted(set(l.split()[0] for l in ans.splitlines()))}
    os.remove(pol)
if debian:
    lib, exe = debian.split(":")
    for name, args in spec["policies"].items():
        pol = os.path.join(stage, name + ".debian.pol")
        r = subprocess.run([exe, *args, "-o", pol, "-f", os.devnull], capture_output=True, text=True,
                           env=dict(os.environ, LD_LIBRARY_PATH=lib))
        if r.returncode:
            out[name + ".debian"] = "HATA: " + r.stderr[-500:]
            continue
        with open(pol, "rb") as f:
            out[name + ".debian"] = sha(f.read())
        os.remove(pol)
print(json.dumps(out))
'''


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def stage_inputs(base, mica_work):
    sys.path.insert(0, KIT)
    from yamalar import selinux as gs
    lineage = gs._lineage_layout()
    roots = {}
    def put(name, src):
        dest = os.path.join(base, name, "etc", "selinux")
        shutil.copytree(src, dest, symlinks=False)
        roots[name] = name
    for part in ("system", "system_ext", "product", "vendor", "odm"):
        put("lineage-" + part, os.path.join(lineage[part], "etc", "selinux"))
    cwd = os.getcwd()
    os.chdir(base)
    try:
        policies = {"lineage": gs.init_inputs(*(roots["lineage-" + p] for p in
                                                ("system", "system_ext", "product", "vendor", "odm")))}
    finally:
        os.chdir(cwd)
    if mica_work:
        put("ours-system", os.path.join(mica_work, "sys", "system", "etc", "selinux"))
        put("ours-system_ext", os.path.join(mica_work, "sysext", "etc", "selinux"))
        put("ours-product", os.path.join(mica_work, "prod", "etc", "selinux"))
        os.chdir(base)
        try:
            policies["ours"] = gs.init_inputs("ours-system", "ours-system_ext", "ours-product",
                                              roots["lineage-vendor"], roots["lineage-odm"])
        finally:
            os.chdir(cwd)
    tuples = []
    with open(gs.BEKLENEN) as f:
        for line in f:
            fields = line.partition("#")[0].split()
            if len(fields) == 4:
                fields[0] = fields[0].lstrip("-")
                tuples.append(" ".join(fields) + "\n")
    with open(os.path.join(base, "girdi.json"), "w") as f:
        json.dump({"policies": policies, "tuples": "".join(tuples)}, f)
    with open(os.path.join(base, "side.py"), "w") as f:
        f.write(SIDE)
    return sha256_file(os.path.join(lineage["odm"], "etc", "selinux", "precompiled_sepolicy")), len(tuples)


def adb_chroot(script, stdin=None):
    if os.path.exists(LOCK):
        raise SystemExit(f"{LOCK} var: tablet ana oturumda, ölçüm yapılmadı")
    return subprocess.run(["adb", "-s", seri(), "shell", "gbos-linux run -- sh -c " + shlex.quote(script)],
                          input=stdin, capture_output=True, check=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kanit", required=True)
    ap.add_argument("--mica-work")
    args = ap.parse_args()
    os.makedirs(args.kanit, exist_ok=True)
    base = tempfile.mkdtemp(prefix="gbos-secilc-esdegerlik-", dir="/var/tmp")
    try:
        stage = os.path.join(base, "stage")
        os.makedirs(stage)
        precompiled, n_tuples = stage_inputs(stage, args.mica_work)
        os.makedirs(os.path.join(base, "selinux", "araclar"))
        for f in ("kur.sh", "avc_kontrol.c"):
            shutil.copy2(os.path.join(KIT, "selinux", "araclar", f), os.path.join(base, "selinux", "araclar", f))

        # computer: kur.sh as yamalar/selinux.py runs it (its usual cache)
        subprocess.run([os.path.join(KIT, "selinux", "araclar", "kur.sh")], check=True)
        cache = os.path.join(os.environ.get("GBOS_SELINUX_CACHE", os.path.expanduser("~/.cache/gbos-selinux")), "bin")
        r = subprocess.run([sys.executable, "-I", os.path.join(stage, "side.py"), stage,
                            os.path.join(cache, "secilc"), os.path.join(cache, "avc_kontrol")],
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit("computer: " + r.stderr[-3000:])
        pc = json.loads(r.stdout)

        # tablet: kur.sh from scratch into a temporary cache, Debian's secilc unpacked beside it
        tar = subprocess.run(["tar", "-cf", "-", "-C", base, "stage", "selinux"], capture_output=True,
                             check=True).stdout
        t = TABLET_DIR
        adb_chroot(f"rm -rf {t}")
        r = adb_chroot(f"mkdir -p {t} && tar -xf - -C {t}", stdin=tar)
        if r.returncode:
            raise SystemExit(r.stderr.decode())
        script = (
            f"set -e; cd {t}; "
            f"GBOS_SELINUX_CACHE={t}/cache bash {t}/selinux/araclar/kur.sh >&2; "
            f"mkdir deb; cd deb; apt-get -q -o APT::Sandbox::User=root download secilc >&2; "
            f"dpkg-deb -x secilc_*.deb root; cd {t}; "
            f"python3 -I stage/side.py {t}/stage {t}/cache/bin/secilc {t}/cache/bin/avc_kontrol "
            f"/usr/lib/$(gcc -dumpmachine):{t}/deb/root/usr/bin/secilc; "
            f"echo SURUM $(dpkg-deb -f deb/secilc_*.deb Version) >&2")
        r = adb_chroot(script)
        log = r.stderr.decode()
        if r.returncode:
            raise SystemExit("tablet:\n" + log[-4000:])
        tab = json.loads(r.stdout)
        tab["debian_surum"] = next((l.split()[1] for l in log.splitlines() if l.startswith("SURUM ")), "")
        adb_chroot(f"rm -rf {t}")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    checks = {
        "pc: AOSP secilc LineageOS politikasını precompiled_sepolicy ile aynı derliyor": pc["lineage"] == precompiled,
        "tablet: kur.sh aarch64'te derledi, AOSP secilc precompiled_sepolicy ile aynı": tab["lineage"] == precompiled,
        "tablet ve pc avc_kontrol --cil dökümü aynı (lineage)": pc["lineage.cil"] == tab["lineage.cil"],
        "tablet ve pc avc_kontrol yanıtları aynı (lineage, beklenen.txt)": pc["lineage.avc"] == tab["lineage.avc"],
    }
    if "ours" in pc:
        checks.update({
            "tablet ve pc AOSP secilc mica çalışma ağacı politikası aynı": pc["ours"] == tab["ours"],
            "tablet ve pc avc_kontrol --cil dökümü aynı (mica)": pc["ours.cil"] == tab["ours.cil"],
            "tablet ve pc avc_kontrol yanıtları aynı (mica, beklenen.txt)": pc["ours.avc"] == tab["ours.avc"],
        })
    debian = {name: {"debian": tab.get(name + ".debian"), "aosp": tab[name],
                     "ayni": tab.get(name + ".debian") == tab[name]}
              for name in pc if "." not in name}
    info = {"precompiled_sepolicy": precompiled, "beklenen_satir": n_tuples, "pc": pc, "tablet": tab,
            "debian_secilc": {"surum": tab["debian_surum"], **debian}, "denetimler": checks}
    with open(os.path.join(args.kanit, "secilc.json"), "w") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    for name, ok in checks.items():
        print(f"{'GEÇTİ' if ok else 'KALDI'}  {name}")
    for name, d in debian.items():
        print(f"bilgi  Debian secilc {tab['debian_surum']} {name}: AOSP ile {'aynı' if d['ayni'] else 'FARKLI'}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
