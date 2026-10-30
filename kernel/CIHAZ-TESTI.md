# Kernel: tablette yazma, geri dönüş ve kabul testi

Neyin neden
değiştiği: [BENIOKU.md](BENIOKU.md).

## Ön koşullar

- `./build.sh hepsi` ya da ayrı ayrı `derle yamasiz`, `derle yamali`, `boot yamasiz`,
  `boot yamali` koşmuş ve `testler/hepsi.sh` "TÜM KERNEL TESTLERİ GEÇTİ" demiş olmalı.
- Yazılacak imaj: `cikti/yamali/boot.img`; özeti `cikti/yamali/boot.img.sha256`.
- Geri dönüş imajı: LineageOS zip'indeki `boot.img`
  (`../lineage-zip.py cikar boot.img lineage-boot.img`). 20261011 nightly'sinde sha256
  `f34f7c7bafcfca4abe9132e218e9767002099735f8752fd8b7bd96e026a02154`.
- Tek tablet bağlıyken komutlar onu bulur; birden çok cihaz varsa
  `export ANDROID_SERIAL=$(python3 ../t870_cihaz.py)`. `adb root` açık olmalı. vbmeta
  bayrakları 3 (doğrulama ve hashtree kapalı), açılış durumu orange: değişen boot
  hash'i açılışı durdurmaz. Yazmadan önce yeniden bak:

```sh
adb shell 'getprop ro.boot.verifiedbootstate; readlink -f /dev/block/by-name/boot'
# orange, /dev/block/sda22 beklenir
```

İlk kurulumda boot `../ilk-kurulum.py` ile recovery'den yazılır (kök BENIOKU,
Kurulum); aşağıdaki adımlar Googlebook OS ya da LineageOS açıkken yalnız kerneli
değiştirmek içindir.

## Yazma

```sh
K="T870-araclar/kernel"
adb push "$K/cikti/yamali/boot.img" /data/local/tmp/boot-yamali.img
adb shell 'sha256sum /data/local/tmp/boot-yamali.img'   # .sha256 ile aynı olmalı
adb shell 'dd if=/data/local/tmp/boot-yamali.img of=/dev/block/by-name/boot bs=1M conv=fsync && sync'
adb shell 'sha256sum /dev/block/by-name/boot'           # yine aynı olmalı
adb reboot
```

Bölüm 71303168 bayt, imaj da tam bu boyda (AVB footer'ı sonda); `dd` bölümün
tamamını yazar.

## Geri dönüş

Tablet açılıyorsa:

```sh
python3 "$K/../lineage-zip.py" cikar boot.img lineage-boot.img
adb push lineage-boot.img /data/local/tmp/boot-lineage.img
adb shell 'dd if=/data/local/tmp/boot-lineage.img of=/dev/block/by-name/boot bs=1M conv=fsync && sync'
adb shell 'sha256sum /dev/block/by-name/boot'   # lineage-boot.img'nin özeti olmalı
adb reboot
```

Tablet açılmıyorsa: recovery bölümü bu işte değişmez. Ses yukarı + güç ile LineageOS
recovery'ye gir; recovery'de adb açıksa (`adb devices` "recovery" gösterir) imajı
`adb push` ile `/tmp`'ye at ve yukarıdaki `dd` komutunu oradan çalıştır. Recovery'de
adb yoksa ya da recovery'ye girilemezse indirme kipinde (kapalıyken ses aşağı + ses
yukarı basılıyken USB) imaj BOOT bölümüne yazılır:
`heimdall flash --BOOT lineage-boot.img` (ya da `boot.img` adıyla `tar` içinde Odin
AP). Bu yol denenmedi; ilk kullanımda `heimdall print-pit` ile bölüm adı doğrulanır.

## Doğrulama (yazdıktan sonra)

Her adımın sonucu ekranda ve `getevent`/`dumpsys` çıktısında ölçülür; bir adım
beklenenden farklıysa test geçmemiş sayılır.

1. Kernel yamalı mı:

   ```sh
   adb shell 'uname -r; cat /proc/version'
   # 4.19.325-cip136-st20-perf-gfd7a9d55451e ... #1 SMP PREEMPT Mon Oct 26 20:00:00 UTC 2026
   adb shell 'zcat /proc/config.gz' | cmp - "$K/beklenen/config-4.19.325-cip136-st20-perf-g4478511c4b37"
   ```

