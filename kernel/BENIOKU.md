# Kernel: kılıf arkaya katlanınca ya da çıkarılınca tablet modu

Galaxy Tab S7 (SM-T870) üzerindeki Googlebook OS, LineageOS 24'ün kernelini ve boot
imajını kullanır. Bu dizin o kerneli birebir yeniden derler, Book Cover Keyboard
(EF-DT870) arkaya katlanınca ya da çıkarılınca tablet moduna geçilmesini sağlayan
yamaları uygular ve
LineageOS'un boot imajına kernel dışında hiçbir alanı değiştirmeden yeniden paketler.

Tablette yazma, geri dönüş ve kabul adımları: [CIHAZ-TESTI.md](CIHAZ-TESTI.md).

## Sorun

Klavye MCU'su kılıfın konumunu pogo bildirimiyle (`POGO_NOTIFIER_EVENTID_HALL`) tek
baytla gönderir: `E_LID_0 = 1` (ekranın üstüne kapalı), `E_LID_NORMAL = 2` (açık),
`E_LID_360 = 3` (ekranın arkasına katlı). LineageOS'taki
`drivers/input/keyboard/hall_ic_logical.c` yalnız ilk ikisini işler; 3 düşer. Bu
yüzden arkaya katlanınca hiçbir anahtar değişmez, klavye ve touchpad bağlı kalır ve
girdi vermeye devam eder.

