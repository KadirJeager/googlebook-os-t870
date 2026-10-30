#!/bin/bash
# Googlebook OS (SM-T870): the camera app as the user uses it, driven by touch (input tap on the
# buttons uiautomator reports by their accessibility names). Needs the screen unlockable without a
# credential (wm dismiss-keyguard); kabul.sh calls it and reports BAK when it cannot run.
#
#   cihaz-kabul/kamera-uygulama.sh [evidence dir]        # one OK/HATA line per check, exit = HATA count
#
# Per camera (back, front): video mode, record ~6 s, take a photo while recording ("Fotoğraf çek"
# during the recording: this is the preview + encoder + JPEG set that froze the back camera until
# the CameraX JpegHalCorruptImageQuirk patch, apply_patches.patch_camera_app), stop, then photo
# mode, one photo. Every result is read where the user sees it: MediaStore rows added after the
# start (duration, size), the files themselves pulled and parsed (H.264 frames with ffprobe, JPEG
# size with Python). Files the test created are deleted from MediaStore again.
set -u
PKG=com.google.android.desktop.camera
HERE=$(cd "$(dirname "$0")" && pwd)
SERIAL=${ANDROID_SERIAL:-$(python3 -I "$HERE/../t870_cihaz.py")} || exit 1
OUT=${1:-$HERE/kanit/$(date +%Y%m%d-%H%M)/kamera-uygulama}
mkdir -p "$OUT"
SH() { adb -s "$SERIAL" shell "$@" 2>&1 | tr -d '\r'; }
FAILS=0
result() { printf '%-4s %-38s %s\n' "$1" "$2" "$3" | tee -a "$OUT/OZET.txt"; [ "$1" = HATA ] && FAILS=$((FAILS + 1)); return 0; }
USER_ID=$(SH 'am get-current-user')

