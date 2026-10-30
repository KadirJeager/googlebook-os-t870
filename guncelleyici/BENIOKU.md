# Googlebook OS güncelleyicisi (SM-T870)

Google yeni mica sürümü çıkarınca tablet onu bulur, hazırlar, kurtarma modunda kurar; yeni sürüm düzgün açılmazsa kendiliğinden öncekine döner. Tablet A/B değil: yeni imajlar `/data`'da bekler, kurtarma (LineageOS recovery) onları super'daki yuvalara yazar.

## Akış

1. **Bulma (uygulama, günde bir):** `com.gbos.guncelleyici` Google'ın kurtarma imajı listesini (`production_recovery.json`, `mica-indir.py` ile aynı resmî kaynak) okur; `device=mica`, `channel=STABLE`, en büyük `buildidentifier`. Çalışan sürüm `ro.build.version.incremental`.
2. **Hazırlama:** `gbos-guncelleyici-is hazirla` Linux ortamında `motor/motor.py`'yi çalıştırır (indir → ağaç → `apply_patches.py` → ext4 → denetim) ve bitmiş kümeyi `/data/gbos-ota/gelen`'e koyar: üç imaj, ağacın `yuvalar.conf`, `super-baslik.bin` ve `kurulum.zip`'i, en son `hazir.conf` (`neden surum|kit`, `kit`, `baslik`). Bilgisayardan hazırlanan küme de aynı yere `adb push` ile konur (aşağıda).
   - **Ne hazırlanır:** yeni mica sürümü; ya da `/data/gbos-ota/motor/KIT` çalışan imajın yapıldığı kit değilse aynı sürüm yeniden (`neden kit`). Geri alınan (bekçi ya da elle) sürüm + kit çifti bir daha kendiliğinden hazırlanmaz (`motor/basarisiz`, kayıt `/cache/gbos/kurulan.conf`); yeni kit ya da sürüm hazırlanır. Bilgisayar yeni kit gönderince (`kit-uret.py gonder` uygulamanın günlük denetimini tetikler) tablet aynı sürümü kendisi hazırlar ve kurar.
   - **Kit imajda:** `yamalar/kit_imaj.py` kiti `/product/etc/gbos-kit/kit.tar.gz` olarak taşır (uygulama imza anahtarı hariç). `/data` silinmişse `hazirla` kiti oradan açar; anahtar yoksa motor tablette bir kez yeni anahtar üretir (bugün hiçbir APK yeniden imzalanmıyor).
   - **Super başlığı:** imajlar `tablete-yaz.HEADER` için yapılır (googlebookos-2: product super'ın sonuna büyütülmüş, `super-baslik/uret.py`). Çalışan kurucu başlık değiştiremiyorsa (`GBOS_KUR_BICIM=2` yok) motor googlebookos-1 için kitsiz bir **köprü imajı** yapar; köprü açılınca kit farklı görünür ve bir sonraki hazırlık tam imajı yapar.
3. **Kurulum (`kur`):** kümeyi kendi `yuvalar.conf`'una göre denetler (yuva boyu, SHA-256, kümenin dosyaları), super'daki başlık kümenin başlığı ya da onun `onceki_baslik`'ı olmalı; çalışan imajları super'dan (kümenin yuva boylarıyla) `/data/gbos-ota/eski`'ye alır ve yuvayla karşılaştırır, altı imajın AOSP `uncrypt` blok haritasını `/cache/gbos`'a çıkarır ve her imajı haritasından ham userdata aygıtından okuyup doğrular (kurtarmanın okuyacağı bayt), kümenin imzalı paketini kopyalar, başlık değişecekse iki başlığı `/cache/gbos/baslik-{hedef,onceki}.bin`'e koyar, `islem.conf` yazar, vold'un userdata checkpoint'ini başlatır, `/cache/recovery/command` yazar ve `reboot,recovery` ile kurtarmaya açar.
4. **Kurtarma (`kurtarma/update-binary`, imzalı `kurulum.zip` içinde):** yalnız bildiği super başlıklarında ve hedef başlık için sabit yuvalara yazar (tablo `tablete-yaz.py`'den). Başlık değişiyorsa önce hedef başlığı yazıp geri okur, sonra imajları; geri dönüşte (ve yeni küme doğrulanmazsa) eski imajları ve önceki başlığı yazar. Yazmadan önce yeni ve eski kümenin tamamını haritalardan okuyup doğrular; yazar, sayfa önbelleğini bırakıp yuvalardan geri okur. Yeni küme doğrulanmazsa eskisini yazar; o da doğrulanmazsa önyükleyici mesajıyla kurtarmada kalır (tutarsız sisteme açılmaz). LineageOS yarısına dokunmaz.
5. **Bekçi (`gbos-guncelleyici-is bekci`, her açılış, post-fs-data):** yeni sürüme 3 açılış hakkı verir; her biri 25 dk içinde `sys.boot_completed`'e ulaşmalı ve ardından 120 s `system_server` yeniden başlamamalı. Deneme süresince kurtarma komutu geri dönüş paketini gösterir (elle kurtarmaya girilirse de geri dönülür). Geçerse checkpoint onaylanır; geçmezse `geri` (neden `bekci`). Yarım yazılmış super ile açılan sistem doğrudan kurtarmaya döner.
6. **Uygulama:** Ayarlar › Sistem › Sistem güncellemesi (`android.settings.SYSTEM_UPDATE_SETTINGS`, öncelik 100); durum, ilerleme, "Şimdi yeniden başlat"; hazır güncelleme şarjda ve boştayken (gece) kendiliğinden kurulur; açılıştan sonra "güncellendi / geri alındı / yapılamadı" bildirimi; imajdan çıkarılan sistem uygulamalarının Play güncellemesi kalıntılarını kaldırır (`kaldirilan-paketler`: ham imajda olup bitmiş ağaçta olmayan her paket, her yeni sürümde bir kez, kullanıcı başına); `durum`'da `kit_farkli=1` görünce hazırlamayı planlar.

Veri: kurulum öncesi başlatılan vold checkpoint'i (`checkpoint=fs`, f2fs) deneme süresince yazılan veriyi onaylanana kadar geçici tutar. Geri dönüşte veri güncellemeden önceki haline döner; eski sürüm yeni sürümün yazdığı veriyi hiç görmez.

## Dosyalar

| Yol | Ne |
|---|---|
| `kurtarma/update-binary`, `kurtarma/paket.py` | kurtarma tarafı ve imzalı paket (`META-INF/com/android/metadata` dahil); `paket.py dogrula PAKET --otacerts ZIP` kurtarmanın imza denetimi |
| `cihaz/system/bin/gbos-guncelleyici-is` | Android tarafı: `durum`, `hazirla`, `iptal`, `kur`, `geri`, `bekci` |
| `cihaz/gbos-guncelleyicid.cpp`, `cihaz/gbos-guncelleyici.cpp` | servis (soket `/dev/socket/gbos_guncelleyici`, SO_PEERCRED: root, adb shell yalnız `ro.debuggable=1`, uygulama) ve komut satırı |
| `cihaz/system/etc/...` | init rc, privapp ve bildirim izni |
| `cihaz/file_contexts`, `selinux/70-gbos-guncelleyici.cil` | etiketler ve politika (Enforcing; uncrypt ve vdc kendi alanlarında) |
| `uygulama/` | "Sistem güncellemesi" |
| `motor/motor.py` | tablette hazırlama (Linux ortamı) |
| `motor/ortam-kur.sh`, `motor/e2fsdroid-kur.sh` | Linux ortamına hattın araçlarını kurar ve her birini kullanarak denetler (aşağıda) |
| `yerel/` | `libcapture_meta.so`, `libprovide_meta.so` ve derleyicisi `derle.py` |
| `build.sh` | arm64/x86_64 ikilileri, uygulama, imzalı paket → `out/<abi>/` (tekrarlanabilir) |
| `../yamalar/guncelleyici.py` | imaj ağacına kurulum |
| `testler/` | bilgisayar testleri (kurtarmanın mksh R59 + toybox 0.8.13'üyle) |
| `emulator/senaryo.sh` | Android 37 emülatöründe Enforcing uçtan uca deneme |

Super başlıkları: `super-baslik/` (`uret.py denetle`). Tablette: `/data/gbos-ota/{gelen,yeni,eski,motor,durum}`, `/cache/gbos/{islem.conf,*.map,kur.zip,geri.zip,deneme,sonuc,kurulum.log,guncelleyici.log}`.

## Tablet Linux ortamı

`motor/ortam-kur.sh` gbos-linux ortamında (Debian trixie, root) motorun ve yama hattının çağırdığı her aracı kurar; eksik paket yoksa apt'ye hiç gitmez, kurulu araçları yeniden derlemez. Sonra her aracı hattın kullandığı biçimde dener (erofs imajı, `make_ext4_images` bayraklarıyla ext4, zipalign, javac, varsa kitin `d8.jar`/`apksigner.jar`'ı, en küçük CIL politikası, cc + nm) ve sürümleri yazar. Bir şey eksik ya da bozuksa 1 ile çıkar; `--denetle` hiçbir şey kurmadan yalnız denetler.

```
adb shell gbos-linux run --bind /data/gbos-ota:/ota --env HOME=/ota/motor/ev \
    -- bash /ota/motor/kit/guncelleyici/motor/ortam-kur.sh
```

- Paketler: erofs-utils, e2fsprogs, openjdk-21-jdk-headless, zipalign, dexdump, aapt (aapt2), binutils, gcc, make, flex, curl ve e2fsdroid'in derleme bağımlılıkları. Liste koddaki çağrılardan çıkarıldı; `testler/test_ortam.py` her `subprocess` çağrısının bir sağlayıcısı olduğunu ve listede gereksiz paket olmadığını denetler.
- `e2fsdroid` Debian'da yok: `e2fsdroid-kur.sh` onu bilgisayardaki android-tools 37.0.0p1 ile aynı AOSP android17-release commit'lerinden derler (içerik SHA-256'ları sabit), `/usr/local/bin/e2fsdroid`.
- `secilc`/`avc_kontrol`: `selinux/araclar/kur.sh`'nin AOSP derlemesi, `$HOME/.cache/gbos-selinux` (yamalar/selinux.py'nin baktığı yer). Debian'ın secilc 3.8.1'i cihazın politikasını aynı derlemiyor, kurulmaz.
- `d8.jar`, `apksigner.jar` ve `android.jar` kitle gelir (SDK build-tools 37.0.0); Debian'ın apksigner paketi masaüstü JRE'si çektiği için kurulmaz.
- Bilgisayarla eşdeğerlik ölçümleri tableti kullanır: `testler/e2fsdroid-esdegerlik.py`, `testler/secilc-esdegerlik.py`, `testler/android-araclari-esdegerlik.py` (`--kanit DİZİN`). Sonuçlar ve bilinen farklar (Debian dexdump ve zipalign'ın yazım farkları, arm64'te `unsigned_directory_hash`): `kanit/ortam-20261024/ozet.txt`.

## Bilgisayardan hazırlanmış küme

`imaj-uret.sh` her kümenin yanına `hazir.conf` ve `KAYNAK` (kümenin üretildiği commit, `+kirli` = commit'lenmemiş değişiklik) yazar. Kurmadan önce `KAYNAK`'ın beklenen commit olduğuna bakılır.

```
D=/var/tmp/mica_work/denetim/imaj
cat $D/KAYNAK
adb push $D/system.img $D/system_ext.img $D/product.img $D/hazir.conf /data/gbos-ota/gelen/
adb shell gbos-guncelleyici --bekle kur      # --ayni: çalışandan yeni değilse de
```

## Testler

```
python3 -I -m pytest --basetemp=/var/tmp/gbos-guncelleyici-test guncelleyici/testler -q
emulator/senaryo.sh yukle && emulator/senaryo.sh kur && emulator/senaryo.sh denetle
```
