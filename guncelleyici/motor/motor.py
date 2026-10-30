#!/usr/bin/env python3
"""
Googlebook OS updater engine: runs the existing image pipeline on the tablet.

  motor.py denetle     # is there a newer mica release than the running one?
  motor.py hazirla     # download + new tree + patch + ext4 images, verified, ready to install
  motor.py durum       # print the state file

It runs inside the Debian environment (gbos-linux run), started by the Android side
(gbos-guncelleyici-is hazirla, which passes GBOS_MEVCUT_ARTIMLI = ro.build.version.incremental and
binds /data/gbos-ota to /ota: GBOS_GUNCELLEYICI=/ota/motor, GBOS_CIKTI=/ota/gelen; one bind,
so the finished images are renamed into place, not copied). Layout, with /ota/motor as /work:

  /work/kit/          copy of T870-araclar's pipeline (mica-indir.py, yeni-surum-hazirla.py,
                      apply_patches.py, make_ext4_images.py, tablete-yaz.py, inputs); its
                      mica-imaj is a link to /work/mica-imaj
  /work/tasinan/      inputs yeni-surum-hazirla.py carries over from the "current work dir"
                      (libcapture_meta.so, libprovide_meta.so built for this environment,
                      lineage/lib32_stage); GBOS_TASINAN points the script at it
  /work/ev/.local/bin fsck.erofs, dump.erofs, mkfs.erofs links (HOME=/work/ev for the scripts)
  /work/is/<build>/   work dir of the release being prepared (GBOS_WORK)
  /work/durum.json    state shared with the app and the Android side (atomic replace, 0644)
  /work/gunluk/       logs
  /ota/gelen/         the finished set, handed to gbos-guncelleyici-is kur: system.img,
                      system_ext.img, product.img, the tree's yuvalar.conf, super-baslik.bin and
                      kurulum.zip, then hazir.conf (written last, only when every check passed:
                      "surum <build>", "bolum <name> <sha256>", "dosya <name> <sha256>",
                      "neden surum|kit", "kit <contents hash>", "baslik <super header>",
                      "kaynak tablet")

What is prepared:
  - a newer mica release (neden surum);
  - the running release again when the kit in /work/KIT is not the one the running image was made
    with (GBOS_IMAJ_KIT): neden kit. A computer that
    sends a new kit gets a new image without a new release; a wiped tablet unpacks the image's own
    kit and finds nothing to do.
The super header the images are made for (tablete-yaz.py HEADER): the current one when the
running installer can change headers (GBOS_KUR_BICIM=2, gbos-guncelleyici-is), otherwise the one
every older installer knows (googlebookos-1): a bridge image, without the kit, whose only job is
to bring an installer that knows the current header; the kit then differs and the next
preparation makes the full image.

The pipeline's app signing key (ev/.android-certs) comes with a kit from the computer and never
with the image's; without one the tablet makes its own key once (make_app_key).

The steps are the ones ota-yamala.py --guncelle runs (mica-indir -> yeni-surum-hazirla ->
apply_patches -> make_ext4_images). yama-dogrula.py is not run: on the tablet the tree is made by
apply_patches from the pristine images in the same run, so there are no hand-made changes for it
to find, and its btrfs reflink copies do not exist on f2fs. Each finished step is recorded in
/work/is/<build>/ADIMLAR; after an interruption hazirla continues with the first unfinished step.
"""
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback

