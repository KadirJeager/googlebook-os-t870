# Doğal yön: sensör ve kamera HAL katmanları

Tablet stok Samsung ve mica gibi doğal yönü yatay olan bir cihaz olur. Ekran, dokunma, kalem, açılış animasyonu ve parmak izi katmanı: [`yamalar/dogal_yon.py`](../../yamalar/dogal_yon.py). Bu modül eksik iki katmanı ekler:

- **Sensör eksenleri.** İvmeölçer, jiroskop, manyetometre, kalibresiz türevleri, yerçekimi, doğrusal ivme, üç rotasyon vektörü ve cihaz yönü sensörü dikey panele göre bildiriliyordu.
- **Kamera yönü.** `android.sensor.orientation` arka kamerada 90, ön kamerada 270 idi. `android.lens.poseRotation` ve `poseTranslation` da dikey çerçeveye göreydi.

Çıkış noktası WhatsApp ön kamera sorunu: [`acilis-loglari/whatsapp-on-kamera-20261023/INCELEME.md`](../../acilis-loglari/whatsapp-on-kamera-20261023/INCELEME.md). Pipeline modülü: [`yamalar/dogal_yon_hal.py`](../../yamalar/dogal_yon_hal.py).

## Ne yapıyor

Tablette çalışan iki vendor HAL'in kendisi kaynaktan yeniden derlenir ve /product'tan çalışır:

| Katman | Vendor servisi | Yerine geçen | Kaynak |
|---|---|---|---|
| sensör | `vendor.sensors-hal-multihal` | `gbos.sensors-hal-multihal` | LineageOS `hardware/samsung` `aidl/sensors` + AOSP `sensors/aidl/default/multihal`, `sensors/common/default/2.X/multihal` |
| kamera | `vendor.camera.provider` | `gbos.camera.provider` | LineageOS `hardware/samsung` `aidl/camera/{common,device,provider}` + AOSP `camera/common/default` |

Tek değişiklik çerçeve dönüşümüdür. Matematiği [`src/natural_orientation.h`](src/natural_orientation.h) dosyasındadır. Android bağımlılığı yoktur ve testlerde ana makinede derlenir. Tek değer `PANEL_ROTATION`'dır: `yamalar/dogal_yon.py`'den okunur ve derlemeye `-DGBOS_PANEL_ROTATION` olarak girer. Kütüphanede `gbos-dogal-yon PANEL_ROTATION=90` işareti bulunur.

- **Sensör** ([`0002`](kaynak-yamalari/hardware_samsung/0002-sensors-dogal-yon-eksenleri.patch), [`0005`](kaynak-yamalari/hardware_samsung/0005-sensors-oto-donus-frameworkte.patch)):
  - Statik sensörlerin olayları çerçeveye taşınır. 90° için `(x, y, z) → (−y, x, z)`, panelin sağ kenarı yeni üst kenardır.
  - Kalibresiz sensörlerde sapma da döner. Sınırlı eksenli türlerde destek bayrakları eksenleriyle yer değiştirir.
  - Kuaterniyonlar `q·q_z(−R)` olur ve `w ≥ 0` yarımküresinde tutulur. Cihaz yönü `(d − R/90) mod 4`, yön açısı (HEADING) `h + R` olur.
  - `AINFO_SENSOR_PLACEMENT`, `[R·R_zᵀ | R_z·t]` olur.
  - `injectSensorData` ters dönüşümle alt HAL'e gider. Dinamik sensörler kendi cihazlarının çerçevesinde kalır.
  - Hiçbir AOSP türü atlanmaz: test, dondurulmuş V3 `SensorType.aidl`'deki her türün başlıkta adıyla anıldığını sınar.
  - Cihaz çerçevesinde olup tanımlı dönüşümü olmayan `ORIENTATION` ve `POSE_6DOF` sunulmaz. Tablette ikisi de yok.
  - **Oto döndürme (`0005`, `framework_preferred`):** Samsung sensorhub'ın `DEVICE_ORIENTATION` (27, `auto_rotation`) sensörü `getSensorsList()` listesinden çıkarılır ve olayları düşürülür. Stok Googlebook OS'ta (`mica`) bu sensör yoktur; `WindowOrientationListener` doğrudan `AccelSensorJudge` kullanır (200 ms alçak geçiren filtre, `config_autoRotationTiltTolerance`, 23° histerezis). Samsung'un anlık `DEVICE_ORIENTATION` sensörü açık kaldığında `OrientationSensorJudge` eğim toleransı ve histerezis olmadan en küçük düzlem içi harekette ekranı döndürüyordu.
  - `dumpsys android.hardware.sensors.ISensors/default` dönüşümleri listeler.
