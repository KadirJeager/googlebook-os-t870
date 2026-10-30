#!/usr/bin/env bash
# Live measurement of one natural-orientation HAL layer on the tablet, without writing any
# partition (BENIOKU.md "Ölçüm"):
#
#   canli-deney.sh sensor|kamera KANIT_DIR
#
# The launcher and library from out/ run from /data/local/tests/product (a "product" section
# directory of the tablet's ld.config.txt, like /product) as root; the vendor service is stopped
# for the measurement and started again at the end, whatever happens in between. Readings:
#   sensor  araclar/sensor-olcum (NDK ASensorManager) with the vendor HAL (A), with the rebuilt HAL
#           (B) and with the vendor HAL again (C); dumpsys of the rebuilt HAL; its library map
#   kamera  dumpsys media.camera and araclar/kamera-olcum (NDK Camera2: open, stream, result pose)
#           with the vendor provider and with the rebuilt one
# Needs adb root (`adb shell id` uid 0) and ANDROID_SERIAL set to the tablet.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
layer="${1:?sensor|kamera}"
kanit="${2:?KANIT_DIR}"
mkdir -p "$kanit"
case "$layer" in
    sensor) name=android.hardware.sensors-service.samsung-multihal-gbos; vsvc=vendor.sensors-hal-multihal ;;
    kamera) name=android.hardware.camera.provider-service.samsung-gbos; vsvc=vendor.camera.provider ;;
    *) echo "unknown layer $layer" >&2; exit 2 ;;
esac
isvc="gbos.${vsvc#vendor.}"
if [[ "$(adb shell getprop init.svc.$isvc)" == running ]]; then
    echo "canli-deney.sh: $isvc zaten imajdan çalışıyor; canlı sistemde bu servis durdurulmaz (system_server çöker)." >&2
    exit 1
fi
[[ "$(adb shell id -u)" == 0 ]] || { echo "adb shell is not root" >&2; exit 1; }
T=/data/local/tests/product
adb shell "mkdir -p $T/bin/hw $T/lib64/gbos-dogal-yon"
adb push "$HERE/out/bin/hw/$name" "$T/bin/hw/$name" >/dev/null
adb push "$HERE/out/lib64/gbos-dogal-yon/$name.so" "$T/lib64/gbos-dogal-yon/$name.so" >/dev/null
adb shell "chmod 755 $T/bin/hw/$name"
adb shell "sha256sum $T/bin/hw/$name $T/lib64/gbos-dogal-yon/$name.so" > "$kanit/$layer-deney-dosyalari.txt"

restore() {
    # pidof matches the launcher's own name; a pattern over command lines would also match this shell
    adb shell "P=\$(pidof $name); [ -z \"\$P\" ] || kill \$P; start $vsvc; sleep 3; getprop init.svc.$vsvc" \
        | tee "$kanit/$layer-geri-donus.txt"
}
trap restore EXIT

if [[ "$layer" == sensor ]]; then
    NDK="${ANDROID_NDK:-${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}/ndk/30.0.16248370}"
    probe="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-sensor-olcum.XXXXXX")"
    "$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang" --target=aarch64-linux-android37 -O2 -Wall -Werror \
        -fPIE -pie -o "$probe/sensor-olcum" "$HERE/araclar/sensor-olcum.c" -landroid
    adb push "$probe/sensor-olcum" /data/local/tmp/gbos-sensor-olcum >/dev/null
    rm -rf "$probe"
    adb shell "chmod 755 /data/local/tmp/gbos-sensor-olcum; logcat -c"
    adb shell "echo '== A: vendor HAL'; /data/local/tmp/gbos-sensor-olcum 2;
        stop $vsvc; (nohup $T/bin/hw/$name >/dev/null 2>&1 &); sleep 5;
        echo '== B: gbos HAL'; /data/local/tmp/gbos-sensor-olcum 2" > "$kanit/sensor-olcum.txt"
    adb shell "dumpsys android.hardware.sensors.ISensors/default | head -24" > "$kanit/sensor-hal-dumpsys.txt"
    adb shell "grep -o '/[^ ]*\.so' /proc/\$(pidof $name)/maps | sort -u" > "$kanit/sensor-hal-maps.txt"
    restore
    trap - EXIT
    adb shell "grep -o '/[^ ]*\.so' /proc/\$(pidof android.hardware.sensors-service.samsung-multihal)/maps | sort -u" \
        > "$kanit/sensor-vendor-maps.txt"
    adb shell "echo '== C: vendor HAL'; /data/local/tmp/gbos-sensor-olcum 2" >> "$kanit/sensor-olcum.txt"
    adb shell "rm /data/local/tmp/gbos-sensor-olcum"
    adb shell "logcat -d | grep -E 'gbos-dogal-yon|natural orientation|SensorService.*(died|reconnect|handles)'" \
        > "$kanit/sensor-logcat.txt" || true
else
    NDK="${ANDROID_NDK:-${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}/ndk/30.0.16248370}"
    probe="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-kamera-olcum.XXXXXX")"
    # ABinderProcess_* (binder thread pool for the camera callbacks) is LLNDK, not NDK: platform
    # headers from the pinned frameworks/native, the device's own libbinder_ndk to link against.
    fn="$(python3 -I "$HERE/araclar/kaynak-getir.py" | sed -n 's/^frameworks_native\t//p')"
    python3 -I "$HERE/araclar/sistem-kutuphaneleri.py" "$probe/lib"
    "$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang" --target=aarch64-linux-android37 -O2 -Wall -Werror \
        -fPIE -pie -I "$fn/libs/binder/ndk/include_platform" -I "$fn/libs/binder/ndk/include_ndk" \
        -o "$probe/kamera-olcum" "$HERE/araclar/kamera-olcum.c" -lcamera2ndk -lmediandk "$probe/lib/libbinder_ndk.so"
    adb push "$probe/kamera-olcum" /data/local/tmp/gbos-kamera-olcum >/dev/null
    rm -rf "$probe"
    adb shell "chmod 755 /data/local/tmp/gbos-kamera-olcum; logcat -c"
    adb shell "dumpsys media.camera" > "$kanit/kamera-vendor-dumpsys.txt"
    adb shell "echo '== vendor provider'; /data/local/tmp/gbos-kamera-olcum 2" > "$kanit/kamera-olcum.txt"
    adb shell "stop $vsvc; (nohup $T/bin/hw/$name >/dev/null 2>&1 &); sleep 6; getprop init.svc.$vsvc"
    adb shell "dumpsys media.camera" > "$kanit/kamera-gbos-dumpsys.txt"
    adb shell "echo '== gbos provider'; /data/local/tmp/gbos-kamera-olcum 2" >> "$kanit/kamera-olcum.txt"
    adb shell "grep -o '/[^ ]*\.so' /proc/\$(pidof $name)/maps | sort -u" > "$kanit/kamera-hal-maps.txt"
    adb shell "logcat -d | grep -E 'gbos-dogal-yon|natural orientation|CameraProviderManager|CameraService.*provider'" \
        > "$kanit/kamera-logcat.txt" || true
    adb shell "rm /data/local/tmp/gbos-kamera-olcum"
fi