WORK = os.environ.get("GBOS_GUNCELLEYICI", "/work")
KIT = os.path.join(WORK, "kit")
IMAGES = os.path.join(WORK, "mica-imaj")
BUILDS = os.path.join(WORK, "is")
STATE = os.path.join(WORK, "durum.json")
OUTPUT = os.environ.get("GBOS_CIKTI", "/ota/gelen")
READY = os.path.join(OUTPUT, "hazir.conf")
LOGS = os.path.join(WORK, "gunluk")
LOCK = os.path.join(WORK, "motor.lock")
# The pipeline's HOME (SDK stand-ins, app key, ~/.local/bin, ~/.cache/gbos-selinux) holds programs,
# so it lives in the environment's own filesystem: gbos_linux may read /data/gbos-ota but not execute
# or map code from it (gbos_ota_data_file). The kit's ev/ is copied there on every run.
KIT_HOME = os.path.join(WORK, "ev")
HOME = "/var/lib/gbos-guncelleyici/ev"
KIT_FILE = os.path.join(WORK, "KIT")
APP_KEY = os.path.join(".android-certs", "googlebookos-t870", "uygulama.p12")
# what the work dir of a release was made with; a different one starts it again
IDENTITY = "KIMLIK"
# tree files that go into the set besides the images (gbos-guncelleyici-is SET_FILES)
SET_FILES = ["yuvalar.conf", "super-baslik.bin", "kurulum.zip"]
SET_FILES_DIR = os.path.join("sys", "system", "etc", "gbos-guncelleyici")
HANDOVER = "teslim"
OLDEST_HEADER = "googlebookos-1"  # the header every installer before GBOS_KUR_BICIM=2 knows
PARTITIONS = ["system", "system_ext", "product"]
STEPS = ["indir", "agac", "yamala", "derle", "denetle", "temizle"]
STEP_TEXT = {
    "indir": "Yeni sürüm indiriliyor",
    "agac": "Dosya ağaçları hazırlanıyor",
    "yamala": "Tablet yamaları uygulanıyor",
    "derle": "Sistem imajları oluşturuluyor",
    "denetle": "İmajlar denetleniyor",
    "temizle": "Geçici dosyalar siliniyor",
}
# Free space each step needs before it starts (GiB); the images are kept until installed.
STEP_SPACE = {"indir": 5, "agac": 7, "yamala": 2, "derle": 6, "denetle": 0, "temizle": 0}
MIB = 1 << 20

_child = None


# --- shared state --------------------------------------------------------------------------------

def read_ready():
    """{"build", "sha": {name: sha256}, "kit", "header"} from the hand-over directory's
    hazir.conf, or None."""
    try:
        with open(READY) as f:
            rows = [line.split() for line in f if line.strip()]
    except FileNotFoundError:
        return None
    out = {"build": None, "sha": {}, "kit": None, "header": None}
    for row in rows:
        if row[0] == "surum" and len(row) == 2:
            out["build"] = row[1]
        elif row[0] == "bolum" and len(row) == 3:
            out["sha"][row[1]] = row[2]
        elif row[0] == "kit" and len(row) == 2:
            out["kit"] = row[1]
        elif row[0] == "baslik" and len(row) == 2:
            out["header"] = row[1]
    return out


def kit_hash(path=KIT_FILE):
    """The contents hash ("icerik") of a KIT file, or None."""
    try:
        with open(path) as f:
            for line in f:
                key, _, value = line.strip().partition(" ")
                if key == "icerik" and value:
                    return value
    except FileNotFoundError:
        pass
    return None


def target_header():
    """The super header the images are made for (see the module docstring)."""
    baslik = kit_module("super_baslik", os.path.join("super-baslik", "uret.py"))
    # a tablet installed from a header recorded with uret.py yakala never ran an older installer
    if os.environ.get("GBOS_KUR_BICIM") == "2" or OLDEST_HEADER not in baslik.SHA:
        return baslik.CURRENT
    return OLDEST_HEADER


def kit_reason():
    """True when the running release is to be prepared again for the kit."""
    kit = kit_hash()
    return bool(kit) and kit != (os.environ.get("GBOS_IMAJ_KIT") or None)


