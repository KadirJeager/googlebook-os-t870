# Pil sağlığı: yapılandırılmış health HAL

Ayarlar › Battery health tablette "0 %" ve "Cycle count 0" gösteriyordu. Kök neden ve gerçek değerler: [`../mica-servisleri/PIL-SAGLIGI.md`](../mica-servisleri/PIL-SAGLIGI.md). Kadir'in 23 Ekim kararı: vendor HAL'in yapılandırılmış derlemesi imaj hattından, /product'tan gelsin. Modül: [`yamalar/pil_sagligi.py`](../yamalar/pil_sagligi.py).

## Ne yapıyor

- Tablette çalışan HAL'in kendisi yeniden derlenir: LineageOS `android_hardware_samsung` `aidl/health/Health.cpp` + AOSP `hardware/interfaces/health/aidl/default` + LineageOS `system/core/healthd` `BatteryMonitor`. Vendor ikilisi de tam bu kaynaklardan derlenmiş; LineageOS'un `BatteryMonitor` farkları bu yüzden korunur.
- Tek değişiklik [`kaynak-yamalari/0001-health-samsung-sysfs-dugumleri.patch`](kaynak-yamalari/0001-health-samsung-sysfs-dugumleri.patch):
  - `batteryStateOfHealthPath = /sys/class/power_supply/battery/fg_asoc`
  - `batteryCycleCountPath = /sys/class/power_supply/battery/battery_cycle`
- Değerler her güncellemede çekirdekten canlı okunur. Sabit ya da türetilmiş değer yok. `battery_cycle` bugün -1; HAL bunu olduğu gibi iletir, uygulama "yok" gösterir.
- Kapalıyken şarj arayüzü derlenmez: `CHARGER_FORCE_NO_UI=1`. Kapalıyken şarjı vendor `vendor.charger` yürütmeye devam eder.

## Neden `override` değil

İlk plan `/product/etc/init` rc'sinde `override` idi. Android 17 init bunu reddeder: `/vendor` ve `/odm` rc'leri vendor_init alt bağlamında okunur, `/product` rc'leri okunmaz. Bağlamı farklı bir servisi geçersiz kılmak "overrides another service across the treble boundary" hatasıdır. Kaynak: AOSP `system/core` android-17.0.0_r1, `init/service_parser.cpp` `EndSection`, `init/subcontext.cpp`.

Yerine [`etc/android.hardware.health-service.samsung-gbos.rc`](etc/android.hardware.health-service.samsung-gbos.rc):

1. `on early-init`: `stop vendor.health-samsung`. Henüz başlamamış bir servise `stop`, onu `class_start`'ın başlatmasını engeller: `init/service.cpp` `StopOrReset`, `StartIfNotDisabled`.
2. `service gbos.health-samsung`: bizim ikili; sınıf, kullanıcı, gruplar, yetenekler ve dosyalar vendor servisiyle birebir. `install()` bunu vendor rc'siyle karşılaştırır.
3. `on property:init.svc.vendor.health-samsung=running`: vendor servisini yeniden durdurur, bizimkini yeniden başlatır. Gerekçe: vendor `vndservicemanager.rc` yeniden başlarken `class_restart hal` çağırıyor ve `--only-enabled` kullanmıyor; durdurulmuş servisi de başlatır.

Vendor bölümü değişmez. IHealth/default'u vendor VINTF manifesti zaten ilan ediyor; VINTF parçası eklenmez.

## SELinux

Politika eklenmez. `product_file_contexts`'e işaretli blokla tek satır eklenir:

```
/product/bin/hw/android\.hardware\.health-service\.samsung-gbos u:object_r:hal_health_default_exec:s0
```

- Bu tür ve `typetransition init hal_health_default_exec process hal_health_default` tabletin LineageOS vendor politikasında var: `init_daemon_domain(hal_health_default)`. init ikiliyi çalıştırınca süreç vendor HAL'in kendi alanına geçer. Alanın bütün izinleri vendor ikilisininkiyle aynıdır.
- mica'nın kendi `product_file_contexts`'i de product ikililerine vendor türleri veriyor; düzen aynı.
- [`beklenen.txt`](beklenen.txt) gereken erişimleri listeler. `yamalar/pil_sagligi.py politika`, çalışma ağacının politikasını init'in yaptığı gibi derler ve bunları ve geçişi sınar. Son koşu: 23 denetim tuttu.
- Etiket imaja `make_ext4_images.py`'nin her zamanki yoluyla girer. Test: ikili `hal_health_default_exec`, `root:shell 0755`.

## Bağlanma: linker ve semboller

- İkili /product'tan çalışır. linkerconfig onu "product" bölümüne koyar ve orada yalnız LLNDK görünür. Bu yüzden libbase, libcutils, libutils, libhidlbase ve libc++ statik bağlanır. DT_NEEDED: `libbinder_ndk.so liblog.so libc.so libdl.so`. Dördü de mica'nın `llndk.libraries.txt`'sinde.
- `araclar/sembol-denetimi.py`, ikilinin içe aldığı her sembolü sürümüyle birlikte mica'nın kendi kütüphanelerinde arar: libbinder_ndk 53, libc 112, libdl 1, liblog 5. Hepsi çözülüyor.
- İkili hiçbir sembol dışa açmaz: `local: *` sürüm betiği. Açsaydı libbinder_ndk'nın kullandığı libutils sembolleri bizim statik kopyamıza bağlanırdı.
- AIDL arayüzü vendor'la aynı: android.hardware.health V5, dondurulmuş hash `33ada68…`. VINTF kararlılığıyla işaretli.
- AArch64 PIE, `/system/bin/linker64`, BIND_NOW, RELRO, 16 KiB hizalama.

