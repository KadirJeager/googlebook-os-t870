#!/usr/bin/env bash
# Terminal and Linux environment on an Android x86_64 test emulator, the way the image carries
# them: yamalar/terminal.py output in /system, the policy (selinux/politika/plat/60-gbos-linux.cil)
# appended to plat_sepolicy.cil as yamalar/selinux.py does, then a full scenario in Enforcing.
#
#   test/emulator.sh yukle             build x86_64, install into /system, label, reboot
#   test/emulator.sh senaryo [OUT_DIR] Enforcing + test/senaryo.sh + denials as CIL
#
# The emulator must already run with -writable-system (a google_apis x86_64 image), started
# through the shared resource gate, e.g.:
#   T870-araclar/kapi.sh --gb 4 --tur emulator \
#     --ad "GooglebookOS: Terminal emülatör testi" -- "$ANDROID_HOME/emulator/emulator" \
#     -avd GBOS_Terminal_Test -port 5600 -writable-system -no-snapshot -no-boot-anim \
#     -gpu swiftshader_indirect -no-audio -no-window
# ANDROID_SERIAL selects it (default emulator-5600). The emulator's own plat_sepolicy.cil and
# plat_file_contexts are kept untouched on the first run in $GBOS_TEST_STATE and every install
# starts from them, so rounds do not stack.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERMINAL="$(cd "$HERE/.." && pwd)"
TOOLS="$(cd "$TERMINAL/.." && pwd)"
CIL="${CIL:-$TOOLS/selinux/politika/plat/60-gbos-linux.cil}"
export ANDROID_SERIAL="${ANDROID_SERIAL:-emulator-5600}"
STATE="${GBOS_TEST_STATE:-${XDG_CACHE_HOME:-$HOME/.cache}/gbos-terminal-test/$ANDROID_SERIAL}"

die() { echo "emulator.sh: $*" >&2; exit 1; }
wait_boot() {
    adb wait-for-device
    for _ in $(seq 120); do
        [[ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]] && return 0
        sleep 5
    done
    die "$ANDROID_SERIAL did not finish booting"
}
reboot_wait() {
    adb reboot
    sleep 10
    wait_boot
    as_root
}
as_root() {
    adb root >/dev/null 2>&1 || true
    adb wait-for-device
    [[ "$(adb shell id -u | tr -d '\r')" == 0 ]] || die "adb root failed on $ANDROID_SERIAL"
}

yukle() {
    adb get-state >/dev/null 2>&1 || die "no device $ANDROID_SERIAL"
    "$TERMINAL/build.sh" --abi x86_64
    as_root
    adb remount >/dev/null || die "remount failed: start the emulator with -writable-system"
    mkdir -p "$STATE"
    for f in plat_sepolicy.cil plat_file_contexts; do
        [[ -f "$STATE/$f" ]] && continue
        adb pull -q "/system/etc/selinux/$f" "$STATE/$f.tmp"
        grep -q gbos_linux "$STATE/$f.tmp" &&
            die "/system/etc/selinux/$f on $ANDROID_SERIAL already has gbos_linux: wipe the AVD's system or put pristine copies in $STATE"
        mv "$STATE/$f.tmp" "$STATE/$f"
    done
    local stage
    stage="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-terminal-stage.XXXXXX")"
    trap 'rm -rf "$stage"' EXIT
    mkdir -p "$stage/system/etc/selinux"
    cp "$STATE/plat_file_contexts" "$stage/system/etc/selinux/"
    python3 "$TOOLS/yamalar/terminal.py" "$stage" x86_64
    { cat "$STATE/plat_sepolicy.cil"; printf '\n'; cat "$CIL"; } > "$stage/system/etc/selinux/plat_sepolicy.cil"
    local files=() rel mode
    while read -r rel mode; do
        files+=("/system/$rel")
        adb push -q "$stage/system/$rel" "/system/$rel" </dev/null
        adb shell -n chmod "$mode" "/system/$rel"
    done < <(cd "$stage/system" && find . -type f -printf '%P %m\n')
    # Every file must have arrived as staged.
    (cd "$stage" && find system -type f -print0 | xargs -0 sha256sum | sed 's#  system/#  /system/#') > "$stage/want"
    adb shell -n sha256sum "${files[@]}" | tr -d '\r' | sort -k2 > "$stage/got"
    diff <(sort -k2 "$stage/want") "$stage/got" >/dev/null || die "pushed files differ from the stage"
    # The image has no updated copy over /system's APK (adb install -r leaves one in /data).
    adb shell -n pm uninstall-system-updates com.gbos.terminal >/dev/null 2>&1 || true
    reboot_wait
    # adbd labels pushed files with the file_contexts loaded at push time, the old ones; the
    # image builder labels from the new ones. Relabel under the new policy, as the image has it,
    # and boot once more so init starts the service from a correctly labelled binary.
    adb remount >/dev/null 2>&1 || die "remount failed"
    adb shell -n restorecon "${files[@]}" || die "restorecon failed"
    reboot_wait
    [[ -z "$(adb shell -n restorecon -nv "${files[@]}" 2>&1 | grep Relabeling)" ]] || die "files still mislabelled"
    adb shell -n ls -Z /system/bin/gbos-linuxd | grep -q gbos_linuxd_exec || die "gbos-linuxd is not labelled gbos_linuxd_exec"
    adb shell -n 'ps -AZ | grep -q "gbos_linuxd.*gbos-linuxd"' || die "gbos-linuxd is not running in its domain"
    adb shell -n pm path com.gbos.terminal | grep -q "^package:/system/priv-app/Terminal/" ||
        die "the Terminal does not run from /system/priv-app"
    echo "installed on $ANDROID_SERIAL: $(adb shell ls -Z /system/bin/gbos-linuxd | tr -d '\r')"
    # The stage is local to this function: remove it here; the EXIT trap (for failures above)
    # would find the variable gone at script exit (set -u).
    rm -rf "$stage"
    trap - EXIT
}

senaryo() {
    local out="${1:-$TERMINAL/kanit/emulator-$(date +%Y%m%d-%H%M%S)}"
    mkdir -p "$out"
    as_root
    adb push -q "$HERE/senaryo.sh" /data/local/tmp/gbos-senaryo.sh
    # Denials since boot count too: the device boots enforcing (yukle has just rebooted).
    adb shell -n 'logcat -G 64M; setenforce 1'
    adb shell sh /data/local/tmp/gbos-senaryo.sh 2>&1 | tee "$out/senaryo.txt"
    adb shell -n 'logcat -b all -d | grep "avc:"' | grep gbos_linux > "$out/avc.txt" || true
    adb shell -n rm /data/local/tmp/gbos-senaryo.sh
    local failed
    failed="$(grep -E '^[a-z_]+=[1-9]' "$out/senaryo.txt" || true)"
    echo "== denials (permissive=0) not accepted in selinux/kabul.txt, as CIL; all in $out/avc.txt"
    grep 'permissive=0' "$out/avc.txt" | python3 "$TERMINAL/selinux/avc2cil.py" --kabul | tee "$out/eksik.cil"
    [[ -z "$failed" ]] || die "failed steps: $(echo $failed)"
    [[ ! -s "$out/eksik.cil" ]] || die "enforcing denials, see $out/eksik.cil"
    echo "pass: every step 0, no denials"
}

case "${1:-}" in
    yukle) shift; yukle "$@" ;;
    senaryo) shift; senaryo "$@" ;;
    *) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
