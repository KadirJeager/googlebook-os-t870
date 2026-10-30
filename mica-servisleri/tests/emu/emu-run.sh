#!/usr/bin/env bash
# Runs inside the kaynak gate (see emu-test.sh): boots the test AVD, installs gbos-vpd-executor
# into its system partition the way the image does, exercises it and shuts the emulator down.
#
# The emulator differs from the tablet in two ways the test accounts for: it is x86_64 (the same
# sources are built for it against its own libbinder_ndk.so), and its SELinux policy has no
# gbos_vpd_executor domain, so the test copy of the rc runs the service as u:r:su:s0 in
# permissive mode. Everything else (lazy start through servicemanager, VINTF declaration, the
# Java client path, /data store, exit when idle, restart) is what the tablet runs.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$HERE/../.." && pwd)"
TOOLS_DIR="$(dirname "$SRC")"
: "${AVD:?}" "${PORT:?}" "${WORK_DIR:?}"
SERIAL="emulator-$PORT"
SDK="${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}"
BT="${ANDROID_BUILD_TOOLS:-$SDK/build-tools/37.0.0}"
# Hidden framework API (Binder stability, ServiceManager) from the Googlebook OS image
FRAMEWORK_JAR="$TOOLS_DIR/yuz-tanima/out/classpath/framework.jar"
A=(adb -s "$SERIAL")

EMU_PID=
stop_emulator() {
    [[ -n "$EMU_PID" ]] || return 0
    "${A[@]}" emu kill >/dev/null 2>&1 || true
    for _ in $(seq 60); do kill -0 "$EMU_PID" 2>/dev/null || return 0; sleep 1; done
    kill "$EMU_PID" 2>/dev/null || true
}
trap stop_emulator EXIT

emulator_alive() { [[ -n "$EMU_PID" ]] && kill -0 "$EMU_PID" 2>/dev/null; }

wait_boot() {
    # Bounded: a dead emulator must not keep the kaynak gate (and its memory) forever.
    if ! timeout 300 "${A[@]}" wait-for-device; then
        echo "emulator did not come up on $SERIAL" >&2
        return 1
    fi
    for _ in $(seq 300); do
        emulator_alive || { echo "emulator process exited" >&2; return 1; }
        [[ "$("${A[@]}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]] && return 0
        sleep 1
    done
    echo "emulator did not finish booting" >&2
    return 1
}

root() {
    timeout 60 "${A[@]}" root >/dev/null
    sleep 2
    timeout 120 "${A[@]}" wait-for-device
}

echo "== Emulator $AVD on $SERIAL"
"$SDK/emulator/emulator" -avd "$AVD" -port "$PORT" -no-window -no-audio \
    -no-boot-anim -no-snapshot -writable-system -gpu swiftshader_indirect \
    > "$WORK_DIR/emulator.log" 2>&1 &
EMU_PID=$!
wait_boot
root
if "${A[@]}" remount 2>&1 | tee "$WORK_DIR/remount.log" | grep -qi reboot; then
    "${A[@]}" reboot
    wait_boot
    root
    "${A[@]}" remount
fi

