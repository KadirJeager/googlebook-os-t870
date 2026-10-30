# Kamera: video kodlayıcının yanında JPEG'i sistem üretir

Tabletin LineageOS/Samsung CamX HAL'i bir oturumda önizleme + donanım video kodlayıcısı + JPEG (BLOB) akışı birlikte kurulunca "Video With Liveshot" senaryosuna giriyor ve video tamponlarını doldurmuyor: kayıt donuyor (`../acilis-loglari/kamera-video-20261022/KOK-NEDEN.md`). Aynı boyutta YUV ile sorun yok. Bu modül düzeltmeyi uygulamaya değil framework'e koyar; her Camera2/CameraX istemcisi (Googlebook kamerası, Instagram, WhatsApp, Meet ...) uygulamaya dokunulmadan düzelir ve Play güncellemeleri etkilenmez.

## Nasıl çalışır

`framework.jar` içindeki `android.hardware.camera2.impl.ICameraDeviceUserWrapper` cameraserver'a giden her binder çağrısının geçtiği ince sınıftır. `yamalar/kamera_jpeg.py` altı metodunu (`createStream`, `deleteStream`, `endConfigure`, `configureStreams`, `submitRequestList`, `disconnect`) `gbos.CameraBridge`'e yönlendirir; asıl iş `gbos.CameraJpegShim`'de (`src/gbos/`):

- Yeni akışlar sınıflanır (`SurfaceUtils`: BLOB = JPEG tüketicisi, kodlayıcı kullanım bayrağı = video).
- Oturumda ikisi de varsa JPEG akışı yerine aynı boyutta `YUV_420_888` akışı istenir. Android 17'nin toplu `configureStreams` yolunda değişim `sessionConfigurationDelta` içinde aynı sırada yapılır; uygulamanın aldığı akış kimliği zaten YUV akışınındır. Eski tek tek `createStream/endConfigure` yolunda JPEG akışı silinip YUV açılır, isteklerdeki kimlik eşlenir.
- Her YUV karesi: `YuvImage` ile JPEG (istekteki kalite), HAL'in yazacağı gibi EXIF (yön, boyut, tarih, marka/model, küçük resim, GPS), `camera3_jpeg_blob` kuyruğu; `ImageWriter` ile uygulamanın JPEG yüzeyine aynı zaman damgasıyla kuyruklanır. Uygulama sonucu eskisi gibi `SENSOR_TIMESTAMP` ile eşler.
- Kodlayıcısız oturumlar HAL JPEG'iyle kalır. 4032×3024 bir kare ~230 ms.

## Hat

`apply_patches.patch_ro_system_features` framework.jar'ı bozulmamış imajdan yeniden yazarken `kamera_jpeg.framework_replacements(zip)` çağırır:

1. `build`: `stubs/` (gizli sınıfların derleme taslakları) + `src/` javac 17 (`-XDstringConcat=inline`; Android'de `StringConcatFactory` yok), d8 `--no-desugaring` (`--min-api 34`). Özel üyeler iç sınıftan çağrılmaz: ART "nestmate" erişimini bilmez.
2. `merge_bridge`: `classes2.dex` + `CameraBridge.class` d8 ile birleşir (metot kimliği 64 871 → 64 884; sınır 65 536, bu yüzden yalnız köprü buraya girer, shim `classes7.dex`'tedir).
3. `hiddenapi_encode`: d8 `hiddenapi_class_data` bölümünü düşürür; bozulmamış dex'ten üye üye okunan bayraklar yeniden yazılır (gidiş-dönüş testi bayt bayt aynı), köprü üyeleri `BLOCKED`. Bölüm map listesinin yerine girer, map listesi sona taşınır.
4. `patch_wrapper`: her metotta `iget mRemoteDevice; invoke-interface` → aynı boyutta `invoke-static {this, ...}` (+2 nop); try blokları değişmez.
5. `dex-dogrula.py` `classes7.dex`'i tabletin ART doğrulayıcısından geçirir; `testler/test_kamera_jpeg.py` 5 test.

## Doğrulama

- `python3 yamalar/kamera_jpeg.py prova`: bozulmamış jar üzerinde bütün adımlar.
- Cihaz: `cihaz-kabul/kabul.sh` KameraAkis matrisi (`0 1920x1080 1`, `0 3840x2160 jpeg4128x3096`, `1 1920x1080 1`) artık OK olmalı; `kamera-shim` maddesi logcat'te değişim ve yazılımsal JPEG satırlarını sayar. `cihaz-kabul/KameraShimDeneme.java` shim'in kodlayıcısını tek başına sınar (EXIF, GPS, küçük resim, ImageWriter teslimi).
- 23 Ekim 2026 kanıtı: `cihaz-kabul/kanit/20261023-0230-kamera-orijinal-uygulama/` (orijinal Google imzalı kamera uygulaması, iki kamerada video + kayıt sırasında fotoğraf + fotoğraf).

## Tuzaklar

- ART önbelleği (`/data/misc/apexdata/com.android.art/dalvik-cache`) **kendiliğinden tazeleniyor**: `cache-info.xml` önyükleme sınıf yolundaki her jar'ın boyutunu ve sağlamasını tutar (`<component file="/system/framework/framework.jar" size=… checksums=…>`), bu yüzden jar değişince odrefresh yeniden derler. Erken denemelerde shim'in devrede olmaması önbellekten değil, BLOB format denetiminin eksikliğinden ve `configureStreams` kancasının yokluğundandı; o denemelerde önbellek her seferinde silindiği için önbelleğin payı ölçülmedi, suçlanması yanlıştı.
- `ImageWriter` BLOB tamponu (boyut × 1) olmalı; yükseklik > 1 `dequeueInputImage`'da çöker.
- `/storage/emulated/<kullanıcı>` adb root'a kapalı; dosyalar `/data/media/<kullanıcı>` yolundan çekilir.
