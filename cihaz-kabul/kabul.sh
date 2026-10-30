#!/bin/bash
# Googlebook OS (SM-T870): acceptance checks that need no touch and no unlocked screen.
# Run from the computer after a new image has booted (Android, adb root available):
#
#   T870-araclar/cihaz-kabul/kabul.sh            # evidence -> cihaz-kabul/kanit/<date-time>/
#
# It collects the evidence the module checklists ask for (selinux/cihaz-testi.md 1-6 and 8,
# mica-servisleri/CIHAZ-TESTI.md 1-4 and 6, terminal/CIHAZ-TESTI.md A and 11-12 when Linux is
# installed), the feature set (every vendor-declared feature available, boot image rebuilt from
# the patched framework.jar, Find Hub: device_admin + Play services Find My Device module)
# plus the media path (codec2 hardware encoder through screenrecord, audio playback
# through a silent AudioTrack), and prints one line per check: OK / HATA / BAK (needs a human
# look). Everything it starts is undone again (lazy services stop by themselves, the screen goes
# back to sleep, temporary files on the tablet are removed). Exit status: number of HATA lines.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SERIAL=${ANDROID_SERIAL:-$(python3 -I "$HERE/../t870_cihaz.py")} || exit 1
OUT=${1:-$HERE/kanit/$(date +%Y%m%d-%H%M)}
SDK=${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}
mkdir -p "$OUT"
A() { adb -s "$SERIAL" "$@"; }
SH() { adb -s "$SERIAL" shell "$@" 2>&1 | tr -d '\r'; }
FAILS=0
result() {  # $1 OK|HATA|BAK, $2 check, $3 detail
    printf '%-4s %-38s %s\n' "$1" "$2" "$3" | tee -a "$OUT/OZET.txt"
    if [ "$1" = HATA ]; then FAILS=$((FAILS + 1)); fi
    return 0
}

[ "$(adb -s "$SERIAL" get-state 2>/dev/null)" = device ] || { echo "tablet is not in Android (adb device)"; exit 100; }
A root >/dev/null 2>&1; sleep 2
A logcat -G 16M >/dev/null 2>&1

# --- system ---------------------------------------------------------------------------------
SH 'getprop ro.build.fingerprint; getprop sys.boot_completed; getenforce; df -k /metadata; ls -l /metadata/gbos-bootlog; head -8 /system/etc/ld.config.32' > "$OUT/sistem.txt"
[ "$(SH getenforce)" = Enforcing ] && result OK selinux-kip Enforcing || result HATA selinux-kip "$(SH getenforce)"
[ "$(SH getprop sys.boot_completed)" = 1 ] && result OK acilis "boot_completed=1" || result HATA acilis "boot_completed!=1"
used=$(SH "df -k /metadata | tail -1" | awk '{print $5}' | tr -d %)
[ -n "$used" ] && [ "$used" -lt 60 ] && result OK metadata-doluluk "%$used" || result HATA metadata-doluluk "%$used"
grep -q "^additional.namespaces = system$" "$OUT/sistem.txt" && result OK ld.config.32 "yalıtılmış vendor ad alanı" || result HATA ld.config.32 "eski düz yapılandırma"

