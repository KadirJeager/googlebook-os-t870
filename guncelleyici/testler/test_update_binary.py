"""kurtarma/update-binary run the way recovery runs it: the shell and toybox of the tablet's
recovery (mksh R59, toybox 0.8.13, built by araclar.sh), the API/status-fd/package arguments of
bootable/recovery install.cpp, block maps in AOSP uncrypt's format over a userdata image with the
image blocks scattered and out of order, and a super image with a LineageOS half that must never
change. The device nodes are files here (GBOS_TEST=1: update-binary's only test seam; recovery
never sets it); the real block devices and uncrypt are exercised on the emulator
(emulator/senaryo.sh) and on the tablet (BENIOKU.md, test plan).

    python3 -I -m pytest guncelleyici/testler -q
"""
import hashlib
import os
import random
import stat
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GUNCELLEYICI = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(GUNCELLEYICI, "kurtarma"))
import paket  # noqa: E402

MIB = 1 << 20
BLOCK = 4096
SLOTS = {"system": (2, 3), "system_ext": (5, 1), "product": (6, 2)}  # first MiB, MiB
# an older header whose product slot is smaller: the real googlebookos-1 -> googlebookos-2 change
OLD_SLOTS = dict(SLOTS, product=(6, 1))
SUPER_MIB = 9
USERDATA_BLOCKS = 16 * 256


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="session")
def bin_dir():
    out = subprocess.run([os.path.join(HERE, "araclar.sh")], check=True, capture_output=True,
                         text=True).stdout.strip().splitlines()[-1]
    assert os.path.exists(os.path.join(out, "sh"))
    return out


