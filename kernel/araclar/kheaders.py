#!/usr/bin/env python3
"""CONFIG_IKHEADERS arşivi (kernel/kheaders_data.tar.xz) için araçlar.

kernel/gen_kheaders.sh başlıkları cpio ile kopyalayıp tar -J ile paketler; arşive
dosyaların kopyalandığı anın zamanı, derleyen kullanıcı ve dosya sisteminin dizin
sırası girer. Bu üst veri derleme ortamının gürültüsüdür, başlıkların içeriği değil;
yine de arşiv Image'ın ortasında durduğu için boyu değişince arkasındaki her şey
kayar ve Image bayt bayt karşılaştırılamaz. Bu araç:

  cikar <Image> <cikti.tar.xz>        Image'a gömülü arşivi çıkarır
  karsilastir <a.tar.xz> <b.tar.xz>   ad, tür, kip ve içerik birebir aynı mı (çıkış 0)
  normalle <girdi> <cikti> <epoch>    sıralı, root:root, sabit zamanlı, xz -6 arşiv
"""
import io
import lzma
import sys
import tarfile

XZ_IMZA = b"\xfd7zXZ\x00"


def _tar(veri: bytes) -> tarfile.TarFile:
    return tarfile.open(fileobj=io.BytesIO(lzma.decompress(veri)))


def cikar(image: str, cikti: str) -> None:
    d = open(image, "rb").read()
    i = d.find(XZ_IMZA)
    while i >= 0:
        try:
            ac = lzma.LZMADecompressor()
            ham = ac.decompress(d[i:])
            boy = len(d) - i - len(ac.unused_data)
            adlar = tarfile.open(fileobj=io.BytesIO(ham)).getnames()
            if any(a.startswith("./include/linux/") for a in adlar):
                open(cikti, "wb").write(d[i:i + boy])
                print(f"kheaders: ofset 0x{i:x}, {boy} bayt, {len(adlar)} üye")
                return
        except (lzma.LZMAError, tarfile.TarError, EOFError):
            pass
        i = d.find(XZ_IMZA, i + 1)
    raise SystemExit("Image içinde kheaders arşivi bulunamadı")


def _icerik(yol: str) -> dict:
    t = _tar(open(yol, "rb").read())
    r = {}
    for m in t.getmembers():
        govde = t.extractfile(m).read() if m.isfile() else None
        r[m.name] = (m.type, m.mode, m.linkname, govde)
    return r


def karsilastir(a: str, b: str) -> int:
    x, y = _icerik(a), _icerik(b)
    if set(x) != set(y):
        fark = sorted(set(x) ^ set(y))
        print(f"üye kümeleri farklı ({len(fark)}): {fark[:10]}")
        return 1
    fark = sorted(n for n in x if x[n] != y[n])
    if fark:
        print(f"içerik ya da kip farklı ({len(fark)}): {fark[:10]}")
        return 1
    print(f"kheaders içerik olarak aynı: {len(x)} üye")
    return 0


def normalle(girdi: str, cikti: str, epoch: int) -> None:
    t = _tar(open(girdi, "rb").read())
    uyeler = sorted(t.getmembers(), key=lambda m: m.name)
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w", format=tarfile.GNU_FORMAT) as yeni:
        for m in uyeler:
            govde = t.extractfile(m) if m.isfile() else None
            m.uid = m.gid = 0
            m.uname = m.gname = "root"
            m.mtime = epoch
            yeni.addfile(m, govde)
    veri = lzma.compress(tampon.getvalue(), format=lzma.FORMAT_XZ,
                         check=lzma.CHECK_CRC64, preset=6)
    open(cikti, "wb").write(veri)


def main() -> None:
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == "cikar":
        cikar(a[1], a[2])
    elif len(a) == 3 and a[0] == "karsilastir":
        raise SystemExit(karsilastir(a[1], a[2]))
    elif len(a) == 4 and a[0] == "normalle":
        normalle(a[1], a[2], int(a[3]))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
