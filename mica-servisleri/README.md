# mica servisleri: Dell donanım servislerini bekleyenler

mica'nın sistem tarafı, Dell XPS Googlebook'un vendor bölümündeki bazı servisleri `ServiceManager.waitForService()` ile süresiz bekliyor. Tablette vendor Samsung/LineageOS'a ait, bu servisler yok. Bekleyen taraf hiç dönmüyor; örneğin My Googlebook gri iskelet kartlarda kalıyor. Bu klasör, bu bekleyişleri tablete uygun ve dürüst biçimde çözer. İmaja `yamalar/mica_servisleri.py` içindeki `install(sys_dir, sysext_dir, prod_dir)` ile girer.

## 1. VPD: `vendor.google.desktop.vpd_executor.IVpdExecutor/default`

### Kim bekliyor

| Çağıran | Yol | Anahtar |
|---|---|---|
| My Googlebook (`DesktopTips`) | `ServiceabilityManager.ubindAttribute()` → `ServiceabilityService.getUbindAttribute()` | RO `ubind_attribute` |
| Setup Wizard (`SetupWizardDesktopPrebuilt_cl3b`) | `readActivateDate` / `writeActivateDate` | RW `activate_date`, yyyy-ww |
| PostManufacturing (açılışta), HardwareDescriptorManager | `getVpdValue`, `getVpdEntries`, `updateVpdEntries` | `post_manufacturing_config`, `mfg_sku_id`, `region` ... |
| `com.android.desktop.serviceability` | `getMlbSerialNumber`, `getImei`, `getMeid`, `getMfgSkuId`, `getRegion`, `getOemKey2`, `getKbInfo` | RO |
| `desktop-services.jar` onarım modu | `readPostManufacturingConfig` | ölü kod, derleyici bayrağıyla kapalı |

Bu liste sistem, system_ext ve product ağaçlarındaki bütün dex'ler taranarak çıkarıldı. Taranan izler: `vpd_executor` ve `ServiceabilityManager` çağrıları. Native tarafta IVpdExecutor'ı kullanan ikili yok.

### Arayüz

Arayüz `vpd/aidl` altında. Donmuş sürüm 1: `VERSION = 1`, `HASH = 11049698b211cb439aad0e72f4c391331ed7063b`. Tanım, `desktop-services.jar` ve serviceability APK'sındaki üretilmiş stub'lardan ve mica'nın `vendor.google.desktop.vpd_executor.vpd-executor` ikilisinden çıkarıldı. İşlem kodları: `getEntries=1`, `getValue=2`, `updateEntries=3`. Bölgeler: `VpdRegion RO=0`, `RW=1`. Bu tanımdan üretilen Java proxy'nin parcel çağrı sırası, orijinal stub'la birebir karşılaştırıldı.

### Orijinal davranış

Kaynak, mica executor ikilisinin disassembly'si. Orijinal executor `/vendor/bin/vpd` aracını çalıştırır:

- `getValue`: `vpd -i RO_VPD|RW_VPD -g key` çalıştırır. Araç "was not found" derse `VpdEntry{key, value=null}` döner, istisna atmaz. serviceability `getValue(...).value` ile bunu `null` olarak alır.
- `getEntries`: `vpd -i ... -l` çıktısındaki `"k"="v"` satırlarını döndürür.
- `updateEntries`: her giriş için `-s key=value` uygular, değer `null` ise `-d key`.
- Hatalarda `ServiceSpecificException(-1, mesaj)` döner. serviceability bunu `IllegalStateException` olarak çağırana iletir. Bu yüzden normal okuma yolunda istisna üretilmez.

### Tablet uygulaması: `gbos-vpd-executor`

Native C++ ile yazıldı, NDK binder kullanıyor. Başlıklar AOSP `android-17.0.0_r1` etiketinden alındı: `third_party/libbinder_ndk`. Bağlama, mica sistem imajındaki `libbinder_ndk.so` ve `liblog.so`'ya karşı yapılıyor ve kullanılan her sembolün bu kütüphanelerde dışa açık olduğu derlemede denetleniyor.

- **Lazy servis:** Orijinal gibi çalışır. rc'de `interface aidl ... disabled oneshot` var. servicemanager istek gelince başlatır, `AServiceManager_registerLazyService` ile kaydolur ve istemci kalmayınca çıkar. Açılışta çalışmaz, boştayken bellek tutmaz.
- **RO bölgesi:** Yalnız cihazın kendi bildirdikleri bulunur:
  - `serial_number`: önce `ro.boot.serialno`, yoksa `ro.serialno`.
  - `model_name`: önce Samsung önyükleyicisinin `ro.boot.em.model` değeri, yoksa `ro.product.vendor.model`.

  Diğer anahtarlar bu tablette yok: `mlb_serial_number`, `imei`, `meid` (Wi-Fi model), `mfg_sku_id`, `region`, `oem_key2`, `kb_info`, `ubind_attribute`. Bunlar, VPD'sinde o anahtar olmayan bir Googlebook'taki gibi `value=null` döner; değer uydurulmaz. `region` için CSC kodundan, örneğin `KOO`, bölge türetilmedi: tek ülkeye net eşleşmiyor ve okuyan bir uygulama yok. RO'ya yazma, orijinaldeki yazma korumalı flash gibi `ServiceSpecificException(-1)` ile reddedilir.