- **Kamera** ([`0004`](kaynak-yamalari/hardware_samsung/0004-camera-dogal-yon-meta-verisi.patch), [`src/camera_natural_orientation.cpp`](src/camera_natural_orientation.cpp)): framework'ün yorumladığı meta veri `convertToAidlNaturalOrientation`'dan geçer: karakteristikler, fiziksel kamera karakteristikleri, yakalama sonuçları ve fiziksel kamera sonuçları. İstek şablonları HAL'in yaptığı gibi kalır. Uygulama şablonu istek olarak geri gönderir, HAL de istekteki `lens.poseRotation`'ı sonuca aynen yansıtır. Şablon da dönüştürülseydi sonuçta poz iki kez dönerdi; canlı deneyde bu ölçüldü ve düzeltildi. Bu yollarda:
  - `sensor.orientation` arka kamerada `O − R`, ön kamerada `O + R` olur. Tablette dört kameranın hepsi 0'a çıkar.
  - `lens.poseRotation` `q·q_z(−R)`, `lens.poseTranslation` `R_z·t` olur.
  - Dış kameralar ve otomotiv çerçeveli pozlar değişmez.
  - cameraserver API1 yönünü karakteristiklerden türetir. AIDL sağlayıcıda ayrı bir `camera_info.orientation` yolu yoktur.
- **Giriş noktası** ([`0001`](kaynak-yamalari/hardware_samsung/0001-sensors-urun-giris-noktasi.patch), [`0003`](kaynak-yamalari/hardware_samsung/0003-camera-urun-giris-noktasi.patch)): `main()` fonksiyonu `gbos_hal_main()` olur. [`hardware_interfaces/0001`](kaynak-yamalari/hardware_interfaces/0001-sensors-multihal-alt-sinif-erisimi.patch), `HalProxyAidl`'ın ISensors metodlarını `protected` yapar. Böylece LineageOS alt sınıfı enjeksiyonu ve dökümü sarabilir.

### Neden etkilenmezler

AOSP android-17 kaynağından doğrulandı.

- **JPEG/EXIF yönü:** uygulama `JPEG_ORIENTATION`'ı `SENSOR_ORIENTATION` ve cihaz yönünden hesaplar. İkisi de aynı R ile değiştiği için fiziksel değer aynıdır. HAL ve [`kamera-jpeg/`](../../kamera-jpeg) shim'i isteğin `JPEG_ORIENTATION`'ını olduğu gibi uygular, `SENSOR_ORIENTATION` okumazlar. Test bunu `getJpegOrientation` formülüyle sınar.
- **Önizleme dönüşümü ve ön kamera aynası:** cameraserver bunları `SENSOR_ORIENTATION` ve `LENS_FACING`'den hesaplar (`CameraUtils::getRotationTransform`).
- **Rotate-and-crop:** HAL yalnız `NONE` bildirir. Yatay doğal yönlü cihaz uyumluluğu (`overrideToPortrait`, serbest pencere kamera uyumu) artık mica'daki gibi çalışır.
- **Yüz tanıma:** ham kareler değişmez. Kayıt ekranı Camera1 formülünü kullandığı için yön fiziksel olarak aynı kalır.
- **sensorservice'in sanal sensörleri** (füzyon, Orientation, Gravity, Linear Acceleration) dönüşmüş HAL verisinden hesaplanır.
- **Samsung'a özel türler:** `light_ir`, `light_cct`, `grip*`, `sar_backoff_motion`, `call_gesture`, `sensorhubs` skaler ya da olay değeri taşır. `interrupt_gyro` (65579) alt HAL'de üç float taşıyor (`sensors.ssc.so` `interrupt_gyro::handle_sns_client_event`, REA/Ghidra). Anlamı Samsung'un, okuyan bir AOSP ya da mica bileşeni yok, olduğu gibi kalır.
- Doğrudan kanal (`DIRECT_CHANNEL/REPORT`) bu tablette hiçbir sensörde yok (`dumpsys sensorservice` bayrakları). Olsaydı olaylar HAL'i atlayacaktı.

