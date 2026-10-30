"""Shared fixtures of the GooglebookOS T870 image pipeline regression tests (BENIOKU.md).

Every test works on real inputs: the pristine mica images (mica-imaj/guncel/*.erofs), the
working trees in /var/tmp/mica_work (read only), the LineageOS zip and the super headers. A test
whose input is missing is skipped with the reason, it never passes without it. Scratch data lives
in /var/tmp/gbos-test-<pid>/ (never /tmp, which is RAM) and is removed when the session ends.
"""
import os
import sys

# pytest compiles this file before it runs, so its own .pyc is already written; from here on no
# module (tests, tools) leaves bytecode behind, and the one file written for conftest is removed.
sys.dont_write_bytecode = True
_HERE = os.path.dirname(os.path.abspath(__file__))


def _remove_own_bytecode():
    cache = os.path.join(_HERE, "__pycache__")
    if not os.path.isdir(cache):
        return
    for name in os.listdir(cache):
        if name.startswith("conftest."):
            os.remove(os.path.join(cache, name))
    if not os.listdir(cache):
        os.rmdir(cache)


_remove_own_bytecode()

import ast  # noqa: E402
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

ARACLAR = os.path.dirname(_HERE)
if ARACLAR not in sys.path:
    sys.path.insert(0, ARACLAR)

# The real working tree; the tools read the same variable (default /var/tmp/mica_work).
GERCEK_WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
MICA = os.path.join(ARACLAR, "mica-imaj", "guncel")
LINEAGE_ZIP = os.path.join(ARACLAR, "LineageOS-24.0-gts7lwifi",
                           "lineage-24.0-20261011-nightly-gts7lwifi-signed.zip")
EROFS_BIN = os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin"))
PLAT_FC = "system/etc/selinux/plat_file_contexts"
GECICI_UST = "/var/tmp"
DISK_TABANI = 25 << 30  # free space / must keep (user rule 9)
MIB = 1 << 20


def gerekli(*yollar):
    """Skips the test unless every real input exists."""
    for p in yollar:
        if not os.path.lexists(p):
            pytest.skip(f"atlandı: gerçek girdi yok: {p}")


def arac_gerekli(*adlar):
    for ad in adlar:
        if not (shutil.which(ad) or os.access(ad, os.X_OK)):
            pytest.skip(f"atlandı: araç yok: {ad}")


def disk_payi(gib):
    """Skips the test if writing `gib` GiB of scratch data would push / below its floor."""
    free = shutil.disk_usage(GECICI_UST).free
    if free - int(gib * (1 << 30)) < DISK_TABANI:
        pytest.skip(f"atlandı: disk tabanı: {free / (1 << 30):.1f} GiB boş, bu test {gib} GiB "
                    f"yazar, {DISK_TABANI >> 30} GiB boş kalmalı")


def arac_modulu(dosya, ad):
    """Imports a tool file whose name is not a Python identifier (tablete-yaz.py, ...)."""
    if ad in sys.modules:
        return sys.modules[ad]
    spec = importlib.util.spec_from_file_location(ad, os.path.join(ARACLAR, dosya))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ad] = mod
    spec.loader.exec_module(mod)
    return mod


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(MIB), b""):
            h.update(block)
    return h.hexdigest()


