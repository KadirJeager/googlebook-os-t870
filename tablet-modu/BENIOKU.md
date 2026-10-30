# Tablet modu: hızlı ayarlar düğmesi

Book Cover Keyboard'un MCU'su kılıfın üç konumunu ayırt eder: ekranın üstüne kapalı, açık ve ekranın arkasına düz katlanmış. Kılıf ekranın arkasında açılı dururken ("ters L") MCU dizüstü konumuyla bayt bayt aynı olayı gönderir (`[04-00-04][02]`); kanıt `cihaz-kabul/kanit/20261025-kernel/`. Bu konumu donanımdan ayırt etmenin yolu yok. Bu düğme kullanıcının "şu an tablet olarak kullanıyorum" demesidir.

## Parçalar

| Parça | Ne yapar |
|---|---|
| `kernel/kaynak-yamalari/0002-...patch` | `/sys/devices/virtual/sec/sec_keypad/tablet_mode`. 1 iken MCU'nun "açık" bildirimi dinleyicilere "arkaya katlı" olarak iletilir: klavye ve touchpad kaldırılır, `hall_logical` `SW_TABLET_MODE=1` bildirir. Gerçek arkaya katlamayla aynı kod yolu. Kapalı kapak kapalı kalır (`SW_LID`, uyku). Açılışta 0. |
| `uygulama/` | `com.gbos.tabletmodu`, priv-app. `TabletModuKutucugu` hızlı ayar kutucuğu: düğümü okur/yazar; açıkken "Klavye kapalı", kapalıyken "Kılıfa göre". `AcilisAlicisi` her kullanıcı başlarken düğüm varsa kutucuğu etkinleştirir, yoksa gizler ve kullanıcının tercihini düğüme geri yazar. Tercih kullanıcının cihaz korumalı depolamasında. |
| `cihaz/system/etc/init/gbos-tablet-modu.rc` | Açılışta düğümü `0666` yapar (uygulama kullanıcının uid'iyle çalışır). |
| `selinux/71-gbos-tablet-modu.cil` | Düğüme `gbos_tablet_mode_sysfs` etiketi, `priv_app_all` okur/yazar, init `setattr`. `mlstrustedobject`: uygulamaların MLS kategorileri var, düğüm sistem çapında tek anahtar. `selinux/politika/plat/` altına bağlı. |
| `../yamalar/tablet_modu.py` | İmaj ağacına kurulum (`apply_patches.py`). |
| `../testler/test_tablet_modu.py` | Dört parçanın aynı düğümü adlandırması, imza, güncel derleme, kurulum. |

## Derleme ve doğrulama

```
tablet-modu/build.sh                          # out/system/priv-app/GbosTabletModu/ (tekrarlanabilir)
python3 -I -m pytest testler/test_tablet_modu.py
python3 -I yamalar/selinux.py prova           # beklenen.txt'de dört satır
```

Kernel tarafı `kernel/build.sh` ile (iki yama sırayla). Uygulama yalnız imajla gelir; düğüm olmayan bir kernelde kutucuk görünmez.

## Tablette

- Kernel anahtarı 25 Ekim'de root ile denendi: açınca tablet düzeni ve klavye/touchpad yok, ters L ile dizüstü arasında tablet modunda kalıyor (Kadir doğruladı); kapatınca klavye geri geliyor.
- Kutucuk hızlı ayarlar düzenleme ekranından eklenir.
