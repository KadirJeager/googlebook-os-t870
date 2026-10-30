"""The updater kit inside the image, so that a tablet whose /data was wiped prepares the next
release by itself (guncelleyici/kit/kit-uret.py assembles the kit, guncelleyici/motor/motor.py
runs it).

    from yamalar import kit_imaj
    kit_imaj.install(PROD_DIR, RELEASE_INDEPENDENT_INPUTS)

    product/etc/gbos-kit/kit.tar.gz   kit/, tasinan/, ev/ and KIT as kit-uret.py uret lays them out
    product/etc/gbos-kit/KIT          the kit's KIT file (commit, contents hash): the kit this
                                      image was made with

The source is GBOS_KIT_KOK, a directory in that layout: on the tablet the motor's own /work (the
kit that is preparing this very image). On the computer, without GBOS_KIT_KOK, kit-uret.py uret
assembles the kit of the checked-out commit into $GBOS_WORK/kit-imaj first. Left out: the app signing key (ev/.android-certs never goes into an image; a
tablet without one makes its own, motor.py), the environment's caches (ev/.local, ev/.cache) and
kit/mica-imaj (a link to the downloaded release). gbos-guncelleyici-is unpacks the archive into
/data/gbos-ota/motor when that has no kit.

gzip, because toybox unpacks it on the Android side without the Linux environment; the archive
is reproducible (sorted, owner root, fixed time, gzip mtime 0). Bridge images
(tablete-yaz.KOPRU) carry no kit: their product slot has no room for it.
"""
import gzip
import io
import os
import tarfile

KOK = os.environ.get("GBOS_KIT_KOK")
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REL = os.path.join("etc", "gbos-kit")
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
EV_SKIP = {".android-certs", ".local", ".cache"}


def _tablete_yaz():
    import importlib.util
    spec = importlib.util.spec_from_file_location("tablete_yaz", os.path.join(TOOLS_DIR, "tablete-yaz.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def members(root, tasinan_items):
    """Relative paths that go into the archive, sorted (directories included)."""
    out = []

    def walk(top, skip_top=()):
        base = os.path.join(root, top)
        for d, dirs, files in os.walk(base):
            rel_d = os.path.relpath(d, root)
            dirs[:] = sorted(x for x in dirs if x not in SKIP_PARTS
                             and not (d == base and x in skip_top))
            out.append(rel_d)
            for n in sorted(files):
                if not n.endswith(".pyc"):
                    out.append(os.path.join(rel_d, n))
            # a link to a directory goes in as the link, never walked
            for x in [x for x in dirs if os.path.islink(os.path.join(d, x))]:
                dirs.remove(x)
                out.append(os.path.join(rel_d, x))

    walk("kit")
    out = [p for p in out if p != os.path.join("kit", "mica-imaj")]
    out.append("tasinan")
    for item in tasinan_items:
        if not os.path.lexists(os.path.join(root, "tasinan", item)):
            raise SystemExit(f"kit_imaj: {root}/tasinan/{item} yok")
        parts = item.split("/")
        for i in range(1, len(parts)):
            out.append(os.path.join("tasinan", *parts[:i]))
        walk(os.path.join("tasinan", item))
    walk("ev", EV_SKIP)
    out.append("KIT")
    return sorted(set(out))


def archive(root, tasinan_items, mtime):
    """The kit as reproducible tar.gz bytes."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for rel in members(root, tasinan_items):
                info = tar.gettarinfo(os.path.join(root, rel), arcname=rel)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = mtime
                info.pax_headers = {}
                if info.isfile():
                    with open(os.path.join(root, rel), "rb") as f:
                        tar.addfile(info, f)
                elif info.isdir() or info.issym():
                    tar.addfile(info)
                else:
                    raise SystemExit(f"kit_imaj: {rel}: desteklenmeyen dosya türü")
    return buf.getvalue()


def computer_kit():
    """The kit of the checked-out commit, assembled by kit-uret.py uret (computer only)."""
    import shutil
    import subprocess
    import sys
    if os.environ.get("GBOS_KIT") == "1":
        raise SystemExit("kit_imaj: tablette GBOS_KIT_KOK verilmeli (motor.py)")
    dest = os.path.join(os.environ.get("GBOS_WORK", "/var/tmp/mica_work"), "kit-imaj")
    shutil.rmtree(dest, ignore_errors=True)
    subprocess.run([sys.executable, "-I", os.path.join(TOOLS_DIR, "guncelleyici", "kit", "kit-uret.py"),
                    "uret", dest], check=True)
    return dest


def install(prod_dir, release_independent_inputs, root=None):
    """Writes product/etc/gbos-kit (removes it for a bridge image). The carried inputs are
    apply_patches.RELEASE_INDEPENDENT_INPUTS without the libraries the tablet builds itself."""
    dest = os.path.join(prod_dir, REL)
    if _tablete_yaz().KOPRU:
        if os.path.isdir(dest):
            for n in os.listdir(dest):
                os.remove(os.path.join(dest, n))
            os.rmdir(dest)
        print("[+] Bridge image: no updater kit (yamalar/kit_imaj.py)")
        return
    root = root or KOK or computer_kit()
    if not os.path.isfile(os.path.join(root, "KIT")):
        raise SystemExit(f"kit_imaj: {root} bir kit dizini değil (kit-uret.py uret)")
    items = [i for i in release_independent_inputs if not i.endswith(".so")]
    mtime = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    data = archive(root, items, mtime)
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "kit.tar.gz"), "wb") as f:
        f.write(data)
    with open(os.path.join(root, "KIT"), "rb") as src, open(os.path.join(dest, "KIT"), "wb") as f:
        f.write(src.read())
    for n in ("kit.tar.gz", "KIT"):
        os.chmod(os.path.join(dest, n), 0o644)
    print(f"[+] Updater kit in the image: {len(data) >> 20} MiB (yamalar/kit_imaj.py)")