mica masaüstü/tablet kararını iki katmanda verir (26 Ekim 2026'da tabletteki
`desktop-services.jar` ve `SystemUITitan.apk`'dan okundu):

- **Cihaz durumu:** `com.android.server.desktop.power.DesktopDeviceStateProvider`
  (`desktop-services.jar`) `SW_LID` ve `SW_TABLET_MODE`'dan dört durum üretir:
  `0 CLOSED` (kapak kapalı), `1 DOCKED` (kapalı + harici ekran), `2 OPEN`,
  `3 TABLET` (`SW_TABLET_MODE=1`). Değişiklik logcat'te
  `DesktopDeviceStateProvider: Device state changed to <n>`.
- **Pencere düzeni:** Shell `DesktopDisplayModeController` varsayılan ekranı tam klavye
  ve touchpad (`InputManager`'da `isEnabled()` olan tam klavye ve `SOURCE_TOUCHPAD`)
  varsa ya da cihaz durumu CLOSED/OPEN/DOCKED ise (fiziksel özellik 101/102/104)
  masaüstü (freeform) yapar. TABLET'te (103) ve klavye/touchpad yokken tam ekran
  tablet düzenine geçer.

Bu yüzden klavye ve touchpad'i kaldırmak tek başına yetmez: `SW_TABLET_MODE` 0 kaldıkça
durum OPEN'dır ve klavyesiz tablet masaüstü düzeninde kalır.

## Samsung stok davranışı

Kaynaklar:

- Samsung Open Source'ta tabletin önyükleyicisiyle aynı sürüm var:
  `SM-T870 T870XXS8DXH1 SM-T870_EUR_13_Opensource.zip` (241 MB). İndirmek için
  sitenin yasal sözleşmesini kabul etmek gerekiyor; bu iş kullanıcı onayı olmadan
  yapılmadı, paket açılmadı. Kullanıcı sözleşmeyi kabul edip paketi indirirse
  aşağıdaki karşılaştırma onunla yinelenebilir.
- LineageOS sm8250 kernelinin pogo sürücüleri `Import G780GXXSBEXG1 from OSRC`
  commit'iyle Samsung'un kendi kaynağından gelir.
- Samsung'un Tab S7+ (SM-T976B) stok kaynağının birebir aktarımı
  (github.com/ianmacd/gts7xl) ile karşılaştırıldı: `stm32_pogo_i2c.c`,
  `stm32_pogo_keyboard.c` aynı; touchpad'de yalnız LineageOS'un dokunmatik
  iyileştirmeleri var.

Stok `hall_ic_logical.c` (T976B):

- `E_LID_0` → `SW_FLIP=1`; `E_LID_NORMAL` → `SW_FLIP=0`, `SW_HALL_LOGICAL=0`;
  `E_LID_360` → durum `LOGICAL_HALL_BACK`, `SW_HALL_LOGICAL=1`.
- `SW_FLIP` (0x15) ve `SW_HALL_LOGICAL` (0x1f) Samsung'un kendi
  `include/uapi/linux/input.h` tanımlarıdır; yalnız One UI çatısı anlar.
- Klavye ve touchpad sürücüleri katlamada hiçbir şey yapmaz; girdiyi kesmek One UI'ın
  işidir.

LineageOS stok `SW_FLIP`'i standart `SW_LID`'e çevirmiş, `SW_HALL_LOGICAL`'ı ise hiç
almamış. Yama aynı yolu izler: Samsung'un `SW_HALL_LOGICAL` bildirdiği yerde
Android'in anladığı standart `SW_TABLET_MODE` bildirilir.

## Yama

Üç yama sırayla uygulanır (yamalı commit `fd7a9d55451e`, taban `4478511c4b37`).

`kaynak-yamalari/0001-input-pogo-report-the-folded-back-cover-as-SW_TABLET.patch`
(commit `5001a4de637a`):

- **`hall_logical`** (`input3`): `E_LID_360` → `LOGICAL_HALL_BACK`, `SW_TABLET_MODE=1`;
  `E_LID_0`, `E_LID_NORMAL` → `SW_TABLET_MODE=0` (klavye çıkarılınca: yama 0003). `SW_LID`
  artık yalnız `CLOSE` durumundan türetilir; önceki kod durum değerini doğrudan
  `SW_LID`'e yazdığı için `BACK` (2) aygıt yeniden açılınca `SW_LID=1` olurdu, yani
  arkaya katlı tablet uyurdu. `hall_logical_detect` sysfs'i `BACK` gösterir (stok).
  Bilinmeyen konum değeri yok sayılır ve günlüğe yazılır.
  - Neden bu aygıt: kılıf konumunu zaten bildiren, açılıştan beri hep kayıtlı tek
    aygıt. Klavye aygıtında bildirilseydi, klavye katlamada kaydını sildiği için
    anahtar onunla birlikte kaybolurdu. Android `SW_TABLET_MODE`'u herhangi bir
    aygıttan toplar (`InputManagerService`, `SWITCH_TABLET_MODE`).
- **Klavye ve touchpad** (`stm32_pogo_keyboard.c`, `stm32_pogo_touchpad.c`): `E_LID_360`
  gelince basılı tuşlar/parmaklar bırakılır ve input aygıtının kaydı silinir (kılıf
  çıkarıldığında çalışan stok yolun aynısı); kılıf öne gelince ya da kapanınca aygıt
  yeniden kaydedilir. Katlıyken yeniden gelen `ATTACHED` aygıtı geri getirmez;
  `DETACHED` katlı durumunu sıfırlar.
  - Neden kayıt silme, girdiyi bastırma değil: bastırılan aygıt Android için hâlâ
    bağlı bir tam klavye ve touchpad'dir; `Configuration.keyboard=QWERTY`,
    `hardKeyboardHidden=NO` kalır, sanal klavye çıkmaz ve mica'nın iki denetimi
    `true` döner. Kayıt silinince Android klavyeyi yok sayar (`keyboard=NOKEYS`), IME
    sanal klavyeyi gösterir, mica tablet düzenine geçer. Gerçek dönüştürülebilir
    Googlebook'lardaki davranış da budur: tablet modunda klavye ve touchpad alt
    katmanda devre dışıdır.
- Konum değerleri (`enum pogo_lid_position`) `include/linux/input/pogo_i2c_notifier.h`'a
  taşındı; üç dinleyici tek tanımı kullanır.

Bildirim zinciri önceliği `HALLIC(2) > KEYPAD(1) > TOUCHPAD(0)` olduğundan önce
`SW_TABLET_MODE` değişir, sonra klavye ve touchpad çıkar/gelir.


`kaynak-yamalari/0002-input-pogo-let-userspace-force-tablet-mode-with-an-o.patch`
(commit `d0a776194149`): `sec_keypad/tablet_mode` anahtarı. 1 iken MCU'nun "açık"
bildirimi bütün dinleyicilere "arkaya katlı" (`E_LID_360`) olarak iletilir; kapalı kapak
kapalı kalır. Kılıf ekranın arkasında açılıyken MCU dizüstüyle aynı olayı gönderdiği için
("ters L") bu konum ancak kullanıcının söylemesiyle bilinir. Kullanıcı tarafı ve
gerekçe: [`../tablet-modu/`](../tablet-modu/BENIOKU.md).

`kaynak-yamalari/0003-input-hall_logical-report-tablet-mode-while-no-keybo.patch`
(commit `fd7a9d55451e`): klavye takılı değilken `hall_logical` `SW_TABLET_MODE=1`
bildirir; `SW_LID` yalnız takılı kılıftan gelir. Yama 0001 klavye çıkarılınca anahtarı
0 yapıyordu; mica bunu OPEN (dizüstü) sayıp klavyesiz tableti masaüstü düzeninde
tutuyordu, oysa kılıfı arkaya katlamak ya da `tablet_mode` anahtarı TABLET'e
geçiriyordu (Sorun bölümü). Ayrılabilir klavyeli öteki cihazlar da böyle bildirir
(`cros_cbas`, `surface_aggregator_tabletsw`).

- `hall_logical` artık `ATTACHED`'ı da izler. `pogo_notifier_register` yeni dinleyiciye
  bağlantı durumunu yeniden oynatır, ama pogo sürücüsü bağlantıyı bilmeden önce durum
  0'dır ve klavyesiz açılışta hiçbir zaman `DETACHED` gönderilmez. Bu yüzden başlangıç
  "klavye yok"tur; `ATTACHED` gelene kadar tablet modu açıktır.
- Zorla tablet modunda (0002) takınca önce `E_LID_360`, sonra `ATTACHED` gelir; anahtar
  arada 0'a düşmez.

## Birebir yeniden üretim

`build.sh` LineageOS'un kurduğu derlemeyi tekrarlar; her girdi kimliğiyle sabittir:

| Girdi | Kaynak | Kimlik |
|---|---|---|
| Kernel | github.com/LineageOS/android_kernel_samsung_sm8250 | commit `4478511c4b3738ad117e674810c2dfde154724bf` |
| Clang | AOSP `prebuilts/clang/host/linux-x86`, `android-17.0.0_r1`, `clang-r563880c` | `bin` ağacı `dd8a90cc…`, `lib` ağacı `4e2b8921…` (git ağaç kimliği, `araclar/git-agac.py`) |
| make ve derleme PATH'i | AOSP `prebuilts/build-tools`, `android-17.0.0_r1`: `linux-x86/bin` (make, toybox, one-true-awk, bc, xz, bzip2) ve soong_ui'nin PATH'in başına koyduğu `path/linux-x86` bağları | blob'lar `build.sh` `BT_BLOB`'da |
| techpack sırası | `techpack/Kbuild` sırasız `find` kullanır; LineageOS derleyicisindeki sıra tabletin `/proc/kallsyms`'inden | `TECHPACK_SIRASI` |
| Config | `vendor/kona-perf_defconfig` + `vendor/samsung/kona-sec-common.config` + `vendor/samsung/gts7lwifi.config` (sm8250-common ve gts7lwifi `BoardConfig*.mk`, `lineage-24.0`), LineageOS `kernel.mk` gibi sırayla `merge_config.sh` + `olddefconfig` | |
| make bayrakları | LineageOS `vendor/lineage` `kernel.mk`/`BoardConfigKernel.mk` | `LLVM=1 LLVM_IAS=1 CC=clang LD=ld.lld CLANG_TRIPLE=aarch64-linux-gnu-` |

Image'a giren derleme ortamı da aynı kurulur (bubblewrap ad alanında, gerçek köke
yazmadan): kaynak `/lineage/lineage-24.0/kernel/samsung/sm8250`, out
`/lineage/lineage-24.0/out/target/product/gts7lwifi/obj/KERNEL_OBJ`, clang
`/lineage/lineage-24.0/prebuilts/clang/host/linux-x86/clang-r563880c`,
PATH başında `/lineage/lineage-24.0/prebuilts/build-tools/path/linux-x86` (IKCONFIG arşivini toybox gzip sıkıştırır; GNU gzip farklı bayt üretir), `KBUILD_BUILD_USER=root`, `KBUILD_BUILD_HOST=6c12973f5f69`, zaman
`Sun Oct 11 19:06:31 UTC 2026`. Clang'ın yolu da önemlidir: `<stdarg.h>` kullanan
dosyaların hata ayıklama bilgisi ThinLTO modül özetine, o da Image'daki
`.llvm.<özet>` sembol adlarına girer.

`CONFIG_IKHEADERS` arşivi başlık içeriği dışında kopyalama anının zamanını, kullanıcıyı
ve dizin sırasını taşır; LineageOS'unkini yeniden üretmek mümkün değildir. Yamasız
derlemede LineageOS kernelinin arşivi (zip'teki boot'tan çıkarılır), içeriği bizim
ürettiğimizle 9829 üyenin tamamında birebir aynıysa yerine konur; değilse derleme
durur. Yamalı derlemede arşiv sıralı, `root:root`, derleme zamanıyla yeniden paketlenir.
Ayrıntı: `araclar/kheaders.py`.

Yamalı kernelin sürüm dizesi
`4.19.325-cip136-st20-perf-gfd7a9d55451e ... #1 SMP PREEMPT Mon Oct 26 20:00:00 UTC 2026`
olur: commit ve tarih yamayı gösterir. Tabletteki kernelde yüklenebilir modül yoktur
(`CONFIG_*=m` yok, hiçbir bölümde ve ramdisk'te `.ko` yok, `/proc/modules` boş);
`CONFIG_MODVERSIONS` açık olduğu için modül yükleyici zaten yalnız CRC'lere bakar ve
yama dışa aktarılan hiçbir sembolün CRC'sini değiştirmez (`testler/modul-crc.sh`).

## Durum (30 Ekim 2026)

- Yamasız Image tabletteki LineageOS kerneliyle bayt bayt aynı; paketleme tabletteki boot
  bölümünü bayt bayt yeniden üretir (`testler/hepsi.sh`, 34 test).
- Tablette üç yamalı kernel (`-g9fb45c3b2fda`, boot sha256 `abc4b30f…`). Yamaların yazar
  kimliği yayın için GitHub noreply adresine çevrildi (30 Eki); kod aynı, commit'ler ve
  sürüm dizesi değişti: güncel derleme `-gfd7a9d55451e`, boot sha256 `29b8fb87…`. Kanıt
  `cihaz-kabul/kanit/20261026-klavye-sokme/`: klavyesiz açılış TABLET (3); üç
  takma/sökme döngüsünde takınca OPEN (2) ve masaüstü, sökünce TABLET (3) ve tam ekran;
  kapak kapanınca CLOSED (0) ve uyku, açınca uyanma; `tablet_mode` 1 → 3, 0 → 2.
  Kadir ekranda doğruladı.

## Kullanım

```sh
./build.sh hepsi            # kaynak, iki derleme (ortak kapıdan), iki boot imajı, testler
./build.sh kaynak           # yalnız indir ve doğrula
./build.sh derle-kapidan yamali
./build.sh boot yamali
testler/hepsi.sh
```

- Çalışma alanı `GBOS_KERNEL_IS` (varsayılan `/var/tmp/gbos-kernel`): kaynak ağacı
  (1,6 GB), clang (2,5 GB), derleme sırasında out dizini (btrfs'te sıkıştırılmış).
  Out dizini derleme bitince silinir (`GBOS_KERNEL_OUT_TUT=1` tutar). Çalışma alanı
  `kaynak.py kayit` ile önbellek olarak kayıtlıdır; silinirse `build.sh kaynak`
  yeniden kurar.
- Derleme ortak bellek/disk kapısından geçer (`derle-kapidan`). Ölçüm: `-j8` ile bellek
  tepesi 6033 MiB (24 Eki), tam çekirdekle 10121 MiB (26 Eki; systemd kapsamının
  `memory.peak` değeri, sayfa önbelleği dahil), varsayılan istek 11 GB;
  `GBOS_KERNEL_JOBS`, `GBOS_KERNEL_GB`, `GBOS_KERNEL_DISK_GB` ile ayarlanır.
- Çıktılar `cikti/<yamasiz|yamali>/`: `Image`, `.config`, `System.map`,
  `Module.symvers`, `boot.img` ve sha256'ları. `cikti/` git dışıdır.

## Boot imajı

`build.sh boot` LineageOS zip'indeki `boot.img`'yi (`../lineage-zip.py`; zip'in
sha256'sı çalışma dizinini kuran `lineage-hazirla.py`'nin kaydıyla karşılaştırılır)
`unpack_bootimg --format=mkbootimg` ile açar ve yalnız
`--kernel`'i değiştirip `mkbootimg` ile yeniden paketler (başlık sürümü 2, sayfa 4096,
taban ve ofsetler, cmdline, os sürümü 17.0.0 / yama 2026-10, ramdisk, dtb aynen). AVB:
LineageOS'unki gibi `add_hash_footer --algorithm NONE`, bölüm boyu 71303168, aynı salt ve
aynı iki özellik (`com.android.build.boot.os_version`, `...boot.fingerprint`). Yöntem,
tabletteki kernelle paketlendiğinde tabletteki bölümü bayt bayt yeniden üretir
(`testler/boot-alanlari.sh`).

AVB durumu (tabletten okundu, 24 Eki 2026): `ro.boot.verifiedbootstate=orange`
(kilitsiz); `vbmeta` bölümünün bayrakları `3` (HASHTREE_DISABLED |
VERIFICATION_DISABLED). vbmeta'daki boot hash tanımlayıcısı eski kernelin özetini
taşır ama doğrulama kapalı olduğu için açılışı durdurmaz; vbmeta'ya dokunulmaz.

## Geri dönüş

LineageOS 24.0 `20261011` nightly'sinin `boot.img`'si (sha256
`f34f7c7bafcfca4abe9132e218e9767002099735f8752fd8b7bd96e026a02154`) tabletteki
LineageOS boot bölümüyle bayt bayt aynıdır (24 Eki 2026'da `dd` ile çekilip
karşılaştırıldı); recovery.img'si de öyle. Geri dönmek: zip'teki `boot.img` boot
bölümüne yazılır (CIHAZ-TESTI.md, Geri dönüş). Kaynakta geri dönmek: `build.sh derle
yamasiz` ve `boot yamasiz`; bu imaj zip'tekiyle bayt bayt aynıdır.

Daha yeni bir LineageOS zip'i kullanılırsa `build.sh boot` onun ramdisk'ini ve dtb'sini
alır, kernel yine `KERNEL_COMMIT` üzerine yamalı olandır (tablette yüklenebilir modül
olmadığı için ikisinin eşleşmesi gerekmez); `build.sh` bunu bildirir ve testler
LineageOS kerneliyle bayt karşılaştırmalarını ATLANDI diye geçer.

## Testler

`testler/hepsi.sh`:

- `config-denk.sh`: tabletin `/proc/config.gz`'si, zip'teki boot'un kernelinin gömülü
  config'i, iki derlemenin `.config`'i ve yamasız Image'a gömülü config aynı; `=m` yok.
- `surum-dizesi.sh`: yamasız Image'ın sürüm dizesi `/proc/version` ile aynı, Image
  tabletteki kernelle bayt bayt aynı; yamalı dizede yalnız commit ve tarih farklı.
- `modul-crc.sh`: tablette modül yok; yamasız derlemenin `__kcrctab` ve
  `__kcrctab_gpl` tabloları (13113 CRC) tabletteki kernelde bayt bayt bulunur
  (yerleşimden bağımsız arama, `araclar/kcrctab.py`), yamasız Image'ın tablosu
  `Module.symvers` ile aynı; yamalı `Module.symvers` ve CRC tabloları da aynı.
- `yama-uygulandi.sh`: depodaki yama yamalı commit'in kendisi, yamanın dizeleri yalnız
  yamalı Image'da, yama yalnız beklenen dört dosyaya dokunuyor.
- `boot-alanlari.sh`: iki boot imajı zip'tekiyle kernel dışındaki her alanda aynı,
  kernel derlenen Image, AVB footer'ı aynı, yamasız boot zip'tekiyle bayt bayt aynı.

Bunlar derleme ve paketleme testleridir; kullanıcı deneyimi testi tablettedir
(CIHAZ-TESTI.md).
