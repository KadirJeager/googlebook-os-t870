#!/usr/bin/env bash
# The updater on an Android 37 x86_64 test emulator, the way the image carries it
# (yamalar/guncelleyici.py output in /system, selinux/70-gbos-guncelleyici.cil appended to the
# emulator's plat_sepolicy.cil), in Enforcing, with the real uncrypt, vdc/vold checkpoint, init,
# the app and the recovery-side update-binary on the emulator's own mksh and toybox.
#
#   emulator/senaryo.sh yukle            build x86_64, install into /system, label, reboot
#   emulator/senaryo.sh kur              a test set through `gbos-guncelleyici kur` (reboots)
#   emulator/senaryo.sh denetle [OUT]    after kur: checkpoint, maps, update-binary, boot guard
#
# The emulator must run with -writable-system, started through kapi.sh (the shared resource
# gate where there is one):
#   T870-araclar/kapi.sh --gb 4 --tur emulator \
#     --ad "GooglebookOS: güncelleyici emülatör testi" -- "$ANDROID_HOME/emulator/emulator" \
#     -avd GBOS_Guncelleyici_Test -port 5610 -writable-system -no-snapshot -no-boot-anim \
#     -gpu swiftshader_indirect -no-audio -no-window
#
# Differences from the tablet, all in the test set-up only (never in the image):
#   - the emulator's super is /dev/block/vda2 labelled vd_device: one test rule lets the domain
#     read it, and the test's yuvalar.conf names three small ranges of it with its own header hash
#   - /cache is a directory on the root filesystem (no cache partition): writable through adb
#     remount's overlay
#   - recovery: the emulator has none; update-binary is run by this script with GBOS_TEST=1 against
#     a file copy of super's first MiBs, the real userdata device and the maps uncrypt made
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUNCELLEYICI="$(cd "$HERE/.." && pwd)"
TOOLS="$(cd "$GUNCELLEYICI/.." && pwd)"
export ANDROID_SERIAL="${ANDROID_SERIAL:-emulator-5610}"
STATE="${XDG_CACHE_HOME:-$HOME/.cache}/gbos-guncelleyici-test/$ANDROID_SERIAL"
TEST_BUILD_OFFSET=1               # the test set claims to be running build + 1
SLOT_MIB="system:8 system_ext:4 product:6"

die() { echo "senaryo.sh: $*" >&2; exit 1; }
sh_() { adb shell -n "$@" | tr -d '\r'; }
wait_boot() {
    adb wait-for-device
    for _ in $(seq 120); do
        [[ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]] && return 0
        sleep 5
    done
    die "$ANDROID_SERIAL did not finish booting"
}
as_root() {
    adb root >/dev/null 2>&1 || true
    adb wait-for-device
    [[ "$(sh_ id -u)" == 0 ]] || die "adb root failed"
}
reboot_wait() { adb reboot; sleep 10; wait_boot; as_root; }

yukle() {
    adb get-state >/dev/null 2>&1 || die "no device $ANDROID_SERIAL"
    "$GUNCELLEYICI/build.sh" --abi x86_64
    as_root
    # the first remount only turns overlayfs on; it takes effect after a reboot
    if [[ "$(adb remount 2>&1)" == *reboot* ]]; then reboot_wait; fi
    adb remount >/dev/null || die "remount failed: start the emulator with -writable-system"
    mkdir -p "$STATE"
    for f in plat_sepolicy.cil plat_file_contexts; do
        [[ -f "$STATE/$f" ]] && continue
        adb pull -q "/system/etc/selinux/$f" "$STATE/$f.tmp"
        grep -q gbos_guncelleyici "$STATE/$f.tmp" && die "$f already has the updater: wipe the AVD's system"
        mv "$STATE/$f.tmp" "$STATE/$f"
    done
    stage="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-guncelleyici-stage.XXXXXX")"
    trap 'rm -rf "${stage:-}"' EXIT
    mkdir -p "$stage/system/etc/selinux"
    cp "$STATE/plat_file_contexts" "$stage/system/etc/selinux/"
    python3 -I - "$stage" "$TOOLS" <<'EOF'
