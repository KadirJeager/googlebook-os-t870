# Öneri: şarj sınırı, `com.android.desktop.power.IDesktopPowerChargeControl/default`

Durum: **öneri.** Kullanıcı kararı yok; imaja giren kod yazılmadı. Bu belge, kararı verdirecek bilgileri ve uygulanırsa izlenecek yolu toplar. Kaynaklar satır sonlarında; tablet ölçümleri `kanit/20261022-sarj/` altında.

## Kısa sonuç

- mica'nın "Şarj optimizasyonu" özelliği, açıkken pili %80'de tutar. Bu tablette sessizce çalışmıyor: masaüstü hizmetleri şarj HAL'ini bulamıyor, ayar değişse de hiçbir şey olmuyor.
- Tabletin çekirdeğinde aynı işi çekirdek düzeyinde yapan bir düğüm var: `/sys/class/power_supply/battery/batt_full_capacity`. Bu Samsung sürücüsünün kendi sınır mekanizması.
- Öneri: mica'nın arayüzünü bu düğüme bağlayan küçük bir system servisi, `gbos-charge-control`, VPD servisiyle aynı yapıda.
- Pili kalıcı olarak boşaltabilecek ya da şarjı tamamen kesebilecek düğümler kullanılmaz: `batt_slate_mode`, `store_mode`.

## 1. mica'da kim kullanıyor

Arayüz: `IDesktopPowerChargeControl`, sürüm 1, HASH `aba939fd9c5f7bbfe96770e7ff3bc7c9859a5725`, tek yöntem `setChargingStrategy(ChargingStrategy)`. `ChargingStrategy` bir union:

| Alan | Anlamı |
|---|---|
| `sustainParams` | `SustainParams{int lowerPercent=-1, int upperPercent=-1}`. Pil üst sınıra ulaşınca şarj durur, alt sınıra inince yeniden başlar. -1/-1 sınırı kaldırır. |
| `reducedRateMilliamps` | Şarj akımını mA cinsinden sınırlar. |

Kaynak: desktop-services.jar, `com/android/desktop/power/{IDesktopPowerChargeControl,ChargingStrategy,SustainParams}`.

Çağıranlar:

1. **system_server, `DesktopChargingOptimizationController`** (desktop-services.jar). Asıl kullanıcı bu.
   - HAL'i `ServiceManager.getService` ile alıyor (`:127`). Bulamazsa log yazıp -1 dönüyor; beklemiyor.
   - Kullanıcı başına `Settings.Secure charge_optimization_mode` okunuyor. 1 ise `sustain(80, 80)`, değilse `sustain(-1, -1)` gönderiliyor (`applyChargeLimit`). Başka değer gönderilmiyor; `reducedRateMilliamps` hiç kullanılmıyor.
   - Ne zaman gönderildiği:
     - açılışta: `restoreChargeLimitState`, `DesktopPowerService:443`
     - kullanıcı değişince: `:477`
     - şarj cihazı takılınca: `handleBatteryChanged`
     - Ayarlar'dan değişince: `IDesktopSettingHealthService.setChargingPolicy(policy)`, `DesktopPowerService:233`. `policy == 2` sınırı açar; HAL yoksa false döner.
2. **`com.android.desktop.serviceability` `setNormalChargingRange`.** HAL'i `waitForService` ile süresiz bekliyor; yalnız PostManufacturing'in fabrika akışı çağırıyor. Bu tablette o akış açılmıyor (README §3).
3. **Ayarlar (SettingsGoogle)** `charge_optimization_mode`, `CHARGE_OPTIMIZATION_CHARGE_LIMIT` ve `charging_optimization_*` metinlerini içeriyor. Seçeneğin tablette görünüp görünmediği henüz ekranda doğrulanmadı.

mica'nın kendi uygulaması (`vendor.google.desktop.charge-control-hal.qcom`) Qualcomm çekirdeğinin standart düğümlerine yazıyor: `charge_control_start_threshold`, `charge_control_end_threshold`, `qcom-battery/charge_control_en` ve `usb/input_current_limit`. Kaynak: `vendor/etc/init/hal_desktop_charge_control_qcom.rc`. Tabletin pil kaydında bu düğümler yok (`kanit/20261022-diagnostics/power_supply.txt`).

## 2. Tablette ne var

