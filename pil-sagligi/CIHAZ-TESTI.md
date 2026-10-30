# Tablette doğrulama: pil sağlığı

`pil_sagligi.install()` imaja girip tablete yazıldıktan sonra ana oturum uygular. Bilgisayardan: `A="adb"`, `$A root`. Çıktılar `kanit/<tarih>/` altına konur.

## Yazmadan önce, karşılaştırma için

```bash
$A shell dumpsys android.hardware.health.IHealth/default > once-health.txt
$A shell dumpsys battery > once-battery.txt
$A shell 'cd /sys/class/power_supply/battery; ls -lZ fg_asoc battery_cycle capacity; cat fg_asoc battery_cycle'
$A shell su system cat /sys/class/power_supply/battery/fg_asoc /sys/class/power_supply/battery/battery_cycle
```

Son iki komut, düğümlerin etiketini ve `system` kullanıcısının okuyup okuyamadığını gösterir. `fg_asoc` ile `capacity` aynı etiketi taşımalı. Vendor HAL `capacity`'yi zaten okuyor, bu yüzden `fg_asoc` da okunabilir olmalı.

## Açılıştan sonra

1. Dosyalar ve etiket:
   ```bash
   $A shell ls -lZ /product/bin/hw/android.hardware.health-service.samsung-gbos /product/etc/init/android.hardware.health-service.samsung-gbos.rc
   ```
   Beklenen: ikili `-rwxr-xr-x root shell u:object_r:hal_health_default_exec:s0`, rc `-rw-r--r-- root root u:object_r:system_file:s0`.

2. Hangi servis çalışıyor:
   ```bash
   $A shell getprop init.svc.vendor.health-samsung; $A shell getprop init.svc.gbos.health-samsung
   $A shell 'ps -AZ | grep -i health'
   $A logcat -b all -d | grep -iE 'health-samsung|treble boundary|vendor.health'
   ```
   Beklenen: `stopped` ve `running`. Tek süreç `u:r:hal_health_default:s0` alanında, `/product/bin/hw/android.hardware.health-service.samsung-gbos`. Vendor ikilisi çalışmıyor. `vendor.charger` normal açılışta zaten yok.

3. HAL değerleri:
   ```bash
   $A shell dumpsys android.hardware.health.IHealth/default
   ```
   Beklenen: `batteryCycleCount: -1`, `batteryStateOfHealth: 94` ya da o anki `fg_asoc` değeri. Diğer alanlar `once-health.txt` ile aynı türden. `batteryFullChargeDesignCapacityUah: 8000000` korunmalı.

4. Framework tarafı:
   ```bash
   $A shell dumpsys battery
   ```
   Beklenen: düzey, şarj durumu ve sıcaklık önceki gibi güncelleniyor. Kablo takılıp çıkarılınca `AC/USB powered` değişmeli: uevent yolu çalışıyor demektir.

5. Gerçek kullanım, Kadir ya da dokunarak: Ayarlar › Battery health / Diagnostics pil kartı.
   - "Battery health" **94 %** ya da o anki `fg_asoc` değeri.
   - "Cycle count" satırı **0 göstermemeli**; uygulama -1'i "yok" sayar.
   - "mAh battery" satırı değişmez; kaynağı vendor serviceability, bu modül değil.
   Ekran görüntüsü kanıta konur.

6. SELinux ve hatalar:
   ```bash
   $A logcat -b all -d | grep -E 'avc: +denied.*hal_health_default'
   $A shell dmesg | grep -E 'avc: +denied.*hal_health_default'
   $A logcat -b all -d | grep -iE 'healthd|HealthLoop' | head -50
   ```
   Beklenen: red yok. `HealthLoop` BPF süzgeci satırı vendor HAL'deki gibi; ya bağlandı ya da aynı uyarı. Kayıtlar `selinux/kanit/` altına da konup `python3 -I yamalar/selinux.py dogrula` çalıştırılır.

7. Kendini toparlama, isteğe bağlı: vndservicemanager yeniden başlarsa `class_restart hal` durdurulmuş vendor servisini de başlatır. rc bunu yakalayıp durdurmalı ve bizimkini yeniden kaydettirmeli:
   ```bash
   $A shell 'kill $(pidof vndservicemanager)'; sleep 5
   $A shell getprop init.svc.vendor.health-samsung; $A shell getprop init.svc.gbos.health-samsung
   $A shell dumpsys android.hardware.health.IHealth/default | grep -E 'batteryStateOfHealth|batteryCycleCount'
   ```
   Beklenen: `stopped`, `running`, değerler yine 94 ve -1.

8. Kapalıyken şarj: tablet kapalıyken kablo takılınca şarj animasyonu önceki gibi gelmeli. Bu yolu vendor `vendor.charger` yürütür, modül ona dokunmaz.

## Geri dönüş

Modül yalnız product'a dosya ekler. Sorun çıkarsa `install()` çağrısı kaldırılıp imaj yeniden üretilir; vendor servisi o zaman eskisi gibi başlar.
