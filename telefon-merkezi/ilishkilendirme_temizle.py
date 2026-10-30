#!/usr/bin/env python3
"""Telefon Merkezi (com.google.ambient.streaming) CDM ilişkilendirme bakım aracı.

Ne yapar: `dumpsys companiondevice` ya da `cmd companiondevice list <user>` çıktısını okur,
yalnız ambient.streaming'in **self-managed, profilsiz** yinelenen ilişkilendirmelerini bulur,
her eş (mUserId + mDisplayName) için en yeni kaydı tutar, kalanları silmek için
`cmd companiondevice disassociate-all <user>` komutunu, yalnız o kullanıcının bütün kayıtları
yinelenen kayıtsa üretir.

Neden gerekiyor: ambient.streaming var olan ilişkilendirmeyi süreç-içi bir haritayla
(kxq.b, kalıcı değil) arıyor; süreç yeniden başlayınca harita boşalır ve uygulama CDM'de
yeni bir self-managed ilişkilendirme açar. Kayıtlar sınırsız birikir (tablette 13 oldu).
Bu birikme DND/görev sürekliliği sorununu AÇIKLAMAZ ve silmek onu DÜZELTMEZ (bkz.
CIHAZ-TESTI.md); bu araç yalnız hijyen içindir: biriken kayıtları bir kez temizler, uygulamanın
hatası sürdükçe kayıtlar yeniden birikir.

Neye DOKUNMAZ (güvenlik değişmezleri):
  - Google hesabının cihaz grubuna (CryptAuth/Better Together, bulut tarafı) dokunmaz;
    buradaki kayıtlar yerel, cihaz-içi CDM ilişkilendirmeleridir, hesapla ilgisizdir.
  - Yalnız mPackageName == com.google.ambient.streaming, mSelfManaged == true,
    mDeviceProfile == 'null' olan kayıtları aday sayar.
  - MAC'li, saat/kulaklık profilli ya da başka paketli hiçbir kaydı listeye almaz
    (Galaxy Watch, Pixel Buds vb. bu yüzden hiç dokunulmaz).
  - Her eş için en az bir kaydı (en yenisini) her zaman tutar.

Varsayılan: kuru çalışma (plan). Canlı komutları yazdırmak için `--uygula`.
Girdi yoksa stdin'den okur. Cihazda okuma güvenlidir; silme komutlarını kullanıcı
ana oturumda kendi çalıştırır.
"""
import argparse
import re
import sys

PAKET = "com.google.ambient.streaming"
_ASSOC = re.compile(r"Association\{([^}]*)\}")
_ALAN = re.compile(r"(\w+)=('[^']*'|[^,]*)")


def _coz(govde):
    """Bir Association{...} gövdesini alan sözlüğüne çevirir. Tırnaklar atılır."""
    alanlar = {}
    for ad, ham in _ALAN.findall(govde):
        deger = ham.strip()
        if len(deger) >= 2 and deger[0] == "'" and deger[-1] == "'":
            deger = deger[1:-1]
        alanlar[ad] = deger
    return alanlar


def ilishkilendirmeleri_ayikla(metin):
    """Metindeki her Association{...} için alan sözlüğü verir (sıra korunur)."""
    return [_coz(m.group(1)) for m in _ASSOC.finditer(metin)]


def _aday_mi(a):
    return (a.get("mPackageName") == PAKET
            and a.get("mSelfManaged") == "true"
            and a.get("mDeviceProfile") == "null"
            and a.get("mDeviceMacAddress") in (None, "null")
            and a.get("mRevoked") != "true")


def _id(a):
    try:
        return int(a.get("mId", "-1"))
    except ValueError:
        return -1


