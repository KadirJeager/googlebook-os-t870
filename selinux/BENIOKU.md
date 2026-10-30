# Googlebook OS (SM-T870) SELinux politikası

Googlebook OS imajını Enforcing'de açmak için politika ekleri, bunların bilgisayarda doğrulanması ve tek bayraklı Enforcing/Permissive anahtarı. Modül: [`yamalar/selinux.py`](../yamalar/selinux.py).

## Sorun ve çözümün özü

- Tablette mica platformu (system, system_ext, product) LineageOS vendor/odm ile çalışıyor. init politikayı açılışta `secilc` ile derliyor: plat, eşleme (202604), system_ext, product, LineageOS vendor ve odm birlikte.
- Googlebook masaüstü servislerinin etiketleri mica'nın **vendor** `service_contexts`'inde duruyor. Tablette bu adlar `default_android_service` etiketini alıyor. Bunun iki sonucu var:
  - system_server bu servisleri kaydedemiyor (`desktop_net`, `desktop_data_migration`, `IDesktopServiceabilityService`, `IVideoChatManager`, `ISnapshot`, `IDrmNetworkService` ...).
  - Tablette olmayan vendor servisleri sorgulandığında (`IGoogleBattery`, `IDesktopPowerChargeControl`, `IGsc` ...) servicemanager `isDeclared` ve `waitForService` çağrılarında "bulunamadı" yerine `EX_SECURITY` döndürüyor. Java tarafı bunu SecurityException olarak görüyor (kaynak: servicemanager `ServiceManager.cpp`, `canFindService`).
- Çözüm: mica'nın vendor politikasında platform tarafına verilen servis etiketleri, türleri ve kuralları [`araclar/mica-servis-aktar.py`](araclar/mica-servis-aktar.py) ile mica imajından otomatik üretiliyor (88 tür, 122 servis adı). Tablete özgü ekler elle yazılmış ve açıklamalı CIL dosyalarında duruyor.

## Dosyalar

| Yol | İçerik |
|---|---|
| `politika/plat/*.cil` | `plat_sepolicy.cil` sonuna eklenenler: Sense yüz tanıma, bootlog, 4.19 ion ve LineageOS kuralları, 21 Ekim açılış düzeltmeleri, adb root (userdebug su) kuralları, mica'nın system_server kuralı, `60-gbos-vpd-executor.cil` (mica-servisleri oturumunun IVpdExecutor servisi), `60-gbos-linux.cil` (terminal; Enforcing, emülatörde tam senaryoyla doğrulandı) |
| `politika/plat-serbest/*.cil` | Yalnız permissive kipte eklenir: init'e `setenforce` izni |
| `politika/system_ext/10-cihaz.cil`, `20-lineage.cil` | `system_ext_sepolicy.cil` sonuna eklenenler: sysfs_ion, soter, LineageOS'un kamera/cas HAL kuralları |
| `politika/system_ext/50-mica-servisleri.cil`, `mica-servisleri.service_contexts` | **Üretilmiş**, elle düzenlenmez. `system_ext_sepolicy.cil` ve `system_ext_service_contexts` sonuna eklenir |
| `beklenen.txt` | Politikanın mutlaka izin vermesi (ya da reddetmesi) gereken erişimler; doğrulama bunları sınar |
| `kabul.txt` | Bilerek kapalı bırakılan erişimlerin karar defteri, her biri gerekçesiyle. Doğrulama, mica ya da LineageOS'un izin verdiği bir red buraya yazılmamışsa hata verir; iki politikanın da reddettiği bir red yazılmamışsa INCELENMEDI olarak listeler |
| `cihaz-testi.md` | Enforcing imajın cihazdaki test adımları (adb komutları, çıktılar `kanit/` altına) |
| `$GBOS_WORK/lineage/bolumler/<bölüm>/etc/selinux` | Tabletin LineageOS 24 bölümlerindeki SELinux dosyaları. Depoda değildir; `lineage-hazirla.py` tableti kurduğun LineageOS zip'inden çıkarır |
| `kanit/` | Cihazdan alınan AVC kayıtları; `../acilis-loglari/` ile birlikte kanıt olarak taranır |
| `reddedilenler.txt` | Bütün kayıtlardaki redler, yalnız politika alanlarıyla (`python3 yamalar/selinux.py topla`); `dogrula` bunları yeniden oynatır, kayıtlar olmadan da (yayın deposu) |
| `dogrulama/` | Doğrulama çıktıları |
| `araclar/` | `kur.sh` (AOSP secilc ve denetleyici), `avc_kontrol.c`, `avc.py`, `mica-servis-aktar.py` |

