"""guncelleyici/motor/ortam-kur.sh and e2fsdroid-kur.sh, the parts that run on this computer:

- both scripts parse (bash -n; shellcheck when installed); ortam-kur.sh refuses to run as non-root
  (exit 2) without touching anything; e2fsdroid-kur.sh --damga names its pinned commits
- every external program the pipeline starts (each subprocess call of motor.py, mica-indir.py,
  yeni-surum-hazirla.py, apply_patches.py, yamalar/*.py, make_ext4_images.py, yerel/derle.py and
  the helper scripts they run, read from their source) has a known provider, and every Debian
  package that provides one is in ortam-kur.sh's list; every package in the list is needed by a
  program or a build script
- the e2fsdroid flags make_ext4_images.py uses are the ones ortam-kur.sh's own check exercises
- e2fsdroid-kur.sh builds from the same external/selinux commit as selinux/araclar/kur.sh

The tablet side (installation from scratch, second run, equivalence with this computer's tools) is
measured by e2fsdroid-esdegerlik.py, secilc-esdegerlik.py and android-araclari-esdegerlik.py
(evidence: guncelleyici/kanit/ortam-*).

    python3 -I -m pytest guncelleyici/testler -q
"""
import ast
import glob
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))
MOTOR = os.path.join(TOOLS, "guncelleyici", "motor")
ORTAM = os.path.join(MOTOR, "ortam-kur.sh")
E2FSDROID = os.path.join(MOTOR, "e2fsdroid-kur.sh")
SELINUX_KUR = os.path.join(TOOLS, "selinux", "araclar", "kur.sh")

# Python files whose subprocess calls run in the tablet's environment
PIPELINE = ["guncelleyici/motor/motor.py", "mica-indir.py", "yeni-surum-hazirla.py", "apply_patches.py",
            "make_ext4_images.py", "guncelleyici/yerel/derle.py", "mica_disk.py",
            "pil-sagligi/araclar/cihaz-kutuphaneleri.py", "pil-sagligi/araclar/sembol-denetimi.py",
            "dogal-yon/hal/araclar/denetim.py", "dogal-yon/hal/araclar/sistem-kutuphaneleri.py",
            "dogal-yon/hal/araclar/vendor-dosyalari.py"] + \
           sorted(os.path.relpath(p, TOOLS) for p in glob.glob(os.path.join(TOOLS, "yamalar", "*.py")))

