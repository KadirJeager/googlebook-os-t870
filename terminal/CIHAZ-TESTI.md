# Terminal: tablette denenecekler

Önkoşul: imaj `yamalar/terminal.py` `install()` ve `selinux/politika/plat/60-gbos-linux.cil` ile üretilip açıldı. Bölüm A bilgisayardan (`adb root` açık), bölüm B Kadir tabletin başındayken. Her maddenin çıktısı `T870-araclar/terminal/kanit/tablet-<tarih>/` altına (`<madde>.txt`, ekran görüntüleri `.png`).

```bash
A="adb"
```

## A. Bilgisayardan

1. **Servis ve etiketler**
   `$A shell 'getenforce; ps -AZ | grep gbos-linuxd; ls -laZ /system/bin/gbos-linuxd /dev/socket/gbos_linux /data/gbos-linux; grep -E "gbos-linux/(homes|exchange) " /proc/self/mountinfo'`
   Beklenen: `u:r:gbos_linuxd:s0 … gbos-linuxd`; ikili `gbos_linuxd_exec` (giriş noktası yalnız bu tür; `system_file` çıkarsa servis açılmaz, imaj etiketlemesi bozuktur); soket `gbos_linux_socket`, `srw-rw-rw-`; dizin `gbos_linux_data_file`; `homes` ve `exchange` satırlarında `shared:`.

2. **Uygulama**
   `$A shell 'pm list packages -f --user 10 com.gbos.terminal; dumpsys package com.gbos.terminal | grep -E "QUERY_USERS: granted|POST_NOTIFICATIONS: granted"'`
   Beklenen: `/system/priv-app/Terminal/Terminal.apk`, iki izin `granted=true`.

3. **Çekirdek varsayımları** (dosya yoksa atla)
   `$A shell 'zcat /proc/config.gz | grep -E "CONFIG_(PID_NS|USER_NS|UTS_NS|DEVTMPFS|ANDROID_PARANOID_NETWORK)[ =]"'`
   Beklenen: PID_NS, USER_NS, DEVTMPFS `is not set`; UTS_NS `=y`.