def plan_yap(metin):
    """(tutulanlar, silinecekler) döndürür. Silinecekler eş başına en yeni hariç tümü.

    'En yeni' = en büyük mId (ilişkilendirme kimlikleri oluşturma sırasına göre monoton artar;
    en büyük kimlik uygulamanın şu an kullandığı kayıttır). Aday olmayan hiçbir kayıt dönmez.
    """
    # Döküm her ilişkilendirmeyi iki kez yazar (Associations bloğu + Remote devices önbelleği);
    # (user, id) ile tekilleştir, yoksa aynı kayıt hem tutulur hem silinir görünür.
    benzersiz = {}
    for a in ilishkilendirmeleri_ayikla(metin):
        if _aday_mi(a) and _id(a) >= 0:
            benzersiz.setdefault((a.get("mUserId"), _id(a)), a)
    adaylar = list(benzersiz.values())
    gruplar = {}
    for a in adaylar:
        gruplar.setdefault((a.get("mUserId"), a.get("mDisplayName")), []).append(a)
    tutulan, silinecek = [], []
    for kayitlar in gruplar.values():
        kayitlar.sort(key=_id)
        tutulan.append(kayitlar[-1])
        silinecek.extend(kayitlar[:-1])
    silinecek.sort(key=_id)
    return tutulan, silinecek


def komutlar(metin, silinecek):
    """Android 17'nin kabuğu tek bir ilişkilendirmeyi yalnız MAC adresiyle siler
    (`disassociate USER PACKAGE ADDRESS`, CompanionDeviceShellCommand); bu kayıtlarda MAC yok.
    Kullanılabilen tek yol `disassociate-all USER`: yalnız o kullanıcının bütün kayıtları
    yinelenen ambient.streaming kayıtlarıysa üretilir. Uygulama bir sonraki bağlantıda tek ve
    yeni bir kayıt açar (23 Ekim 2026, tablette 13 kayıt -> 1). Başka bir kayıt varsa
    (saat, kulaklık, App Cast) o kullanıcı için komut üretilmez."""
    hepsi = {}
    for a in ilishkilendirmeleri_ayikla(metin):
        hepsi.setdefault(a.get("mUserId"), {})[_id(a)] = a
    out = []
    for user in sorted({a.get("mUserId") for a in silinecek}, key=str):
        if all(_aday_mi(a) for a in hepsi.get(user, {}).values()):
            out.append(f"cmd companiondevice disassociate-all {user}")
    return out


def _rapor(metin, tutulan, silinecek, uygula):
    out = []
    if not silinecek:
        out.append("Yinelenen self-managed ambient.streaming ilişkilendirmesi yok; yapılacak bir şey yok.")
        return "\n".join(out)
    out.append(f"Tutulan (eş başına en yeni): {len(tutulan)}")
    for a in sorted(tutulan, key=_id):
        out.append(f"  TUT   user={a.get('mUserId')} id={_id(a):>3} '{a.get('mDisplayName')}'"
                   f" sonBaglanti={a.get('mLastTimeConnectedMs')}")
    out.append(f"Silinecek yinelenen: {len(silinecek)}")
    for a in silinecek:
        out.append(f"  SİL   user={a.get('mUserId')} id={_id(a):>3} '{a.get('mDisplayName')}'"
                   f" sonBaglanti={a.get('mLastTimeConnectedMs')}")
    out.append("")
    if uygula:
        kom = komutlar(metin, silinecek)
        out.append("# Ana oturumda, yalnız bu cihazda çalıştır:")
        out.extend(kom)
        if not kom:
            out.append("# Bu kullanıcıda başka ilişkilendirmeler de var; kabuk MAC'siz kaydı tek tek"
                       " silemediği için güvenli komut yok.")
    else:
        out.append("Komutları görmek için --uygula ekle (yalnız yazdırır, kendi çalıştırmaz).")
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dosya", nargs="?", help="dumpsys/cmd companiondevice çıktısı; yoksa stdin")
    p.add_argument("--uygula", action="store_true",
                   help="temizlik komutunu yazdır (çalıştırmaz)")
    ns = p.parse_args(argv)
    metin = open(ns.dosya, encoding="utf-8").read() if ns.dosya else sys.stdin.read()
    tutulan, silinecek = plan_yap(metin)
    print(_rapor(metin, tutulan, silinecek, ns.uygula))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
