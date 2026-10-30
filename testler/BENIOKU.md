# İmaj hattı regresyon testleri

Bu testler `T870-araclar` imaj hattının araçlarını gerçek girdilerle sınar. Araçların kendi fonksiyonları içe aktarılır ya da araçlar komut satırından çalıştırılır; hiçbir araç testte yeniden yazılmaz. Testler araç dosyalarını, `/var/tmp/mica_work/{sys,sysext,prod}` ağaçlarını ve `denetim/` imajlarını değiştirmez. Tablete (adb) dokunmaz.

## Çalıştırma

```sh
cd GooglebookOS   # the directory that holds T870-araclar
python3 -I -m pytest T870-araclar/testler          # hepsi, yaklaşık 7 sn
python3 -I -m pytest T870-araclar/testler -k ld_config -v
```

- Gerekenler: `python-pytest` (Arch `extra`, 9.1.1), `~/.local/bin/{dump,fsck}.erofs`, `debugfs`, `mke2fs`, `e2fsdroid`, `e2fsck`, `brotli`, `zstd`, `llvm-readelf`. `h` için ağ gerekir.
- `GBOS_WORK` tanımlıysa testler çalışan ağaç olarak onu okur. Varsayılan `/var/tmp/mica_work`.
- Geçici veri `/var/tmp/gbos-test-<pid>/` altında tutulur ve oturum sonunda silinir. `/tmp` (RAM) ve pytest'in `tmp_path`'i kullanılmaz. `.pytest_cache` ve `__pycache__` bırakılmaz (`pytest.ini`, `conftest.py`).
- Disk payı: Diske yazan her test önce ne kadar yazacağını hesaplar. `/` için 25 GiB boş yer kalmayacaksa test "atlandı: disk tabanı" der. En büyük geçici dosya 01:30 imajından açılan `system.img`'dir (1,4 GiB). Bu dosya okunur okunmaz silinir.
- Gerçek girdi yoksa test "atlandı: gerçek girdi yok: <yol>" der, geçti demez. Ağ yoksa `h` testleri "atlandı: ağ yok" der. Bu durum ağsız bir isim alanında denendi.

## Testler ve neyi korudukları