def failed_before(build):
    """True when this release with this kit was installed and rolled back (gbos-guncelleyici-is
    remember_failed: GBOS_BASARISIZ_SURUM, GBOS_BASARISIZ_KIT); another kit or release is tried."""
    return build == os.environ.get("GBOS_BASARISIZ_SURUM") and \
        kit_hash() == (os.environ.get("GBOS_BASARISIZ_KIT") or None)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    dfd = os.open(os.path.dirname(path), os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def update_state(**fields):
    state = read_json(STATE, {}) or {}
    state.update(fields)
    state["surum"] = 1
    state["guncellendi"] = int(time.time())
    write_json(STATE, state)
    return state


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(LOGS, exist_ok=True)
    with open(os.path.join(LOGS, "motor.log"), "a") as f:
        f.write(line + "\n")


class Failure(Exception):
    """A step failed; the message is shown to the user."""

    def __init__(self, step, message, detail=""):
        super().__init__(message)
        self.step, self.message, self.detail = step, message, detail


# --- pipeline helpers ----------------------------------------------------------------------------

def kit_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(KIT, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, KIT)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(KIT)
    return mod


def slots():
    """Partition slots in super, from tablete-yaz.py (the one place they are defined)."""
    return kit_module("tablete_yaz", "tablete-yaz.py").SLOTS


def build_incremental(build):
    m = re.match(r"(\d+)_", build)
    if not m:
        raise ValueError(f"sürüm adı tanınmadı: {build}")
    return int(m.group(1))


def tool_env(work=None, epoch=None):
    env = dict(os.environ, HOME=HOME, ZIPALIGN=shutil.which("zipalign") or "zipalign",
               PYTHONUNBUFFERED="1")
    env["GBOS_TASINAN"] = CARRIED
    env["GBOS_META_LIB_DIR"] = META_LIB_DIR
    # The kit carries the prebuilt binaries; a stale one is an error, never a build here
    # (yamalar/hazir_ikili.py).
    env["GBOS_KIT"] = "1"
    # the kit that prepares the image is the one the image carries (yamalar/kit_imaj.py)
    env["GBOS_KIT_KOK"] = WORK
    if work:
        env["GBOS_WORK"] = work
    if epoch:
        env["SOURCE_DATE_EPOCH"] = str(epoch)
    return env


def run_step(step, cmd, env, progress=None):
    """Runs one pipeline command in its own process group, logging its output."""
    global _child
    log(f"[{step}] {' '.join(cmd)}")
    logfile = open(os.path.join(LOGS, f"{step}.log"), "a")
    _child = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace", start_new_session=True)
    tail = []
    try:
        for line in _child.stdout:
            logfile.write(line)
            logfile.flush()
            tail = (tail + [line.rstrip()])[-20:]
            if progress:
                pct = progress(line)
                if pct is not None:
                    update_state(ilerleme=pct)
        rc = _child.wait()
    finally:
        _child.stdout.close()
        _child = None
        logfile.close()
    if rc != 0:
        raise Failure(step, f"{STEP_TEXT[step]} başarısız (çıkış kodu {rc})", "\n".join(tail))


DOWNLOAD_RE = re.compile(r"açılan (\d+) / (\d+) MiB")


def download_progress(line):
    m = DOWNLOAD_RE.search(line)
    if m and int(m.group(2)):
        return min(99, int(100 * int(m.group(1)) / int(m.group(2))))
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 * MIB), b""):
            h.update(block)
    return h.hexdigest()


def free_gib(path):
    return shutil.disk_usage(path).free / 2**30


def remove_tree(path):
    def onexc(func, p, _exc):
        os.chmod(os.path.dirname(p), 0o755)
        if os.path.isdir(p) and not os.path.islink(p):
            os.chmod(p, 0o755)
        func(p)
    if os.path.lexists(path):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, onexc=onexc)
        else:
            os.unlink(path)


# --- commands ------------------------------------------------------------------------------------

def denetle():
    current = int(os.environ["GBOS_MEVCUT_ARTIMLI"])
    update_state(durum="denetleniyor", mesaj="Güncelleme denetleniyor", ilerleme=None)
    try:
        rel = kit_module("mica_indir", "mica-indir.py").latest_release("STABLE")
    except Exception as e:  # network or listing errors are reported, not raised
        update_state(durum="hata", mesaj="Güncelleme sunucusuna ulaşılamadı",
                     hata={"asama": "denetle", "ayrinti": str(e)})
        log(f"denetle: {e}")
        return 2
    build = rel["buildidentifier"]
    newer = build_incremental(build) > current
    ready = read_ready()
    fields = dict(son_denetim=int(time.time()),
                  sunucu={"build": build, "tarih": rel.get("dateupdated", "")},
                  mevcut_artimli=current, ilerleme=None, hata=None)
    if not newer:
        update_state(durum="guncel", mesaj="Sistem güncel", **fields)
    elif ready and ready["build"] == build:
        update_state(durum="hazir", mesaj="Güncelleme kurulmaya hazır", **fields)
    else:
        update_state(durum="yeni-surum", mesaj=f"Yeni sürüm var: {build}", **fields)
    log(f"denetle: sunucuda {build}, tablette {current}, yeni={newer}")
    return 0