2. Anahtar yeteneği: `hall_logical` artık `SW_TABLET_MODE` bildirir.

   ```sh
   adb shell 'getevent -lp' | grep -A4 hall_logical
   # EV_SW: SW_LID SW_TABLET_MODE
   ```

3. Klavye takılı, açık (dizüstü): `getevent -l` kapalıyken başlat, klavyeyi tak.
   - `Book Cover Keyboard (EF-DT870)` ve `sec_touchpad_pogo` eklenir.
   - `cat /sys/class/sec/hall_logical/hall_logical_detect` → `OPEN`.
   - `dumpsys input | grep -iE "Book Cover|touchpad_pogo"` iki aygıtı gösterir.
   - Tuş ve touchpad girdi verir; sanal klavye bir metin alanında açılmaz
     (`dumpsys input_method | grep mInputShown` → false).

4. Kılıfı arkaya katla:
   - `getevent -l` içinde `hall_logical: EV_SW SW_TABLET_MODE 00000001`, ardından
     `remove device ... Book Cover Keyboard` ve `remove device ... sec_touchpad_pogo`.
   - `hall_logical_detect` → `BACK`; `SW_LID` 0 kalır (ekran kapanmaz).
   - `dumpsys input | grep -i "tablet mode"` → tablet modu açık;
     `dumpsys input` aygıt listesinde klavye ve touchpad yok.
   - Klavyede tuşa basmak, touchpad'e dokunmak ekranda hiçbir şey yapmaz.
   - Bir metin alanına dokununca sanal klavye çıkar
     (`dumpsys window | grep -i "hardKeyboardHidden\|keyboard="` → `keyboard=1`
     (NOKEYS)).
   - mica tablet düzenine geçer:
     `adb logcat -s DesktopDisplayModeController` ya da ekranda görev çubuğu ve
     pencere düzeni; `hasAnyPhysicalKeyboardDevice=false hasAnyTouchpadDevice=false`.

5. Kılıfı öne aç (dizüstü):
   - `SW_TABLET_MODE 00000000`, ardından klavye ve touchpad yeniden `add device`.
   - Tuşlar ve touchpad çalışır, mica masaüstü düzenine döner, sanal klavye çıkmaz.

6. Kapağı ekranın üstüne kapat:
   - `SW_LID 00000001`, `SW_TABLET_MODE` 0; ekran kapanır ve tablet uyur (önceki
     davranış). Açınca `SW_LID 0`, ekran açılır.
   - Arkaya katlıyken doğrudan kapatma da (arka → ön → kapalı) aynı sonuçla biter.

7. Klavyeyi çıkar (yama 0003; açıkken ya da arkaya katlıyken): `SW_TABLET_MODE 1`,
   logcat'te `DesktopDeviceStateProvider: Device state changed to 3`, ekranda tablet
   düzeni (kılıfı arkaya katlamakla aynı). Yeniden takınca kılıf açıksa
   `SW_TABLET_MODE 0`, durum 2, klavye ve touchpad gelir; arkaya katlıysa tablet modu
   sürer. Klavyesiz yeniden başlatma: açılışta `SW_TABLET_MODE 1`, durum 3.

8. Yeniden başlatma: kılıf arkaya katlıyken `adb reboot`; açılışta MCU'nun
   konum bildirimiyle tablet modu yeniden kurulur. Bildirim gelmezse 3. adım
   durumunda açılır; bu, kalan risklerde yazılı.

9. Uyku/uyanma: arkaya katlıyken güç tuşuyla uyut ve uyandır; durum korunur.

Kanıt: `getevent -lt` dökümü, `dumpsys input` ve ekran görüntüleri
`cihaz-kabul/kanit/<tarih>/` altına konur.

10. Tablet modu anahtarı (yama 0002): kılıf açıkken
    `echo 1 > /sys/devices/virtual/sec/sec_keypad/tablet_mode` → `SW_TABLET_MODE 1`, klavye
    ve touchpad `remove device`, `hall_logical_detect` `BACK`; kılıfı dizüstü ile ters L
    arasında oynatınca öyle kalır; kapak kapanınca yine uyur; `echo 0` → klavye ve touchpad
    geri gelir. Hızlı ayarlar kutucuğu imajla gelir (`../tablet-modu/`).