Kaynak: LineageOS `android_kernel_samsung_sm8250` lineage-24.0, `drivers/battery_v2/sec_battery.c` ve `sec_battery_sysfs.c`. Tablette çalışan çekirdek bu ağaçtan.

| Düğüm | Sürücüdeki davranış | Kullanılır mı |
|---|---|---|
| `batt_full_capacity` | 0–100 kabul eder (`sysfs.c` BATT_FULL_CAPACITY store). Değer 1–99 iken izleme işi `sec_bat_check_full_capacity` çalışır: kapasite ≥ X olunca `SEC_BAT_CHG_MODE_CHARGING_OFF` ve durum `NOT_CHARGING`; kapasite ≤ X−2 olunca şarj yeniden başlar (`sec_battery.c` 5651–5676). 0 ya da 100 sınırı kaldırır ve şarjı geri açar. Açılışta 0'dır (`:9981`). | **Evet.** Çekirdek uyguluyor, yani servis çökse ya da tablet uykuda olsa da sınır geçerli. Giriş açık kalıyor, tablet şarj cihazından beslenmeye devam ediyor. |
| `charging_enabled` | 0 yazılınca şarj durur ve giriş akımı mağaza modu sınırına çekilir (`sec_battery.c` 1783–1785). LineageOS'un `vendor.lineage.health` HAL'i bu düğümü TOGGLE ve BYPASS olarak kullanıyor (`hardware/lineage/interfaces` lineage-24.0 `health/aidl/default/ChargingControl.cpp`). | Yedek yol. Sınırı ancak kullanıcı alanında izleyen bir süreç uygulayabilir; giriş akımı da kısıldığı için yük altında pil yavaşça boşalabilir. |
| `batt_slate_mode` | Girişi tamamen keser. | **Hayır:** takılıyken pil boşalır. |
| `store_mode` | Mağaza tanıtım modu; 0 yazmak kapatmıyor, yalnız yeniden başlatmada sıfırlanıyor. | **Hayır.** |
| `vendor.lineage.health.IChargingControl/default` | Tablette kayıtlı. sm8250-common yapılandırması şarj yolu tanımlamıyor; varsayılan uygulama `charging_enabled`'ı kendisi buluyor. Tabletteki `getSupportedMode` yanıtı 3, yani TOGGLE ve BYPASS (`kanit/20261022-sarj/lineage-supported-mode.txt`). | Sınır modu (LIMIT) yok. |

## 3. Önerilen tasarım

Yeni bir servis, `gbos-charge-control`. VPD executor gibi `mica-servisleri/` altında olacak: native NDK binder, lazy AIDL, system bölümünde, VINTF parçasıyla ilan edilmiş.

- **`sustainParams(-1, -1)`:** `batt_full_capacity`'ye 0 yazılır.
- **`sustainParams(lower, upper)`:**
  - Koşul: 0 ≤ lower ≤ upper ≤ 100. Değilse `IllegalArgumentException`.
  - `upper` 100 ise 0 yazılır, yani sınır yok. Değilse `upper` yazılır.
  - Çekirdeğin geri dönüş eşiği sabit `upper − 2`. mica'nın gönderdiği 80/80 için tablet 80'de durur, 78'de yeniden başlar. `lower` bu yüzden tam uygulanamaz.
  - Uygulanan eşik logda ve belgede açıkça yazılır.
  - Fark güvenli yönde: pil istenenden en çok %2 daha alçakta yeniden şarj olur, hiçbir zaman sınırın üstüne çıkmaz.
- **`reducedRateMilliamps`:** `EX_UNSUPPORTED_OPERATION`. Samsung sürücüsünde mA cinsinden güvenli, kullanıcıya açık bir giriş yok; `siop_level` ısıl kısma içindir ve sistem kullanıyor. mica bu yolu zaten hiç kullanmıyor.
- **Doğrulama:** Yazdıktan sonra değer geri okunur. Tutmazsa `ServiceSpecificException(-1)` atılır; desktop-services bunu -1 olarak alır ve logluyor.
- **Kalıcılık:** Çekirdek değeri açılışta sıfırlıyor. Yeniden uygulama desktop-services'in açılış ve takılma olaylarıyla zaten yapılıyor (`restoreChargeLimitState`, `handleBatteryChanged`), servisin ayrıca dosya tutması gerekmiyor.
- **Erişim:** Yalnız uid 1000 (system_server) ve 0.