# Who provides each program. ("paket", P): Debian package P; other kinds are built or carried.
PROVIDERS = {
    "fsck.erofs": ("paket", "erofs-utils"), "dump.erofs": ("paket", "erofs-utils"),
    "mkfs.erofs": ("paket", "erofs-utils"),
    "mke2fs": ("paket", "e2fsprogs"), "e2fsck": ("paket", "e2fsprogs"), "debugfs": ("paket", "e2fsprogs"),
    "e2fsdroid": ("derleme", "guncelleyici/motor/e2fsdroid-kur.sh"),
    "zipalign": ("paket", "zipalign"), "dexdump": ("paket", "dexdump"),
    "javac": ("paket", "openjdk-21-jdk-headless"),
    "keytool": ("paket", "openjdk-21-jdk-headless"),
    "d8": ("kit", "build-tools/37.0.0/lib/d8.jar (java: openjdk-21-jdk-headless)"),
    "apksigner": ("kit", "build-tools/37.0.0/lib/apksigner.jar (java: openjdk-21-jdk-headless)"),
    "nm": ("paket", "binutils"), "readelf": ("paket", "binutils"), "cc": ("paket", "gcc"),
    "cp": ("paket", "coreutils"), "rm": ("paket", "coreutils"), "python3": ("paket", "python3"),
    "bash": ("paket", "bash"),
    "secilc": ("derleme", "selinux/araclar/kur.sh"), "avc_kontrol": ("derleme", "selinux/araclar/kur.sh"),
    "kur.sh": ("betik", "selinux/araclar/kur.sh"),
    "stamp.sh": ("betik", "<modül>/stamp.sh"),
    "build.sh": ("bilgisayar", "hazir_ikili.derle: in the kit (GBOS_KIT) a stale out/ is an error, "
                               "build.sh only runs on the computer"),
    "getir.sh": ("bilgisayar", "apply_patches.py fetches the face unlock sources when missing; "
                 "never under GBOS_KIT (the kit carries FaceUnlock's files and outputs)"),
    "fetch.sh": ("bilgisayar", "yamalar/terminal.py fetches the Terminal's third-party files when "
                 "missing; never under GBOS_KIT (the kit carries them)"),
    "brotli": ("bilgisayar", "lineage-dosya-cikar.py reads the LineageOS zip, kept on the computer"),
    "adb": ("bilgisayar", "tablete-yaz.py writes from the computer"),
}
# Variables holding a program that the call site cannot show (resolved by reading the code)
VARIABLES = {
    "secilc": "secilc",           # yamalar/selinux._tools() -> CACHE_BIN/secilc
    "checker": "avc_kontrol",     # yamalar/selinux._tools() -> CACHE_BIN/avc_kontrol
    "cc": "cc",                   # yerel/derle.py --cc, default "cc"
    "tool": "readelf",            # dogal-yon/hal/araclar/denetim.py readelf(tool, ...)
    "readelf": "readelf",         # pil-sagligi/araclar/sembol-denetimi.py, NDK llvm-readelf or binutils
    "cmd": None,                  # motor.run_step / selinux.compile_policy: a list built above
}
# Programs the shell scripts run on the tablet need, beyond coreutils/findutils:
# (script, text that must still be in it, package)
SCRIPT_NEEDS = [
    ("selinux/araclar/kur.sh", "curl -fsSL", "curl"),
    ("selinux/araclar/kur.sh", "tar -xzf", "tar"),
    ("selinux/araclar/kur.sh", "tar -xzf", "gzip"),
    ("selinux/araclar/kur.sh", "make -C \"$BUILD/libsepol\"", "make"),
    ("selinux/araclar/kur.sh", "make -C \"$BUILD/libsepol\"", "flex"),   # libsepol/src/Makefile: LEX = flex
    ("selinux/araclar/kur.sh", "gcc -O2", "gcc"),
    ("guncelleyici/motor/e2fsdroid-kur.sh", "curl -fsSL", "curl"),
    ("guncelleyici/motor/e2fsdroid-kur.sh", "tar -xzf", "tar"),
    ("guncelleyici/motor/e2fsdroid-kur.sh", "tar -xzf", "gzip"),
    ("guncelleyici/motor/e2fsdroid-kur.sh", "g++ -o", "g++"),
    ("guncelleyici/motor/e2fsdroid-kur.sh", "ar rcs", "binutils"),
]
# Packages needed only to build, with the script that needs them
BUILD_ONLY = {
    "libc6-dev": "gcc's C library headers (yerel/derle.py, kur.sh, e2fsdroid-kur.sh)",
    "ca-certificates": "https downloads (kur.sh, e2fsdroid-kur.sh, mica-indir.py)",
    "g++": "e2fsdroid-kur.sh (libcutils is C++)",
    "libpcre2-dev": "e2fsdroid-kur.sh (libselinux label_file)",
    "zlib1g-dev": "e2fsdroid-kur.sh", "android-libbase-dev": "e2fsdroid-kur.sh",
    "android-liblog-dev": "e2fsdroid-kur.sh", "android-libsparse-dev": "e2fsdroid-kur.sh",
    "make": "selinux/araclar/kur.sh", "flex": "selinux/araclar/kur.sh (libsepol cil_lexer.l)",
    "curl": "kur.sh, e2fsdroid-kur.sh", "tar": "kur.sh, e2fsdroid-kur.sh", "gzip": "kur.sh, e2fsdroid-kur.sh",
    "findutils": "stamp.sh, kur.sh, e2fsdroid-kur.sh (find, xargs)",
}


def bash_array(path, name):
    text = open(path).read()
    m = re.search(rf"^{name}=\((.*?)\)", text, re.S | re.M)
    assert m, f"{name} not found in {path}"
    return re.sub(r"#.*", "", m.group(1)).split()


def program_of(node, consts):
    """Program name of a subprocess call's first argument, or None when it cannot be told."""
    if isinstance(node, (ast.List, ast.Tuple)):
        return program_of(node.elts[0], consts) if node.elts else None
    if isinstance(node, ast.BinOp):  # [secilc] + inputs
        return program_of(node.left, consts)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return os.path.basename(node.value)
    if isinstance(node, ast.Attribute) and ast.unparse(node) == "sys.executable":
        return "python3"
    if isinstance(node, ast.Name):
        if node.id in consts:
            return program_of(consts[node.id], consts)
        return ("?", node.id)
    if isinstance(node, ast.Call):  # os.path.join(X, "tool"), os.environ.get("K", default), expanduser("~/x")
        strings = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        fn = ast.unparse(node.func)
        if fn in ("os.environ.get", "os.getenv") and len(node.args) == 2:
            return program_of(node.args[1], consts)
        if strings:
            return os.path.basename(strings[-1].value)
        if node.args:
            return program_of(node.args[-1], consts)
    return ("?", ast.unparse(node))


def _bound(nodes):
    """Names assigned exactly once in these statements (not looking into nested functions)."""
    seen = {}
    stack = list(nodes)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    seen.setdefault(t.id, []).append(n.value)
        stack.extend(ast.iter_child_nodes(n))
    return {k: v[0] for k, v in seen.items() if len(v) == 1}