echo "== Build for the emulator"
mkdir -p "$WORK_DIR/libs"
for lib in libbinder_ndk.so liblog.so; do "${A[@]}" pull "/system/lib64/$lib" "$WORK_DIR/libs/" >/dev/null; done
BUILD_ARCH=x86_64 DEVICE_LIB_DIR="$WORK_DIR/libs" OUT_DIR="$WORK_DIR/build" "$SRC/build.sh" >/dev/null
rm -rf "$WORK_DIR/java"
mkdir -p "$WORK_DIR/java/gen" "$WORK_DIR/java/classes" "$WORK_DIR/java/dex"
"$BT/aidl" --lang=java --structured --stability=vintf --version=1 \
    --hash=11049698b211cb439aad0e72f4c391331ed7063b -I "$SRC/vpd/aidl" -o "$WORK_DIR/java/gen" \
    "$SRC"/vpd/aidl/vendor/google/desktop/vpd_executor/*.aidl
mapfile -t java_src < <(find "$WORK_DIR/java/gen" -name '*.java')
"$TOOLS_DIR/yuz-tanima/sinif-yolu.sh"
javac -source 17 -target 17 -Xlint:-options -cp "$FRAMEWORK_JAR" -d "$WORK_DIR/java/classes" \
    "$HERE/VpdClientTest.java" "${java_src[@]}"
jar --create --file "$WORK_DIR/java/classes.jar" -C "$WORK_DIR/java/classes" .
"$BT/d8" --min-api 35 --output "$WORK_DIR/java/dex" "$WORK_DIR/java/classes.jar"
(cd "$WORK_DIR/java/dex" && jar --create --file "$WORK_DIR/vpdtest.jar" classes.dex)

echo "== Install"
sed 's/^    group system$/    group system\n    seclabel u:r:su:s0/' "$SRC/vpd/etc/gbos-vpd-executor.rc" > "$WORK_DIR/gbos-vpd-executor.rc"
grep -q 'seclabel u:r:su:s0' "$WORK_DIR/gbos-vpd-executor.rc"
"${A[@]}" push "$WORK_DIR/build/gbos-vpd-executor" /system/bin/gbos-vpd-executor >/dev/null
"${A[@]}" push "$WORK_DIR/gbos-vpd-executor.rc" /system/etc/init/gbos-vpd-executor.rc >/dev/null
"${A[@]}" push "$SRC/vpd/etc/gbos-vpd-executor.xml" /system/etc/vintf/manifest/gbos-vpd-executor.xml >/dev/null
"${A[@]}" shell "chown root:shell /system/bin/gbos-vpd-executor && chmod 755 /system/bin/gbos-vpd-executor"
"${A[@]}" push "$WORK_DIR/vpdtest.jar" /data/local/tmp/vpdtest.jar >/dev/null
"${A[@]}" reboot
wait_boot
root
"${A[@]}" shell setenforce 0

FAIL=0
step() { echo "PASS $*"; }
fail() { echo "FAIL $*"; FAIL=1; }
client() { "${A[@]}" shell "CLASSPATH=/data/local/tmp/vpdtest.jar app_process /system/bin VpdClientTest $1"; }
state() { "${A[@]}" shell getprop init.svc.gbos_vpd_executor | tr -d '\r'; }
wait_exit() {
    for _ in $(seq 60); do
        [[ -z "$("${A[@]}" shell pidof gbos-vpd-executor | tr -d '\r')" ]] && return 0
        sleep 1
    done
    return 1
}

echo "== Tests"
s=$(state)
[[ "$s" != running ]] && step "not started at boot (init.svc '$s')" || fail "running at boot"
d=$("${A[@]}" shell "stat -c '%A %U %G' /data/misc/gbos_vpd" | tr -d '\r')
[[ "$d" == "drwx------ system system" ]] && step "store directory $d" || fail "store directory '$d'"
client first || FAIL=1
f=$("${A[@]}" shell "stat -c '%A %U %G' /data/misc/gbos_vpd/rw_vpd" | tr -d '\r')
[[ "$f" == "-rw------- system system" ]] && step "rw_vpd $f" || fail "rw_vpd '$f'"
[[ -z "$("${A[@]}" shell ls /data/misc/gbos_vpd/rw_vpd.tmp 2>/dev/null | tr -d '\r')" ]] \
    && step "no temporary file left" || fail "rw_vpd.tmp left behind"
wait_exit && step "exited once no client held it (init.svc '$(state)')" || fail "still running when idle"
client again || FAIL=1
wait_exit || fail "still running when idle after the second client"
"${A[@]}" shell "su 2000 sh -c 'CLASSPATH=/data/local/tmp/vpdtest.jar app_process /system/bin VpdClientTest denied'" || FAIL=1

# The shell path CIHAZ-TESTI.md uses on the tablet: start through init like servicemanager does,
# then call over `service call` (which only looks the service up).
wait_exit || true
"${A[@]}" shell setprop ctl.interface_start aidl/vendor.google.desktop.vpd_executor.IVpdExecutor/default
sleep 2
[[ -n "$("${A[@]}" shell pidof gbos-vpd-executor | tr -d '\r')" ]] && step "ctl.interface_start starts it" || fail "ctl.interface_start did not start it"
serial=$("${A[@]}" shell getprop ro.serialno | tr -d '\r')
# `service call` prints the reply as lines of "0x<offset>: <word> <word> ... '<text>'".
words() { printf '%s' "$1" | grep -o '0x[0-9a-f]*: [0-9a-f ]*' | sed 's/^0x[0-9a-f]*: //' | tr -s ' \n' ' ' | sed 's/^ //'; }
out=$("${A[@]}" shell service call vendor.google.desktop.vpd_executor.IVpdExecutor/default 1 i32 0)
text=$(printf '%s' "$out" | grep -o "'[^']*'" | tr -d "'\n" | tr -d '. ')
[[ "$(words "$out")" == "00000000 "* && "$text" == *"serial_number"*"${serial:0:6}"* ]] \
    && step "service call getEntries(RO) shows serial_number" || fail "service call getEntries(RO): $out"
out=$("${A[@]}" shell service call vendor.google.desktop.vpd_executor.IVpdExecutor/default 2 i32 0 s16 ubind_attribute)
[[ "$(words "$out")" == "00000000 00000001 "* && "$(words "$out")" == *"ffffffff"* ]] \
    && step "service call getValue(RO, ubind_attribute): entry, null value" || fail "service call getValue: $out"

"${A[@]}" logcat -d -b main,system > "$WORK_DIR/logcat.txt"
grep -E "gbos-vpd-executor|vpd_executor" "$WORK_DIR/logcat.txt" | grep -v VpdClientTest | tail -40
if grep -q "Waited one second for vendor.google.desktop.vpd_executor" "$WORK_DIR/logcat.txt"; then
    fail "a client waited a second or more for the service"
else
    step "no client waited a second for the service"
fi
[[ $FAIL == 0 ]] && echo "EMULATOR TEST PASSED" || { echo "EMULATOR TEST FAILED"; exit 1; }