class Tablet:
    """cache dir, super, userdata and misc images, block maps and islem.conf."""

    def __init__(self, root, bin_dir, seed=1, header_change=False):
        self.root, self.bin = str(root), bin_dir
        self.header_change = header_change
        self.rng = random.Random(seed)
        self.dir = os.path.join(self.root, "cache", "gbos")
        os.makedirs(self.dir)
        self.super = os.path.join(self.root, "super.img")
        self.userdata = os.path.join(self.root, "userdata.img")
        self.misc = os.path.join(self.root, "misc.img")
        header = self.rng.randbytes(MIB)
        self.lineage = self.rng.randbytes((SUPER_MIB - 1) * MIB)
        with open(self.super, "wb") as f:
            f.write(header + self.lineage)
        self.header_sha = sha(header)
        # header_change: super holds an older header, the images are made for a newer one
        self.target = header
        if header_change:
            self.old_header, self.target = header, self.rng.randbytes(MIB)
            for name, data in (("onceki", self.old_header), ("hedef", self.target)):
                with open(os.path.join(self.dir, f"baslik-{name}.bin"), "wb") as f:
                    f.write(data)
        self.target_sha = sha(self.target)
        with open(self.userdata, "wb") as f:
            f.write(self.rng.randbytes(USERDATA_BLOCKS * BLOCK))
        with open(self.misc, "wb") as f:
            f.write(b"\0" * 4096)
        self.free = list(range(USERDATA_BLOCKS))
        self.rng.shuffle(self.free)
        self.images = {"yeni": {}, "eski": {}}
        for name, (first, size) in SLOTS.items():
            self.images["yeni"][name] = self.rng.randbytes(size * MIB)
            # the running system: what super holds now
            self.images["eski"][name] = self.read_slot(name)
        for s in ("yeni", "eski"):
            for name in SLOTS:
                self.place(s, name)
        self.write_conf()

    def read_slot(self, name):
        first, size = SLOTS[name]
        with open(self.super, "rb") as f:
            f.seek(first * MIB)
            return f.read(size * MIB)

    def outside_slots(self):
        with open(self.super, "rb") as f:
            data = f.read()
        keep = bytearray(data)
        for first, size in SLOTS.values():
            keep[first * MIB:(first + size) * MIB] = b""  # placeholder, removed below
        parts, pos = [], 0
        for first, size in sorted(SLOTS.values()):
            parts.append(data[pos:first * MIB])
            pos = (first + size) * MIB
        parts.append(data[pos:])
        return b"".join(parts)

    def place(self, s, name, device=None):
        """Writes an image into scattered userdata blocks and its uncrypt-format map."""
        data = self.images[s][name]
        n = len(data) // BLOCK
        blocks = [self.free.pop() for _ in range(n)]
        with open(self.userdata, "r+b") as f:
            for i, b in enumerate(blocks):
                f.seek(b * BLOCK)
                f.write(data[i * BLOCK:(i + 1) * BLOCK])
        ranges = []
        for b in blocks:  # uncrypt's add_block_to_ranges
            if ranges and ranges[-1][1] == b:
                ranges[-1][1] += 1
            else:
                ranges.append([b, b + 1])
        with open(os.path.join(self.dir, f"{s}-{name}.map"), "w") as f:
            f.write(f"{device or self.userdata}\n{len(data)} {BLOCK}\n{len(ranges)}\n")
            for a, b in ranges:
                f.write(f"{a} {b}\n")

    def write_conf(self, neden="kullanici", extra=(), slots=None, header=None):
        lines = ["bicim 1", "surum_yeni 16560795_CL3B.260622.276.R2",
                 "surum_eski 16471258_CL3B.260622.273.R1", f"neden {neden}",
                 f"super_baslik_sha256 {header or self.header_sha}"]
        if self.header_change:
            lines.append(f"super_baslik_hedef {self.target_sha}")
        for name, (first, size) in (slots or SLOTS).items():
            lines.append(f"bolum {name} {first} {size} {size * MIB} "
                         f"{sha(self.images['yeni'][name])} {sha(self.images['eski'][name])}")
        with open(os.path.join(self.dir, "islem.conf"), "w") as f:
            f.write("\n".join([*lines, *extra]) + "\n")

    def run(self, package="kur.zip", path_prefix=None, old_slots=OLD_SLOTS):
        table = " ".join(f"{self.target_sha} {n} {a} {b}" for n, (a, b) in sorted(SLOTS.items()))
        if self.header_change:
            table += " " + " ".join(f"{self.header_sha} {n} {a} {b}"
                                    for n, (a, b) in sorted(old_slots.items()))
        ub = os.path.join(self.root, "update-binary")
        with open(ub, "wb") as f:
            f.write(paket.update_binary(table))
        os.chmod(ub, 0o755)
        env = {"PATH": ":".join(filter(None, [path_prefix, self.bin])), "GBOS_TEST": "1",
               "GBOS_DIZIN": self.dir, "GBOS_SUPER": self.super, "GBOS_USERDATA": self.userdata,
               "GBOS_MISC": self.misc, "TZ": "UTC"}
        # recovery hands the updater the write end of a pipe (install.cpp TryUpdateBinary)
        r, w = os.pipe()
        try:
            res = subprocess.run([os.path.join(self.bin, "sh"), ub, "3", str(w),
                                  f"/cache/gbos/{package}"], env=env, pass_fds=(w,),
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True)
        finally:
            os.close(w)
        with os.fdopen(r, encoding="utf-8") as f:
            self.status = f.read()
        return res

    def sonuc(self):
        return open(os.path.join(self.dir, "sonuc")).read().strip()

    def exists(self, name):
        return os.path.exists(os.path.join(self.dir, name))

    def slots_are(self, s):
        return all(self.read_slot(n) == self.images[s][n] for n in SLOTS)

    def header_now(self):
        with open(self.super, "rb") as f:
            return sha(f.read(MIB))


@pytest.fixture
def tablet(tmp_path, bin_dir):
    return Tablet(tmp_path, bin_dir)


def test_kurulum(tablet):
    lineage = tablet.outside_slots()
    res = tablet.run("kur.zip")
    assert res.returncode == 0, res.stderr
    assert tablet.slots_are("yeni")
    assert tablet.outside_slots() == lineage
    assert tablet.sonuc() == "kuruldu 16560795_CL3B.260622.276.R2"
    deneme = open(os.path.join(tablet.dir, "deneme")).read()
    assert "durum=deneme\n" in deneme and "sayac=0\n" in deneme
    assert not tablet.exists("yaziliyor")
    assert "ui_print Doğrulandı: product" in tablet.status
    assert "progress 0.30" in tablet.status