def hazirla():
    current = int(os.environ["GBOS_MEVCUT_ARTIMLI"])
    os.makedirs(BUILDS, exist_ok=True)
    try:
        build = _hazirla(current)
    except Failure as f:
        update_state(durum="hata", mesaj=f.message, ilerleme=None,
                     hata={"asama": f.step, "ayrinti": f.detail})
        log(f"HATA [{f.step}] {f.message}\n{f.detail}")
        return 1
    except Exception:
        # Any other error still reaches the app as an error, never as a stuck "hazirlaniyor".
        detail = traceback.format_exc()
        state = read_json(STATE, {})
        update_state(durum="hata", mesaj="Hazırlık beklenmeyen bir hatayla durdu", ilerleme=None,
                     hata={"asama": state.get("asama"), "ayrinti": detail[-2000:]})
        log(f"HATA (beklenmeyen)\n{detail}")
        return 1
    if build is None:
        return 0
    log(f"hazir: {build}")
    return 0


def _hazirla(current):
    update_state(durum="hazirlaniyor", asama="indir", mesaj=STEP_TEXT["indir"], ilerleme=0, hata=None)
    header = target_header()
    # tablete-yaz.py (slots, make_ext4_images.py, the trees' yuvalar.conf) reads it
    os.environ["GBOS_SUPER_BASLIK"] = header
    kit = kit_hash()
    prepare_environment()
    need_space("indir")
    run_step("indir", [sys.executable, "-I", os.path.join(KIT, "mica-indir.py")], tool_env(),
             download_progress)
    source = read_json(os.path.join(IMAGES, "guncel", "kaynak.json"))
    build = source["release"]["buildidentifier"]
    if failed_before(build):
        update_state(durum="guncel", mesaj="Bu sürüm bu kitle geri alındı; yeni sürüm ya da kit bekleniyor",
                     asama=None, ilerleme=None)
        log(f"hazirla: {build} bu kitle daha önce geri alındı")
        return None
    if build_incremental(build) > current:
        reason = "surum"
    elif build_incremental(build) == current and kit_reason():
        reason = "kit"
    else:
        update_state(durum="guncel", mesaj="Sistem güncel", asama=None, ilerleme=None)
        log(f"hazirla: {build} tablettekinden ({current}) yeni değil, kit aynı")
        return None
    log(f"hazirla: {build} ({reason}), kit {kit}, super başlığı {header}")
    ready = read_ready()
    if ready and ready["build"] == build and ready["kit"] == kit and ready["header"] == header and all(
            os.path.exists(os.path.join(OUTPUT, f"{p}.img")) for p in PARTITIONS):
        update_state(durum="hazir", mesaj="Güncelleme kurulmaya hazır", asama=None, ilerleme=None)
        return build

    work = os.path.join(BUILDS, build)
    done_file = os.path.join(work, "ADIMLAR")
    identity = f"kit {kit}\nbaslik {header}\n"
    drop_stale_work(work, identity)

    def done():
        try:
            with open(done_file) as f:
                return {line.split()[0]: line.split()[1:] for line in f if line.strip()}
        except FileNotFoundError:
            return {}

    def mark(step, *extra):
        with open(done_file, "a") as f:
            f.write(" ".join([step, *map(str, extra)]) + "\n")
            f.flush()
            os.fsync(f.fileno())

    if "agac" not in done():
        begin("agac")
        remove_tree(work)
        run_step("agac", [sys.executable, "-I", os.path.join(KIT, "yeni-surum-hazirla.py"),
                          "--hedef", work], tool_env())
        with open(os.path.join(work, IDENTITY), "w") as f:
            f.write(identity)
        mark("agac")
    epoch = int(done().get("yamala", [int(time.time())])[0])
    if "yamala" not in done():
        begin("yamala")
        try:
            run_step("yamala", [sys.executable, "-I", os.path.join(KIT, "apply_patches.py")],
                     tool_env(work))
        except Failure as f:
            # apply_patches.py asserts every byte pattern it patches; a release that changed one
            # of those files needs the patches updated on the computer and a new kit.
            if "AssertionError" in f.detail:
                f.message = "Tablet yamaları bu sürüme uymuyor; yamaların bilgisayarda güncellenmesi gerekiyor"
            raise
        mark("yamala", epoch)
    if "derle" not in done():
        begin("derle")
        run_step("derle", [sys.executable, "-I", os.path.join(KIT, "make_ext4_images.py"),
                           *PARTITIONS], tool_env(work, epoch))
        mark("derle")

    begin("denetle")
    layout = slots()
    parts = {}
    for i, name in enumerate(PARTITIONS):
        img = os.path.join(work, f"{name}.img")
        first, size_mib = layout[name]
        if not os.path.exists(img) or os.path.getsize(img) != size_mib * MIB:
            raise Failure("denetle", f"{name}.img yuvaya uymuyor",
                          f"{img}: {os.path.getsize(img) if os.path.exists(img) else 'yok'} bayt, "
                          f"yuva {size_mib * MIB}")
        res = subprocess.run(["e2fsck", "-fn", img], capture_output=True, text=True,
                             env=tool_env(work, epoch))
        if res.returncode != 0:
            raise Failure("denetle", f"{name}.img dosya sistemi hatalı",
                          (res.stdout + res.stderr)[-2000:])
        dense(img)
        parts[name] = {"dosya": img, "bayt": size_mib * MIB, "sha256": sha256(img),
                       "ilk_mib": first, "boyut_mib": size_mib}
        update_state(ilerleme=int(100 * (i + 1) / len(PARTITIONS)))
        log(f"denetle: {name} {parts[name]['sha256']}")
    files = set_files(work, header)

    begin("temizle")
    for item in os.listdir(work):
        if item not in {f"{p}.img" for p in PARTITIONS} | {"ADIMLAR", "SURUM", IDENTITY, HANDOVER}:
            remove_tree(os.path.join(work, item))
    for item in os.listdir(BUILDS):
        if item != build:
            remove_tree(os.path.join(BUILDS, item))
    keep = os.path.basename(os.path.realpath(os.path.join(IMAGES, "guncel")))
    for item in os.listdir(IMAGES):
        if item not in (keep, "guncel"):
            remove_tree(os.path.join(IMAGES, item))

    hand_over(build, parts, files, reason, kit, header)
    # The images and set files now live in OUTPUT; what is left records steps whose results are
    # gone (the tree went in temizle), so a later preparation of this build starts afresh.
    remove_tree(work)
    update_state(durum="hazir", asama=None, ilerleme=None, hata=None,
                 mesaj="Güncelleme kurulmaya hazır", hazir_build=build)
    return build