## Derleme

```bash
pil-sagligi/build.sh                          # kaynaklar, hidl-gen, AIDL, ikili, denetimler
python3 -I yamalar/pil_sagligi.py build       # STAMP eskiyse derler, ikilinin özetini yazar
python3 -I yamalar/pil_sagligi.py politika    # SELinux denetimi, çalışma ağacında
python3 -I -m pytest T870-araclar/testler/test_pil_sagligi.py
```

- **Kaynaklar:** [`kaynaklar.lock`](kaynaklar.lock) her projeyi URL, commit, alınan yollar ve içerik özetiyle sabitler. `araclar/kaynak-getir.py` yalnız o yolları seyrek, blobsuz ve tek derinlikte çeker, `~/.cache/gbos-pil-sagligi/src` altına koyar ve içerik özetini doğrular. Toplam yaklaşık 11 MB, ağ bir kez gerekir.
  - LineageOS lineage-24.0: `android_hardware_samsung`, `android_system_core`. `healthd` farkları burada.
  - AOSP android-17.0.0_r1: `hardware/interfaces/health`, LineageOS'unki aynı; libbase, liblog, libhidl, hidl, libhwbinder, libfmq, fmtlib, frameworks/native binder NDK başlıkları ve batteryservice, Connectivity bpf başlıkları.
- **hidl-gen:** `BatteryMonitor` hâlâ HIDL health@2.1 yapılarını dolduruyor, AOSP TODO b/177269435. SDK hidl-gen içermiyor; `araclar/hidl-gen-kur.sh` onu sabitlenmiş AOSP kaynağından ana makine için derler. Gerekenler: bison, flex, jsoncpp, OpenSSL libcrypto.
- **Cihaz kütüphaneleri:** `araclar/cihaz-kutuphaneleri.py`, mica sistem imajından libbinder_ndk ve liblog'u, runtime APEX'ten bionic'i çıkarır.
- **Tekrarlanabilir:** aynı girdiler aynı baytı verir; iki ardışık derleme aynı SHA-256'yı verdi. `out/STAMP` girdilerin özetidir.
- Ara dosyalar `mktemp -d` ile `/var/tmp` altında açılır ve iş bitince silinir. Derleme birkaç yüz MB'ı geçmez; kaynak kapısı gerekmez.

## Dosyalar

| Yol | Ne |
|---|---|
| `build.sh`, `stamp.sh` | derleme ve girdi özeti |
| `kaynaklar.lock` | sabitlenmiş kaynaklar |
| `kaynak-yamalari/*.patch` | LineageOS `Health.cpp`'ye tek yapılandırma yaması |
| `etc/*.rc` | init tanımı |
| `beklenen.txt` | SELinux erişim listesi |
| `lineage.lock` | Portun doğrulandığı LineageOS zip'i ile vendor ikilisinin, rc'nin ve VINTF parçasının özetleri. Dosyaların kendisi `$GBOS_WORK/lineage/bolumler/vendor` altında, `lineage-hazirla.py` üretir |
| `araclar/` | `kaynak-getir.py`, `hidl-gen-kur.sh`, `cihaz-kutuphaneleri.py`, `sembol-denetimi.py` |
| `out/` | ikili ve `STAMP` |
| `CIHAZ-TESTI.md` | tablette ana oturumun adımları |

## Testler

`testler/test_pil_sagligi.py`, 7 test, gerçek girdilerle:
- ikilinin cihaz kütüphaneleriyle bağlanması,
- vendor'la aynı arayüz ve zip'ten yeniden çıkarılan vendor dosyaları,
- servis tanımının vendor'la denkliği ve vendor politikası,
- `install()`'ın iki kez aynı sonucu vermesi,
- politika derlemesi,
- product imajında etiket ve sahiplik,
- init kurallarının AOSP android-17 kaynağında sabitlenmesi; ağ gerekir.

rc'den `stop` satırı silinince ya da bir yetenek eksilince üç test kalıyor.

## Sınırlar

- "mAh battery" satırı değişmez. Diagnostics onu vendor serviceability'den alıyor ve Samsung girdisinde `cycle_count`, `manufacturer`, `model_name` yok. Gerçek kaynak yok.
- Döngü sayısı, Samsung'un `/efs` hesabı LineageOS'ta çalışmadığı için -1 kalır. `/efs` yazılmaz.
- `fg_asoc` Samsung'un yaşlanma yüzdesidir: tablette 94. `charge_full / charge_full_design` oranı ise %85. Ekranda Samsung'un kendi değeri görünür.
- Asıl kök neden LineageOS cihaz yapılandırmasında; aynı iki satır LineageOS'a katkı olarak da gönderilebilir.

## Durum, 23 Ekim 2026

- **Bitti:** derleme, yerel bağlanma ve sembol denetimi, SELinux denetimi, 7 test.
- **Açık:** `apply_patches.py`'ye bağlama ve tablette kabul, ana oturumda: [CIHAZ-TESTI.md](CIHAZ-TESTI.md). Tablette henüz hiç çalışmadı.