## Mimari: neden kütüphane + başlatıcı

İlk plan pil-sagligi gibiydi: statik bağlı bir /product ikilisi, alt HAL'leri `sphal` ad alanından yükleyecekti. Bu yol reddedildi.

Sensör alt HAL sınırı C++'tır: `ISensorsSubHal`, `IHalProxyCallback`, `RefBase` sayaçları, `hidl_vec`, `std::function` ve `ScopedWakelock`. Süreçte iki libc++ olurdu: NDK'nın `std::__ndk1` kopyası ve vendor'un `std::__1` kopyası. Aynı durum libutils ve libhidlbase için de geçerliydi. Bu iki kopyanın aynı yerleşimi kullanacağı varsayılmak zorundaydı: kırılgan.

Uygulanan yol:

- **HAL kütüphanesi** vendor modülü gibi derlenir. Soong'un `android_libc++/platform` başlıkları kullanılır: release clang'ı `clang-r584948b`. Ayrıca `__ANDROID_VENDOR__`, `__ANDROID_VENDOR_API__=202604`, `-fno-rtti` ve `-fno-exceptions` verilir. Kütüphane vendor bölümünün kendi `.so`'larına bağlanır (`libc++`, `libutils`, `libhidlbase`, `libfmq`, HIDL/AIDL arayüz kütüphaneleri). Bu `.so`'lar tabletteki LineageOS zip'inden okunur.
- **Başlatıcı** ([`src/baslatici.c`](src/baslatici.c)) /product/bin/hw'dedir ve yalnız LLNDK'ya bağlıdır. Sırasıyla:
  1. Kütüphanenin LLNDK olmayan bağımlılıklarını `sphal` ad alanına yükler. libvndksupport ve libhidl da vendor modüllerini oraya yükler.
  2. `sphal`'ı klonlayan bir ad alanı açar (`ANDROID_NAMESPACE_TYPE_SHARED_ISOLATED`, bionic `create_namespace`). Bu ad alanı `/product/lib64/gbos-dogal-yon`'u da görür.
  3. Kütüphaneyi bu ad alanına yükler ve `gbos_hal_main()`'i çağırır.

Sonuç: her vendor kütüphanesi süreçte tek kopyadır, sistemdeki LLNDK kopyaları vendor sürecindeki gibi ayrıdır. Canlı deneyde süreç haritası vendor süreciyle aynıydı, fazladan yalnız bizim kütüphane vardı: `../kanit/20261024/sensor-hal-maps.txt`.

Kamera modülü sınırı C'dir: `camera_module_t`, `camera3_*`. Yerleşimini LineageOS cihaz bayrakları belirler: sm8250-common `needs_sec_reserved_field`. [`araclar/kamera-abi.py`](araclar/kamera-abi.py) vendor sağlayıcının makine kodundan şu değerleri okur:

- `set_torch_mode_strength` ofseti: 0x148,
- `sizeof(camera3_stream_t)`: 0x88,
- `usage` genişliği: 4 bayt.

`build.sh` derlenen başlıkları bu değerlere karşı `static_assert` ile sınar.

## init ve SELinux

pil-sagligi ile aynı desen. `override` Android 17'de treble sınırında reddediliyor; ayrıntı: [`pil-sagligi/BENIOKU.md`](../../pil-sagligi/BENIOKU.md). Her HAL'in rc dosyası ([`etc/`](etc)) şunları yapar:

1. `on early-init`: vendor servisini durdurur.
2. Kendi servisini vendor'unkiyle aynı sınıf, kullanıcı, grup, yetenek, ioprio, task_profiles ve rlimit ile tanımlar. `service_problems()` bunu vendor rc'siyle karşılaştırır.
3. `on property:init.svc.<vendor>=running`: vendor servisini yeniden durdurur, kendi servisini yeniden başlatır.

Kamera rc'si vendor'un `interface aidl ...ICameraProvider/internal/0` satırını kopyalamaz. init bir arayüz için tek servis kabul eder (`service_parser.cpp` `ParseInterface`). Vendor servisi yine de iki yoldan başlayabilir:

