# Ayarlar › "Pil sağlığı" (Diagnostics): yanlış sıfırlar ve gerçek veri

Durum: inceleme bitti. Dürüst düzeltmenin yeri bu projenin imajı (system, system_ext, product) değil; tabletin vendor health HAL'i ve çekirdeği. Bu bölümler LineageOS'a ait ve imaj hattımızda üretilmiyor. Kanıtlar `kanit/20261022-pil/`, `kanit/20261022-diagnostics/` altında.

## Kullanıcı ne görüyor

Diagnostics'in pil kartı (`ui/battery/BatteryCardKt`) değerleri koşulsuz basıyor; değer gelmezse `BatteryState` varsayılanı olan 0 görünüyor:

| Satır | Tablette görünen | Gerçek |
|---|---|---|
| Battery health | **0 %** | Yakıt göstergesinin yaşlanma değeri %94 (`fg_asoc`) |
| Cycle count | **0** | Bilinmiyor (aşağıya bakın) |
| "… mAh battery" | **0mAh** | Tasarım kapasitesi 8000 mAh (`charge_full_design`) |
| Düzey, akım, kalan süre | doğru | — |

## Alanlar nereden geliyor

Kaynak: `datasource/DefaultBatteryDataSource`, `viewmodel/battery/BatteryViewModel`.

| Alan | Yol | Tabletteki değer |
|---|---|---|
| Sağlık yüzdesi | `BatteryManager.getIntProperty(10)` (STATE_OF_HEALTH) → system_server `BatteryService` → vendor `IHealth.getBatteryHealthData().batteryStateOfHealth` | 0 (`health-hal.txt`) |
| Döngü | `BATTERY_CHANGED` `android.os.extra.CYCLE_COUNT` → `HealthInfo.batteryCycleCount` | 0; -1 olsaydı uygulama "yok" sayardı |
| Tam ve tasarım kapasitesi | `ServiceabilityManager.fetchBatteryInfo` → vendor serviceability, `/sys/class/power_supply/*` | Kayıt reddediliyor: Samsung girdisinde `cycle_count`, `manufacturer` ve `model_name` yok, istisna yakalanıyor |

Tablet health HAL'i (`android.hardware.health-service.samsung`, LineageOS `android_hardware_samsung` lineage-24.0 `aidl/health/Health.cpp`), AOSP'nin varsayılan `Health` sınıfını `InitHealthdConfig` varsayılanlarıyla çalıştırıyor. Varsayılan izleyici yalnız standart düğüm adlarını tanıyor: `cycle_count`, `state_of_health`, `manufacturer` ve diğerleri. Samsung sürücüsü aynı bilgiyi başka adlarla veriyor; standart adlar bulunamayınca alanlar 0 kalıyor.

## Tabletteki gerçek veri

Çekirdek kaynağı: LineageOS `android_kernel_samsung_sm8250` lineage-24.0, `drivers/battery_v2`.

- **`fg_asoc` = 94:** MAX77705 yakıt göstergesinin `POWER_SUPPLY_PROP_ENERGY_FULL` yanıtı (`sec_battery_sysfs.c` FG_ASOC). Samsung'un pil yaşlanma yüzdesi. Sağlık yüzdesinin gerçek karşılığı bu.
- **`charge_full` = 6827000 µAh, `charge_full_design` = 8000000 µAh:** Health HAL bunları zaten okuyor (`batteryFullChargeUah`, `batteryFullChargeDesignCapacityUah`). Diagnostics ise kapasiteyi health HAL'den değil vendor serviceability'den alıyor.
- **Döngü sayısı:** Lineage'da gerçek ve güncel bir kaynak yok.
  - `battery_cycle` = -1: Çekirdek bu değeri -1 ile başlatıyor (`sec_battery.c:9973`). Değeri Samsung'un kendi kullanıcı alanı `/efs`'teki kayıtlardan geri yazıyor; bu yazılım LineageOS'ta yok.
  - `/efs/FactoryApp/batt_discharge_level` = 10299: Samsung'un tuttuğu birikmiş boşalma değeri, son güncelleme 2026-10-05. Bu tarih stok yazılımın son çalıştığı gün; o günden beri güncellenmiyor ve bayat.
  - `fg_cycle` = 27: yakıt göstergesinin `CYCLES_REG` yazmacı. Sürücü bu yazmacı tam kapasite yeniden hesaplanırken ve hızlı başlatmada sıfırlıyor (`max77705_fuelgauge.c` 102, 929, 959). Yani pilin ömür boyu döngüsü değil, son sıfırlamadan beri geçen süreyi gösteriyor. Döngü olarak gösterilmesi yanıltıcı olur.
- **Üretici ve model:** `batt_type` boş; gerçek bir kaynak yok.

**Yan bulgu, pil ömrü:** `battery_cycle` -1 kaldığı için Samsung sürücüsünün yaşa göre şarj gerilimi düşürme adımları hiç devreye girmiyor. İlgili kod: `sec_bat_aging_check`, `sec_battery.c` 2956'da `batt_cycle < 0` ise çıkıyor. Bu LineageOS portunun durumu, bizim imajımızla ilgili değil; LineageOS'a bildirilmeye değer.

