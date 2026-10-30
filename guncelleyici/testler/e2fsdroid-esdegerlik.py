#!/usr/bin/env python3
"""
Cross-device acceptance test: the tablet's e2fsdroid (guncelleyici/motor/e2fsdroid-kur.sh, built in
the gbos-linux environment) against the computer's (Arch android-tools), run the way the pipeline
runs it: make_ext4_images.build_ext4 builds system_ext.img from a tree, its *.meta capture and the
device file_contexts.

  python3 -I guncelleyici/testler/e2fsdroid-esdegerlik.py --kanit DIR [--gercek /var/tmp/mica_work]

Without --gercek the tree is a small generated one that exercises what make_ext4_images hands
e2fsdroid: owners and modes from the capture and from its defaults (/bin/ -> 0:2000 0755),
setuid/setgid/sticky bits, file capabilities, labels from the capture and labels that only the
device file_contexts give (regex precedence, the "new file" path), symlinks, hard links, empty and
multi-block files, names with regex characters (file_contexts and fs_config take ASCII names
without whitespace only, as on the device). With --gercek the real system_ext tree of a mica work dir is
used (its sysext/, sysext.meta and the three device file_contexts).

Two measurements, both on the computer and on the tablet (adb, gbos-linux run, as root):
  A  same input image: the image mke2fs made on the computer, just before e2fsdroid, is given to
     the tablet's e2fsdroid with the same arguments and the same fs_config / file_contexts;
     the two results must be byte-identical.
  B  whole build_ext4 on each machine (each with its own mke2fs and e2fsck: Arch e2fsprogs here,
     Debian's on the tablet): owner, mode, size, links, times, flags, security.selinux,
     security.capability and content of every path, read back with debugfs, must be identical.
Writes DIR/e2fsdroid-<kucuk|sysext>-{pc,tablet}.txt (debugfs listing) and DIR/e2fsdroid-<...>.json.
Exits non-zero on any difference.

The script is also the tablet-side helper (subcommands "yerel" and "uygula").
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
TABLET_DIR = "/var/tmp/gbos-e2fsdroid-esdegerlik"
LOCK = "/var/tmp/gbos-dogal-yon/TABLET-ANA-OTURUMDA"
EPOCH = 1761300000  # fixed SOURCE_DATE_EPOCH: both builds stamp the same times
MOUNT = "/system_ext"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# --- the small generated tree --------------------------------------------------------------------

CAP_ROOT = "/kayit/sysext"  # path the capture claims to come from (make_ext4_images: meta_root)
LABEL_FILE = "u:object_r:system_file:s0"


def vfs_cap(caps):
    """security.capability value (VFS_CAP_REVISION_2, effective bit set), as the device stores it."""
    lo, hi = caps & 0xFFFFFFFF, caps >> 32
    return (0x02000001).to_bytes(4, "little") + lo.to_bytes(4, "little") + b"\0" * 4 + \
        hi.to_bytes(4, "little") + b"\0" * 4


def make_small(work):
    """sysext/ + sysext.meta + device file_contexts under work (deterministic)."""
    root = os.path.join(work, "sysext")
    files = {
        "bin/arac": b"#!/system/bin/sh\necho ok\n",
        "bin/yetkili": b"\x7fELF" + bytes(range(256)) * 9,           # capabilities
        "bin/suid": b"\x7fELF-suid",                                  # 04750 root:shell
        "bin/yeni-ikili": b"\x7fELF-new",                             # not captured: defaults + fc
        "etc/izinler/a.xml": b"<permissions/>\n",
        "etc/bos": b"",
        "etc/ad-arti+[x].txt": b"x" * 5000,
        "lib64/libornek.so": bytes((i * 7) % 251 for i in range(300000)),
        "app/Ornek/Ornek.apk": b"PK\x05\x06" + b"\0" * 18,
        "priv-app/Yeni/Yeni.apk": b"PK\x05\x06" + b"\0" * 18,          # labelled by fc only
        "framework/oat/arm64/x.odex": b"dex\n039\0",
        "derin/a/b/c/d/e.txt": b"derin\n",
    }
    for rel, data in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    os.makedirs(os.path.join(root, "bos-dizin"))
    os.makedirs(os.path.join(root, "yapisik"))
    os.symlink("lib64", os.path.join(root, "lib"))
    os.symlink("/system/bin/linker64", os.path.join(root, "bin/linker-bag"))
    os.link(os.path.join(root, "etc/izinler/a.xml"), os.path.join(root, "etc/izinler/b.xml"))

    meta = []
    def cap(rel, kind, *fields):
        path = CAP_ROOT + ("/" + rel if rel else "")
        meta.append("\t".join([kind, path, *map(str, fields)]))
    def label(rel, ctx):
        cap(rel, "XATTR", "security.selinux", (ctx.encode() + b"\0").hex())
    label("", LABEL_FILE)
    for rel in ["bin", "etc", "etc/izinler", "lib64", "app", "app/Ornek", "framework",
                "framework/oat", "framework/oat/arm64", "derin", "derin/a", "derin/a/b",
                "derin/a/b/c", "derin/a/b/c/d", "bos-dizin", "lib", "bin/linker-bag",
                "etc/izinler/a.xml", "etc/izinler/b.xml", "etc/bos", "etc/ad-arti+[x].txt",
                "app/Ornek/Ornek.apk", "framework/oat/arm64/x.odex", "derin/a/b/c/d/e.txt"]:
        label(rel, LABEL_FILE)
    label("bin/arac", "u:object_r:system_file:s0")
    label("bin/yetkili", "u:object_r:gbos_test_exec:s0")
    label("bin/suid", "u:object_r:gbos_suid_exec:s0")
    label("lib64/libornek.so", "u:object_r:same_process_hal_file:s0")
    label("yapisik", "u:object_r:system_data_file:s0")
    cap("bin/yetkili", "CHOWN", 1000, 2000)
    cap("bin/yetkili", "CHMOD", "0750")
    cap("bin/yetkili", "XATTR", "security.capability",
        vfs_cap((1 << 10) | (1 << 12) | (1 << 35)).hex())  # NET_BIND_SERVICE NET_ADMIN WAKE_ALARM
    cap("bin/suid", "CHOWN", 0, 2000)
    cap("bin/suid", "CHMOD", "04750")
    cap("yapisik", "CHOWN", 1000, 1000)
    cap("yapisik", "CHMOD", "01771")
    cap("etc/izinler", "CHMOD", "02751")
    cap("app/Ornek/Ornek.apk", "CHOWN", 1000, 1000)
    with open(os.path.join(work, "sysext.meta"), "w") as f:
        f.write("\n".join(meta) + "\n")

    fc = {  # the device file_contexts make_ext4_images merges (plat, system_ext, product)
        "sys/system/etc/selinux/plat_file_contexts":
            "/system_ext(/.*)?    u:object_r:system_file:s0\n"
            "/system_ext/bin(/.*)?    u:object_r:system_file:s0\n",
        "sysext/etc/selinux/system_ext_file_contexts":
            "/system_ext/bin/yeni-ikili    u:object_r:gbos_yeni_exec:s0\n"
            "/system_ext/priv-app(/.*)?    u:object_r:system_file:s0\n"
            "/system_ext/priv-app/Yeni(/.*)?    u:object_r:gbos_yeni_app_file:s0\n",
        "prod/etc/selinux/product_file_contexts": "/product(/.*)?    u:object_r:system_file:s0\n",
    }
    for rel, text in fc.items():
        p = os.path.join(work, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)


def copy_real(src, work):
    for rel in ["sysext", "sysext.meta", "sys/system/etc/selinux/plat_file_contexts",
                "prod/etc/selinux/product_file_contexts"]:
        s, d = os.path.join(src, rel), os.path.join(work, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        subprocess.run(["cp", "-a", "--reflink=auto", s, d], check=True)


# --- one machine: build_ext4 as the pipeline runs it, recording e2fsdroid ------------------------

def build(work, kit):
    """make_ext4_images.build_ext4('system_ext') in work; returns the record of the e2fsdroid run."""
    os.environ["GBOS_WORK"] = work
    os.environ["SOURCE_DATE_EPOCH"] = str(EPOCH)
    sys.path.insert(0, kit)
    import importlib
    m = importlib.import_module("make_ext4_images")
    record = {}
    real_run = m.subprocess.run

    def run(cmd, *a, **kw):
        if cmd and cmd[0] == "e2fsdroid":
            img = cmd[-1]
            subprocess.run(["cp", "--sparse=always", img, os.path.join(work, "once.img")], check=True)
            record.update(argv=cmd, once=sha256(img))
            res = real_run(cmd, *a, **kw)
            subprocess.run(["cp", "--sparse=always", img, os.path.join(work, "sonra.img")], check=True)
            record["sonra"] = sha256(img)
            return res
        return real_run(cmd, *a, **kw)

    m.subprocess.run = run
    m.build_ext4("system_ext", os.path.join(work, "sysext"), os.path.join(work, "sysext.meta"),
                 MOUNT, "system_ext")
    img = os.path.join(work, "system_ext.img")
    record["son"] = sha256(img)
    for name in ("system_ext_fs_config", "system_ext_file_contexts"):
        # make_ext4_images lists the tree with os.walk, so the line order follows the directory
        # order of the file system the tree is on (btrfs here, f2fs on the tablet); the lines
        # themselves must be the same
        with open(os.path.join(work, name), "rb") as f:
            record[name] = hashlib.sha256(b"".join(sorted(f.readlines()))).hexdigest()
    record["e2fsdroid"] = shutil.which("e2fsdroid")
    record["mke2fs"] = subprocess.run(["mke2fs", "-V"], capture_output=True, text=True).stderr.splitlines()[0]
    return record


def listing(img, tree):
    """Every path of the image, sorted: debugfs stat (without block maps) and content sha256."""
    paths = ["/"]
    for root, dirs, files in os.walk(tree):
        dirs.sort()
        for n in sorted(dirs + files):
            paths.append("/" + os.path.relpath(os.path.join(root, n), tree))
    paths.sort()
    with tempfile.TemporaryDirectory(dir=os.path.dirname(img)) as tmp:
        cmds = os.path.join(tmp, "cmd")
        with open(cmds, "w") as f:
            for p in paths:
                f.write(f'stat "{p}"\n')
        out = subprocess.run(["debugfs", "-f", cmds, img], capture_output=True, text=True,
                             check=True, env=dict(os.environ, LC_ALL="C")).stdout
        dump = os.path.join(tmp, "dump")
        os.makedirs(dump)
        subprocess.run(["debugfs", "-R", f"rdump / {dump}", img], capture_output=True, check=True)
        lines, skip = [], False
        for line in out.splitlines():
            if line.startswith("debugfs: stat"):
                lines.append("")
                lines.append(line[len("debugfs: "):])
                skip = False
            elif line.startswith(("EXTENTS:", "BLOCKS:")):
                skip = True        # block numbers: layout of each mke2fs, not e2fsdroid's choice
            elif not skip:
                lines.append(line.rstrip())
        lines.append("")
        lines.append("# content")
        for p in paths[1:]:
            local = os.path.join(dump, p.lstrip("/"))
            if os.path.islink(local):
                lines.append(f"{p} -> {os.readlink(local)}")
            elif os.path.isfile(local):
                lines.append(f"{p} {sha256(local)}")
    return "\n".join(lines) + "\n"


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def cmd_yerel(args):
    rec = build(args.work, args.kit)
    rec["liste"] = listing(os.path.join(args.work, "system_ext.img"), os.path.join(args.work, "sysext"))
    rec["liste_sonra"] = listing(os.path.join(args.work, "sonra.img"), os.path.join(args.work, "sysext"))
    write_json(args.cikti, rec)


def cmd_uygula(args):
    """A: the computer's pre-e2fsdroid image and inputs through this machine's e2fsdroid."""
    with open(args.kayit) as f:
        pc = json.load(f)
    girdi = args.girdi
    img = os.path.join(args.work, "A.img")
    subprocess.run(["cp", "--sparse=always", os.path.join(girdi, "once.img"), img], check=True)
    assert sha256(img) == pc["once"], "once.img changed in transit"
    argv = list(pc["argv"])
    remap = {pc["work"]: girdi}
    for i, a in enumerate(argv):
        for old, new in remap.items():
            if a.startswith(old):
                argv[i] = new + a[len(old):]
    argv[-1] = img
    env = dict(os.environ, E2FSPROGS_FAKE_TIME=str(EPOCH), E2FSCK_TIME=str(EPOCH))
    subprocess.run(argv, check=True, env=env, stdout=subprocess.DEVNULL)
    write_json(args.cikti, {"argv": argv, "sonra": sha256(img),
                            "liste_sonra": listing(img, os.path.join(girdi, "sysext"))})


# --- the computer: drives both sides -------------------------------------------------------------

def adb_chroot(script, stdin=None):
    if os.path.exists(LOCK):
        raise SystemExit(f"{LOCK} var: tablet ana oturumda, ölçüm yapılmadı")
    return subprocess.run(["adb", "-s", seri(), "shell", "gbos-linux run -- sh -c " + shlex.quote(script)],
                          input=stdin, capture_output=True, check=False)


def tar_to_tablet(src_dir, names, dest):
    tar = subprocess.run(["tar", "-S", "-cf", "-", "-C", src_dir, *names], capture_output=True, check=True).stdout
    r = adb_chroot(f"mkdir -p {dest} && tar -xf - -C {dest}", stdin=tar)
    if r.returncode:
        raise SystemExit(r.stderr.decode())


def olc(args):
    kind = "sysext" if args.gercek else "kucuk"
    os.makedirs(args.kanit, exist_ok=True)
    base = tempfile.mkdtemp(prefix="gbos-e2fsdroid-esdegerlik-", dir="/var/tmp")
    try:
        work = os.path.join(base, "w")
        os.makedirs(work)
        if args.gercek:
            copy_real(args.gercek, work)
        else:
            make_small(work)
        girdi = os.path.join(base, "girdi")
        # pristine inputs for the tablet (cp -a: hard links stay hard links)
        subprocess.run(["cp", "-a", "--reflink=auto", work, girdi], check=True)

        sonuc = os.path.join(base, "pc.json")
        r = subprocess.run([sys.executable, "-I", __file__, "yerel", work, KIT, "--cikti", sonuc],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if r.returncode:
            raise SystemExit("computer build failed:\n" + r.stdout[-4000:])
        with open(sonuc) as f:
            pc = json.load(f)
        pc["work"] = work
        for name in ("once.img", "system_ext_fs_config", "system_ext_file_contexts"):
            shutil.copy2(os.path.join(work, name), os.path.join(girdi, name))
        with open(os.path.join(girdi, "kayit.json"), "w") as f:
            json.dump(pc, f)
        shutil.copy2(__file__, os.path.join(base, "esdegerlik.py"))
        shutil.copy2(os.path.join(KIT, "make_ext4_images.py"), os.path.join(base, "make_ext4_images.py"))

        adb_chroot(f"rm -rf {TABLET_DIR}")
        tar_to_tablet(base, ["girdi", "esdegerlik.py", "make_ext4_images.py"], TABLET_DIR)
        t = TABLET_DIR
        rb = adb_chroot(f"cp -a {t}/girdi {t}/w && rm -f {t}/w/once.img {t}/w/kayit.json "
                        f"{t}/w/system_ext_fs_config {t}/w/system_ext_file_contexts && "
                        f"python3 -I {t}/esdegerlik.py yerel {t}/w {t} --cikti {t}/tablet.json >&2 && "
                        f"cat {t}/tablet.json")
        if rb.returncode:
            raise SystemExit("tablet build failed:\n" + rb.stderr.decode()[-4000:])
        tab = json.loads(rb.stdout)
        ra = adb_chroot(f"mkdir -p {t}/a && python3 -I {t}/esdegerlik.py uygula {t}/a {t}/girdi "
                        f"{t}/girdi/kayit.json --cikti {t}/a.json >&2 && cat {t}/a.json")
        if ra.returncode:
            raise SystemExit("tablet e2fsdroid (A) failed:\n" + ra.stderr.decode()[-4000:])
        tab_a = json.loads(ra.stdout)
        adb_chroot(f"rm -rf {TABLET_DIR}")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    for name, head, text in [
            ("B-pc", f"{pc['e2fsdroid']}, {pc['mke2fs']}, system_ext.img sha256 {pc['son']}", pc["liste"]),
            ("B-tablet", f"{tab['e2fsdroid']}, {tab['mke2fs']}, system_ext.img sha256 {tab['son']}", tab["liste"]),
            ("A-pc", f"{pc['e2fsdroid']} on the computer's mke2fs image, sha256 {pc['sonra']}", pc["liste_sonra"]),
            ("A-tablet", f"{tab['e2fsdroid']} on the same image, sha256 {tab_a['sonra']}", tab_a["liste_sonra"])]:
        with open(os.path.join(args.kanit, f"e2fsdroid-{kind}-{name}.txt"), "w") as f:
            f.write(f"# {head}\n{text}")
    checks = {
        "fs_config satırları aynı": pc["system_ext_fs_config"] == tab["system_ext_fs_config"],
        "file_contexts satırları aynı": pc["system_ext_file_contexts"] == tab["system_ext_file_contexts"],
        "A: aynı imaj + aynı argümanlar -> bayt bayt aynı sonuç": pc["sonra"] == tab_a["sonra"],
        "A: debugfs listesi aynı": pc["liste_sonra"] == tab_a["liste_sonra"],
        "B: tam build_ext4 (her makine kendi mke2fs/e2fsck) debugfs listesi aynı": pc["liste"] == tab["liste"],
    }
    info = {
        "agac": kind,
        "pc": {k: pc[k] for k in ("e2fsdroid", "mke2fs", "once", "sonra", "son")},
        "tablet": {k: tab[k] for k in ("e2fsdroid", "mke2fs", "once", "sonra", "son")},
        "tablet_A": {"sonra": tab_a["sonra"], "argv": tab_a["argv"]},
        "pc_argv": pc["argv"],
        "B_tam_imaj_bayt_ayni": pc["son"] == tab["son"],
        "denetimler": checks,
    }
    with open(os.path.join(args.kanit, f"e2fsdroid-{kind}.json"), "w") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    for name, ok in checks.items():
        print(f"{'GEÇTİ' if ok else 'KALDI'}  {name}")
    print(f"bilgi  B: tam imaj bayt bayt {'aynı' if info['B_tam_imaj_bayt_ayni'] else 'farklı'} "
          f"(pc {pc['mke2fs']}; tablet {tab['mke2fs']})")
    sys.exit(0 if all(checks.values()) else 1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    y = sub.add_parser("yerel")
    y.add_argument("work")
    y.add_argument("kit")
    y.add_argument("--cikti", required=True)
    u = sub.add_parser("uygula")
    u.add_argument("work")
    u.add_argument("girdi")
    u.add_argument("kayit")
    u.add_argument("--cikti", required=True)
    ap.add_argument("--kanit")
    ap.add_argument("--gercek")
    args = ap.parse_args()
    if args.cmd == "yerel":
        cmd_yerel(args)
    elif args.cmd == "uygula":
        cmd_uygula(args)
    elif args.kanit:
        olc(args)
    else:
        ap.error("--kanit DIR gerekli")


if __name__ == "__main__":
    main()