- vndservicemanager'ın `class_restart hal` çağrısı,
- servicemanager'ın isteğe bağlı başlatması.

İkinci yolu cameraserver yalnız tembel sağlayıcıda tetikler (`ro.camera.enableLazyHal`). İmajda bu ayar yok, test sınıyor. Her iki durumda da 3. adım vendor servisini durdurur.

SELinux politikası eklenmez:

- `product_file_contexts`'e işaretli blokla iki satır eklenir: başlatıcılar `hal_sensors_default_exec` ve `hal_camera_default_exec` alır. Tablet politikasında ikisi de var, init geçişiyle birlikte (`init_daemon_domain`).
- Kütüphaneler /product'ın varsayılan `system_lib_file` etiketinde kalır.
- [`beklenen.txt`](beklenen.txt): 47 erişim ve 2 geçiş. `python3 -I yamalar/dogal_yon_hal.py politika` ile 49 denetimin hepsi tutuyor.

## Derleme

```bash
dogal-yon/hal/build.sh                          # kaynaklar, hidl-gen, AIDL, iki kütüphane, iki başlatıcı, denetimler
python3 -I yamalar/dogal_yon_hal.py build       # STAMP eskiyse derler
python3 -I yamalar/dogal_yon_hal.py politika
python3 -I -m pytest testler/test_dogal_yon_hal.py
```

- **Kaynaklar** ([`kaynaklar.lock`](kaynaklar.lock)): pil-sagligi'nin getiricisiyle alınır. Çekim seyrek, blobsuz ve tek derinliklidir; içerik özetiyle doğrulanır. Önbellek `~/.cache/gbos-dogal-yon` altındadır, yaklaşık 35 MB.
  - LineageOS çatalları: `hardware/samsung`, `hardware/interfaces`, `system/core`, `system/libfmq`, `frameworks/native`, `system/media`, `hardware/libhardware`. Commit'ler tabletteki 20261011 gecelik derlemesinin dalıdır, 11 Ekim'den önceki son baş. Vendor ikilisi de buna uyuyor: 13 Ekim'deki `device_orientation_workaround` özelliği vendor'da yok, test sınıyor.
  - Geri kalanlar AOSP android-17.0.0_r1.
- **Vendor kütüphaneleri** ([`araclar/vendor-dosyalari.py`](araclar/vendor-dosyalari.py)): zip'ten okunur. Zip tabletteki dosyalarla bayt bayt aynı, 24 Ekim'de karşılaştırıldı. **LLNDK** ([`araclar/sistem-kutuphaneleri.py`](araclar/sistem-kutuphaneleri.py)) bozulmamış mica imajından okunur.
- **Denetim** ([`araclar/denetim.py`](araclar/denetim.py)):
  - Kütüphaneler yalnız vendor HAL sürecinin zaten yüklediği kütüphanelere bağlıdır.
  - Her sembol çözülür; yalnız `gbos_hal_main` dışa açılır.
  - Başlatıcılar yalnız `libdl_android`, `liblog`, `libc` ve `libdl`'ye bağlıdır.
  - Dosyalar 16 KiB hizalı ve BIND_NOW'dır.
- Aynı girdiler aynı baytı verir: ardışık derlemelerde dört dosya da aynı SHA-256'yı verdi. `out/STAMP` mica sürümünü ve makine yollarını içermez. Yamalanan sürüme uyum `dogal_yon_hal.uyum_denetle()` ile her kurulumda ayrıca sınanır. Ara dosyalar `mktemp -d` ile `/var/tmp` altında açılır ve silinir. Derleme bir dakikadan kısa sürer ve birkaç yüz MB yer kaplar; kaynak kapısı gerekmez.

## Ölçüm

24 Ekim 2026'da tabletteki .273 imajında, ürün bölümüne yazmadan ölçüldü. Kanıtlar [`../kanit/20261024/`](../kanit/20261024) altında, araç [`araclar/canli-deney.sh`](araclar/canli-deney.sh):

