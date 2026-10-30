#!/bin/bash
# Googlebook OS (SM-T870): HWUI renderer acceptance over everyday apps.
#
#   T870-araclar/cihaz-kabul/cizim-kabul.sh [OUT]   # evidence -> cihaz-kabul/kanit/<date-time>-cizim/
#
# Each app is started cold, left to settle, scrolled the same way six times through real input
# events, and measured: the HWUI pipeline it really ran (gfxinfo "Pipeline="), frames, janky share,
# 50th/90th percentile, whether its process is still alive, its crashes and any Vulkan/GL error it
# logged, and a screenshot for a human look at the rendering. One line per app: OK / HATA (process
# died, crash, renderer error, or a different pipeline than the device should run) / BAK (no
# frames to measure). Exit status: number of HATA lines. The device must be awake and unlocked;
# apps are stopped again afterwards. The expected pipeline comes from what the device is set to
# (debug.hwui.renderer, else ro.hwui.use_vulkan), so the same run serves OpenGL and Vulkan.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SERIAL=${ANDROID_SERIAL:-$(python3 -I "$HERE/../t870_cihaz.py")} || exit 1
OUT=${1:-$HERE/kanit/$(date +%Y%m%d-%H%M)-cizim}
mkdir -p "$OUT"
A() { adb -s "$SERIAL" "$@"; }
SH() { adb -s "$SERIAL" shell "$@" 2>&1 | tr -d '\r'; }
FAILS=0
result() {
    printf '%-4s %-44s %s\n' "$1" "$2" "$3" | tee -a "$OUT/OZET.txt"
    if [ "$1" = HATA ]; then FAILS=$((FAILS + 1)); fi
    return 0
}

[ "$(A get-state 2>/dev/null)" = device ] || { echo "tablet is not in Android (adb device)"; exit 100; }
A root >/dev/null 2>&1; sleep 2
USER_ID=$(SH am get-current-user)
# headless system user: user 0 is the sign-in screen, the apps to measure are not there
if [ "$USER_ID" = 0 ] && [ "$(SH cmd user is-headless-system-user-mode)" = true ]; then
    echo "BAK  oturum                                 giriş ekranında; kullanıcı giriş yapınca çalıştırılır"; exit 0
fi
renderer=$(SH getprop debug.hwui.renderer)
[ -z "$renderer" ] && { [ "$(SH getprop ro.hwui.use_vulkan)" = true ] && renderer=skiavk || renderer=skiagl; }
case $renderer in skiavk) want="Skia (Vulkan)";; *) want="Skia (OpenGL)";; esac
{ echo "renderer=$renderer beklenen=\"$want\""; SH getprop ro.build.fingerprint; } > "$OUT/ortam.txt"