def drop_stale_work(work, identity):
    """Removes a build's work directory that cannot be resumed: one made with another kit or for
    another header, and one whose images already left it (handed over; recorded "derle" with no
    images: the set was then installed, replaced or removed, and the tree they were made from was
    removed in temizle). Before 30 Oct 2026 the directory stayed after the hand-over, and every
    later preparation of the same build skipped to denetle and failed on the missing images."""
    if read_text(os.path.join(work, IDENTITY)) != identity.strip():
        remove_tree(work)
        return
    try:
        with open(os.path.join(work, "ADIMLAR")) as f:
            steps = {line.split()[0] for line in f if line.strip()}
    except FileNotFoundError:
        return
    if "derle" in steps and not all(os.path.exists(os.path.join(work, f"{p}.img")) for p in PARTITIONS):
        log(f"hazirla: {os.path.basename(work)} imajları daha önce teslim edilmiş; baştan hazırlanıyor")
        remove_tree(work)


def set_files(work, header):
    """The tree's set files, copied out of the tree before it is removed: {name: (path, sha256)}.
    The header file must be the header the images are made for."""
    src = os.path.join(work, SET_FILES_DIR)
    dst = os.path.join(work, HANDOVER)
    os.makedirs(dst, exist_ok=True)
    out = {}
    for name in SET_FILES:
        if os.path.exists(os.path.join(src, name)):
            shutil.copyfile(os.path.join(src, name), os.path.join(dst, name))
        path = os.path.join(dst, name)
        if not os.path.isfile(path):
            raise Failure("denetle", f"kümenin {name} dosyası yok", src)
        out[name] = (path, sha256(path))
    want = kit_module("super_baslik", os.path.join("super-baslik", "uret.py")).SHA[header]
    if out["super-baslik.bin"][1] != want:
        raise Failure("denetle", "imajdaki super başlığı hedef başlık değil", out["super-baslik.bin"][1])
    return out