import sys
stage, tools = sys.argv[1:3]
sys.path.insert(0, tools)
sys.dont_write_bytecode = True
import apply_patches
from yamalar import guncelleyici
guncelleyici.install(stage, apply_patches.pristine_file, apply_patches.REMOVED_APPS, abi="x86_64")
EOF
    # the image's policy has the Linux environment's rules (60-gbos-linux.cil) before the
    # updater's, which refer to its types
    { cat "$STATE/plat_sepolicy.cil"; printf '\n'; cat "$TOOLS/selinux/politika/plat/60-gbos-linux.cil"
      printf '\n'; cat "$GUNCELLEYICI/selinux/70-gbos-guncelleyici.cil"
      printf '\n;; emulator only: its super is vd_device\n(allow gbos_guncelleyici vd_device (blk_file (read open getattr ioctl)))\n'
    } > "$stage/system/etc/selinux/plat_sepolicy.cil"
    # init loads the vendor's precompiled policy while this hash matches the one recorded next
    # to it; with the updater's rules the platform policy changed, so the hash must too
    # (system/core/init/selinux.cpp; on the tablet LineageOS's vendor never matches mica)
    local vers
    vers="$(sh_ cat /vendor/etc/selinux/plat_sepolicy_vers.txt)"
    # compile it here first, as init will (a policy init cannot compile stops the boot)
    local pol="$stage/politika"
    mkdir -p "$pol/system/etc" "$pol/system_ext/etc" "$pol/product/etc" "$pol/vendor/etc" "$pol/odm/etc"
    adb pull -q /system/etc/selinux "$pol/system/etc/"
    cp "$stage/system/etc/selinux/plat_sepolicy.cil" "$pol/system/etc/selinux/plat_sepolicy.cil"
    for part in system_ext product vendor odm; do adb pull -q "/$part/etc/selinux" "$pol/$part/etc/" || true; done
    python3 -I - "$pol" "$TOOLS" <<'EOF' || die "the policy does not compile; nothing pushed"
import os, sys
pol, tools = sys.argv[1:3]
sys.path.insert(0, tools)
sys.dont_write_bytecode = True
from yamalar import selinux as sel
secilc, _ = sel._tools()
r = lambda p: os.path.join(pol, p)
sel.compile_policy(secilc, sel.init_inputs(r("system"), r("system_ext"), r("product"), r("vendor"), r("odm")),
                   os.path.join(pol, "derlenen.pol"))
print("policy compiles")
EOF
    rm -rf "$pol"
    adb pull -q "/system/etc/selinux/mapping/$vers.cil" "$stage/mapping.cil"
    cat "$stage/system/etc/selinux/plat_sepolicy.cil" "$stage/mapping.cil" | sha256sum | cut -d' ' -f1 \
        > "$stage/system/etc/selinux/plat_sepolicy_and_mapping.sha256"
    rm "$stage/mapping.cil"
    # emulator only: /cache is a read-only directory there; a persistent /data directory is
    # bound over it before the updater's post-fs-data actions (rc files run in name order)
    mkdir -p "$stage/system/etc/init"
    cat > "$stage/system/etc/init/00-gbos-test-cache.rc" <<'RC'
# guncelleyici/emulator/senaryo.sh: the test emulator has no cache partition
on post-fs-data
    mkdir /data/gbos-test-cache 0770 system cache encryption=Require
    mount none /data/gbos-test-cache /cache bind
    restorecon /cache
    mkdir /cache/recovery 0770 system cache