def _calls(nodes):
    stack = list(nodes)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(n, ast.Call) and n.args and ast.unparse(n.func) in (
                "subprocess.run", "subprocess.Popen", "subprocess.check_output", "subprocess.check_call"):
            yield n
        stack.extend(ast.iter_child_nodes(n))


def pipeline_programs():
    found = {}
    for rel in PIPELINE:
        path = os.path.join(TOOLS, rel)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path).read())
        module = _bound(tree.body)
        scopes = [tree.body] + [f.body for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for body in scopes:
            consts = dict(module, **_bound(body)) if body is not tree.body else module
            for node in _calls(body):
                prog = program_of(node.args[0], consts)
                if isinstance(prog, tuple):
                    name = prog[1]
                    assert name in VARIABLES, f"{rel}:{node.lineno}: program in '{name}' not known; " \
                                              f"add it to VARIABLES in {os.path.basename(__file__)}"
                    prog = VARIABLES[name]
                    if prog is None:
                        continue
                found.setdefault(prog, set()).add(f"{rel}:{node.lineno}")
    return found


def test_scripts_parse():
    for script in (ORTAM, E2FSDROID):
        subprocess.run(["bash", "-n", script], check=True)
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", "-S", "warning", ORTAM, E2FSDROID], check=True)


def test_ortam_needs_root():
    if os.geteuid() == 0:
        pytest.skip("runs as root here; the refusal is for non-root")
    r = subprocess.run(["bash", ORTAM, "--denetle"], capture_output=True, text=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "root" in r.stdout


def test_e2fsdroid_stamp_names_pins():
    r = subprocess.run(["bash", E2FSDROID, "--damga"], capture_output=True, text=True, check=True)
    stamp = r.stdout.strip()
    text = open(E2FSDROID).read()
    for name in ("E2FSPROGS", "CORE", "SELINUX"):
        commit = re.search(rf"^{name}=([0-9a-f]{{40}})", text, re.M).group(1)
        assert commit in stamp
    assert re.search(r"betik=[0-9a-f]{64}$", stamp)


def test_same_selinux_commit_as_kur_sh():
    a = re.search(r"^SELINUX=([0-9a-f]{40})", open(E2FSDROID).read(), re.M).group(1)
    b = re.search(r"^COMMIT=([0-9a-f]{40})", open(SELINUX_KUR).read(), re.M).group(1)
    assert a == b


def test_every_program_has_a_provider():
    found = pipeline_programs()
    assert {"fsck.erofs", "mke2fs", "e2fsdroid", "zipalign", "apksigner", "dexdump", "d8", "javac",
            "secilc"} <= set(found), sorted(found)  # the scan itself works
    unknown = {p: sorted(w) for p, w in found.items() if p not in PROVIDERS}
    assert not unknown, f"programs without a provider (add to ortam-kur.sh and PROVIDERS): {unknown}"


def test_packages_match_programs():
    packages = set(bash_array(ORTAM, "PAKETLER")) | set(bash_array(ORTAM, "TEMEL"))
    found = pipeline_programs()
    needed = {}
    for prog, where in found.items():
        kind, what = PROVIDERS[prog]
        if kind == "paket":
            needed.setdefault(what, set()).update(where)
        elif kind == "kit":
            needed.setdefault("openjdk-21-jdk-headless", set()).update(where)
    for script, text, pkg in SCRIPT_NEEDS:
        assert text in open(os.path.join(TOOLS, script)).read(), f"{script} no longer runs '{text}'"
        needed.setdefault(pkg, set()).add(script)
    missing = {p: sorted(w) for p, w in needed.items() if p not in packages and p != "coreutils"}
    assert not missing, f"packages ortam-kur.sh does not install: {missing}"
    unused = sorted(p for p in packages if p not in needed and p not in BUILD_ONLY and p != "coreutils")
    assert not unused, f"packages in ortam-kur.sh no program or build needs: {unused}"


def test_e2fsdroid_flags_exercised():
    tree = ast.parse(open(os.path.join(TOOLS, "make_ext4_images.py")).read())
    flags = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.args[0] if node.args else None, ast.List):
            elts = node.args[0].elts
            if elts and isinstance(elts[0], ast.Constant) and elts[0].value == "e2fsdroid":
                flags = {e.value for e in elts if isinstance(e, ast.Constant) and str(e.value).startswith("-")}
    assert flags == {"-e", "-T", "-f", "-C", "-S", "-a"}, flags
    line = next(l for l in open(ORTAM).read().split("\n") if "e2fsdroid -e" in l)
    call = line + next(l for l in open(ORTAM).read().split(line)[1].split("\n")[1:2])
    for flag in flags:
        assert re.search(rf"\s{flag}\s", call), f"ortam-kur.sh's e2fsdroid check does not use {flag}"