SH 'ls -Z /system/bin/gbos-vpd-executor /system/bin/gbos-linuxd /system/etc/ld.config.32 /system/lib/bootstrap/libc.so /product/etc/security/adb_keys /system/lib/gbos-vendor-override/libbinder.so' > "$OUT/etiketler.txt"
for pair in "gbos-vpd-executor:gbos_vpd_executor_exec" "gbos-linuxd:gbos_linuxd_exec" "ld.config.32:system_linker_config_file" "bootstrap/libc.so:system_lib_file" "adb_keys:adb_keys_file" "gbos-vendor-override/libbinder.so:system_lib_file"; do
    f=${pair%%:*}; l=${pair##*:}
    grep -q "$l.* .*$f" "$OUT/etiketler.txt" && result OK "etiket $f" "$l" || result HATA "etiket $f" "$(grep "$f" "$OUT/etiketler.txt" | awk '{print $1}')"
done

# --- SELinux checklist 1-6, 8 (selinux/cihaz-testi.md) ----------------------------------------
SH 'service list' | grep -E 'desktop_net|desktop_data_migration|desktop_diagnostics|IDesktopServiceabilityService|IDesktopSettingHealthService|IVideoChatManager|snapshot\.ISnapshot|IDrmNetworkService|IAqueduct|IFace' > "$OUT/servisler.txt"
n=$(wc -l < "$OUT/servisler.txt"); [ "$n" -ge 10 ] && result OK masaustu-servisleri "$n kayıtlı" || result HATA masaustu-servisleri "$n kayıtlı (≥10 beklenir)"
SH 'ps -AZ' > "$OUT/ps.txt"
SH 'ls -l /system/etc/init/gbos-selinux.rc' > "$OUT/selinux-rc.txt"
grep -q "No such file" "$OUT/selinux-rc.txt" && result OK gbos-selinux.rc "imajda yok" || result HATA gbos-selinux.rc "imajda var"
{ SH id; SH 'cmd package list packages | head -2'; SH 'dumpsys activity processes | head -3'; SH 'am get-current-user'; } > "$OUT/root.txt"
# Googlebook OS runs a headless system user: after a reboot user 0 shows the sign-in screen and
# the person's apps live in their own user. Package checks look at that user; checks that need
# the person's session say BAK until they sign in (24 Oct 2026: two false HATA at the sign-in screen)
KISI=$(SH 'am get-current-user'); OTURUM=1
if [ "$(SH 'cmd user is-headless-system-user-mode')" = true ] && [ "$KISI" = 0 ]; then
    OTURUM=0
    KISI=$(SH 'pm list users' | sed -n 's/.*UserInfo{\([1-9][0-9]*\):.*/\1/p' | head -1)
fi
grep -q "context=u:r:su:s0" "$OUT/root.txt" && result OK adb-root "su alanı, cmd/dumpsys yanıt verdi" || result HATA adb-root "$(head -1 "$OUT/root.txt")"

# --- VPD / GSC (mica-servisleri/CIHAZ-TESTI.md) -------------------------------------------------
S=vendor.google.desktop.vpd_executor.IVpdExecutor/default
{ SH 'pidof udsattestation'; echo "--"; SH "setprop ctl.interface_start aidl/$S"; sleep 2
  SH "service call $S 1 i32 0"; SH "service call $S 2 i32 0 s16 ubind_attribute"; SH 'pidof gbos-vpd-executor'
  SH 'ls -ldZ /data/misc/gbos_vpd'; SH 'logcat -d -s gbos-vpd-executor | tail -5'; } > "$OUT/vpd.txt"
grep -q "^--$" "$OUT/vpd.txt" && [ "$(head -1 "$OUT/vpd.txt")" = "--" ] && result OK gsc-udsattestation "çalışmıyor" || result HATA gsc-udsattestation "hâlâ çalışıyor"
# service call prints the parcel as hex words plus a quoted text column split over offset lines;
# joining the quoted parts and dropping the dots of the UTF-16 padding gives the strings back.
vpd_text=$(grep -o "'[^']*'" "$OUT/vpd.txt" | tr -d "'.\n")
serialno=$(SH getprop ro.serialno)
case "$vpd_text" in
    *"$serialno"*SM-T870*|*SM-T870*"$serialno"*) result OK vpd-ro "seri numarası ve model döndü" ;;
    *) result HATA vpd-ro "RO yanıtında seri/model yok (vpd.txt)" ;;
esac
grep -q gbos_vpd_data_file "$OUT/vpd.txt" && result OK vpd-rw-dizin "gbos_vpd_data_file" || result HATA vpd-rw-dizin "etiket/dizin yok"
sleep 3
w=$(SH 'logcat -d' | grep -c -E "Waited one second for (vendor.google.desktop.vpd_executor|android.system.desktop.security.gsc)")
[ "$w" -eq 0 ] && result OK servis-bekleme "VPD/GSC için bekleyen yok" || result HATA servis-bekleme "$w 'Waited one second' satırı"

# --- Face (Enforcing) -----------------------------------------------------------------------
SH 'dumpsys face' > "$OUT/face.txt"
f=$(grep -c "Donanım kullanılamıyor\|HW_UNAVAILABLE\|hardware unavailable" "$OUT/face.txt")
[ "$f" -eq 0 ] && result BAK yuz "HAL hatası yok; kilit ekranında yüzle açma Kadir'le" || result HATA yuz "$f 'donanım kullanılamıyor' (dumpsys face)"