- **RW bölgesi:** `/data/misc/gbos_vpd/rw_vpd` dosyasında durur; rc bu dizini `post-fs-data`'da 0700 system:system olarak oluşturur. Dosyada sihirli değer, sürüm ve CRC32 var; içerik uzunluk önekli kayıtlardır.
  - Her güncelleme önce `rw_vpd.tmp`'ye yazılır. Ardından sırasıyla `fsync`, `rename` ve dizin `fsync` yapılır; çökmede eski ya da yeni hâl kalır, karışık hâl kalmaz.
  - Güncelleme hep ya hiç ilkesiyle çalışır.
  - Sınır, Qualcomm Chromebook'lardaki RW_VPD boyutu kadardır: 32 KiB. Kaynak: coreboot `trogdor`/`herobrine` `chromeos.fmd`.
  - CRC'si tutmayan dosya `rw_vpd.corrupt` olarak kenara alınır, logcat'e hata yazılır ve depo boş başlar.
- **Erişim:** Yalnız uid 0 ve 1000 çağırabilir. serviceability ve system_server uid 1000 ile çalışır. Diğer uid'ler `SecurityException` alır.
- **Sınırlama:** Googlebook'ta RW_VPD flash'ta durur ve fabrika ayarlarına dönüşte silinmez. Tablette `/data` silinince `activate_date` da gider; Setup Wizard yeniden kurulumda tarihi yeniden yazar.

### Dosyalar

`install()` system ağacına şunları yazar:

| Yol | Ne |
|---|---|
| `system/bin/gbos-vpd-executor` | servis, 0755 |
| `system/etc/init/gbos-vpd-executor.rc` | init servisi ve RW dizini |
| `system/etc/vintf/manifest/gbos-vpd-executor.xml` | framework manifestinde HAL ilanı; bu olmadan VINTF kararlı binder kaydedilemez |
| `system/etc/selinux/plat_file_contexts` | işaretli blok: `gbos_vpd_executor_exec`, `gbos_vpd_data_file` |

## 2. GSC: `android.system.desktop.security.gsc.IGsc/default`

`/system/bin/udsattestation` (class main, oneshot) açılışta çalışır. Görevi, GSC çipinin TPM NV alanındaki UDS sertifikasını okuyup KeyMint'e eklemek. Bunun için `IGsc/default`'u bekliyor. Tablette GSC çipi yok, `gscd.rc` de apply_patches tarafından zaten kaldırılıyor. Sonuç: süreç açılıştan itibaren saniyede bir "Waited one second for ...IGsc/default" yazarak sonsuza dek bekliyordu. Son açılış logunda bu satır 1078 kez geçiyor.

Sahte GSC yazılmadı. `install()` `system/etc/init/udsattestation.rc` dosyasını siler; ikili yerinde kalır ama başlatılmaz.

IGsc'yi kullanan diğer parçalar şunlar:

- `gscd`, `gscutil` ve `gscutil-executor` rc'leri zaten kaldırılıyor.
- `trunks_send` elle çalıştırılan bir araç, rc'si yok.
- Trusty VM ikilileri tablette çalışmıyor.

Açılış loglarında süresiz bekleyen başka servis yok. `keystore2` ve `keymaster@4.0` beklemeleri açılışın ilk saniyeleriyle sınırlı ve kendiliğinden bitiyor.

## 3. Diğer Dell executor'ları: neden HAL yazılmadı

serviceability şu servisleri ilk kullanımda `waitForService` ile süresiz bekliyor: `IEcExecutor`, `IFutilityExecutor`, `ICameraToolExecutor`, `IDesktopPowerChargeControl`, `II2CExecutor`, `IDiskInfoService`, `IAmpCalibration`. Hepsi mica'nın vendor bölümünde ve ChromeOS donanımına özgü; karşılıkları EC, AP firmware yazma koruması, GBB bayrakları, CBI, I2C geçişi ve MIPI kamera kimliği. Tabletin bunlara karşılık gelen bir donanımı yok. Serviceability'nin kendi hesapladıkları (`IDiagnosticsService`, `IProcfsReader`, `ISuperPartitionUtils`) system_ext'te var.

Bekleyişe hangi yoldan ulaşıldığı, uygulamaların dex'leri, bu üç uygulamanın kodu ve tabletin 22 Ekim dökümü (`kanit/20261022-executor`) birlikte incelenerek çıkarıldı:

| Kim | Tetikleyici | Dell HAL'ine ulaşıyor mu | Kanıt |
|---|---|---|---|
| PostManufacturing | Her kullanıcıda açılış | Hayır: RW VPD'de `post_manufacturing_config` yoksa varsayılan ayara düşüyor ve kendi açılış alıcısını kalıcı olarak kapatıyor | `PostmanufacturingStartupService` 121-168, `ConfigUtils` 78-90; 21 Ekim logunda iki kullanıcıda da görülüyor |
| HardwareDescriptorManager | Yalnız imza izinli bir fabrika ya da onarım başlatıcısı; imajda böyle bir başlatıcı yok | Hayır; HAL hataları `catch (Throwable)` ile yakalanıyor | Manifest 29-101; tablette `pm list packages -d` içinde |
| Diagnostics | Başlatıcı ve Ayarlar › Battery health / Diagnostics | Hayır. Pil, CPU ve testler vendor serviceability, procfs, stressapptest ve diag-service üzerinden yürüyor; klavye olayları system_server'da 5 sn zaman aşımlı `waitForDeclaredService` ile alınıyor | Bu README'nin kanıt klasörü ve alt ajan raporları |
| system_server (desktop-services) | — | Beklemiyor: `getService`, `waitForDeclaredService` ya da bildirim kaydı kullanıyor | `DesktopChargingOptimizationController:127`, `ActionPerformer:129`, `KeyboardProxyClient:446` |
| `google_desktop_firmware_updater` | Yalnız mica OTA postinstall'ı; rc'si yok | Futility ve gscutil'i çağrı zaman aşımıyla bekliyor | ikili dizeleri |

Tabletin dökümünde de süresiz beklenen yalnız `IGsc` ve `IVpdExecutor` var; ikisini 1. ve 2. bölüm çözüyor. Bu yüzden ötekiler için servis yazılmadı. "Bu donanım yok" diye yanıt veren bir HAL, VINTF'te olmayan bir EC'yi var gibi gösterir ve normal kullanımda zaten çağrılmıyor.

Bilinen sınırlar:
- RW VPD'ye `autoLaunch=true` içeren bir `post_manufacturing_config` yazılırsa PostManufacturing tam ekran açılır ve şarj HAL'ini bekler. Bunu yalnız imza izinli çağıranlar yazabilir; tablette böyle bir yazan yok.
- HDM'nin hata ayıklama ekranındaki "probe" düğmesi EC'yi bekler. Uygulama kapalı olduğu için bu ekrana erişilemiyor.
- **Diagnostics pil kartı:** Tablette "Battery health 0 %", "Cycle count 0" ve "0mAh battery" gösteriyor; üçü de gerçek değil.
  - Kök neden tabletin vendor health HAL'i ve çekirdeği: Samsung düğümleri standart adlarla sunulmuyor.
  - Gerçek değerler: sağlık `fg_asoc` = 94, tasarım kapasitesi 8000 mAh. Döngü için güncel bir kaynak yok.
  - Düzeltmenin yeri ve öneri: [PIL-SAGLIGI.md](PIL-SAGLIGI.md).
- **Diagnostics CPU ve bellek testleri:** diag-service tablette istenince başlıyor. Kanıt: `kanit/20261022-diagnostics/diag-lazy.txt` (pid, `running`, "found"). Bu yüzden testler beklemeden çalışır; ekranda deneme Kadir gelince.

## Derleme ve test

```bash
mica-servisleri/build.sh                      # AIDL kodu, host testleri, aarch64 ikili ve sembol denetimi
python3 -I mica-servisleri/tests/test_install.py   # install(): sonuç, tekrar çalıştırma, bozulma tespiti
mica-servisleri/tests/emu/emu-test.sh         # Android 37 emülatöründe uçtan uca, kaynak kapısından
```

- Derleme tekrarlanabilir: aynı girdilerden aynı bayt çıkar. `out/STAMP` girdilerin özetini tutar; `install()` özet değişmişse yeniden derler.
- Emülatör testi, serviceability'nin yolunu sınar: `waitForService` ve aynı AIDL'den üretilmiş Java proxy. Ölçülenler şunlar:
  - lazy başlama
  - RO, RW, hata ve istisna türleri
  - dosya izinleri
  - boşta çıkış
  - yeniden başlatmada RW'nin korunması
  - yetkisiz uid'nin reddi

  Emülatör x86_64 olduğundan aynı kaynaklar onun kütüphanelerine karşı derlenir. Emülatörün politikasında bu alan olmadığından servis orada `su` etiketiyle, permissive modda çalışır.

Son emülatör koşusu, 22 Ekim sabahı: 41 denetimin hepsi geçti. Bunlara CIHAZ-TESTI.md'nin kabuk yolu da dahil: `ctl.interface_start` ve `service call`. Kanıt: `tests/emu/son-kosu.log`.

Tablette doğrulanacaklar: `CIHAZ-TESTI.md`.