APPS=${APPS:-"com.android.settings com.android.chrome com.google.android.youtube com.google.android.gm
com.google.android.apps.photos com.google.android.apps.maps com.android.vending com.google.android.apps.nbu.files
com.whatsapp org.telegram.messenger com.instagram.android com.google.android.apps.docs com.google.android.keep
com.google.android.calendar com.google.android.apps.messaging com.google.android.googlequicksearchbox
com.google.android.apps.youtube.music com.google.android.desktop.camera"}

SH input keyevent WAKEUP >/dev/null
for pkg in $APPS; do
    SH "pm path --user $USER_ID $pkg" | grep -q '^package:' || { result BAK "$pkg" "kurulu değil"; continue; }
    SH "am force-stop --user $USER_ID $pkg" >/dev/null
    A logcat -c
    SH "monkey --pct-syskeys 0 -p $pkg -c android.intent.category.LAUNCHER 1" >/dev/null
    sleep 6
    # The window that came up may belong to another package (mica opens YouTube and Gmail as
    # Chrome web apps): measure and scroll the focused window, wherever it is on the desktop.
    focus=$(SH "dumpsys window" | grep -m1 'mCurrentFocus=' | grep -o 'u[0-9]* [^ /}]*/' | awk '{print $2}' | tr -d /)
    win=${focus:-$pkg}
    frame=$(SH "dumpsys window windows" | grep -A60 "Window{[0-9a-f]* u[0-9]* $win/" | grep -m1 -o ' frame=\[[0-9]*,[0-9]*\]\[[0-9]*,[0-9]*\]' | tr -c '0-9\n' ' ')
    read -r l t r b <<<"${frame:-1000 400 1560 1300}"
    cx=$(((l + r) / 2)); y1=$((t + (b - t) * 3 / 4)); y2=$((t + (b - t) / 3))
    # The app's main process must survive; helper processes (Chrome renderers) come and go.
    procs() { SH "ps -A -o PID,NAME" | awk -v p="$win" '$2 == p {print $1}' | sort | tr '\n' ' '; }
    pids=$(procs)
    SH "dumpsys gfxinfo $win reset" >/dev/null
    for _ in 1 2 3; do SH "input swipe $cx $y1 $cx $y2 250" >/dev/null; sleep 0.6; SH "input swipe $cx $y2 $cx $y1 250" >/dev/null; sleep 0.6; done
    sleep 1
    SH "dumpsys gfxinfo $win" > "$OUT/$pkg-gfxinfo.txt"
    A exec-out screencap -p > "$OUT/$pkg.png"
    A logcat -d -b main,system,crash > "$OUT/$pkg-logcat.txt"
    pids2=$(procs)
    pipe=$(grep -m1 -o 'Pipeline=.*' "$OUT/$pkg-gfxinfo.txt" | cut -d= -f2-)
    frames=$(grep -m1 'Total frames rendered' "$OUT/$pkg-gfxinfo.txt" | awk '{print $NF}')
    jank=$(grep -m1 '^Janky frames:' "$OUT/$pkg-gfxinfo.txt" | grep -o '([0-9.]*%)')
    p50=$(grep -m1 '^50th percentile' "$OUT/$pkg-gfxinfo.txt" | awk '{print $NF}')
    p90=$(grep -m1 '^90th percentile' "$OUT/$pkg-gfxinfo.txt" | awk '{print $NF}')
    crash=$(grep -cE "FATAL EXCEPTION|Fatal signal|>>> $win[: ]" "$OUT/$pkg-logcat.txt")
    rerr=$(grep -iE "vulkan.*(fail|error|lost)|VK_ERROR|DEVICE_LOST|GrVk.*(fail|error)|EGL_BAD|GL error" "$OUT/$pkg-logcat.txt" | grep -c "$win\|HWUI\|OpenGLRenderer\|vulkan")
    detail="pencere=$win pipeline=${pipe:-?} kare=${frames:-0} jank=${jank:-?} p50=${p50:-?} p90=${p90:-?}"
    if [ -n "$pids" ] && [ "$pids" != "$pids2" ] || [ "$crash" -gt 0 ]; then
        result HATA "$pkg" "$detail süreç=[${pids}]->[${pids2}] çökme=$crash"
    elif [ "$rerr" -gt 0 ]; then
        result HATA "$pkg" "$detail çizici-hatası=$rerr"
    elif [ -z "$pipe" ] || [ "${frames:-0}" -eq 0 ]; then
        result BAK "$pkg" "$detail (ölçülecek kare yok)"
    elif [ "$pipe" != "$want" ]; then
        result HATA "$pkg" "$detail beklenen=$want"
    else
        result OK "$pkg" "$detail"
    fi
    [ "$win" != "$pkg" ] && SH "am force-stop --user $USER_ID $win" >/dev/null
    SH "am force-stop --user $USER_ID $pkg" >/dev/null
done

# SystemUI and the launcher draw the shell itself; they are not restarted, only checked.
for pkg in com.android.systemui com.google.android.apps.nexuslauncher; do
    pipe=$(SH "dumpsys gfxinfo $pkg" | grep -m1 -o 'Pipeline=.*' | cut -d= -f2-)
    [ "$pipe" = "$want" ] && result OK "$pkg" "pipeline=$pipe" || result HATA "$pkg" "pipeline=${pipe:-?} beklenen=$want"
done
SH "logcat -d -b crash" > "$OUT/crash-tampon.txt"
echo "HATA=$FAILS kanıt: $OUT"
exit "$FAILS"
