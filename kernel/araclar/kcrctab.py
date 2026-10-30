#!/usr/bin/env python3
"""Bir arm64 kernel Image'ından modversions CRC tablosunu okur.

CONFIG_MODVERSIONS=y ve CONFIG_MODULE_REL_CRCS kapalıyken __kcrctab ve
__kcrctab_gpl bölümleri her dışa aktarılan sembol için 32 bit mutlak CRC
taşır; bölüm sınırları System.map'teki __start___kcrctab* / __stop___kcrctab*
sembolleridir ve Image dosyasının 0. baytı _text'tir.

Kullanım: kcrctab.py <System.map> <Image>   -> her satırda bir CRC (0x%08x),
          bölüm sırasıyla (önce __kcrctab, sonra __kcrctab_gpl).
          kcrctab.py <System.map> <Image> <başka-Image>
              -> iki tablonun başka Image'da aynı baytlarla bulunduğu ofsetler.
              Başka Image'ın yerleşimi farklı olabilir (System.map'i yoksa); tablo
              bayt bayt bulunursa oradaki CRC'ler aynıdır. Bulunamazsa çıkış 1.
"""
import struct
import sys


def semboller(harita):
    s = {}
    with open(harita) as f:
        for satir in f:
            p = satir.split()
            if len(p) == 3:
                s[p[2]] = int(p[0], 16)
    return s


def tablolar(harita, image):
    s = semboller(harita)
    veri = open(image, "rb").read()
    taban = s["_text"]
    for bolum in ("__kcrctab", "__kcrctab_gpl"):
        bas, son = s[f"__start_{bolum}"], s[f"__stop_{bolum}"]
        if (son - bas) % 4:
            raise SystemExit(f"{bolum} boyu 4'ün katı değil")
        parca = veri[bas - taban:son - taban]
        if len(parca) != son - bas:
            raise SystemExit(f"{bolum} Image dışına taşıyor")
        yield bolum, parca


def main():
    if len(sys.argv) not in (3, 4):
        raise SystemExit(__doc__)
    if len(sys.argv) == 3:
        for _, parca in tablolar(sys.argv[1], sys.argv[2]):
            for (crc,) in struct.iter_unpack("<I", parca):
                print(f"0x{crc:08x}")
        return
    baska = open(sys.argv[3], "rb").read()
    durum = 0
    for bolum, parca in tablolar(sys.argv[1], sys.argv[2]):
        i = baska.find(parca)
        if i < 0:
            print(f"{bolum}: {len(parca) // 4} CRC başka Image'da bulunamadı")
            durum = 1
        else:
            print(f"{bolum}: {len(parca) // 4} CRC, başka Image'da ofset 0x{i:x}")
    raise SystemExit(durum)


if __name__ == "__main__":
    main()