## Tek bayrak: Enforcing ve Permissive

- Varsayılan `enforcing`: `yamalar/selinux.py` içinde `SELINUX_KIP`. Bir derleme için ortam değişkeniyle değiştirilir: `GBOS_SELINUX_KIP=permissive python3 apply_patches.py`.
- `permissive` seçilince `politika/plat-serbest` eklenir ve `system/etc/init/gbos-selinux.rc` yazılır (early-init'te `enforce 0`). `enforcing` seçilince ikisi de çıkar; eski bir `gbos-selinux.rc` varsa silinir.
- Cihazda yeniden imaj yazmadan geçici olarak serbest kipe geçmek için: `adb root`, sonra `adb shell setenforce 0`. su alanı izin vericidir; bu yüzden komut çalışır. Ayar yeniden açılışta sıfırlanır.

## apply_patches.py ile bağlama (ana oturumun işi)

`uygula()` bu modülün sahip olduğu dosyaları bozulmamış mica kopyasından yeniden üretir. Bu yüzden `apply_patches.py`'nin sonunda çağrılması yeterli; eski satır içi kurallar üzerine yazılır:

```python
# dosyanın başına (apply_patches.py'nin klasörü zaten sys.path'te; sistemdeki libselinux
# Python modülü "selinux" ile karışmaması için paket adıyla alınır)
from yamalar import selinux as gbos_selinux

# if __name__ == "__main__": bloğunun sonuna, patch_product() sonrası
    gbos_selinux.uygula(SYS_DIR, SYSEXT_DIR, pristine_file)
    gbos_selinux.dogrula(WORK)   # derlenmiyorsa ya da bir denetim düşerse DogrulamaHatasi
```

Bağlandıktan sonra artık kullanılmayan satır içi kodlar silinebilir:
- `patch_system()`: `permissive_cil`, `gbos-selinux.rc` yazımı, `face_cil`, `bootlog_cil`, `ion_and_lineage_cil` ve `cil_path` yazımı. `gbos-bootlog.rc` yazımı kalır.
- `patch_system_ext()`: `se_cil_add` ve `system_ext_sepolicy.cil` yazımı.

Bu kuralların tamamı `politika/` altında birebir duruyor. Tek fark: `udsattestation → default_android_service find` yaması kaldırıldı; yerine `IGsc` adı `gscd_service` etiketini alıyor.

## Doğrulama

```bash
python3 yamalar/selinux.py prova
python3 yamalar/selinux.py dogrula --work /var/tmp/mica_work
```

`prova` bozulmamış mica dosyalarına ekleri geçici bir ağaçta uygular. `dogrula` ise apply_patches'ın ürettiği ağaçta çalışır. İkisi de şunları yapar:
1. **Araç zinciri:** `araclar/kur.sh`, AOSP `external/selinux` android17-release (a8c5e74f) kaynağından secilc'i derler. Bu derleyici LineageOS'un kendi politikasını derlediğinde tabletteki `odm/etc/selinux/precompiled_sepolicy` dosyasıyla bayt bayt aynı çıktıyı veriyor; doğrulama bunu her seferinde yeniden sınar.
2. **Derleme:** init'in komutuyla derler (`secilc -m -M true -G -N -c 30`, aynı dosyalar, aynı sıra).
3. **Beklenen erişimler:** `beklenen.txt`.
4. **Servis etiketleri:** taşınan 122 adın her biri tek ve doğru etiketi alıyor mu?
5. **Kanıt:** kayıtlı açılışlardaki her red üç politikaya sorulur: bizimki, mica'nınki ve LineageOS'unki. mica'nın izin verdiği ya da LineageOS'un bir vendor alanına izin verdiği bir red açık kalırsa hata sayılır.
6. **Statik karşılaştırma:** LineageOS politikasında vendor alanlarına verilen her izin bizde de olmalı. Olmayanlar ya kapatılır ya da gerekçesiyle `kabul.txt`'ye yazılır.

Son çıktı: [`dogrulama/20261022-prova.txt`](dogrulama/20261022-prova.txt). İki kipte de doğrulama geçiyor. Aynı denetimler eski çalışma ağacında 24 beklenen hatası, 0/122 servis etiketi ve 2374 açık vendor farkı buluyor.

## Cihazda denenecekler (Enforcing imaj)

Komutlarıyla birlikte adım listesi: [cihaz-testi.md](cihaz-testi.md).


1. `getenforce` komutu `Enforcing` döndürmeli. Açılış ve masaüstü gelmeli; `gbos-selinux.rc` imajda olmamalı.
2. `service list | grep -E 'desktop_net|desktop_data_migration|IDesktopServiceabilityService|IDesktopSettingHealthService|IVideoChatManager|ISnapshot/|IDrmNetworkService|IAqueduct|desktop_diagnostics'`: hepsi kayıtlı olmalı.
3. Kayıtlarda red olmamalı: `logcat -b all -d | grep -E 'avc: +denied.*permissive=0'` ve `dmesg | grep 'avc: +denied'`.
4. `logcat -d | grep 'Access denied finding property'` çıktısındaki özellik adları toplanıp gönderilmeli. Serbest kipte bu adlar görünmüyordu; vendor_default_prop redlerinin hangi özelliklerden geldiği ancak böyle anlaşılır.
5. system_server'da `SecurityException` ya da `SELinux denied` olmamalı: `logcat -d | grep -iE 'SecurityException|SELinux denied'`.
6. Yüz tanıma: kilit ekranında açma ve yeni yüz kaydı çalışmalı; `sense-face-hal` ile `co.aospa.sense` için red olmamalı.
7. Ses (32 bit vendor HAL), video oynatma (codec2), Widevine içerik, Wi-Fi, Bluetooth ve kamera çalışmalı.
8. Googlebook uygulamaları: İpuçları, Onarım modu, Görev yöneticisi ve masaüstü servis uygulaması açılıp çökmeden çalışmalı.
9. `adb root` ile `adb shell` çalışmalı; root kabuktan `cmd`, `dumpsys` ve `am` yanıt vermeli.
10. Bootlog: `/metadata/gbos-bootlog/logcat.txt` yazılmalı.
11. Sorun çıkarsa `adb shell setenforce 0` ile serbest kipe geçilebilir, ya da imaj `GBOS_SELINUX_KIP=permissive` ile yeniden üretilir. Kayıtlar `selinux/kanit/` altına konunca doğrulama onları da tarar.

## Durum (22 Ekim 2026 akşamı, devir)

- **Bitti:** Bütün ekler Enforcing için hazır ve çalışma ağacına girdi; ana oturumda yama-dogrula 0 fark verdi. `prova` iki kipte de temiz: beklenen 100/100, servis etiketi 137/137, kararsız red 0, LineageOS'a göre açık vendor farkı 0. Terminal ve VPD alanları Enforcing. Sense HAL'i bulma izni `priv_app_all`'da. `vendor_sys_qti_display` yaması ve 32 bit HAL'lerin `system_file` okuma kuralları kaldırıldı.
- **Açık:**
  - Yeni imajın tablette tam testi: [cihaz-testi.md](cihaz-testi.md), yüz tanıma ve terminal adımları dahil. Çıktılar `kanit/` altına konur, ardından `python3 yamalar/selinux.py dogrula --work …` çalıştırılır. Yeni redler INCELENMEDI ya da IZIN VERIYOR olarak listelenir; her biri politikaya ya da `kabul.txt`'ye işlenir. Sonra `python3 yamalar/selinux.py topla` ile `reddedilenler.txt` güncellenir (`testler/test_selinux_reddedilenler.py` eksik reddi yakalar).
  - `system_server`'ın `vendor_default_prop`'tan okuduğu özelliğin adı: testteki `ps` çıktısıyla pid eşlenecek.
- **Öneri (kullanıcının kararı):**
  - Sense HAL'de arayan uid denetimi.
  - HAL'in `systemMain()` kullanmayı bırakıp kendi alanına geçmesi.
- **Yapılmayacak:** Güncelleyici politikası (`60-guncelleyici.cil`) iptal; iş Kadir'de.

## Bilinen açık noktalar

- **İmaj etiketleri:** `make_ext4_images.py` 22 Ekim'den beri yeni dosyaları ağacın kendi `file_contexts`'iyle etiketliyor. Böylece `ld.config.32` artık `system_linker_config_file`. 32 bit HAL'lere verilen `system_file` okuma kuralları bu yüzden kaldırıldı; `beklenen.txt` hem okumanın sürdüğünü hem de geri kalan `/system`'in kapalı olduğunu sınıyor. Bilinçli tek istisna `/system/lib/bootstrap`. 32 bit bionic orada `system_lib_file` olarak etiketleniyor, plat ise bu dizine `system_bootstrap_lib_file` diyor. Bu yolu 32 bit linker'ın kendisi seçiyor: mica'nın runtime APEX'i yalnız 64 bit, bu yüzden 32 bit bionic'in APEX yolu yok. Arama yolundan çıkarmak tablette denendi, işe yaramadı (`acilis-loglari/codec2-20261022/KOK-NEDEN.md`). `beklenen.txt` dört 32 bit HAL'in bu kitaplıkları okuyabildiğini sınıyor.
- **Bilerek izin verici bırakılan alanlar:**
  - `su`: userdebug'daki gibi.
  - `gbos_bootlog`: açılış kaydı bir kural eksiği yüzünden satır kaybetmemeli.
  - Terminal alanları (`gbos_linuxd`, `gbos_linux`) artık bu listede değil, Enforcing çalışıyor; `/proc` taraması politikada açıkça `dontaudit`. Doğrulama, dontaudit edilen redleri politikanın kendi kararı sayıyor.
  - `vendor_sys_qti_display` yaması kaldırıldı: `apply_patches.py` mica product'taki `init.qti.display.{sh,rc}` dosyalarını siliyor (betik bu SoC'de iş yapmıyordu).