RC
    # the emulator's super layout for the test (the tablet's comes from tablete-yaz.py)
    local header first=4 conf="$stage/system/etc/gbos-guncelleyici/yuvalar.conf"
    header="$(sh_ "dd if=/dev/block/by-name/super bs=1048576 count=1 2>/dev/null | sha256sum" | cut -d' ' -f1)"
    { echo "# emulator test layout (guncelleyici/emulator/senaryo.sh)"; echo "super_baslik_sha256 $header"
      for s in $SLOT_MIB; do echo "bolum ${s%%:*} $first ${s#*:}"; first=$((first + ${s#*:})); done
    } > "$conf"
    local files=() rel mode
    while read -r rel mode; do
        files+=("/system/$rel")
        adb push -q "$stage/system/$rel" "/system/$rel" </dev/null
        adb shell -n chmod "$mode" "/system/$rel"
    done < <(cd "$stage/system" && find . -type f -printf '%P %m\n')
    (cd "$stage" && find system -type f -print0 | xargs -0 sha256sum | sed 's#  system/#  /system/#') > "$stage/want"
    adb shell -n sha256sum "${files[@]}" | tr -d '\r' | sort -k2 > "$stage/got"
    diff <(sort -k2 "$stage/want") "$stage/got" >/dev/null || die "pushed files differ from the stage"
    reboot_wait
    adb remount >/dev/null 2>&1 || die "remount failed"
    adb shell -n restorecon "${files[@]}" || die "restorecon failed"
    reboot_wait
    [[ -z "$(adb shell -n restorecon -nv "${files[@]}" 2>&1 | grep Relabeling)" ]] || die "files still mislabelled"
    local ok=0
    for _ in $(seq 15); do
        [[ "$(sh_ "ps -AZ")" == *"gbos_guncelleyici"*"gbos-guncelleyicid"* ]] && { ok=1; break; }
        sleep 2
    done
    [[ $ok == 1 ]] || die "the service is not running in its domain"
    rm -rf "$stage"
    trap - EXIT
    echo "installed on $ANDROID_SERIAL"
}

kur() {
    as_root
    local inc id build
    inc="$(sh_ getprop ro.build.version.incremental)"
    id="$(sh_ getprop ro.build.id)"
    build="$((inc + TEST_BUILD_OFFSET))_${id}"
    tmp="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-guncelleyici-set.XXXXXX")"
    trap 'rm -rf "${tmp:-}"' EXIT
    echo "surum $build" > "$tmp/hazir.conf"
    for s in $SLOT_MIB; do
        head -c "$(( ${s#*:} * 1048576 ))" /dev/urandom > "$tmp/${s%%:*}.img"
        echo "bolum ${s%%:*} $(sha256sum "$tmp/${s%%:*}.img" | cut -d' ' -f1)" >> "$tmp/hazir.conf"
    done
    echo "kaynak bilgisayar" >> "$tmp/hazir.conf"
    sh_ 'rm -rf /data/gbos-ota/gelen/*'
    adb push -q "$tmp"/*.img "$tmp/hazir.conf" /data/gbos-ota/gelen/
    cp "$tmp/hazir.conf" "$STATE/son-set.conf"
    rm -rf "$tmp"
    trap - EXIT
    sh_ 'logcat -c; logcat -G 16M'
    echo "== gbos-guncelleyici --bekle kur as adb shell (uid 2000, shell domain)"
    adb unroot >/dev/null 2>&1 || true
    adb wait-for-device
    [[ "$(sh_ id -u)" == 2000 ]] || die "adb unroot failed"
    timeout 120 adb shell -n gbos-guncelleyici --bekle kur > "$STATE/kur-cikti.txt" 2>&1 &
    local client=$!
    for _ in $(seq 60); do
        grep -q "Yeniden başlatılıyor" "$STATE/kur-cikti.txt" 2>/dev/null && break
        grep -q "^hata" "$STATE/kur-cikti.txt" 2>/dev/null && { cat "$STATE/kur-cikti.txt"; die "kur failed"; }
        sleep 2
    done
    cat "$STATE/kur-cikti.txt"
    grep -q "Yeniden başlatılıyor" "$STATE/kur-cikti.txt" || die "kur did not reach the reboot"
    # init cannot reboot into recovery here (no misc partition: "failed to find /misc"); the
    # tablet does. A plain reboot leaves everything as recovery would find it.
    sleep 3
    as_root
    adb reboot
    kill "$client" 2>/dev/null || true
    sleep 10
    wait_boot
    as_root
    echo "rebooted: $(sh_ getprop sys.boot.reason)"
}

denetle() {
    local out="${1:-$GUNCELLEYICI/kanit/emulator-$(date +%Y%m%d-%H%M%S)}" fail=0
    mkdir -p "$out"
    as_root
    check() {  # check NAME COMMAND...
        if "${@:2}" >> "$out/ayrinti.txt" 2>&1; then echo "$1=0" | tee -a "$out/sonuc.txt"
        else echo "$1=1" | tee -a "$out/sonuc.txt"; fail=1; fi
    }
    : > "$out/sonuc.txt"
    echo "== bekci after the kur reboot: checkpoint committed, state" >> "$out/ayrinti.txt"
    for _ in $(seq 40); do  # boot_completed + HEALTH_WINDOW
        [[ -z "$(sh_ 'ls /cache/gbos/checkpoint 2>/dev/null')" ]] && break
        sleep 5
    done
    check checkpoint-onaylandi bash -c "[[ -z \"\$(adb shell -n ls /cache/gbos/checkpoint 2>/dev/null)\" ]] && adb shell -n 'vdc checkpoint needsCheckpoint; [ \$? -eq 0 ]'"
    check sonuc-dosyasi-yok bash -c "[[ -z \"\$(adb shell -n ls /cache/gbos/sonuc 2>/dev/null)\" ]]"
    sh_ 'gbos-guncelleyici durum' > "$out/durum.txt"
    check durum-okunur grep -q '^durum=' "$out/durum.txt"

    echo "== maps: every image reads back through its uncrypt map from the raw userdata device" >> "$out/ayrinti.txt"
    sh_ 'cat /cache/gbos/islem.conf' > "$out/islem.conf"
    check islem-dosyasi grep -q '^bicim 1$' "$out/islem.conf"
    sh_ 'head -3 /cache/gbos/yeni-system.map' > "$out/harita-basi.txt"
    check harita-aygiti grep -qx '/dev/block/vdc' <(head -1 "$out/harita-basi.txt")

    echo "== update-binary (recovery side) against a file copy of super, the real userdata, real maps" >> "$out/ayrinti.txt"
    local ub=/data/local/tmp/gbos-ub
    sh_ "mkdir -p $ub && rm -f $ub/*"
    unzip -p "$GUNCELLEYICI/out/x86_64/system/etc/gbos-guncelleyici/kurulum.zip" \
        META-INF/com/google/android/update-binary > "$out/update-binary"
    # the emulator's slot table instead of the tablet's (same script otherwise)
    python3 -I - "$out/update-binary" "$(sh_ 'cat /system/etc/gbos-guncelleyici/yuvalar.conf')" <<'EOF'
import re, sys
path, conf = sys.argv[1], sys.argv[2]
header = re.search(r"^super_baslik_sha256 (\S+)", conf, re.M).group(1)
table = " ".join(f"{header} {n} {a} {b}" for n, a, b in re.findall(r"^bolum (\S+) (\d+) (\d+)", conf, re.M))
s = open(path).read()
s = re.sub(r'^SLOT_TABLE=".*"$', f'SLOT_TABLE="{table}"', s, flags=re.M)
open(path, "w").write(s)
EOF
    adb push -q "$out/update-binary" "$ub/update-binary"
    sh_ "dd if=/dev/block/by-name/super of=$ub/super.img bs=1048576 count=24 2>/dev/null; dd if=/dev/zero of=$ub/misc.img bs=4096 count=1 2>/dev/null; chmod 755 $ub/update-binary"
    sh_ "cp $ub/super.img $ub/super.before"
    local env="GBOS_TEST=1 GBOS_DIZIN=/cache/gbos GBOS_SUPER=$ub/super.img GBOS_USERDATA=/dev/block/vdc GBOS_MISC=$ub/misc.img"
    sh_ "$env sh $ub/update-binary 3 1 /cache/gbos/kur.zip > $ub/status.txt 2>&1; echo \$? > $ub/rc"
    adb pull -q "$ub/status.txt" "$out/update-binary-kur.txt"
    check update-binary-kur bash -c "[[ \"\$(adb shell -n cat $ub/rc | tr -d '\r')\" == 0 ]] && adb shell -n cat /cache/gbos/sonuc | grep -q '^kuruldu '"
    # the slots now hold the test set, byte for byte
    local verify=""
    for s in $SLOT_MIB; do
        local n=${s%%:*} want
        want="$(grep "^bolum $n " "$STATE/son-set.conf" | cut -d' ' -f3)"
        verify+="[ \"\$(dd if=$ub/super.img bs=1048576 skip=\$(sed -n 's/^bolum $n \\([0-9]*\\) .*/\\1/p' /system/etc/gbos-guncelleyici/yuvalar.conf) count=${s#*:} 2>/dev/null | sha256sum | cut -d' ' -f1)\" = $want ] && "
    done
    check yuvalar-yeni-kume adb shell -n "${verify}true"
    sh_ "$env sh $ub/update-binary 3 1 /cache/gbos/geri.zip > $ub/status2.txt 2>&1; echo \$? > $ub/rc"
    adb pull -q "$ub/status2.txt" "$out/update-binary-geri.txt"
    check update-binary-geri adb shell -n "[ \"\$(cat $ub/rc)\" = 0 ] && cmp $ub/super.img $ub/super.before"
    sh_ "rm -rf $ub; rm -f /cache/gbos/deneme /cache/gbos/sonuc"

    echo "== boot guard, success: a trial of the running build passes and is confirmed" >> "$out/ayrinti.txt"
    local running
    running="$(sh_ getprop ro.build.version.incremental)_$(sh_ getprop ro.build.id)"
    sh_ "printf 'durum=deneme\\nsurum_yeni=$running\\nsurum_eski=x\\nsayac=0\\n' > /cache/gbos/deneme; restorecon /cache/gbos/deneme; mkdir -p /data/gbos-ota/yeni; restorecon -R /data/gbos-ota; setprop ctl.start gbos_bekci"
    for _ in $(seq 40); do [[ -z "$(sh_ 'ls /cache/gbos/deneme 2>/dev/null')" ]] && break; sleep 5; done
    check bekci-onay bash -c "adb shell -n cat /data/gbos-ota/durum | grep -qx 'durum=guncellendi' && [[ -z \"\$(adb shell -n ls /cache/recovery/command 2>/dev/null)\" ]]"

    echo "== the app: Settings' System update intent opens it, it shows the state" >> "$out/ayrinti.txt"
    sh_ 'cmd package query-activities --brief -a android.settings.SYSTEM_UPDATE_SETTINGS' > "$out/sistem-guncellemesi-hedefleri.txt"
    check uygulama-hedef bash -c "grep -m1 -A2 'Activity #0' '$out/sistem-guncellemesi-hedefleri.txt' | grep -q com.gbos.guncelleyici/.GuncellemeActivity"
    sh_ 'am start -W -a android.settings.SYSTEM_UPDATE_SETTINGS' >> "$out/ayrinti.txt"
    sleep 3
    sh_ 'uiautomator dump /data/local/tmp/ui.xml >/dev/null; cat /data/local/tmp/ui.xml; rm /data/local/tmp/ui.xml' > "$out/ui.xml"
    adb exec-out screencap -p > "$out/ekran.png"
    check uygulama-ekrani grep -q 'Googlebook OS' "$out/ui.xml"

    echo "== Enforcing denials of the updater's domains" >> "$out/ayrinti.txt"
    sh_ 'logcat -b all -d' | grep 'avc:' | grep -E 'gbos_guncelleyici|gbos_ota|scontext=u:r:(uncrypt|vdc)' > "$out/avc.txt" || true
    # init's noatsecure on entering a service domain is refused by a platform neverallow for
    # every such service and changes nothing; every other refusal counts
    check ret-yok bash -c "! grep 'permissive=0' '$out/avc.txt' | grep -qv 'noatsecure'"
    echo "kanıt: $out"
    [[ $fail == 0 ]] || die "failed steps: $(grep '=1$' "$out/sonuc.txt" | tr '\n' ' ')"
    echo "pass: every step 0"
}

case "${1:-}" in
    yukle) shift; yukle "$@" ;;
    kur) shift; kur "$@" ;;
    denetle) shift; denetle "$@" ;;
    *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