# --- Media: codec2 hardware encoder + audio playback (silent) -----------------------------------
A logcat -c
SH 'input keyevent KEYCODE_WAKEUP' >/dev/null; sleep 1
SH 'screenrecord --time-limit 4 /data/local/tmp/gbos-kabul.mp4' > "$OUT/screenrecord.txt"
SH 'input keyevent KEYCODE_SLEEP' >/dev/null
A pull /data/local/tmp/gbos-kabul.mp4 "$OUT/screenrecord.mp4" >/dev/null 2>&1; SH 'rm -f /data/local/tmp/gbos-kabul.mp4' >/dev/null
frames=$(ffprobe -v error -select_streams v:0 -show_entries stream=nb_frames -of csv=p=0 "$OUT/screenrecord.mp4" 2>/dev/null)
# screenrecord encodes only when the screen changes (a still screen gives one frame), so this only
# proves the 32-bit codec2 path starts and writes H.264; sustained 30 fps encoding is measured by
# the camera checks below (KameraAkis counts encoder output frames).
codec=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "$OUT/screenrecord.mp4" 2>/dev/null)
[ "$codec" = h264 ] && [ -n "$frames" ] && [ "$frames" -ge 1 ] && result OK codec2-kodlayici "h264, $frames kare (ekran değiştikçe)" \
    || result HATA codec2-kodlayici "$(tail -1 "$OUT/screenrecord.txt")"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
if javac --release 17 -cp "$SDK/platforms/android-37.0/android.jar" -d "$TMP" "$HERE/SessizSes.java" 2>"$OUT/ses-derleme.txt" \
   && "$SDK/build-tools/37.0.0/d8" --min-api 34 --output "$TMP" "$TMP/SessizSes.class" 2>>"$OUT/ses-derleme.txt"; then
    A push "$TMP/classes.dex" /data/local/tmp/gbos-sessiz.dex >/dev/null
    SH 'CLASSPATH=/data/local/tmp/gbos-sessiz.dex app_process /system/bin SessizSes; echo rc=$?' > "$OUT/ses.txt"
    SH 'rm -f /data/local/tmp/gbos-sessiz.dex' >/dev/null
    grep -q "rc=0" "$OUT/ses.txt" && result OK ses-calma "$(head -1 "$OUT/ses.txt")" || result HATA ses-calma "$(head -1 "$OUT/ses.txt")"
else
    result HATA ses-calma "SessizSes derlenemedi (ses-derleme.txt)"
fi
SH 'logcat -d -b all' > "$OUT/medya-logcat.txt"
m=$(grep -c -E "blocked syscall|component .* died|Mixing copies of libbinder" "$OUT/medya-logcat.txt")
[ "$m" -eq 0 ] && result OK medya-hatalari "seccomp/binder hatası yok" || result HATA medya-hatalari "$m satır (medya-logcat.txt)"
SH 'lshal' | grep -E "drm|cas" > "$OUT/drm-cas.txt"
grep -q "IDrmFactory/widevine" "$OUT/drm-cas.txt" && result OK widevine-hal "kayıtlı" || result HATA widevine-hal "yok"

# --- Features: everything the tablet's vendor declares is available (apply_patches.py
# reconcile_features + patch_ro_system_features), except the decided-off managed_users ------------
SH 'pm list features' | sed -n 's/^feature:\([^=]*\).*/\1/p' | sort > "$OUT/ozellikler-etkin.txt"
SH 'cat /vendor/etc/permissions/*.xml' | python3 -I -c '
import re, sys
text = re.sub(r"<!--.*?-->", "", sys.stdin.read(), flags=re.S)
print("\n".join(sorted(set(re.findall(r"<feature\s+name=\"([^\"]+)\"", text)))))' > "$OUT/ozellikler-vendor.txt"
eksik=$(comm -23 "$OUT/ozellikler-vendor.txt" "$OUT/ozellikler-etkin.txt" | grep -vx android.software.managed_users | tr '\n' ' ')
[ -z "$eksik" ] && result OK vendor-ozellikleri "$(wc -l < "$OUT/ozellikler-vendor.txt") bildirimin hepsi etkin" \
    || result HATA vendor-ozellikleri "etkin değil: $eksik"
# odrefresh compiles one primary boot.art on /data (framework included); none may come from /system
SH 'cat /proc/$(pidof zygote64)/maps' | grep -o '/[^ ]*/boot[^ /]*\.art' | sort -u > "$OUT/onyukleme-imaji.txt"
grep -q "^/data/misc/apexdata/com.android.art/dalvik-cache/arm64/boot.art$" "$OUT/onyukleme-imaji.txt" \
    && ! grep -q "^/system/" "$OUT/onyukleme-imaji.txt" \
    && result OK onyukleme-imaji "yamalı framework.jar'dan /data'da derlendi" \
    || result HATA onyukleme-imaji "$(tr '\n' ' ' < "$OUT/onyukleme-imaji.txt")"