def test_geri_donus(tablet):
    assert tablet.run("kur.zip").returncode == 0
    lineage = tablet.outside_slots()
    tablet.write_conf(neden="bekci")
    res = tablet.run("geri.zip")
    assert res.returncode == 0, res.stderr
    assert tablet.slots_are("eski")
    assert tablet.outside_slots() == lineage
    assert tablet.sonuc() == "geri-donuldu 16471258_CL3B.260622.273.R1 bekci"
    assert not tablet.exists("deneme")


def unchanged(tablet, expect):
    before = open(tablet.super, "rb").read()
    res = tablet.run("kur.zip")
    assert res.returncode == 0, res.stderr
    assert open(tablet.super, "rb").read() == before
    assert tablet.sonuc().startswith("yapilmadi kur"), tablet.sonuc()
    assert expect in tablet.sonuc()
    assert not tablet.exists("deneme")


def test_bozuk_yeni_imaj_yazilmaz(tablet):
    with open(os.path.join(tablet.dir, "yeni-system_ext.map")) as f:
        lines = f.read().splitlines()
    first = int(lines[3].split()[0])
    with open(tablet.userdata, "r+b") as f:
        f.seek(first * BLOCK)
        f.write(b"\xff" * 16)
    unchanged(tablet, "yeni system_ext imajı bozuk okunuyor")


def test_eski_harita_yoksa_yazilmaz(tablet):
    os.remove(os.path.join(tablet.dir, "eski-product.map"))
    unchanged(tablet, "eski product blok haritası geçersiz")


def test_baska_aygita_isaret_eden_harita(tablet):
    tablet.place("yeni", "system", device=tablet.super)
    unchanged(tablet, "yeni system blok haritası geçersiz")


def test_aygit_disina_tasan_aralik(tablet):
    path = os.path.join(tablet.dir, "yeni-product.map")
    lines = open(path).read().splitlines()
    a, b = map(int, lines[3].split())
    lines[3] = f"{USERDATA_BLOCKS + 5} {USERDATA_BLOCKS + 5 + (b - a)}"
    open(path, "w").write("\n".join(lines) + "\n")
    unchanged(tablet, "yeni product blok haritası geçersiz")


def test_baslik_farkli(tablet):
    with open(tablet.super, "r+b") as f:
        f.write(b"\0" * MIB)
    unchanged(tablet, "beklenmeyen super başlığı")


def test_bilinmeyen_baslik(tablet):
    tablet.write_conf(header="0" * 64)
    unchanged(tablet, "bilinmeyen super başlığı")


def test_tanimsiz_yuva(tablet):
    slots = dict(SLOTS, product=(1, 2))  # would overwrite the header area
    tablet.write_conf(slots=slots)
    unchanged(tablet, "yuva product 1+2 bu başlık için tanımlı değil")


@pytest.mark.parametrize("satir", ["bolum vendor 1 1 1048576 " + "a" * 64 + " " + "b" * 64,
                                   "bilinmeyen 1", "bolum system 2 3 3145728 x y"])
def test_bozuk_islem_dosyasi(tablet, satir):
    tablet.write_conf(extra=[satir])
    unchanged(tablet, "islem-bozuk")


def test_bilinmeyen_paket_adi(tablet):
    before = open(tablet.super, "rb").read()
    res = tablet.run("baska.zip")
    assert res.returncode == 0
    assert tablet.sonuc() == "yapilmadi paket-adi"
    assert open(tablet.super, "rb").read() == before


