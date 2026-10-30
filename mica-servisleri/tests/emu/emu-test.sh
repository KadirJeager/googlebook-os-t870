#!/usr/bin/env bash
# Emulator test of gbos-vpd-executor: creates a throw-away Android 37 AVD, runs emu-run.sh through
# the kaynak memory/disk gate and deletes the AVD and work files afterwards (KEEP=1 keeps them).
#
#   mica-servisleri/tests/emu/emu-test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export AVD="${AVD:-gbos_vpd_test}"
export PORT="${PORT:-5620}"
export WORK_DIR="${WORK_DIR:-/var/tmp/gbos-vpd-emu-test}"
IMAGE="system-images;android-37.0;google_apis;x86_64"
# AVD data: AVD_DIR, else under the emulator's own ANDROID_AVD_HOME (put it on a data disk with
# room for a few GB), else ~/.android/avd.
AVD_DIR="${AVD_DIR:-${ANDROID_AVD_HOME:-$HOME/.android/avd}/$AVD.avd}"
AVDMANAGER="${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}/cmdline-tools/latest/bin/avdmanager"

# One run at a time: a second run on the same AVD and port would kill the first one's emulator.
exec 9<"${BASH_SOURCE[0]}"
flock -n 9 || { echo "another emu-test.sh run is in progress" >&2; exit 1; }
if adb devices | grep -q "^emulator-$PORT"; then
    echo "emulator-$PORT is in use; set PORT" >&2
    exit 1
fi
cleanup() {
    [[ "${KEEP:-0}" == 1 ]] && return 0
    "$AVDMANAGER" delete avd -n "$AVD" >/dev/null 2>&1 || true
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
if ! "$AVDMANAGER" list avd -c | grep -qx "$AVD"; then
    echo no | "$AVDMANAGER" create avd -n "$AVD" -k "$IMAGE" -d pixel_tablet -p "$AVD_DIR" >/dev/null
fi
# The test needs little data space; a small partition keeps the AVD within the disk floor.
CONFIG="$AVD_DIR/config.ini"
grep -q '^disk.dataPartition.size=' "$CONFIG" \
    && sed -i 's/^disk.dataPartition.size=.*/disk.dataPartition.size=2G/' "$CONFIG" \
    || echo 'disk.dataPartition.size=2G' >> "$CONFIG"
"$HERE/../../../kapi.sh" --gb 4 --tur emulator --disk-gb 3 --ad "GooglebookOS: VPD HAL emülatör testi" \
    -- "$HERE/emu-run.sh"