def allocated(path):
    """True when every byte of the file has a block on disk (no holes)."""
    st = os.stat(path)
    return st.st_blocks * 512 >= st.st_size


def dense(path):
    """Rewrites a sparse image so every block is allocated. The installer maps the images with AOSP
    uncrypt (FIBMAP) and recovery reads them from the raw device: a hole has no block, so uncrypt
    "fails to find" it and the bytes read back are not the image's (24 Oct 2026, the first
    tablet-made set; mke2fs leaves the unused space as holes, adb push never did)."""
    if allocated(path):
        return
    tmp = path + ".yogun"
    with open(path, "rb") as src, open(tmp, "wb") as dst:
        for block in iter(lambda: src.read(8 * MIB), b""):
            dst.write(block)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp, path)
    if not allocated(path):
        raise Failure("denetle", f"{os.path.basename(path)} diskte tam ayrılamadı", path)


def hand_over(build, parts, files, reason, kit, header):
    """Moves the verified images and set files into the hand-over directory and writes hazir.conf
    last: the Android side installs a set only when hazir.conf names it and every hash matches."""
    os.makedirs(OUTPUT, exist_ok=True)
    if os.path.exists(READY):
        os.unlink(READY)
    for name, p in parts.items():
        os.replace(p["dosya"], os.path.join(OUTPUT, f"{name}.img"))
    for name, (path, _sha) in files.items():
        os.replace(path, os.path.join(OUTPUT, name))
    lines = [f"surum {build}", *(f"bolum {n} {parts[n]['sha256']}" for n in PARTITIONS),
             *(f"dosya {n} {files[n][1]}" for n in SET_FILES),
             f"neden {reason}", f"kit {kit}", f"baslik {header}", "kaynak tablet"]
    tmp = READY + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, READY)
    log(f"teslim: {build} -> {OUTPUT}")


EROFS_TOOLS = ["fsck.erofs", "dump.erofs", "mkfs.erofs"]
REQUIRED_TOOLS = EROFS_TOOLS + ["e2fsdroid", "mke2fs", "e2fsck", "debugfs", "zipalign",
                                "dexdump", "java", "javac", "keytool", "readelf", "cc", "make", "curl"]
CARRIED = os.path.join(WORK, "tasinan")
# The preloaded metadata helpers are code: they live in the environment's own filesystem, which
# may map code, not under /ota (apply_patches.META_LIB_DIR).
META_LIB_DIR = "/var/lib/gbos-guncelleyici"