4. **SELinux başlangıç kaydı**: `$A shell 'logcat -G 16M; logcat -c'` (sonraki maddelerin retleri madde 12'de toplanır).

## B. Kadir tabletin başında (dokunarak)

5. **İlk kurulum, uygulamadan.** Görev çubuğundan/başlatıcıdan "Terminal"i aç.
   Beklenen: "Linux'u kurun" ekranı, kullanıcı adı önerili (Android kullanıcı adından, Türkçe harfler sadeleşmiş). Adı `jk` yap, "Kur".
   İlerleme: "Debian imajı aranıyor → Debian indiriliyor (x MB / 49 MB) → doğrulanıyor → açılıyor → Paketler kuruluyor"; "Ayrıntılar" apt günlüğünü gösterir. 2–10 dk sonra "Android dosyaları" izni sorulabilir (İzin ver → sistem ayarı → geri), ardından `jk@googlebook:~$` istemi.
   Ekran görüntüsü: `$A exec-out screencap -p > 5-kabuk.png`.

6. **Kabukta gerçek iş** (POGO klavyeyle yaz):
   ```
   id; locale | head -1; date
   sudo apt-get install -y sl && sl
   python3 -c "import urllib.request as u; print(u.urlopen('https://deb.debian.org').status)"
   git clone --depth 1 https://github.com/termux/termux-app /tmp/t && ls /tmp/t | head -3
   ls ~/android; echo merhaba > ~/android/Download/linux.txt
   echo şğıöüçİĞÜŞÖÇ
   ```
   Beklenen: `uid=10xxxxx(jk) gruplar=… sudo … aid_inet … aid_everybody`, `LANG=tr_TR.UTF-8`, Türkçe tarih ve İstanbul saati; tren animasyonu; `200`; depo dosyaları; Android klasörleri ve Dosyalar uygulamasında `Download/linux.txt`; Türkçe harfler doğru (AltGr ile `@`, `\`, `|` da).

7. **Masaüstü davranışı**
   - Ctrl+Shift+T yeni sekme, Ctrl+Tab / Ctrl+Shift+Tab geçiş, sekmeye orta tık kapatır, Ctrl+Shift+W kapatır.
   - Fareyle/dokunmatik yüzeyle sürükleyerek seç → Ctrl+Shift+C, başka yere Ctrl+Shift+V; sağ tık menüsü (Kopyala, Yapıştır, Yeni sekme).
   - Ctrl+= / Ctrl+- / Ctrl+0 yazı boyutu; iki parmakla yakınlaştırma; tekerlek/iki parmakla kaydırma.
   - `vim` ya da `htop` içinde fare tıklaması programa gider.
   - Pencereyi yeniden boyutlandır (`stty size` değişir), ⋮ → "Yeni pencere" ikinci pencere açar.
   - POGO klavyeyi çıkar: dokunmatik klavyenin üstünde Esc/Tab/Ctrl/Alt/oklar satırı; Ctrl+C ile `sleep 100` kesilir, Türkçe harfler dokunmatik klavyeden de doğru. Klavyeyi tak: satır kalkar.
   - ⋮ → Ayarlar: yazı boyutu kaydırıcısı, tema (Açık/Koyu/Sistem) anında değişir, "Android dosyalarına erişim" durumu, Debian sürümü.
   Ekran görüntüsü: `7-sekmeler.png`, `7-koyu-tema.png`.

8. **xdg-open**: `xdg-open https://www.debian.org` → Chrome açılır; `echo test > ~/a.txt; xdg-open ~/a.txt` → Android'in "Birlikte aç" seçicisi.

9. **Arka planda ağ** (Linux süreçleri Terminal'in uid'iyle çalışır; Android arka plandaki uygulamanın ağını keser, Terminal bunu ön plan hizmetiyle önler)
   - Bir sekmede: `for i in $(seq 30); do curl -s -o /dev/null -w "%{http_code} " https://deb.debian.org; sleep 2; done; echo`
   - Hemen başka bir uygulamaya geç, 60 sn sonra geri dön. Beklenen: 30 tane `200`, bildirimde "Linux çalışıyor · 1 terminal açık".
   - `nohup sh -c 'while true; do curl -s -o /dev/null -w "%{http_code}\n" https://deb.debian.org >> ~/ag.log; sleep 10; done' >/dev/null 2>&1 &` → bütün sekmeleri kapat. Beklenen bildirim: "Linux programları çalışıyor · N program çalışıyor" ve "Durdur". 2 dk sonra Terminal'i aç: `tail -3 ~/ag.log` hep `200`. Bildirimden "Durdur" → program biter, bildirim kalkar.
   - Ekranı kapat, 5 dk bekle, aç: `tail -3 ~/ag.log` (Durdur'dan önce yapılırsa) yine `200`.

10. **Kalıcılık**: tableti yeniden başlat, Terminal'i aç. Beklenen: kurulum ekranı gelmez, doğrudan kabuk; `ls ~` önceki dosyalar; `ls ~/android/Download/linux.txt` var.

## C. Yine bilgisayardan

11. **Root arayüzü (güncelleyicinin yolu)**
    `$A shell 'gbos-linux status; gbos-linux run -- sh -c "uname -m; cat /etc/debian_version; hostname; grep CapBnd /proc/self/status; tail -2 /etc/resolv.conf; getent hosts deb.debian.org | head -1"; echo rc=$?; gbos-linux run -- sh -c "exit 7"; echo rc=$?; gbos-linux run -- yok-boyle-komut; echo rc=$?'`
    Beklenen: `state=installed … running=true`; `aarch64`, `13.x`, `googlebook`, `CapBnd: 00000000a00425fb`, `nameserver 127.0.0.53`, bir IP, `rc=0`; `rc=7`; `rc=127`.
    `$A shell 'mkdir -p /data/gbos-linux/test-bind; gbos-linux run --bind /data/gbos-linux/test-bind:/work -- sh -c "touch /work/ok && ls /work"; ls /data/gbos-linux/test-bind; grep -c " /data/gbos-linux/exchange/" /proc/self/mountinfo; rm -r /data/gbos-linux/test-bind'`
    Beklenen: `ok` iki kez, sayı `0`.
    `$A shell 'gbos-linux run -- sh -c "useradd -m deneme && id deneme && userdel -r deneme"'` → `uid=60000(deneme)` (Android uid'leriyle çakışmayan aralık).

12. **SELinux kanıtı**
    `$A shell 'logcat -b all -d | grep "avc:"' | grep -E "gbos_linux|gbos-linux" > 12-avc.txt; $A shell 'dmesg | grep "avc:"' | grep gbos_linux >> 12-avc.txt`
    Beklenen: `python3 terminal/selinux/avc2cil.py --kabul < 12-avc.txt` boş (selinux/kabul.txt'deki bilinçli retler hariç). Değilse çıktıyı ve dosyayı terminal-chroot oturumuna ver. Alanlar Enforcing'dir: buradaki her ret kullanıcının gördüğü bir hatadır, ilgili maddeyi de yeniden dene.

## D. Antigravity (Android'in Linux terminali sözleşmesi, BENIOKU "Android'in Linux terminali sözleşmesi")

13. **Paket ve izinler (bilgisayardan)**
    `$A shell 'pm path com.android.virtualization.terminal; dumpsys package com.android.virtualization.terminal | grep -E "versionCode|codePath"; for p in com.google.android.apps.antigravity com.gbos.terminal; do dumpsys package $p | grep "MANAGE_TERMINAL: granted"; done; ls -l /apex/com.android.virt/priv-app/*/'`
    Beklenen: yol `/apex/com.android.virt/priv-app/VmTerminalAppGoogle@…/VmTerminalAppGoogle.apk`, `versionCode=1000000`, iki pakette `MANAGE_TERMINAL: granted=true`.
    `$A shell 'content call --uri content://com.gbos.terminal.status --method getVmStatus'` → gerçek durum (`is_installed=true`, Linux kapalıysa `is_running=false`).
14. **Kadir, Antigravity'de (dokunarak)**: Antigravity'yi aç → Terminal VM Setup. Beklenen: Antigravity'nin üstünde Terminal'in onay penceresi: başlık "Antigravity, Linux'ta bir komut çalıştırmak istiyor", komut `bash /home/droid/.setup_terminal.sh`, kopyalanacak dosya `/home/droid/.setup_terminal.sh` (kaynak Antigravity, ~3,4 kB), "Arka planda çalışır". **Çalıştır**. Bildirimde "Linux çalışıyor · 1 terminal açık"; birkaç dakika içinde (openssh-server kuruluyor) Antigravity kurulumu geçer, ajan sohbeti açılır. Reddet'e basılırsa Antigravity "iptal edildi" der, Linux'ta hiçbir şey değişmez.
15. **Kanıt (bilgisayardan)**
    `$A shell 'gbos-linux run -- sh -c "getent passwd droid; ls -la /home/droid /home/droid/.ssh; readlink /mnt/shared /storage/emulated/10; cat /etc/ssh/sshd_config.d/*.conf; ps -ef | grep [s]shd"; grep -i " 0100007F:08A[E-F]\| 0100007F:08B" /proc/net/tcp | grep " 0A "'`
    Beklenen: `droid:x:<Terminal uid>:…:/home/droid`, `.ssh/authorized_keys`, `.setup_terminal.sh` yok (betik kendini siler), `/mnt/shared → /mnt/android/10/emulated/10`, `ListenAddress 127.0.0.1` + `Port 22xx`, sshd çalışıyor, dinleme yalnız `0100007F` (127.0.0.1).
    `$A shell 'logcat -d -s Terminal TerminalBridge ActivityTaskManager | tail -40'` → -91 yok; `logcat -d | grep -i "LinuxVmManager\|TerminalVmManager"` Antigravity tarafında hata yok.
15b. **Depolama görünümü** (onaydan sonra; kadir hesabı için önce `gbos-linux install --repair` da bindfs'i kurar)
    `$A shell 'gbos-linux run -- sh -c "grep \" /mnt/storage\" /proc/self/mountinfo; ps -ef | grep [g]bos-storage-view"; gbos-linux status | grep processes'`
    Beklenen: `/mnt/storage/10 … fuse.bindfs … user_id=<Terminal uid>`, bindfs Terminal uid'iyle çalışıyor, `processes` onu saymıyor.
    Terminal'de (dosya erişimi açıkken): `cp /bin/true ~/android/Download/t && ~/android/Download/t && echo OK; ls -l ~/android/Download/t; rm ~/android/Download/t` → `OK`, `-rwx------`. Android'in Dosyalar uygulamasında Download'da dosya görünür (MediaProvider'dan geçti).
16. **Çalışma klasörü**: Antigravity'de çalışma klasörü seç (ör. Belgeler/proje). Beklenen: "Antigravity, bir klasörü Linux ile paylaşmak istiyor", yol `/storage/emulated/10/Documents/proje`; Terminal'in dosya erişimi kapalıysa önce "Erişime izin ver". Sonra Antigravity'nin ajanına `ls` ve `uname -a` çalıştırt: çıktı Linux'tan (`aarch64`, Debian) gelir.
17. **Yeniden başlatma**: tableti yeniden başlat, Antigravity'yi aç. Beklenen: Linux çalışmadığı için onay penceresi yeniden gelir (Android'in terminalinde de her açılışta böyle); onaydan sonra bağlanır.
18. Retler: madde 12 aynen; yeni alan kuralı gerekirse `avc2cil.py` çıktısı selinux-port'a.