def corrupting_dd(tablet, fail_writes, skip_writes=0):
    """A dd in front of toybox's that writes zeros instead of the data for `fail_writes` writes
    into super after the first `skip_writes` (a slot that does not take what was written)."""
    wrap = os.path.join(tablet.root, "sarmal")
    os.makedirs(wrap)
    counter = os.path.join(tablet.root, "dd-sayac")
    with open(os.path.join(wrap, "dd"), "w") as f:
        f.write(f"""#!{tablet.bin}/sh
case " $* " in
  *" of={tablet.super} "*)
    n=$(cat {counter} 2>/dev/null || echo 0); n=$((n + 1)); echo $n > {counter}
    if [ $n -gt {skip_writes} ] && [ $n -le {skip_writes + fail_writes} ]; then
      set -- "$@"; args=""
      for a in "$@"; do case "$a" in if=*) a=if=/dev/zero ;; skip=*) a=skip=0 ;; esac; args="$args $a"; done
      exec {tablet.bin}/toybox dd $args
    fi ;;
esac
exec {tablet.bin}/toybox dd "$@"
""")
    os.chmod(os.path.join(wrap, "dd"), 0o755)
    return wrap


def test_yeni_dogrulanamazsa_eski_geri_yazilir(tablet):
    lineage = tablet.outside_slots()
    wrap = corrupting_dd(tablet, fail_writes=1)
    res = tablet.run("kur.zip", path_prefix=wrap)
    assert res.returncode == 0, res.stderr
    assert tablet.slots_are("eski")
    assert tablet.outside_slots() == lineage
    assert tablet.sonuc() == "kurulamadi-geri-yuklendi 16560795_CL3B.260622.276.R2"
    assert not tablet.exists("deneme") and not tablet.exists("yaziliyor")


def bcb(tablet):
    data = open(tablet.misc, "rb").read()
    return data[:32].rstrip(b"\0"), data[64:64 + 768].rstrip(b"\0")


def test_iki_kume_de_dogrulanamazsa_kurtarmada_kalir(tablet):
    wrap = corrupting_dd(tablet, fail_writes=10 ** 6)
    res = tablet.run("kur.zip", path_prefix=wrap)
    assert res.returncode == 3, res.stderr
    assert tablet.sonuc().startswith("felaket ")
    assert bcb(tablet) == (b"boot-recovery", b"recovery\n--show_text\n")
    assert open(os.path.join(tablet.dir, "test-reboot")).read() == "STAY_IN_RECOVERY\n"
    assert tablet.exists("yaziliyor")  # the boot guard would send a booting system back here


def test_bekci_geri_donusu_yapilamazsa_kurtarmada_kalir(tablet):
    tablet.write_conf(neden="bekci")
    os.remove(os.path.join(tablet.dir, "eski-system.map"))
    before = open(tablet.super, "rb").read()
    res = tablet.run("geri.zip")
    assert res.returncode == 3
    assert open(tablet.super, "rb").read() == before
    assert tablet.sonuc().startswith("yapilmadi geri")
    assert bcb(tablet) == (b"boot-recovery", b"recovery\n--show_text\n")


def test_kullanici_geri_donusu_yapilamazsa_sisteme_doner(tablet):
    os.remove(os.path.join(tablet.dir, "eski-system.map"))
    res = tablet.run("geri.zip")
    assert res.returncode == 0
    assert bcb(tablet) == (b"", b"")


def test_yarida_kalan_yazma_bastan_tamamlanir(tablet):
    with open(os.path.join(tablet.dir, "yaziliyor"), "w") as f:
        f.write("kur kur.zip\n")
    with open(tablet.super, "r+b") as f:  # half-written system slot
        f.seek(SLOTS["system"][0] * MIB)
        f.write(tablet.images["yeni"]["system"][:MIB])
    tablet.images["eski"]["system"] = tablet.images["eski"]["system"]  # eski still on userdata
    res = tablet.run("kur.zip")
    assert res.returncode == 0, res.stderr
    assert tablet.slots_are("yeni")
    assert "önceki çalışma yazarken kesilmiş" in open(os.path.join(tablet.dir, "kurulum.log")).read()


def test_32_bit_sinirinda_boyut(tablet):
    """The byte count of the real 3096 MiB product slot exceeds 32 bits: update-binary compares it
    through toybox expr (64-bit), because mksh's $(( )) wraps (checked here too)."""
    sh = os.path.join(tablet.bin, "sh")
    run = lambda c: subprocess.run([sh, "-c", c], capture_output=True, text=True,
                                   env={"PATH": tablet.bin}).stdout.strip()
    assert run('expr 3096 \\* 1048576') == "3246391296"
    assert run('echo $((3096 * 1048576))') != "3246391296"
    assert '$(expr "$size" \\* 1048576)' in open(paket.UPDATE_BINARY).read()