def prepare_environment():
    """Tools and inputs the pipeline scripts expect at their computer paths."""
    missing = [t for t in REQUIRED_TOOLS if not shutil.which(t)]
    if missing:
        # The kit's ortam-kur.sh installs them (Debian packages, e2fsdroid built from AOSP source);
        # it needs the network, as the download does.
        log(f"eksik araçlar: {' '.join(missing)}; ortam-kur.sh çalışıyor")
        run_step("indir", ["bash", os.path.join(KIT, "guncelleyici", "motor", "ortam-kur.sh")], tool_env())
        missing = [t for t in REQUIRED_TOOLS if not shutil.which(t)]
    if missing:
        raise Failure("indir", "Linux ortamında gerekli araçlar yok", " ".join(missing))
    make_app_key()
    # .local/bin is not copied: the links to the environment's erofs tools are made below.
    shutil.copytree(KIT_HOME, HOME, symlinks=True, dirs_exist_ok=True,
                    ignore=lambda d, names: [".local"] if d == KIT_HOME else [])
    os.chmod(HOME, 0o700)
    bindir = os.path.join(HOME, ".local", "bin")
    os.makedirs(bindir, exist_ok=True)
    for t in EROFS_TOOLS:
        link = os.path.join(bindir, t)
        target = shutil.which(t)
        if os.path.realpath(link) != os.path.realpath(target):
            if os.path.lexists(link):
                os.unlink(link)
            os.symlink(target, link)
    # The metadata helpers are preloaded into this environment's fsck.erofs/mkfs.erofs: built
    # here by the pipeline's own builder whenever their sources change.
    os.makedirs(META_LIB_DIR, exist_ok=True)
    res = subprocess.run([sys.executable, "-I", os.path.join(KIT, "guncelleyici", "yerel", "derle.py"),
                          "--hedef", META_LIB_DIR], capture_output=True, text=True)
    if res.returncode != 0:
        raise Failure("indir", "meta veri kütüphaneleri derlenemedi", (res.stdout + res.stderr)[-2000:])
    if not os.path.isdir(os.path.join(CARRIED, "lineage", "lib32_stage", "system_lib")):
        raise Failure("indir", "LineageOS 32-bit çalışma zamanı kitte yok",
                      os.path.join(CARRIED, "lineage", "lib32_stage"))


def make_app_key():
    """apply_patches.sign_apk re-signs an APK the pipeline changes with the project's app key, which
    comes with a kit from the computer. A kit unpacked from the image has none (an image never
    carries a private key): the tablet makes a key of its own once and keeps it with the kit
    (/work/ev). Today no APK is re-signed (patch_camera_app is off); if one is, the image where
    the key changes still updates: for a system package outside a shared user Android keeps the
    data ("System package ... signature changed; retaining data.", frameworks/base
    ReconcilePackageUtils.java, main, read 25 Oct 2026)."""
    path = os.path.join(KIT_HOME, APP_KEY)
    if os.path.isfile(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    tmp = path + ".yeni"
    if os.path.exists(tmp):
        os.unlink(tmp)
    # the same parameters as anahtar-olustur.sh, so apply_patches.py signs with it
    res = subprocess.run(["keytool", "-genkeypair", "-keystore", tmp, "-storetype", "PKCS12",
                          "-alias", "uygulama", "-keyalg", "RSA", "-keysize", "4096", "-validity", "10950",
                          "-storepass", "gbos-uygulama", "-keypass", "gbos-uygulama",
                          "-dname", "CN=Googlebook OS T870 tablet anahtari, O=Googlebook OS T870, C=TR"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise Failure("indir", "uygulama imza anahtarı oluşturulamadı", (res.stdout + res.stderr)[-2000:])
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    log(f"uygulama imza anahtarı yoktu; tablette yeni anahtar oluşturuldu: {path}")


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def begin(step):
    need_space(step)
    update_state(asama=step, mesaj=STEP_TEXT[step], ilerleme=None)
    log(f"adım: {step}")


def need_space(step):
    free = free_gib(WORK)
    if free < STEP_SPACE[step]:
        raise Failure(step, f"Yer yetersiz: {free:.1f} GiB boş, {STEP_SPACE[step]} GiB gerekiyor")


def on_signal(signum, _frame):
    if _child is not None:
        try:
            os.killpg(_child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    update_state(durum="duraklatildi", mesaj="Hazırlık duraklatıldı; yeniden başlatılınca kaldığı yerden sürer",
                 ilerleme=None)
    log(f"sinyal {signum}: duraklatıldı")
    os._exit(128 + signum)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("denetle", "hazirla", "durum"):
        print(__doc__, file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    os.makedirs(LOGS, exist_ok=True)
    if cmd == "durum":
        print(json.dumps(read_json(STATE, {}), ensure_ascii=False, indent=1))
        return 0
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log(f"{cmd}: motor zaten çalışıyor")
        return 75
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    return denetle() if cmd == "denetle" else hazirla()


if __name__ == "__main__":
    sys.exit(main())