SH 'service list' | grep -q -E "^[0-9]+\s+wifiaware:" && result OK wifi-aware "servis yayımlandı" || result HATA wifi-aware "servis yok"
# traced died on the vsock relay port (no AF_VSOCK in the 4.19 kernel) until apply_patches dropped
# traced.relay_producer_port from product build.prop; it must stay up and init must not restart it
traced_kip=$(SH 'getprop init.svc.traced'); traced_port=$(SH 'getprop traced.relay_producer_port')
[ "$traced_kip" = running ] && [ -z "$traced_port" ] && result OK traced "çalışıyor, vsock aktarma kapalı" \
    || result HATA traced "init.svc.traced=$traced_kip relay_port=${traced_port:-boş}"

# --- Camera stream sets (KameraAkis.java): preview, hardware H.264 encoder surface, still ------
# Preview+video and preview+still must work on both cameras. Preview+video+JPEG put the
# LineageOS/Samsung CamX HAL into its "Video With Liveshot" usecase, which holds every video
# buffer and fills none (22 Oct 2026). Since 23 Oct the framework (yamalar/kamera_jpeg.py,
# gbos.CameraJpegShim in framework.jar) swaps that JPEG stream for YUV and encodes the JPEG
# itself, for every Camera2/CameraX client: the set must now record and deliver a still. The
# camera app additionally carries CameraX's JpegHalCorruptImageQuirk (apply_patches.patch_camera_app).
mkdir -p "$TMP/kamera"
if javac --release 17 -cp "$SDK/platforms/android-37.0/android.jar" -d "$TMP/kamera" "$HERE/KameraAkis.java" 2>"$OUT/kamera-derleme.txt" \
   && "$SDK/build-tools/37.0.0/d8" --min-api 34 --output "$TMP/kamera" "$TMP/kamera"/*.class 2>>"$OUT/kamera-derleme.txt"; then
    A push "$TMP/kamera/classes.dex" /data/local/tmp/gbos-kamera.dex >/dev/null
    SH 'am force-stop --user $(am get-current-user) com.google.android.desktop.camera' >/dev/null
    for c in "0 1920x1080 0" "0 3840x2160 0" "0 0x0 1" "1 1920x1080 0" "1 0x0 1" "0 1920x1080 1" "0 1920x1080 yuv1920x1080" "0 3840x2160 yuv4128x3096" "1 1920x1080 yuv3264x2448"; do
        SH "CLASSPATH=/data/local/tmp/gbos-kamera.dex app_process /system/bin KameraAkis $c 4; echo rc=\$?" | grep -E "^kamera=|^rc=" | tr '\n' ' '
        echo
    done > "$OUT/kamera-akislari.txt"
    SH 'rm -f /data/local/tmp/gbos-kamera.dex' >/dev/null
    # the framework shim logs every substitution and every still it encoded
    SH 'logcat -d' | grep -E "GbosCameraJpeg" > "$OUT/kamera-shim-logcat.txt"
    n=$(grep -c "now YUV stream" "$OUT/kamera-shim-logcat.txt"); m=$(grep -c "GbosCameraJpeg: still " "$OUT/kamera-shim-logcat.txt")
    [ "$n" -ge 1 ] && [ "$m" -ge 1 ] && result OK kamera-shim "$n akış değişimi, $m yazılımsal JPEG (kamera-shim-logcat.txt)" \
        || result HATA kamera-shim "$n akış değişimi, $m yazılımsal JPEG: framework shim devrede değil"
    while read -r line; do
        rc=$(printf '%s' "$line" | sed -n 's/.*rc=\([0-9]*\).*/\1/p')
        sonuc=${line%% rc=*}
        ad=$(printf '%s' "$sonuc" | awk '{print $1, $2, $3}')
        if [ "$rc" = 0 ]; then
            result OK "$ad" "${sonuc#* * * }"
        else
            result HATA "$ad" "${sonuc:-sonuç yok}"
        fi
    done < "$OUT/kamera-akislari.txt"
else
    result HATA kamera-akislari "KameraAkis derlenemedi (kamera-derleme.txt)"
fi

