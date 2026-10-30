# Tablette doğrulama: VPD servisi ve GSC

Bu adımlar, `mica_servisleri.install()` imaja girdikten sonra uygulanır. Ön koşullar:

1. `apply_patches.py`, `mica_servisleri.install(SYS_DIR, SYSEXT_DIR, PROD_DIR)` çağrısını yapmış olmalı. `selinux.uygula`'dan önce ya da sonra olması fark etmez, çünkü `selinux.py` file_contexts'e dokunmuyor.
2. Politika: `selinux/politika/plat/60-gbos-vpd-executor.cil`, selinux-port oturumundan. Bu dosya tipleri tanımlamıyorsa `install()` kurulumu reddeder.
3. İmaj oluşturucu yeni dosyaları ağacın file_contexts'iyle etiketlemeli. Bu sayede `/system/bin/gbos-vpd-executor` `gbos_vpd_executor_exec` olur. Etiket yanlışsa init, permissive modda bile servisi başlatmaz ve şu hatayı yazar: "has incorrect label or no domain transition".

Komutların hepsi bilgisayardan çalışır: `A="adb"`, `$A root`.

## Yazmadan önce, karşılaştırma için

```bash
$A shell pidof udsattestation
$A logcat -d | grep -c "Waited one second for android.system.desktop.security.gsc"
$A logcat -d | grep -c "Waited one second for vendor.google.desktop.vpd_executor"
```

## Açılıştan sonra

1. Dosyalar ve etiketler:
   ```bash
   $A shell ls -lZ /system/bin/gbos-vpd-executor /system/etc/init/gbos-vpd-executor.rc /system/etc/vintf/manifest/gbos-vpd-executor.xml
   $A shell ls -ldZ /data/misc/gbos_vpd
   ```
   Beklenen:
   - ikili `-rwxr-xr-x root shell` ve `u:object_r:gbos_vpd_executor_exec:s0`
   - dizin `drwx------ system system` ve `u:object_r:gbos_vpd_data_file:s0`

2. GSC beklemesi bitti:
   ```bash
   $A shell pidof udsattestation; $A shell getprop init.svc.android.system.desktop.security.udsattestation
   $A logcat -d | grep -c "Waited one second for android.system.desktop.security.gsc"
   ```
   Beklenen: pid boş, özellik boş, sayı 0.

3. Servis açılışta çalışmıyor, istenince başlıyor. `service check` ve `service call` servisi checkService ile arar ve lazy servisi başlatmaz; başlatmayı servis yöneticisi yapar. Kabuktan aynı yol `ctl.interface_start` ile izlenir:
   ```bash
   $A shell getprop init.svc.gbos_vpd_executor
   basla() { $A shell setprop ctl.interface_start aidl/vendor.google.desktop.vpd_executor.IVpdExecutor/default; sleep 2; }
   basla
   $A shell pidof gbos-vpd-executor; $A shell getprop init.svc.gbos_vpd_executor
   $A shell service check vendor.google.desktop.vpd_executor.IVpdExecutor/default
   ```
   - İlk komut boş dönmeli.
   - `basla`dan sonra bir pid görünmeli, özellik `running` olmalı ve `service check` "found" demeli.
   - Enforcing'de `su`'nun `ctl.interface_start` yazması reddedilebilir; tablet logunda bu ret permissive modda görüldü (`kanit/20261022-diagnostics/diag-lazy.txt`). O zaman servis 5. adımda My Googlebook'u açınca doğal yoldan başlar; 4. adım 5. adımdan sonra koşulur.
   - `$A logcat -d -s gbos-vpd-executor` içinde `registered vendor.google.desktop.vpd_executor.IVpdExecutor/default` satırı olmalı.

4. Doğrudan çağrılar. adb root uid 0'dır ve servis bu uid'ye izin verir. Yanıttaki ilk sözcük `00000000` ise istisna yok demektir.
   ```bash
   S=vendor.google.desktop.vpd_executor.IVpdExecutor/default
   basla   # servis istemcisi kalmayınca kendiliğinden çıkar; her çağrı grubundan önce
   $A shell service call $S 1 i32 0                       # getEntries(RO)
   $A shell service call $S 2 i32 0 s16 ubind_attribute   # getValue(RO, ubind_attribute)
   $A shell service call $S 2 i32 1 s16 activate_date     # getValue(RW, activate_date)
   ```
   - `getEntries(RO)` yanıtının UTF-16 dökümünde iki giriş olmalı: `serial_number` ile tabletin seri numarası (`getprop ro.serialno`), `model_name` ile `SM-T870`.
   - `getValue(RO, ubind_attribute)` yanıtında istisna olmamalı. Girişte anahtar `ubind_attribute` olmalı; değer null'dır ve `ffffffff` olarak görünür.
   - `getValue(RW, activate_date)` için: Setup Wizard tarih yazdıysa `yyyy-ww` görünür, yazmadıysa `ffffffff`.

5. My Googlebook, kullanıcı gibi:
   ```bash
   $A logcat -c
   $A shell monkey -p com.google.android.desktop.tips -c android.intent.category.LAUNCHER 1
   sleep 8; $A exec-out screencap -p > my-googlebook.png
   $A logcat -d | grep -E "Waited one second for vendor.google.desktop|gbos-vpd-executor|ServiceabilityService|AndroidRuntime"
   ```
   - Ekranda kartlar dolu olmalı, gri iskelet kalmamalı. Uygulama ayrıca dokunarak da açılıp kartlar arasında gezilmeli.
   - Logda `Waited one second for vendor.google.desktop.vpd_executor` satırı ve `FATAL EXCEPTION` olmamalı.

6. serviceability'de hiçbir iş parçacığı servis beklememeli:
   ```bash
   $A shell debuggerd -j $($A shell pidof com.android.desktop.serviceability) | grep -A12 waitForService
   ```
   Çıktı boş olmalı. Doluysa, beklenen servis adı yığından okunup bildirilmeli.

7. RW kalıcılığı ve yeniden başlatma:
   ```bash
   $A shell ls -l /data/misc/gbos_vpd/
   $A reboot; $A wait-for-device; $A root
   basla; $A shell service call vendor.google.desktop.vpd_executor.IVpdExecutor/default 2 i32 1 s16 activate_date
   ```
   - `rw_vpd` dosyası `-rw------- system system` olmalı ve `rw_vpd.tmp` bulunmamalı. Dosya ancak bir RW yazımından sonra oluşur.
   - Yeniden başlatmadan sonra değer, önceki 4. adımdaki ile aynı olmalı.

8. SELinux:
   ```bash
   $A shell getenforce
   $A logcat -d -b all | grep "avc:" | grep -E "gbos_vpd|vpd_executor"
   ```
   Enforcing'de reddetme olmamalı. Permissive'de çıkan satırlar selinux-port oturumuna iletilmeli.

Bekleyen servisleri toplu görmek için: `$A logcat -d | grep -o "Waited one second for [^ ]*" | sort | uniq -c`. Bu çıktıda yalnız açılışın ilk saniyelerindeki `keystore2` ve `keymaster@4.0` beklemeleri kalmalı.
