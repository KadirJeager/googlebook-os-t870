#!/usr/bin/env bash
# Tablet recovery ya da herhangi bir adb durumunda gorundugu an acilis
# dongusunun nedenini anlamak icin onceki acilisin cekirdek ve init loglarini toplar.
# Hicbir bolume yazmaz; yalniz okur.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SER=${ANDROID_SERIAL:-$(python3 -I "$HERE/t870_cihaz.py")} || exit 1
ADB="adb -s $SER"
OUT="$HERE/acilis-loglari/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
if adb devices | grep -qE "${SER}[[:space:]]+recovery"; then
  echo "[*] Tablet su an recovery modunda; kullanicinin yeniden baslatmasi bekleniyor..."
  while adb devices | grep -qE "${SER}[[:space:]]+recovery"; do sleep 1; done
  echo "[*] Tablet yeniden baslatildi, acilis bekleniyor..."
fi
echo "[*] Tablet ($SER) bekleniyor: recovery, sideload ya da normal adb..."
until adb devices | grep -qE "${SER}[[:space:]]+(device|recovery|sideload)"; do sleep 2; done
DURUM=$(adb devices | awk "/${SER}/{print \$2}")
echo "[+] Tablet baglandi, durum: $DURUM"
adb devices -l > "$OUT/adb-devices.txt"
$ADB root >/dev/null 2>&1; sleep 2
$ADB shell 'id; getprop ro.bootmode; getprop ro.build.fingerprint' > "$OUT/kimlik.txt" 2>&1
$ADB shell 'ls -la /sys/fs/pstore/ 2>&1' > "$OUT/pstore-liste.txt" 2>&1
for f in $($ADB shell 'ls /sys/fs/pstore/ 2>/dev/null' | tr -d '\r'); do
  $ADB shell "cat /sys/fs/pstore/$f" > "$OUT/pstore-$f.txt" 2>&1
done
$ADB shell 'cat /proc/last_kmsg 2>/dev/null' > "$OUT/last_kmsg.txt" 2>&1
$ADB shell 'dmesg 2>/dev/null' > "$OUT/dmesg-simdi.txt" 2>&1
$ADB shell 'cat /cache/recovery/last_log 2>/dev/null; ls -la /cache/recovery /metadata 2>&1' > "$OUT/recovery-last_log.txt" 2>&1
$ADB shell 'lpdump /dev/block/by-name/super 2>&1 || lpdump 2>&1' > "$OUT/lpdump.txt" 2>&1
# Kalici acilis gunlugu: /metadata/gbos-bootlog
if [ "$DURUM" = "recovery" ]; then
  $ADB shell 'mkdir -p /mnt/gbmeta; mountpoint -q /mnt/gbmeta || mount -t ext4 -o ro /dev/block/by-name/metadata /mnt/gbmeta' >/dev/null 2>&1
  $ADB pull /mnt/gbmeta/gbos-bootlog "$OUT/gbos-bootlog" >/dev/null 2>&1
  $ADB shell 'umount /mnt/gbmeta' >/dev/null 2>&1
else
  $ADB pull /metadata/gbos-bootlog "$OUT/gbos-bootlog" >/dev/null 2>&1
fi
if [ "$DURUM" = "device" ]; then
  # Normal acilis: sistem ozelliklerini, logcat ve SELinux redlerini topla
  sleep 20
  $ADB shell 'getprop' > "$OUT/getprop.txt" 2>&1
  $ADB logcat -d -b all > "$OUT/logcat-all.txt" 2>&1
  $ADB shell 'dmesg | grep -iE "avc|init:|denied"' > "$OUT/dmesg-init-avc.txt" 2>&1
  $ADB shell 'getprop sys.boot_completed; getprop ro.zygote; getprop ro.product.cpu.abilist; getprop init.svc.zygote; getprop init.svc.surfaceflinger; getprop bpf.progs_loaded' > "$OUT/ozet.txt" 2>&1
fi
echo "[+] Loglar: $OUT"
ls -la "$OUT"