# --- super header change (googlebookos-1 -> googlebookos-2) ----------------------------------

@pytest.fixture
def header_change(tmp_path, bin_dir):
    return Tablet(tmp_path, bin_dir, header_change=True)


def after_header(tablet):
    """super outside the slots and its first MiB: the LineageOS half."""
    return tablet.outside_slots()[MIB:]


def test_baslik_degisimi_kurulum(header_change):
    t = header_change
    lineage = after_header(t)
    res = t.run("kur.zip")
    assert res.returncode == 0, res.stderr
    assert t.header_now() == t.target_sha
    assert t.slots_are("yeni")
    assert after_header(t) == lineage
    assert t.sonuc() == "kuruldu 16560795_CL3B.260622.276.R2"
    assert t.status.index("Doğrulandı: super başlığı") < t.status.index("Yazılıyor: system")


def test_baslik_degisimi_geri_donus(header_change):
    t = header_change
    assert t.run("kur.zip").returncode == 0
    t.write_conf(neden="bekci")
    res = t.run("geri.zip")
    assert res.returncode == 0, res.stderr
    assert t.slots_are("eski")
    assert t.header_now() == t.header_sha
    assert t.sonuc() == "geri-donuldu 16471258_CL3B.260622.273.R1 bekci"


def test_baslik_yazilamazsa_hicbir_sey_degismez(header_change):
    t = header_change
    before = open(t.super, "rb").read()
    wrap = corrupting_dd(t, fail_writes=1)  # the first write into super is the header
    res = t.run("kur.zip", path_prefix=wrap)
    assert res.returncode == 0, res.stderr
    assert open(t.super, "rb").read() == before
    assert t.sonuc() == "yapilmadi kur super başlığı yazılamadı"
    assert not t.exists("yaziliyor") and not t.exists("deneme")


def test_baslik_sonrasi_yeni_dogrulanamazsa_eski_ve_onceki_baslik(header_change):
    t = header_change
    lineage = after_header(t)
    wrap = corrupting_dd(t, fail_writes=1, skip_writes=1)  # the header goes through, an image not
    res = t.run("kur.zip", path_prefix=wrap)
    assert res.returncode == 0, res.stderr
    assert t.slots_are("eski")
    assert t.header_now() == t.header_sha
    assert after_header(t) == lineage
    assert t.sonuc() == "kurulamadi-geri-yuklendi 16560795_CL3B.260622.276.R2"


def test_baslik_yazildiktan_sonra_kesilen_kurulum_tamamlanir(header_change):
    t = header_change
    with open(t.super, "r+b") as f:
        f.write(t.target)
    with open(os.path.join(t.dir, "yaziliyor"), "w") as f:
        f.write("kur kur.zip\n")
    res = t.run("kur.zip")
    assert res.returncode == 0, res.stderr
    assert t.header_now() == t.target_sha and t.slots_are("yeni")
    assert "Super başlığı yazılıyor" not in t.status


def test_baslik_dosyasi_bozuksa_yazilmaz(header_change):
    t = header_change
    with open(os.path.join(t.dir, "baslik-hedef.bin"), "r+b") as f:
        f.write(b"\0")
    unchanged(t, "baslik-hedef.bin bozuk")


def test_onceki_yuva_yenisinin_icinde_degilse_yazilmaz(header_change):
    t = header_change
    before = open(t.super, "rb").read()
    res = t.run("kur.zip", old_slots=dict(SLOTS, product=(6, 3)))
    assert res.returncode == 0
    assert open(t.super, "rb").read() == before
    assert "yenisinin içinde değil" in t.sonuc()


def test_bilinmeyen_hedef_baslik(header_change):
    t = header_change
    t.write_conf(extra=["super_baslik_hedef " + "c" * 64])
    unchanged(t, "bilinmeyen hedef super başlığı")
