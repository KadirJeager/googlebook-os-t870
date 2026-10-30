#!/usr/bin/env python3
"""Bir dizinin git ağaç kimliğini (tree id) hesaplar.

AOSP önceden derlenmiş araçları (clang, build-tools) gitiles'tan tar olarak
iner; tar dosyasının baytları sabit değildir ama içeriğin git ağaç kimliği
sabittir. build.sh indirdiği dizini bu kimlikle doğrular: kimlik, depodaki
etiketin gösterdiği ağaçla aynıysa içerik bayt bayt aynıdır.

Kullanım: git-agac.py <dizin>   -> stdout'a 40 haneli ağaç kimliği
"""
import hashlib
import os
import stat
import sys


def _nesne(tur: bytes, veri: bytes) -> bytes:
    h = hashlib.sha1()
    h.update(tur + b" " + str(len(veri)).encode() + b"\0")
    h.update(veri)
    return h.digest()


def _blob_dosya(yol: str) -> bytes:
    boy = os.path.getsize(yol)
    h = hashlib.sha1()
    h.update(b"blob " + str(boy).encode() + b"\0")
    with open(yol, "rb") as f:
        for parca in iter(lambda: f.read(1 << 20), b""):
            h.update(parca)
    return h.digest()


def agac(dizin: str) -> bytes:
    girdiler = []
    for ad in os.listdir(dizin):
        yol = os.path.join(dizin, ad)
        st = os.lstat(yol)
        if stat.S_ISLNK(st.st_mode):
            kip, kimlik = b"120000", _nesne(b"blob", os.readlink(yol).encode())
        elif stat.S_ISDIR(st.st_mode):
            kip, kimlik = b"40000", agac(yol)
        elif stat.S_ISREG(st.st_mode):
            kip = b"100755" if st.st_mode & 0o111 else b"100644"
            kimlik = _blob_dosya(yol)
        else:
            raise SystemExit(f"desteklenmeyen dosya türü: {yol}")
        # git, dizinleri sıralarken adın sonuna '/' eklenmiş gibi davranır
        anahtar = ad.encode() + (b"/" if kip == b"40000" else b"")
        girdiler.append((anahtar, kip + b" " + ad.encode() + b"\0" + kimlik))
    girdiler.sort(key=lambda g: g[0])
    return _nesne(b"tree", b"".join(g[1] for g in girdiler))


def main() -> None:
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        raise SystemExit("kullanım: git-agac.py <dizin>")
    print(agac(sys.argv[1]).hex())


if __name__ == "__main__":
    main()