def oku(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def yaz(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)


def reflink(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    subprocess.run(["cp", "-a", "--reflink=always", src, dst], check=True)


def sil(path):
    """Removes a scratch tree even where copied files or directories are read-only."""
    def onexc(func, p, _exc):
        parent = os.path.dirname(p)
        os.chmod(parent, os.stat(parent).st_mode | stat.S_IRWXU)
        if os.path.isdir(p) and not os.path.islink(p):
            os.chmod(p, os.stat(p).st_mode | stat.S_IRWXU)
        func(p)
    if os.path.lexists(path):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, onexc=onexc)
        else:
            os.remove(path)


def ap_girdileri():
    """The entries apply_patches.py passes to add_plat_file_contexts, read from its own source
    (every call, in source order), so a new label added there is covered without editing tests."""
    tree = ast.parse(oku(os.path.join(ARACLAR, "apply_patches.py")))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "add_plat_file_contexts":
            for a in node.args:
                assert isinstance(a, ast.Constant) and isinstance(a.value, str), ast.dump(a)
                out.append((node.lineno, a.value))
    assert out, "apply_patches.py no longer calls add_plat_file_contexts"
    return [v for _, v in sorted(out)]


@pytest.fixture(scope="session")
def gecici_kok():
    kok = os.path.join(GECICI_UST, f"gbos-test-{os.getpid()}")
    assert not os.path.lexists(kok), f"{kok} already exists"
    os.makedirs(kok)
    try:
        yield kok
    finally:
        sil(kok)


@pytest.fixture
def gecici(gecici_kok, request):
    """A scratch directory of its own for each test (pytest's tmp_path would be in /tmp)."""
    d = os.path.join(gecici_kok, re.sub(r"[^A-Za-z0-9_.-]", "_", request.node.name))
    os.makedirs(d)
    try:
        yield d
    finally:
        sil(d)


@pytest.fixture(scope="session")
def ap(gecici_kok):
    """apply_patches.py itself, pointed (GBOS_WORK) at a scratch work dir; the real tree is
    never written. Tests that call its writers further point SYS_DIR at their own directory."""
    gerekli(os.path.join(ARACLAR, "apply_patches.py"))
    work = os.path.join(gecici_kok, "work")
    os.makedirs(work)
    eski = os.environ.get("GBOS_WORK")
    os.environ["GBOS_WORK"] = work
    try:
        mod = arac_modulu("apply_patches.py", "apply_patches")
    finally:
        if eski is None:
            del os.environ["GBOS_WORK"]
        else:
            os.environ["GBOS_WORK"] = eski
    assert mod.WORK == work
    return mod


@pytest.fixture(scope="session")
def ap_asamalar(ap):
    """Reflink copies of the LineageOS stages apply_patches.py reads (lib32_stage, vendor_stage)."""
    for rel in ("lineage/lib32_stage", "lineage/vendor_stage"):
        gerekli(os.path.join(GERCEK_WORK, rel))
        dst = os.path.join(ap.WORK, rel)
        if not os.path.exists(dst):
            reflink(os.path.join(GERCEK_WORK, rel), dst)
    return ap


@pytest.fixture(scope="session")
def pristine_fc(ap):
    """plat_file_contexts as shipped in the pristine mica system image."""
    gerekli(os.path.join(MICA, "system_a.erofs"), os.path.join(EROFS_BIN, "dump.erofs"))
    text = ap.pristine_file(PLAT_FC).decode()
    assert text.startswith("/ ") and len(text.splitlines()) > 500, "pristine read failed"
    return text


def _extract_dir(image, path, dest):
    """Copies directory `path` out of a partition image (ext4 with debugfs, erofs with fsck.erofs);
    returns False if the image has no such directory."""
    with open(image, "rb") as f:
        f.seek(1024 + 0x38)
        ext4 = f.read(2) == b"\x53\xef"
    os.makedirs(dest)
    if ext4:
        probe = subprocess.run(["debugfs", "-R", f"stat {path}", image], capture_output=True,
                               text=True)
        if "File not found" in probe.stderr or "Inode:" not in probe.stdout:
            return False
        subprocess.run(["debugfs", "-R", f"rdump {path} {dest}", image], check=True,
                       capture_output=True)
        inner = os.path.join(dest, os.path.basename(path))
    else:
        r = subprocess.run([os.path.join(EROFS_BIN, "fsck.erofs"), f"--extract={dest}/x",
                            f"--path={path}", image], capture_output=True, text=True)
        if r.returncode != 0:
            if "No such file" in r.stderr or "not found" in r.stderr.lower():
                return False
            raise RuntimeError(f"fsck.erofs {path}: {r.stderr.strip()[-300:]}")
        inner = os.path.join(dest, "x")
    for name in os.listdir(inner):
        os.rename(os.path.join(inner, name), os.path.join(dest, name))
    os.rmdir(inner)
    return True


@pytest.fixture(scope="session")
def lineage_vendor(gecici_kok):
    """vendor /lib, /bin and odm /lib of the LineageOS build the tablet runs, rebuilt from the
    OTA zip with lineage-dosya-cikar.py's own rebuild() (the images are removed right away)."""
    gerekli(LINEAGE_ZIP)
    arac_gerekli("brotli", "debugfs", os.path.join(EROFS_BIN, "fsck.erofs"))
    disk_payi(1)
    ldc = arac_modulu("lineage-dosya-cikar.py", "lineage_dosya_cikar")
    import zipfile
    kok = os.path.join(gecici_kok, "lineage")
    os.makedirs(kok)
    out = {}
    with zipfile.ZipFile(LINEAGE_ZIP) as zf:
        for part, dirs in (("vendor", ("/lib", "/bin")), ("odm", ("/lib",))):
            image = os.path.join(kok, f".{part}.img")
            try:
                ldc.rebuild(zf, part, image)
                for d in dirs:
                    dest = os.path.join(kok, part + d)
                    out[part + d] = dest if _extract_dir(image, d, dest) else None
            finally:
                if os.path.exists(image):
                    os.remove(image)
    assert out["vendor/lib"], "vendor image has no /lib"
    return out