- **Sense:** HAL'i bulma izni `priv_app_all`'a veriliyor. mica, ayrıcalıklı uygulamaları hedef SDK'ya göre `priv_app` ya da `priv_app_36` alanına koyuyor; Sense SDK 35'i hedeflediği için `priv_app_36`'da çalışıyor. Sense'e kendi uygulama alanını vermek mümkün ama şimdilik uygulanmadı:
  - **Seçilmeme nedeni:** LineageOS vendor'ının priv_app kuralları (GPU/kgsl, gles verisi) yalnız sürümlü `priv_app_202604` özniteliğindeki alanlara ulaşıyor. Yeni alanı bu özniteliğe eklemek politikayı vendor sürümüne bağlar; eklemezsek kamera ve GPU yolu kırılabilir.
  - **Önerilen kalıcı adım:** HAL kodunda arayan kimliğinin denetlenmesi. `FaceHalService` şu an `Binder.getCallingUid()` denetimi yapmıyor; yalnız system (1000) ve Sense'in uid'i kabul edilmeli. Böylece izin özniteliğe açık kalsa da HAL'i yalnız Sense ve system_server kullanabilir. Bu `yuz-tanima/` altında, HAL'in sahibinin işi.
- **Sense HAL'in alanı:** HAL init'ten `system_server` etiketiyle başlıyor, çünkü `ActivityThread.systemMain()` ile sistem bağlamı kuruyor. Kendi alanına geçebilmesi için önce HAL'in `systemMain()` kullanmayı bırakması gerekiyor: `startService` ve `FaceManager` çağrılarının binder üzerinden yapılması gerekiyor. Ardından `hal_face_server` özniteliğiyle dar bir alan (binder, servicemanager, system_server ve priv_app_all ile çağrı) yazılabilir. Bu da yuz-tanima kodunda bir değişiklik; öneri olarak duruyor.
- **Her iki politikanın da reddettikleri** (`ikisi de reddediyor`, 95 grup) aynen bırakıldı. Bunlar mica ve LineageOS'ta da aynı davranıyor. Örnekler: `netd` `/proc/net/xt_quota/globalAlert` oluşturma, uygulamaların özellik alanlarını tarama, `shell` kullanıcısının soket `getattr` istekleri.
- **Gelecek root servisleri** (güncelleyici; bu gece yazılmayacak): kuralları `politika/plat/60-<ad>.cil` dosyasına, beklenen erişimleri `beklenen.txt`'ye eklenir; ardından `prova` çalıştırılır.