**`yamalar/mica_servisleri.py` içinde:** VPD ile aynı biçimde `VPD_FILES` yanına bir `CHARGE_FILES` kümesi:
- `system/bin/gbos-charge-control`
- `system/etc/init/gbos-charge-control.rc`
- `system/etc/vintf/manifest/gbos-charge-control.xml`
- `plat_file_contexts` satırı

Düğümün sahibi zaten `system:radio`, izni 0660 (`kanit/20261022-sarj/dugumler.txt`). `user system` ile çalışan servis rc'de chown gerekmeden yazabilir. `check()` ilanın tek olduğunu ve dosyaları denetler; `build.sh` ikinci hedefi de derler.

**SELinux (selinux-port):**
- `gbos_charge_control` alanı
- `vpd_executor_service` gibi bir servis tipi; system_ext service_contexts'te mica'dan gelen `hal_desktop_charge_control_service` zaten var
- düğümün tablet etiketine yazma izni: `sysfs_batteryinfo`, bir platform tipi, `kanit/20261022-sarj/dugumler.txt`

Platform politikası bu tipe yazmayı belirli alanlarla sınırlıyor olabilir. selinux-port'un `secilc` neverallow denetimiyle doğrulanmalı.

Kural yazılamazsa **yedek yol** Lineage'in `IChargingControl.setChargingEnabled` HAL'i olur. Bu yol Treble açısından temizdir, ama sınırı kullanıcı alanında uygulamak gerekir: pil yüzdesi `IHealth` geri çağrısıyla izlenir. Giriş akımı kısıldığı için daha zayıf bir çözüm.

**Test:**
- Host birim testleri: sınır ve doğrulama mantığı.
- Emülatör: emülatörde Samsung düğümü yok. Düğüm yolu yalnız test yapılandırmasında geçici bir dosyaya yönlendirilerek protokol ve davranış sınanır.
- Tablet, gerçek kullanımla:
  - Ayarlar'da sınırı aç; şarj %80'de dursun ve `status` `Not charging` olsun.
  - `batt_full_capacity` 80 okunsun; 78'de şarj yeniden başlasın.
  - Kapatınca şarj %100'e çıksın.
  - Yeniden başlatma ve kullanıcı değişiminden sonra ayar geri gelsin.

## 4. Riskler

- **Pil ve şarj güvenliği:** Samsung sürücüsünün sıcaklık, aşırı gerilim, güvenlik zamanlayıcısı ve şişme korumaları bu düğümden bağımsız çalışıyor; düğüm yalnız üst sınırı aşağı çekiyor. `batt_slate_mode` ve `store_mode` kullanılmadığı için takılıyken boşalma ya da geri dönmeyen bir mod riski yok. Değer aralığı çekirdekte de denetleniyor (0–100).
- **Açılıştaki kısa pencere:** Çekirdek 0 ile açılıyor. desktop-services sınırı yeniden uygulayana kadar, yani açılıştan sonraki birkaç saniye, sınır yok. Bu güvenli yönde bir sapma.
- **Gösterge:** Sınırda pil durumu `Full` değil `Not charging` görünür. mica Ayarlar'ının "sınıra ulaşıldı" metinleri bunu karşılıyor olabilir; ekranda doğrulanmalı.
- **Çakışma:** Aynı düğüme yazan başka bir şey olmamalı. LineageOS'un Lineage Health servisi mica sisteminde yok, Samsung'un One UI pil koruması da yok. Lineage HAL'i yalnız `charging_enabled`'a yazıyor ve onu çağıran yok.
- **Alt sınır sapması:** `lower` tam uygulanamıyor (sabit −2). mica yalnız 80/80 gönderdiği için pratikte fark 2 puan.
- **Belirsiz:** Ayarlar seçeneğinin tablette görünüp görünmediği. Ekran gerektiriyor; ana oturumun "Kadir gelince" listesinde. Ayar değeri tablette var: `charge_optimization_mode`, kullanıcı 10 için 0.

## 5. Karar gerekenler

1. Özellik açılsın mı? Açılırsa tasarım 3. bölümdeki gibi; kod, test ve SELinux birlikte gelir.
2. Sınır değeri mica'nın 80'i olarak kalır. Değeri desktop-services belirliyor; değiştirmek platform kodunu değiştirmeyi gerektirir, önerilmez.
