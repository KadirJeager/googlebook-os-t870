# Doğal yön yatay: cihaz kabulü

Modül: `yamalar/dogal_yon.py`. Bu belge, imaj tablete yazıldıktan sonra ana oturumun koşacağı kabulü anlatır. Tablete ADB ile yalnız ana oturum dokunur.

## Ön koşul

İmajda sensör ekseni ve kamera yönü katmanları da bulunmalı: [`hal/BENIOKU.md`](hal/BENIOKU.md). `apply_patches.py`, `dogal_yon.install_all()` ile üç katmanı birlikte kurar ve doğrular. Yalnız ekran ve dokunma değişikliğiyle yazılan bir imajda otomatik döndürme ve kamera önizlemesi 90° yanlış olur.

## 1. Ölçümle doğrulama

```
A="adb shell"   # birden çok cihaz bağlıysa önce: export ANDROID_SERIAL=$(python3 T870-araclar/t870_cihaz.py)
$A getprop ro.surface_flinger.primary_display_orientation        # ORIENTATION_90
$A getprop | grep bootanim.set_orientation                        # boş
$A getprop ro.vendor.fingerprint.sensor_location                  # 295|-36|130|local:4630947232161729153
$A wm size                                                        # Physical size: 2560x1600
$A dumpsys display | grep -m1 -o 'installOrientation [0-9]'       # installOrientation 1
$A dumpsys window displays | grep -m1 mRotation                   # yatay tutuşta ROTATION_0
$A dumpsys input | grep -A3 -E 'sec_touchscreen|sec_e-pen' | grep -i 'Orientation'
#   her iki aygıt: Orientation: ROTATION_90; ConfigurationFile: /product/usr/idc/<ad>.idc
$A dumpsys input | grep -B2 -A6 'Vendor_04e8_Product_a035\|Book Cover' | grep ConfigurationFile
#   kılıf klavyesi: /vendor/usr/idc/Vendor_04e8_Product_a035.idc kalmalı
```

Sensör ve kamera HAL katmanı:

```
$A getprop init.svc.gbos.sensors-hal-multihal                     # running
$A getprop init.svc.vendor.sensors-hal-multihal                   # stopped
$A getprop init.svc.gbos.camera.provider                          # running
$A getprop init.svc.vendor.camera.provider                        # stopped
$A ps -AZ | grep -E 'samsung-multihal-gbos|provider-service.samsung-gbos'
#   u:r:hal_sensors_default:s0 ... ve u:r:hal_camera_default:s0 ...
$A dumpsys android.hardware.sensors.ISensors/default | head -16
#   "Googlebook OS natural orientation (dogal-yon/hal): panel rotation 90" ve dönüşen sensörler
$A dumpsys media.camera | grep -E 'Facing|Orientation: '        # dört kamerada Orientation: 0
$A logcat -d -b all | grep -E 'avc: denied.*(hal_sensors_default|hal_camera_default)'   # boş
$A logcat -d | grep -E 'gbos-dogal-yon|natural orientation'      # başlatıcı ve dönüşüm satırları
```

Tablet yatay tutuşta (ön kamera üst uzun kenarda) masada dik dururken `dumpsys sensorservice` ivmeölçerde y ≈ +9.8 ve x ≈ 0 göstermeli. `DEVICE_ORIENTATION` (type 27) sensörü listede bulunmamalı ve `dumpsys window` içinde `AccelSensorJudge` görünmelidir. Ayrıntılı ölçüm aracı: `hal/araclar/sensor-olcum.c`. (Not: `hal/araclar/canli-deney.sh` yalnız katmanın henüz imaja girmediği ve `vendor.sensors-hal-multihal`'in çalıştığı ağaçlar içindir; canlı sistemde `gbos.sensors-hal-multihal` durdurulmaz.)

## 2. Gerçek kullanımla kabul

Her madde elle yapılır. Sonuç ekranda ve kaydedilen dosyada ölçülür.

| # | Adım | Geçme ölçütü |
|---|---|---|
| 1 | Açılış | Açılış animasyonu ve kilit ekranı yatay tutuşta düz; yan dönmüş ya da gerilmiş değil |
| 2 | Otomatik döndürme | Dört yöne çevir: yatay, dikey, ters yatay, ters dikey. Ekran her yönde düz döner. Yön sensörü ve ivmeölçer yolu ayrı ayrı sınanır: `settings put secure` ile değil, gerçek çevirmeyle |
| 3 | Dokunma | Dört yönde ekranın dört köşesine ve ortasına dokun. Geliştirici seçeneklerinde "İşaretçi konumu" açıkken iz parmağın altında |
| 4 | Kalem | Dört yönde aynı ölçüm. Havada tutunca imleç ucun altında. Eğimle çizim: Google Keep'te eğik çizgi eğimle aynı yönde kalınlaşır |
| 5 | Kılıf touchpad'i | Yatay ve dikey tutuşta parmak sağa giderken imleç ekranda sağa gider; değişiklikten önceki davranışla aynı |
| 6 | Kamera önizleme | Google Kamera ve WhatsApp, ön ve arka, dört yönde önizleme düz ve ön kamera ayna |
| 7 | Foto ve video | İki uygulamada, iki kamerada, yatay ve dikey birer foto ve video. Bilgisayarda açınca düz; EXIF yönü ve video döndürme etiketi tutarlı |
| 8 | WhatsApp ön kamera | Bu işin çıkış noktası: önizleme ve gönderilen video baş aşağı değil |
| 9 | Ekran görüntüsü | Yatay ve dikey tutuşta alınan ekran görüntüsü ekranda görülenle aynı yönde |
| 10 | Ekran kaydı | Yatay başlatılıp dikeye çevrilen kayıt oynatılınca düz |
| 11 | Yüz kilidi | Yatay ve dikey tutuşta kilit açılır; yeni kayıt da tamamlanır |
| 12 | Parmak izi | Kayıtta yan sensör ipucu güç tuşunun yanında, üst kenarda gösterilir; kilit açılır |
| 13 | Görev çubuğu ve masaüstü modu | Görev çubuğu alt uzun kenarda; serbest pencereler döndürmeden sonra ekranda kalır |
| 14 | Cast | Ekranı yayınla: alıcıda görüntü düz ve 16:10 |

Kanıt: `cihaz-kabul/kanit/<tarih>-dogal-yon/`. Her maddede ekran görüntüsü ya da dosya ve `dumpsys input`, `dumpsys sensorservice`, `dumpsys media.camera` çıktıları bulunur.

## 3. Geri dönüş

- Önceki imaj seti `tablete-yaz.py` ile yazılır: `denetim/imaj/` ya da `denetim/bilinen-iyi-272-0130/`. Bu değişiklik yalnız system ve product yuvalarını etkiler. vendor, boot ve recovery yuvalarına dokunmaz; recovery her durumda açılır.
- Değişiklik açılışı engellemez: SurfaceFlinger bilinmeyen değerde ORIENTATION_0'a düşer, idc hatası yalnız dokunmayı etkiler.