dump() {  # $1 label -> prints "desc|x|y" per node that has an accessibility name
    SH 'uiautomator dump /data/local/tmp/gbos-ui.xml >/dev/null 2>&1; cat /data/local/tmp/gbos-ui.xml' \
        | tee "$OUT/ui-$1.xml" | python3 -I -c '
import re, sys
for n in re.finditer(r"<node[^>]*>", sys.stdin.read()):
    n = n.group(0)
    d = re.search(r"content-desc=\"([^\"]*)\"", n).group(1)
    b = re.search(r"bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"", n)
    if d and b:
        x1, y1, x2, y2 = map(int, b.groups())
        print(f"{d}|{(x1 + x2) // 2}|{(y1 + y2) // 2}")'
}
tap() {  # $1 label, $2 accessibility name (prefix match) -> taps it, 1 when absent
    local hit
    hit=$(dump "$1" | grep "^$2" | head -1) || true
    [ -n "$hit" ] || return 1
    SH "input tap $(echo "$hit" | cut -d'|' -f2) $(echo "$hit" | cut -d'|' -f3)" >/dev/null
}
wait_for() {  # $1 label, $2 accessibility name, $3 seconds
    local i; for i in $(seq 1 "$3"); do dump "$1" | grep -q "^$2" && return 0; sleep 1; done; return 1
}
media() {  # $1 video|images $2 since-epoch -> rows added after $2 (id|name|duration|size|path)
    SH "content query --user $USER_ID --uri content://media/external/$1/media --projection _id:_display_name:duration:_size:date_added:_data --sort 'date_added DESC'" \
        | sed -n 's/^Row: [0-9]* _id=\([0-9]*\), _display_name=\([^,]*\), duration=\([^,]*\), _size=\([^,]*\), date_added=\([0-9]*\), _data=\(.*\)$/\5 \1|\2|\3|\4|\6/p' \
        | awk -v s="$2" '$1 >= s {print $2}'
}
pull() {  # $1 MediaStore _data path, $2 local file; another user's /storage/emulated is closed even to adb root
    local raw=${1/#\/storage\/emulated\//\/data\/media\/}
    adb -s "$SERIAL" pull "$raw" "$2" >/dev/null 2>&1 || adb -s "$SERIAL" pull "$1" "$2" >/dev/null 2>&1
}
cleanup_ids=()
finish() {
    for e in "${cleanup_ids[@]}"; do SH "content delete --user $USER_ID --uri content://media/external/${e%%:*}/media --where '_id=${e##*:}'" >/dev/null; done
    SH "am force-stop --user $USER_ID $PKG; rm -f /data/local/tmp/gbos-ui.xml" >/dev/null
}
trap finish EXIT

adb -s "$SERIAL" root >/dev/null 2>&1; sleep 1
SH 'input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard' >/dev/null; sleep 1
if SH 'dumpsys window' | grep -q "mDreamingLockscreen=true\|isKeyguardShowing=true"; then
    result BAK kamera-uygulama "ekran kilidi açılamadı (kimlik bilgisi istiyor); dokunarak sınanmalı"
    exit 0
fi
SH "am force-stop --user $USER_ID $PKG" >/dev/null
SH "am start --user $USER_ID -n $PKG/.main.Launcher" >/dev/null
if ! wait_for acilis "Kamera önizlemesi" 15; then
    result HATA kamera-uygulama "uygulama açılmadı (ui-acilis.xml)"; exit 1
fi
SH 'logcat -c' >/dev/null

for kamera in arka on; do
    ad=$([ "$kamera" = arka ] && echo "arka" || echo "ön")
    # the switch button names the camera in use; one tap, then wait for the name to change (a
    # second blind tap would switch back)
    if ! dump "$kamera-kamera" | grep -q "^Kamera değiştir, $ad kamera"; then
        tap "$kamera-degistir" "Kamera değiştir" || true
        wait_for "$kamera-kamera" "Kamera değiştir, $ad kamera" 10 || { result HATA "kamera-$kamera" "kameraya geçilemedi (ui-$kamera-kamera.xml)"; continue; }
    fi

    # --- video mode: record, snapshot while recording, stop -------------------------------------
    tap "$kamera-mod" "Video Modu" || true; sleep 2
    baslangic=$(date +%s)
    if ! tap "$kamera-kayit" "Kaydı başlat"; then result HATA "video-$kamera" "Kaydı başlat düğmesi yok (ui-$kamera-kayit.xml)"; continue; fi
    sleep 3
    foto_sirasinda=0
    if tap "$kamera-kayitta-foto" "Fotoğraf çek"; then foto_sirasinda=1; fi
    sleep 3
    sayac=$(dump "$kamera-kayit-sonu" | grep -E '^[0-9]+ saniye' | head -1 | cut -d'|' -f1)
    tap "$kamera-durdur" "Kaydı durdur" || result HATA "video-$kamera" "Kaydı durdur düğmesi yok: kayıt başlamadı"
    sleep 4
    satir=$(media video "$baslangic" | head -1)
    if [ -n "$satir" ]; then
        IFS='|' read -r vid vad vsure vboyut vyol <<< "$satir"
        cleanup_ids+=("video:$vid")
        pull "$vyol" "$OUT/$kamera-$vad"
        kare=$(ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of csv=p=0 "$OUT/$kamera-$vad" 2>/dev/null)
        if [ "${vsure:-0}" -ge 4000 ] && [ "${kare:-0}" -ge 60 ]; then
            result OK "video-$kamera" "$vad: ${vsure} ms, $kare kare, $vboyut bayt (sayaç ${sayac:-?})"
        else
            result HATA "video-$kamera" "$vad: ${vsure:-?} ms, ${kare:-?} kare, ${vboyut:-?} bayt: kayıt dondu ya da boş"
        fi
    else
        result HATA "video-$kamera" "kayıt dosyası oluşmadı (MediaStore'da yeni video yok)"
    fi
    if [ "$foto_sirasinda" = 1 ]; then
        satir=$(media images "$baslangic" | head -1)
        if [ -n "$satir" ]; then
            IFS='|' read -r iid iad _ iboyut iyol <<< "$satir"; cleanup_ids+=("images:$iid")
            pull "$iyol" "$OUT/$kamera-kayitta-$iad"
            boyut=$(python3 -I -c 'import struct,sys
d=open(sys.argv[1],"rb").read(); i=2
while i<len(d)-9:
    if d[i]!=0xFF: break
    m=d[i+1]; l=struct.unpack(">H",d[i+2:i+4])[0]
    if m in (0xC0,0xC1,0xC2): print(struct.unpack(">H",d[i+7:i+9])[0],"x",struct.unpack(">H",d[i+5:i+7])[0]); break
    i+=2+l' "$OUT/$kamera-kayitta-$iad" 2>/dev/null | tr -d ' ')
            [ -n "$boyut" ] && result OK "kayitta-foto-$kamera" "$iad: $boyut, $iboyut bayt" || result HATA "kayitta-foto-$kamera" "$iad JPEG değil ya da boş"
        else
            result HATA "kayitta-foto-$kamera" "kayıt sırasında çekilen fotoğraf oluşmadı"
        fi
    else
        result BAK "kayitta-foto-$kamera" "kayıt sırasında Fotoğraf çek düğmesi görünmedi"
    fi

    # --- photo mode -----------------------------------------------------------------------------
    tap "$kamera-foto-mod" "Fotoğraf Modu" || true; sleep 2
    baslangic=$(date +%s)
    if tap "$kamera-foto" "Fotoğraf çek"; then
        sleep 4
        satir=$(media images "$baslangic" | head -1)
        if [ -n "$satir" ]; then
            IFS='|' read -r iid iad _ iboyut iyol <<< "$satir"; cleanup_ids+=("images:$iid")
            pull "$iyol" "$OUT/$kamera-$iad"
            [ -s "$OUT/$kamera-$iad" ] && [ "$(head -c 2 "$OUT/$kamera-$iad" | xxd -p)" = ffd8 ] \
                && result OK "foto-$kamera" "$iad: $iboyut bayt" || result HATA "foto-$kamera" "$iad JPEG değil ya da boş"
        else
            result HATA "foto-$kamera" "fotoğraf oluşmadı"
        fi
    else
        result HATA "foto-$kamera" "Fotoğraf çek düğmesi yok (ui-$kamera-foto.xml)"
    fi
done
SH 'logcat -d' | grep -E "CameraX|ImageCapture|Camera2CameraImpl|camera2|CameraDevice" > "$OUT/kamera-logcat.txt"
echo "Kanıt: $OUT" | tee -a "$OUT/OZET.txt"
exit $FAILS