- Başlatıcı ve kütüphane root olarak `/data/local/tests/product`'tan çalıştı. Bu dizin tabletin `ld.config.txt`'sinde de "product" bölümüdür.
- Vendor servisi durduruldu, bizimki başlatıldı, ölçüldü, sonra vendor servisi geri başlatıldı. Her deneyden sonra vendor servisleri running, SELinux Enforcing.
- Süreç root'tu, `hal_*_default` alanında değildi. SELinux erişimleri çevrimdışı derlenen politikayla sınandı (`politika`).

**Sensör.** [`araclar/sensor-olcum.c`](araclar/sensor-olcum.c) sensörleri uygulama gibi NDK `ASensorManager` ile okur. Okuma sırası: A vendor, B bizim HAL, C yine vendor (`sensor-olcum.txt`).

| | A (vendor) | B (bizim HAL) |
|---|---|---|
| ivmeölçer | 9.18, 0.35, 3.88 | −0.35, 9.18, 3.88 |
| Screen Orientation Sensor | 1 | 0 |

- Test, vektör, kalibresiz ve cihaz yönü türlerinden 9 sensörde bu ilişkiyi sınadı. A ile C aynı çıktı, yani tablet kıpırdamadı.
- B'nin rotasyon kuaterniyonları yerçekimini B'nin kendi ivmeölçerinin gösterdiği yöne koyuyor.
- sensorservice yeni HAL'e yeniden bağlandı. Sensör tutamakları aynı olduğu için `Sensor handles changed` hatası yok.
- Süreçteki kütüphane listesi vendor HAL süreciyle birebir aynı, fazladan yalnız bizim kütüphane var (`sensor-hal-maps.txt`, `sensor-vendor-maps.txt`).
- `dumpsys` dönüşümleri listeliyor (`sensor-hal-dumpsys.txt`).

**Kamera.** [`araclar/kamera-olcum.c`](araclar/kamera-olcum.c) dört kamerayı NDK Camera2 ile açıp YUV akışı alır (`kamera-olcum.txt`):

- Bizim sağlayıcıyla her kamera 23-28 kare verdi, yön 0.
- Yakalama sonucundaki `lens.poseRotation` vendor değerinin bir kez dönüştürülmüşü: arka `(1,0,0,0)`, ön `(0,0,0,1)`. Statik karakteristiklerle aynı.
- `dumpsys media.camera` beş cihazda da `Orientation: 0` gösterdi (`kamera-gbos-dumpsys.txt`).
- Bizim sağlayıcı çalışırken vendor servisi kendiliğinden başlamadı.

Deneyde bir hata bulundu ve düzeltildi: istek şablonları da dönüştürülünce poz sonuçta iki kez dönüyordu.

Yan bulgu, bu modülden bağımsız: kamera açılışında system_server'ın `ICameraServiceProxy` çağrısı NFC servisini 10 saniye bekliyor ("Could not connect to NFC service") ve süreç yığınları toplanıyor. Bu, ölçüm aracının ilk sürümünü SIGQUIT ile durdurdu.

Testler bu kanıtları okur:

- `test_tablet_olcumu_vendor_ile_donusum_tutarli`
- `test_tablet_kamera_dokumu_donusum_tutarli`
- `test_tablet_kamera_akisi_ve_sonuc_pozu`

## Sınırlar

- Doğal yönü yatay yapmak bilerek bir davranış değişikliğidir. Sensör eksenleri ve kamera yönü ancak ekran katmanıyla birlikte doğrudur. Bu yüzden `dogal_yon.install_all()` üç katmanı birlikte kurar ve doğrular.
- LineageOS vendor'u güncellenirse ya da `kaynaklar.lock` değişirse yeniden derlemek gerekir:
  - Vendor HAL'i değişirse `lineage.lock` özetleri tutmaz, testler bunu gösterir. HAL'in okuduğu vendor rc, VINTF ve `hals.conf` dosyaları `$GBOS_WORK/lineage/bolumler/vendor` altındadır; `lineage-hazirla.py` bunları LineageOS zip'inden çıkarır.
  - Vendor kütüphanelerinin dışa açtığı semboller değişirse `denetim.py` derlemeyi durdurur.
  - Kamera yapı yerleşimi değişirse `static_assert` derlemeyi durdurur.
- `interrupt_gyro`'nun üç değerinin anlamı bilinmiyor ve değiştirilmiyor. Okuyan bileşen yok.
