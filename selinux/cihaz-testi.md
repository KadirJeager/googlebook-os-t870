# SELinux Enforcing cihaz testi (ana oturum çalıştırır)

Tablet: `adb` (birden çok cihaz bağlıysa `export ANDROID_SERIAL=$(python3 T870-araclar/t870_cihaz.py)`). Her adımın çıktısı `selinux/kanit/<tarih-saat>-<ad>.txt` olarak kaydedilir. `yamalar/selinux.py dogrula`, `kanit/` altındaki bütün `.txt` dosyalarındaki AVC satırlarını tarar. Adımlar açılış tamamlandıktan ve masaüstü göründükten sonra, bu sırayla çalıştırılır. Aşağıda `K` kayıt klasörünü, `D` tarih-saati gösterir.

```bash
A="adb"; K=T870-araclar/selinux/kanit; D=$(date +%Y%m%d-%H%M)
```

## 1. Kip ve imaj
```bash
{ $A shell getenforce; $A shell cat /sys/fs/selinux/enforce; $A shell ls -l /system/etc/init/gbos-selinux.rc; \
  $A shell ls -Z /system/bin/gbos-linuxd /system/bin/gbos-vpd-executor /system/etc/ld.config.32 /system/lib/gbos-vendor-override; \
  $A shell getprop ro.build.fingerprint; } > $K/$D-kip.txt 2>&1
```
Beklenen sonuçlar:
- `Enforcing` ve `1`.
- `gbos-selinux.rc` için "No such file".
- Etiketler `file_contexts`'e uymalı: `gbos_linuxd`, `gbos_vpd_executor_exec`, `system_linker_config_file`.

## 2. Ret kayıtları: değerlendirmenin asıl girdisi
```bash
$A shell dmesg > $K/$D-dmesg.txt
$A shell logcat -b all -d > $K/$D-logcat-all.txt
$A shell 'ps -A -o PID,USER,LABEL,NAME' > $K/$D-ps.txt
```
- Beklenen: `grep 'avc: *denied' $K/$D-dmesg.txt $K/$D-logcat-all.txt | grep permissive=0` çıktısında yalnız `selinux/kabul.txt`'te kararı olan redler bulunmalı.
- `ps` çıktısı, `Access denied finding property` satırlarındaki pid'leri süreç adına çevirmek için gerekli. Hangi sürecin hangi özelliği okuyamadığı ancak böyle anlaşılır.

## 3. Masaüstü servisleri kayıtlı mı
```bash
$A shell service list | grep -E 'desktop_net|desktop_data_migration|desktop_diagnostics|IDesktopServiceabilityService|IDesktopSettingHealthService|IVideoChatManager|snapshot\.ISnapshot|IDrmNetworkService|IAqueduct|IFace|IVpdExecutor' > $K/$D-servisler.txt
```
Beklenen: ilk dokuz ad ve `android.hardware.biometrics.face.IFace/default` listede olmalı. `IVpdExecutor` talep üzerine açılan bir servis; ilk çağrıdan önce listede görünmeyebilir.

## 4. Çalışma zamanı hataları
```bash
grep -nE 'SecurityException|SELinux denied|Access denied finding property|does not have a SELinux domain|Could not start service|FATAL EXCEPTION|has died' $K/$D-logcat-all.txt > $K/$D-hatalar.txt
```
Beklenen sonuçlar:
- `system_server` için SecurityException ya da "SELinux denied" olmamalı.
- "does not have a SELinux domain" olmamalı. Bu ileti, etiketi yanlış bir servis ikilisi olduğunu gösterir.

## 5. Süreçler ve alanlar
```bash
$A shell 'ps -AZ' | grep -E 'sense-face-hal|co\.aospa\.sense|gbos_linuxd|gbos-linuxd|gbos_vpd|gbos_bootlog|audio\.service|media\.c2|drm|cas|wifi|bluetooth|camera' > $K/$D-alanlar.txt
```
Beklenen etiketler:

| Süreç | Etiket |
|---|---|
| sense-face-hal | `u:r:system_server:s0` |
| `co.aospa.sense` | `priv_app` |
| gbos-linuxd | `u:r:gbos_linuxd:s0` |
| bootlog | `u:r:gbos_bootlog:s0` |
| ses, codec2, drm, cas HAL'leri | kendi vendor alanları; hepsi çalışıyor olmalı |

## 6. adb root (su) yolu
```bash
{ $A root; sleep 3; $A shell id; $A shell cmd package list packages | head -3; $A shell dumpsys activity processes | head -5; \
  $A shell am get-current-user; $A shell cmd statusbar collapse; echo "rc=$?"; } > $K/$D-root.txt 2>&1
```
Beklenen sonuçlar:
- `uid=0` ve `context=u:r:su:s0`.
- Komutların hepsi çıktı vermeli; `cmd` takılmamalı.

## 7. Kullanıcının deneyimi (dokunarak, ekranda)
Her denemeden sonra o adımın `logcat -b all -d` çıktısı ayrı dosyaya kaydedilir.
```bash
$A shell logcat -b all -d > $K/$D-<deneme>.txt
```
1. **Kilit ekranında yüz tanıma:** kilitle, yüzle aç. Yeni yüz kaydı Ayarlar'dan yapılır.
2. **Ses:** müzik çal, ses düğmeleriyle değiştir.
3. **Video:** bir video oynat; codec2 HAL'i çalışmalı.
4. **Widevine:** korumalı bir akışı başlat; IDrmFactory açılmalı.
5. **Wi-Fi ve Bluetooth:** aç, kapat, bağlan.
6. **Kamera:** uygulamayı aç; ön ve arka kamerayla birer fotoğraf çek.
7. **Googlebook uygulamaları:** İpuçları, Onarım modu, Görev yöneticisi ve masaüstü servis uygulaması açılmalı, çökmemeli.
8. **Terminal (kuruluysa):** uygulamayı aç; oturum açılmalı. Ardından `$A shell gbos-linux status` çalıştırılır.

## 8. Bootlog
```bash
$A shell ls -l /metadata/gbos-bootlog/ > $K/$D-bootlog.txt 2>&1
```
Beklenen: `logcat.txt` dosyası var ve büyüyor.

## Sonrası
Bilgisayarda `python3 yamalar/selinux.py dogrula --work <imajın çalışma ağacı>` çalıştırılır.
- Yeni redler `INCELENMEDI` ya da `MICA/LINEAGE IZIN VERIYOR` olarak listelenir.
- selinux-port oturumu bunları politikaya ya da `kabul.txt`'ye işler.