# --- The camera app as the user uses it (kamera-uygulama.sh: touch-driven, needs the screen
# unlockable without a credential; it says BAK itself when it cannot run) ------------------------
# The camera app is Google's, updated by Play (either copy is fine: the fix lives in framework.jar)
kamera_pkg=$(SH "pm path --user ${KISI:-0} com.google.android.desktop.camera" | head -1)
kamera_imza=$(SH 'dumpsys package com.google.android.desktop.camera' | grep -oE "signatures:\[[0-9a-f]+\]" | head -1)
case "$kamera_pkg" in
    *"/product/app/AL_Camera/"*) result OK kamera-uygulama-paketi "imajdaki Google sürümü, $kamera_imza" ;;
    *"/data/app/"*) result OK kamera-uygulama-paketi "Play güncellemesi, $kamera_imza: $kamera_pkg" ;;
    *) result HATA kamera-uygulama-paketi "kurulu değil: $kamera_pkg" ;;
esac
if [ "$OTURUM" = 1 ]; then
    "$HERE/kamera-uygulama.sh" "$OUT/kamera-uygulama" | grep -E "^(OK|HATA|BAK) " | tee -a "$OUT/OZET.txt" | grep -q "^HATA " && FAILS=$((FAILS + $(grep -c "^HATA " "$OUT/kamera-uygulama/OZET.txt")))
else
    result BAK kamera-uygulama "giriş ekranında, kullanıcı oturumu açık değil; giriş yapılınca kamera-uygulama.sh"
fi

# --- Find Hub: Play services keeps its Find My Device module off without device_admin -----------
# --query-flags 786432 (MATCH_DIRECT_BOOT_AWARE | MATCH_DIRECT_BOOT_UNAWARE): works right after a
# reboot while User 10 is still in RUNNING_LOCKED before the lockscreen credential is entered.
{ SH 'pm list features'; SH 'dumpsys device_policy | grep mHasFeature'
  SH 'cmd package query-activities --user 10 --components --query-flags 786432 -a com.google.android.gms.settings.FIND_MY_DEVICE_SETTINGS'
  SH 'cmd package query-services --user 10 --components --query-flags 786432 -n com.google.android.gms/.mdm.services.RingService'; } > "$OUT/find-hub.txt"
grep -q "^feature:android.software.device_admin$" "$OUT/find-hub.txt" && grep -q "mHasFeature=true" "$OUT/find-hub.txt" \
    && result OK device-admin "özellik var, DevicePolicyManager açık" || result HATA device-admin "özellik yok (find-hub.txt)"
# On a Googlebook (com.google.desktop.gms) Play services enables Find My Device only when server
# flag 45772135 is off, or when flag 45853812 is on and the Bluetooth finder (powered-off finding,
# persist.bluetooth.finder.supported, SELinux bluetooth_finder_prop) exists: FmdModuleInitIntentOperation -> dxiv.f/c/g, GMS 26.42.
SH "cat \"/data/user_de/${KISI:-0}/com.google.android.gms/files/phenotype/shared/com.google.android.gms.security.fmd#com.google.android.gms.pb\"" \
    | protoc --decode_raw > "$OUT/fmd-bayraklari.txt" 2>&1
bayrak() { python3 -I -c 'import re,sys; t=open(sys.argv[1]).read(); m=re.search(r"1: \"%s\"\s*\n\s*3: (\d)" % sys.argv[2], t); print(m.group(1) if m else "yok")' "$OUT/fmd-bayraklari.txt" "$1"; }
masaustu_kapisi=$(bayrak 45772135); googlebook_bayragi=$(bayrak 45853812); finder=$(SH 'getprop persist.bluetooth.finder.supported')
if grep -q "com.google.android.gms/.mdm.settings" "$OUT/find-hub.txt" && grep -q "com.google.android.gms/.mdm.services.RingService" "$OUT/find-hub.txt"; then
    result OK find-hub "GMS Find My Device ve RingService (Ses çal) açık"
elif grep -q "com.google.android.gms/.mdm.settings" "$OUT/find-hub.txt"; then
    result BAK find-hub "GMS Find My Device açık; RingService kontrolü (find-hub.txt)"
elif [ "$masaustu_kapisi" = 1 ] && { [ "$googlebook_bayragi" != 1 ] || [ "$finder" != true ]; }; then
    result BAK find-hub "Google politikası: Googlebook'ta FMD kapalı (45772135=1, 45853812=$googlebook_bayragi, bluetooth.finder=${finder:-yok})"
else
    result HATA find-hub "GMS koşulları sağlıyor ama mdm modülü kapalı (fmd-bayraklari.txt, find-hub.txt)"
fi