## Neden system tarafından düzeltilmiyor

- Değerleri üreten health HAL vendor'da. Aynı adla system tarafından ikinci bir `IHealth/default` kaydetmek gerçek HAL'le çakışır.
- vendor serviceability'yi değiştirmek Google imzasını bozar ve imza izinli çağıranları kırar.
- sysfs'e eksik adları kullanıcı alanından eklemek mümkün değil. Durağan bir kopya ise canlı değer yerine eski veri gösterir.
- `BatteryService`'i yamamak kök nedeni çözmez, framework'ü Samsung düğümlerine bağlar.

## Dürüst düzeltme (öneri, kullanıcı kararı gerekiyor)

1. **Vendor health HAL yapılandırması.** LineageOS `android_device_samsung_sm8250-common` ya da `android_hardware_samsung` `aidl/health`. `healthd_config` (AOSP android-17.0.0_r1 `healthd/include/healthd/healthd.h`) şu iki yolun cihaza göre verilmesine izin veriyor:
   - `batteryStateOfHealthPath = /sys/class/power_supply/battery/fg_asoc`: Ekranda 94 % görünür.
   - `batteryCycleCountPath = /sys/class/power_supply/battery/battery_cycle`: Bugün -1 döner, uygulama bunu "yok" sayar ve 0 göstermez. Samsung'un döngü kaydı ileride geri yazılırsa gerçek değer görünür.

   Bu değişiklik vendor bölümünü yeniden derlemeyi ya da LineageOS'a katkıyı gerektirir. Bu projenin imaj hattında vendor üretilmiyor.
2. **Döngü sayısının gerçek kaynağı:** Samsung'un `/efs` hesabını sürdürmek. Bunun için Samsung'un kapalı yazılımının davranışını yeniden yazmak ve `/efs`'e yazmak gerekir. `/efs` cihaza özgü fabrika verisidir; bu gecenin kurallarıyla da yasak. Önerilmiyor.
3. **"mAh" satırı:** vendor serviceability, her power_supply girdisinde `cycle_count`, `manufacturer` ve `model_name` dosyalarının bulunmasını şart koşuyor. Çekirdeğin bu standart özellikleri sunması gerekir. Üretici ve model için gerçek bir kaynak olmadığından dürüst bir değer verilemez. Bu satır, çekirdek değişse bile ancak gerçek bir kaynak bulunursa düzelir.

Bu projenin system, system_ext ve product imajları içinde yapılabilecek, uydurma değer içermeyen bir düzeltme yok. SELinux kuralı gerekmiyor.

## Ek (23 Ekim 2026): imaj hattından yapılabilecek dürüst yol, öneri

Tablette doğrulanan durum: `vendor.health-samsung` servisi `/vendor/etc/init/android.hardware.health-service.samsung.rc`'de tanımlı, ikili `/vendor/bin/hw/android.hardware.health-service.samsung` (`hal_health_default_exec`), alan `hal_health_default`. `fg_asoc` = 94, `battery_cycle` = -1, `cycle_count` yok.

Vendor bölümüne dokunmadan, system tarafından **aynı HAL'in yapılandırılmış bir derlemesini** koymak mümkün görünüyor:
- init, `/product/etc/init/*.rc` dosyalarını vendor'dan sonra okur; `service vendor.health-samsung <yol> ... override` satırı vendor tanımını değiştirir (AOSP init `override` anahtar sözcüğü). Böylece vendor ikilisi dururken /product'taki derleme çalışır; seclabel vendor'daki gibi `hal_health_default` olur (file_contexts'e `/product/bin/hw/...` için `hal_health_default_exec` etiketi; hattımız file_contexts ekliyor).
- İkili: LineageOS `android_hardware_samsung` `aidl/health/Health.cpp` + AOSP `hardware/interfaces/health/aidl/default` kaynağından, `healthd_config` içinde `batteryStateOfHealthPath = /sys/class/power_supply/battery/fg_asoc` ve `batteryCycleCountPath = /sys/class/power_supply/battery/battery_cycle` verilerek derlenir. Sonuç: "Battery health 94 %", döngü -1 → uygulama "yok" gösterir (0 değil). mAh satırı için gerçek kaynak yok; değişmez.
- Maliyet: HAL AOSP kütüphanelerine (libhealthloop, libbatterymonitor, libhealth_aidl_impl, libbinder_ndk) bağlı; tam AOSP ağacı olmadan derlemek için bu kütüphanelerin kaynağı NDK ile elle derlenmeli ya da AOSP android17 ağacı indirilip yalnız bu modül derlenmeli (çok GB disk, kaynak kapısı). Tahmini birkaç gün. Vendor HAL'in kendisi değil kopyası değiştiği için LineageOS'a katkı ayrıca gönderilebilir (asıl kök neden orada).
- Uydurma değer yok: bütün değerler çekirdeğin sysfs düğümlerinden gelir.

Karar Kadir'in: (a) bu derleme işine girilsin, (b) LineageOS'a yama gönderilip beklensin, (c) olduğu gibi kalsın (Diagnostics 0 gösterir).