| Dosya | Test | Korunan davranış |
|---|---|---|
| `test_file_contexts.py` (a) | `test_tek_sirali_blok` | `add_plat_file_contexts` girdilerini dosyanın sonunda tek, sıralı bir blokta tutar. Bozulmamış kısım bayt bayt korunur. Girdiler `apply_patches.py`'nin kendi çağrılarından (`ast`) okunur. |
| | `test_cagri_sirasindan_bagimsiz` | Çağrı sırası (bütün permütasyonlar), tek çağrı ya da yinelenen girdi aynı dosyayı verir. |
| | `test_tekrar_calistirma_ayni` | Yamalı dosyada yeniden çalıştırmak hiçbir şeyi değiştirmez. |
| | `test_eski_bicim_tasinir` | Eski sürümün eklediği `"\n/system/bin/netbpfload …\n"` biçimi bloğa taşınır. Girdi, 01:30 bilinen-iyi `system.img.zst` içindeki gerçek `plat_file_contexts`'tir. Bu dosya bozulmamış mica dosyası ile eski ekin birleşimidir. Ekin biçimi 21 Ekim 22:46 tarihli `apply_patches` bytecode'undaki sabitten doğrulandı. |
| | `test_blok_onundeki_bos_satir` | Blok önünde kalmış boş satır ve eski ekle birleşimi temizlenir. |
| | `test_modul_bloklari_onunde_bos_satir` | Aynı boş satır, gerçek düzende de (ardından modül blokları gelirken) silinir. Blok bu düzende sonda olmadığı için sondaki boş satır temizliği bu artığı gizleyemez. |
| | `test_modul_bloklari_varken_idempotent` | Çalışan ağacın dosyasında (mica-servisleri ve GBOS_TERMINAL blokları içinde) iki çalıştırma aynı sonucu verir. Modül blokları bozulmaz ve tek kalır. |
| `test_file_contexts.py` (b) | `test_modul_iki_kez_ayni_dosya[mica_servisleri\|terminal]` | Her modülün `install()`'ı bozulmamış dosyada iki kez çalıştırılınca bir kez çalıştırılmış gibi sonuç verir. Bloklar üst üste binmez, boş satırlar çoğalmaz. |
| | `test_zincir_pristine_ve_tekrar_ayni` | `apply_patches.py` `__main__` sırası (kendi etiketleri, ardından mica-servisleri, ardından terminal) bozulmamış dosyada ve kendi çıktısında aynı dosyayı verir. |
| | `test_calisan_agac_zincirle_ayni` | Çalışan ağacın `plat_file_contexts`'i bugünkü araçların ürettiğiyle aynıdır. Bu, yama-dogrula'nın bu dosya için "0 fark" sonucudur. |
| `test_ld_config_32.py` (c) | `test_vendor_ad_alani_yalitilmis` | `install_32bit_runtime`'ın yazdığı `ld.config.32` (geçici çalışma dizininde üretilir) şu koşulları sağlar: `[vendor]` yalıtılmıştır, override hem arama hem izinli yollarda ilk sıradadır, `system` bağlantısı vardır. LLNDK listesinde `libbinder_ndk.so` ve `libc.so` bulunur, `libbinder.so` ve `libhidlbase.so` bulunmaz. Sistem ad alanı `/system/lib/bootstrap`'a ulaşır. |
| | `test_calisan_agacla_ayni` | Çalışan ağaçtaki `ld.config.32` bugünkü çıktıyla bayt bayt aynıdır. |
| | `test_vendor_needed_cozuluyor` | LineageOS zip'inden yeniden kurulan vendor bölümünün 32-bit ARM kodu (bütün `/vendor/lib` ve `/vendor/bin`'deki 32-bit programlar) incelenir. Bu dosyaların bütün `NEEDED`'ları `[vendor]` arama yollarından ya da LLNDK bağlantısından çözülür. LLNDK adları çalışan ağacın sistem ad alanında gerçekten bulunur. `rfsa/adsp` dışarıda bırakılır. Bu kitaplıklar ARM değil Hexagon'dur, test bunu ELF başlığıyla doğrular. (codec2 KOK-NEDEN) |
| `test_lineage_dosya_cikar.py` (d) | `test_vendor_kitapliklari_asamayla_ayni` | `lineage-dosya-cikar.py` komut satırından çalıştırılır. `SHA256SUMS`, `KAYNAK` ve dosyalar `lineage/vendor_stage` ile aynıdır, kip 0644'tür. Yeniden kurulan bölüm imajı silinir. |
| `test_binder_yamalari.py` (e) | `test_kaliplar_bir_kez_ve_yamali` | `patch_binder_and_hidlbase_threadpool` geçici bir SYS_DIR'de gerçek kaynaklarla çalıştırılır. Her özgün kalıp kaynakta tam bir kez geçer, çıktıda geçmez. Yamalı kalıp bir kez fazla geçer. Başka hiçbir bayt değişmez. |
| | `test_calisan_agac_yamali_ciktiyla_ayni` | Yazılan altı kitaplık çalışan ağaçta bayt bayt aynıdır: `system/lib/libbinder.so`, `gbos-vendor-override/*`, lib64 kopyaları ve libhidlbase. Override dizininde fazladan dosya yoktur. |
| `test_super_yuvalari.py` (f) | `test_yuvalar_lp_ile_ayni[…]` | `tablete-yaz.HEADER_SLOTS` başlıklardan okunur ve iki Googlebook OS başlığının (`super-baslik/`) LP metadatasıyla aynıdır (`mica_disk.parse_lp`): her yuva tek bir doğrusal kapsamdır. |
| | `test_basliklar_ozetleriyle_ve_lpdump_ile` | Her başlık super'ın ilk MiB'ıdır ve kayıtlı özetini taşır (yeniden üretilen googlebookos-2 dahil). AOSP `lpdump` googlebookos-2'yi kabul eder: product 12740608..19865600 sektör. |
| | `test_yeni_baslik_yalniz_product_buyutur` | googlebookos-2 googlebookos-1'den yalnız product'ta ayrılır; product super'ın sonuna (9700 MiB) uzanır. |
| | `test_eski_yuvalar_yenilerin_icinde` | Eski başlığın her yuvası yenisiyle aynı yerde başlar ve büyük değildir: eski imajlar yeni başlıkta da bağlanır. |
| | `test_lineage_yarisina_dokunmaz[…]` | İki başlığın yuvaları da LineageOS başlığındaki hiçbir bölümle çakışmaz. vendor/odm aynı yerdedir. |
| | `test_ext4_blok_sayilari_yuvaya_esit` | `make_ext4_images.slot_blocks` her başlık için (`GBOS_SUPER_BASLIK`) yuva boyutunu verir. |
| | `test_hazir_imaj_boyutu_yuvaya_esit[…]` | googlebookos-1 için yapılmış `denetim/surum-dusur/*.img` boyutları (`stat`) ve 01:30 geri dönüş setinin açılmış boyutları (`zstd -l`) yuva boyutuna eşittir. İmajlar yalnız okunur. |
| | `test_googlebook_basligi_lineage_basligindan` | `uret.relocate` tabletin LineageOS başlığından çalışan `googlebookos-2`'yi (f64551d7…) bayt bayt üretir. |
| | `test_baska_lineage_yerlesimi` | system_ext'i 200 MiB büyük başka bir LineageOS derlemesinde yuvalar 200 MiB kayar, product o kadar küçülür ve super sonunda biter; LineageOS kapsamlarıyla çakışma yoktur, vendor/odm yerinde kalır, `lpdump` kabul eder. |
| | `test_googlebook_basligi_lineage_diye_kaydedilmez` | Zaten Googlebook olan ya da yer kalmayan başlık reddedilir; yanlış durumda `yakala` hiçbir şey yazmaz. |
| | `test_kayit_baska_tablet` | `record()` başka bir tabletin başlığını `lineage.bin.xz` olarak bayt bayt yazar; `SHA256SUMS` yalnız onu ve ondan yapılan `googlebookos-2`'yi listeler, `googlebookos-1` düşer. |
| `test_ext4_etiketleme.py` (g) | `test_yeni_dosyalar_dogru_etiketlenir` | `make_ext4_images.py system` küçük bir ağaçta çalıştırılır (`GBOS_WORK` geçici dizindir). Ağaç gerçek dosyalardan, gerçek `sys.meta`'dan ve gerçek file_contexts'lerden oluşur. Etiketler imajdan `debugfs ea_get` ile okunur. Beklenen etiketler: `gbos-linuxd` için `gbos_linuxd_exec`, `gbos-vpd-executor` için `gbos_vpd_executor_exec`, `ld.config.32` için `system_linker_config_file`, `/system/lib/bootstrap/*` için `system_lib_file` (mica'daki `system_bootstrap_lib_file` değil), `gbos-linux` için `system_file`. Meta'daki dosyalar yakalanmış etiketlerini korur. Rapor dosyası imajla aynıdır. |
| | `test_eslesmeyen_dosyada_durur` | Hiçbir file_contexts girdisinin karşılamadığı yeni bir dosyada araç sıfırdan farklı kodla durur ve dosyayı adıyla bildirir. Raporda bu dosya `rootfs` olarak görünür. |
| `test_kamera_yamasi.py` (i) | `test_tek_yer_ve_sadece_o_baytlar`, `test_komutlar_lens_facing`, `test_apk_butunu`, `test_calisan_agac_orijinal` | `patch_camera_app` gerçek bozulmamış AL_Camera.apk üzerinde: tek yer, 20 kod birimi `LENS_FACING == BACK` dizisi (dal hedefleri dizinin içinde), dex dışı girdiler aynı, dex'ler stored ve hizalı, proje imzası; çalışan ağaçla bayt bayt aynı. |
| `test_kamera_jpeg.py` (j) | `test_hiddenapi_gidis_donus`, `test_birlesik_dex_bayraklar_ve_tablolar`, `test_bes_cagri_kopruye`, `test_shim_dex`, `test_calisan_agac_ayni` | `yamalar/kamera_jpeg.py` gerçek framework.jar üzerinde: hiddenapi bölümü gidiş-dönüşte bayt bayt aynı; birleşik classes2.dex'te her üye bayrağını korur, köprü BLOCKED, metot kimliği sınırın altında, map sonu `[hiddenapi, map]`; altı çağrı `invoke-static CameraBridge.<ad>` (ilk yazmaç wrapper), metodun kalanı kod birimi adresiyle aynı; classes7.dex yalnız shim sınıfları; çalışan ağaç aynı. |
| `test_cekirdek_kaynak.py` (h) | `test_userrequested_normalboot` | LineageOS `android_kernel_samsung_sm8250` `lineage-24.0` dalındaki `sec_debug.c` incelenir: `SYSTEM_BOOT_REASON` ile eşleşen ilk `magic[]` girdisi `"userrequested"` ve `PON_RESTART_REASON_NORMALBOOT`'tur, eşleşme yolu PON'a yazar. |
| | `test_eslesmeyen_neden_pon_yazmaz` | `"shell"` (çıplak `adb reboot`) hiçbir girdiyle eşleşmez. Eşleşmeme yolu `__done`'dan önce döner, PON'a yazmaz. |
| | `test_recovery_dali_pon_recovery` | `msm-poweroff.c` `msm_restart_prepare` içinde `"recovery"` dalı `PON_RESTART_REASON_RECOVERY` yazar. `SYSTEM_BOOT_REASON` hiçbir dala girmez. Kararı `sec_debug_update_restart_reason` dallardan sonra verir. |
| `test_pil_sagligi.py` (k) | `test_ikili_cihazin_kutuphaneleriyle_baglanir`, `test_vendor_ile_ayni_arayuz_ve_yapilandirma`, `test_servis_tanimi_ve_vendor_politikasi`, `test_install_iki_kez_ayni`, `test_politika_gecis_ve_erisimler`, `test_product_imajinda_etiket_ve_sahiplik`, `test_init_kurallari_android17` | `pil-sagligi` health HAL'i: DT_NEEDED yalnız LLNDK, her sembol mica kütüphanelerinde sürümüyle çözülür, ikili sembol açmaz; vendor HAL'le aynı AIDL V5 hash'i ve iki sysfs yolu; zip'ten yeniden çıkarılan vendor rc/VINTF modülün kopyasıyla aynı; servis seçenekleri vendor'la birebir, vendor servisi early-init'te durur; `install()` iki kez aynı; politika init gibi derlenince `beklenen.txt` ve geçiş tutar; product imajında `hal_health_default_exec`, `root:shell 0755`; AOSP android-17 init'te override yasağı ve `stop` davranışı. Ayrıntı `pil-sagligi/BENIOKU.md`. |
| `test_lineage_hazirla.py` (l) | `test_calisan_girdilerle_ayni` | `lineage-hazirla.py --denetle` gerçek LineageOS zip'inde çalıştırılır. Beş aşamanın (`lib32_stage`, `vendor_stage`, `vendor_features`, `vendor_dogal_yon`, `bolumler`) bütün dosyaları çalışma dizinindekilerle bayt bayt aynıdır; eksik ya da fazla dosya yoktur, geçici ağaç silinir. |
| | `test_ext4_dizin_turleri` | `ext4_entries` dosya, dizin (`040755`) ve sembolik bağı ayırır, bağın mutlak hedefini döndürür. |
| | `test_bos_ve_eksik_dosya` | Boş dosya boş olarak okunur (LineageOS boş `odm_file_contexts` ve `bug_map` taşır); olmayan dosya hata verir ve geride dosya bırakmaz. Alt dizinli `etc/selinux` (`mapping/`) eksiksiz listelenir. |
| `test_yuz_tanima.py` (m) | `test_yamalar_lisans_ve_kisisel_veri` | `yuz-tanima/yamalar/*.patch` üst kaynağın hiçbir telif/lisans satırını silmez; GitHub noreply dışında e-posta adresi taşımaz. |
| | `test_kaynak_agaci_sabitle_ayni` | Derlemenin okuduğu `FaceUnlock/` (`.git` hariç) Evolution-X 817e02a9 + yamalar ağacıdır: `getir.sh`'nin sabitlediği ağaç özetiyle aynı. Yamaya aktarılmamış değişiklik burada kalır. |
| | `test_temiz_derleme_imajdakiyle_ayni` | `build.sh` boş bir dizinde (eski `out/` ve imaj ağacındaki APK olmadan, sınıf yolu bozulmamış mica imajından) çalışır; HAL, uygulama ve overlay çalışan ağaçtaki kopyalarla bayt bayt aynıdır. |
| `test_selinux_reddedilenler.py` (n) | `test_kayitlarin_her_reddi_dosyada` | Cihaz kayıtlarındaki (`selinux/kanit`, `acilis-loglari`, `cihaz-kabul/kanit/*/avc-ham.txt`) her ret `selinux/reddedilenler.txt`'de var: kayıtlar olmadan (yayın deposu) `selinux.py dogrula` aynı redleri oynatır. Kayıt yoksa atlanır. |
| | `test_yalniz_politika_alanlari` | Dosyanın her satırı kanonik `avc: denied { izin } for [name=servis] scontext= tcontext= tclass=` biçiminde; yol, süreç adı, pid, inode taşımaz; sıralı ve tekrarsız. |
| `test_disa_aktar.py` (o) | `test_secim_kanit_ve_kisisel_dizinleri_disarida` | `yayin/disa-aktar.py` commit'li dosyalardan hattı seçer; kanıt, log, araştırma, eski deneme dizinleri, yazarın super başlıkları, kernel yedeği ve indirilen üçüncü taraf ikilileri seçilmez. |
| | `test_tarama_kisisel_veri_ve_anahtar_yakalar` | Tarama seri numarası, ev dizini yolu, kişisel e-posta, 18+ haneli sayı, özel anahtar bloğu ve izinsiz ikili dosyayı yakalar. |
| | `test_tarama_izinli_olanlari_gecirir` | noreply adresi, `/home/droid`, tek rakamlı uzun sayı, üçüncü taraf lisans metnindeki adres ve sha256'sı sabit AOSP AVB test anahtarı geçer; aynı adla başka anahtar geçmez. |

## Son koşu (22 Ekim 2026, 08:10)

`python3 -I -m pytest T870-araclar/testler -v`: **32 geçti, 0 atlandı, 0 kaldı, 7,2 sn**. `/` için 28 GB boş vardı. Koşudan sonra `__pycache__`, `.pytest_cache` ya da `/var/tmp/gbos-test-*` kalmadı.

**Testlerin hatayı yakaladığının kanıtı:** Araçların `/var/tmp` altında bir yansı kopyasında 10 bozma denendi. Her birinde ilgili test kaldı, araç dosyalarının kendisi değişmedi. Yansı kopya sonra silindi. Denenen bozmalar şunlardı:

- eski `add_plat_file_contexts` (6 test kaldı)
- blok önündeki boş satırın silinmemesi (`test_modul_bloklari_onunde_bos_satir` kaldı; ilk sürümde bu bozma yakalanmıyordu, test bunun üzerine eklendi)
- yalıtılmamış `[vendor]`
- LLNDK'dan `libbinder_ndk.so`'nun çıkması (`NEEDED` testi dahil 3 test kaldı)
- override yama baytı
- kaymış `SLOTS`
- eşleşmeyen dosyada durmama
- terminal bloğunun önceki satırı almaması
- `lineage-dosya-cikar` dosya kipi
- `SYSTEM_BOOT_REASON = "recovery"`

## Bulunan hatalar ve notlar

- **Araçlarda gerçek hata bulunmadı.** Bütün davranışlar kaynaklarıyla ve çalışan ağaçla tutarlı.
- **Vendor bölümü EROFS'tur, ext4 değildir.** Bu yüzden görev tanımındaki "debugfs rdump ile /lib" vendor'a uygulanamaz. Testler dosya sistemini `lineage-dosya-cikar.read_file` gibi ext4 imzasından tanır: EROFS'ta `fsck.erofs --extract --path=/lib`, ext4'te `debugfs rdump` kullanılır. odm bölümü ext4'tür ama `/lib`'i yoktur.
- **Öneri:** `make_ext4_images.py` içe aktarıldığında bütün imajları derliyor, çünkü modül düzeyinde `_wanted` döngüsü var. `build_ext4` bu yüzden içe aktarılamıyor; `g` testi aracı program olarak çalıştırıyor, `f` testi `EXACT_BLOCKS`'u kaynaktan okuyor. Döngü `if __name__ == "__main__":` altına alınırsa bu iki dolaylı yol gereksizleşir. Karar ana oturumun.