# --- Googlebook masaüstü AI ve bağlamsal bileşenler (ContextualTools, ALAI/Loam, ContextualCursor, ASI, GSA) -
{ SH 'cmd package query-activities --user 10 --components --query-flags 786432 -a android.intent.action.PROCESS_TEXT -t text/plain'
  SH 'cmd package query-receivers --user 10 --components --query-flags 786432 -n com.google.android.desktop.alai/.loam.app.widget.SmartCollectionWidgetProvider'
  SH 'cmd package query-services --user 10 --components --query-flags 786432 -n com.google.android.as/com.google.android.apps.miphone.aiai.safecomms.featurestatusservice.FeatureStatusService'
  SH 'cmd package query-receivers --user 10 --components --query-flags 786432 -n com.google.android.googlequicksearchbox/com.google.android.apps.search.assistant.verticals.snapshot.widgets.weather.WeatherWidget_Receiver'
  SH 'settings get --user 10 secure use_contextual_cursor'; } > "$OUT/masaustu-ai.txt"
grep -q "com.google.android.desktop.contextualtools/.entrypoints.ProcessTextGateway" "$OUT/masaustu-ai.txt" \
    && grep -q "com.google.android.desktop.alai/.loam.app.widget.SmartCollectionWidgetProvider" "$OUT/masaustu-ai.txt" \
    && grep -q "com.google.android.as/com.google.android.apps.miphone.aiai.safecomms.featurestatusservice.FeatureStatusService" "$OUT/masaustu-ai.txt" \
    && grep -q "com.google.android.googlequicksearchbox/com.google.android.apps.search.assistant.verticals.snapshot.widgets.weather.WeatherWidget_Receiver" "$OUT/masaustu-ai.txt" \
    && [ "$(tail -1 "$OUT/masaustu-ai.txt" | tr -d '\r')" = 1 ] \
    && result OK masaustu-ai "Quick Look, ALAI/Loam, ContextualCursor, ASI SafeComms ve GSA Weather açık" \
    || result HATA masaustu-ai "Masaüstü AI bileşenlerinden biri kapalı (masaustu-ai.txt)"

# --- Terminal (terminal/CIHAZ-TESTI.md A, 11)--------------------------------------------------
SH 'ps -AZ | grep gbos-linuxd; ls -laZ /dev/socket/gbos_linux /data/gbos-linux; gbos-linux status' > "$OUT/terminal.txt"
grep -q "u:r:gbos_linuxd:s0" "$OUT/terminal.txt" && result OK terminal-servis "gbos_linuxd alanında" || result HATA terminal-servis "çalışmıyor"
SH 'pm list packages -f --user 10 com.gbos.terminal' >> "$OUT/terminal.txt"
grep -q "priv-app/Terminal/Terminal.apk" "$OUT/terminal.txt" && result OK terminal-uygulama "kurulu" || result HATA terminal-uygulama "yok"
if grep -q "state=installed" "$OUT/terminal.txt"; then
    SH 'gbos-linux run -- sh -c "uname -m; cat /etc/debian_version; getent hosts deb.debian.org | head -1"; echo rc=$?' > "$OUT/terminal-run.txt"
    grep -q "rc=0" "$OUT/terminal-run.txt" && result OK terminal-run "$(tr '\n' ' ' < "$OUT/terminal-run.txt")" || result HATA terminal-run "$(tail -1 "$OUT/terminal-run.txt")"
else
    result BAK terminal-run "Linux henüz kurulmadı (terminal/CIHAZ-TESTI.md B5, dokunarak)"
fi

# --- Denials: the raw lines go to avc-ham.txt, which yamalar/selinux.py dogrula replays against
# our, mica's and LineageOS's policy (KANIT_GLOBS); redler.txt is the human summary ---------------
{ SH 'dmesg'; cat "$OUT/medya-logcat.txt"; } | grep -E "avc: +denied" | grep "permissive=0" > "$OUT/avc-ham.txt"
grep -E "avc: +denied" "$OUT/avc-ham.txt" \
  | sed -E 's/.*avc: +denied +\{ ([^}]*)\}.*scontext=([^ ]*) tcontext=([^ ]*) tclass=([^ ]*).*/\2 \3 \4 {\1}/; s/:c[0-9,c]+//g' \
  | sort | uniq -c | sort -rn > "$OUT/redler.txt"
result BAK selinux-redler "$(wc -l < "$OUT/redler.txt") farklı red (redler.txt → selinux dogrula)"

echo "Kanıt: $OUT" | tee -a "$OUT/OZET.txt"
exit $FAILS
