#!/usr/bin/env bash
# Builds out/android.hardware.health-service.samsung-gbos: the tablet's own health HAL
# (LineageOS hardware/samsung aidl/health on AOSP's default AIDL implementation) with the Samsung
# battery driver's state-of-health and cycle-count nodes configured (BENIOKU.md).
#
#   ./build.sh
#
# Inputs: kaynaklar.lock (pinned upstream sources, fetched and hash-checked by
# araclar/kaynak-getir.py into ~/.cache/gbos-pil-sagligi), kaynak-yamalari/*.patch, the NDK,
# build-tools' aidl, hidl-gen (araclar/hidl-gen-kur.sh builds it from the pinned AOSP source) and
# the pristine mica images (their libbinder_ndk.so, liblog.so and bionic are what the binary
# links and is checked against). Same inputs, same bytes. out/STAMP records the hash of the
# inputs; yamalar/pil_sagligi.py rebuilds when it no longer matches.
#
# Everything except the device's LLNDK libraries (libbinder_ndk, liblog, libc, libm, libdl) is
# linked statically, as the vendor build links libhealth_aidl_impl, libbatterymonitor and
# libhealthloop: the binary runs from /product, whose linker namespace sees only LLNDK.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/out"
NAME=android.hardware.health-service.samsung-gbos
SDK="${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}"
NDK="${ANDROID_NDK:-$SDK/ndk/30.0.16248370}"
BT="${ANDROID_BUILD_TOOLS:-$SDK/build-tools/37.0.0}"
API=37
AIDL_VERSION=5   # what the tablet's vendor HAL implements (android.hardware.health-V5-ndk.so)
LLVM="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
CXX="$LLVM/bin/clang++"
for tool in "$CXX" "$LLVM/bin/llvm-strip" "$LLVM/bin/llvm-readelf" "$LLVM/bin/llvm-nm" "$BT/aidl"; do
    [[ -x "$tool" ]] || { echo "missing: $tool" >&2; exit 1; }
done
command -v patch >/dev/null || { echo "missing: patch" >&2; exit 1; }

cd "$HERE"
echo "== Sources (kaynaklar.lock)"
SOURCES_TSV="$(python3 -I araclar/kaynak-getir.py)"
declare -A S
while IFS=$'\t' read -r name dir; do S[$name]="$dir"; done <<<"$SOURCES_TSV"
HIDL_GEN="$(bash araclar/hidl-gen-kur.sh <<<"$SOURCES_TSV")"

BUILD="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-pil-sagligi.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT
mkdir -p "$BUILD/src" "$BUILD/obj" "$BUILD/lib" "$BUILD/gen"

echo "== Patched LineageOS Health.cpp"
cp "${S[hardware_samsung]}/aidl/health/Health.cpp" "$BUILD/src/Health.cpp"
for p in kaynak-yamalari/*.patch; do
    patch --quiet --no-backup-if-mismatch --fuzz=0 -p3 -d "$BUILD/src" < "$p"
done

HW="${S[hardware_interfaces]}/health"
echo "== HIDL health@1.0/2.0/2.1 headers (hidl-gen)"
for v in 1.0 2.0 2.1; do
    "$HIDL_GEN" -o "$BUILD/gen/hidl" -Lc++-headers \
        -randroid.hardware:"${S[hardware_interfaces]}" -randroid.hidl:"${S[libhidl]}/transport" \
        "android.hardware.health@$v"
done

echo "== AIDL android.hardware.health V$AIDL_VERSION (NDK backend, VINTF stability)"
API_DIR="$HW/aidl/aidl_api/android.hardware.health/$AIDL_VERSION"
"$BT/aidl" --lang=ndk --structured --stability=vintf --version="$AIDL_VERSION" \
    --hash="$(tail -n1 "$API_DIR/.hash")" -I "$API_DIR" \
    -h "$BUILD/gen/aidl/include" -o "$BUILD/gen/aidl/src" "$API_DIR"/android/hardware/health/*.aidl

echo "== Device libraries (mica system image and runtime APEX)"
python3 -I araclar/cihaz-kutuphaneleri.py "$BUILD/lib"

SC="${S[system_core]}"
B="${S[libbase]}"
HIDL="${S[libhidl]}"
INCLUDES=(
    -I "$BUILD/gen/aidl/include" -I "$BUILD/gen/hidl"
    -I "$HW/aidl/default/include" -I "$HW/aidl/default" -I "$HW/aidl/include"
    -I "$HW/utils/libhealthloop/include"
    -I "$SC/healthd/include" -I "$SC/libcutils/include" -I "$SC/libutils/include"
    -I "$SC/libutils/binder/include" -I "$SC/libsystem/include"
    -I "$B/include" -I "${S[logging]}/liblog/include" -I "${S[fmtlib]}/include"
    -I "$HIDL/base/include" -I "$HIDL/transport/include" -I "${S[libfmq]}/base"
    -I "${S[frameworks_native]}/services/batteryservice/include"
    -I "${S[frameworks_native]}/libs/binder/ndk/include_cpp"
    -I "${S[frameworks_native]}/libs/binder/ndk/include_ndk"
    -I "${S[frameworks_native]}/libs/binder/ndk/include_platform"
    -I "${S[connectivity]}/bpf/headers/include" -I "${S[connectivity]}/bpf/syscall_wrappers/include"
)
COMMON=(
    --target="aarch64-linux-android$API" -O2 -fPIE -fstack-protector-strong -D_FORTIFY_SOURCE=2
    -ffunction-sections -fdata-sections -fno-exceptions -Wno-unused-command-line-argument
    -Wno-c99-designator   # Soong's global cflags (build/soong/cc/config/global.go) have it too
    -ffile-prefix-map="$BUILD"=build -ffile-prefix-map="$HOME/.cache/gbos-pil-sagligi/src"=src
    -ffile-prefix-map="$NDK"=ndk
    # As Soong builds these modules for the device; CHARGER_FORCE_NO_UI drops the off-mode
    # charger UI (vendor.charger keeps running the vendor binary, see BENIOKU.md).
    -DBINDER_STABILITY_SUPPORT -D_FILE_OFFSET_BITS=64 -DCHARGER_FORCE_NO_UI=1
)
CXXSTD=(-std=gnu++23)

# The translation units the vendor build compiles into this binary (libhealth_aidl_impl,
# libhealthloop, libbatterymonitor + translate-ndk, the generated AIDL code), and from libbase,
# libcutils, libutils, libhidlbase and fmtlib the sources of the parts the HAL calls;
# --gc-sections drops the rest. Every source sets its own LOG_TAG, as in the vendor build.
UNITS=(
    "$BUILD/src/Health.cpp"
    "$HW/aidl/default/Health.cpp" "$HW/aidl/default/HalHealthLoop.cpp"
    "$HW/aidl/default/LinkedCallback.cpp" "$HW/aidl/default/health-convert.cpp"
    "$HW/aidl/android/hardware/health/translate-ndk.cpp"
    "$HW/utils/libhealthloop/HealthLoop.cpp" "$HW/utils/libhealthloop/utils.cpp"
    "$SC/healthd/BatteryMonitor.cpp"
)
for f in chrono_utils cmsg file hex logging mapped_file parsebool parsenetaddress posix_strerror_r \
         process properties result stringprintf strings threads errors_unix; do
    UNITS+=("$B/$f.cpp")
done
for f in android_get_control_file klog uevent properties; do UNITS+=("$SC/libcutils/$f.cpp"); done
for f in Errors RefBase SharedBuffer String16 String8 StrongPointer Unicode VectorImpl; do
    UNITS+=("$SC/libutils/binder/$f.cpp")
done
UNITS+=("$HIDL/base/HidlSupport.cpp" "${S[fmtlib]}/src/format.cc")
while IFS= read -r -d '' f; do UNITS+=("$f"); done < <(find "$BUILD/gen/aidl/src" -name '*.cpp' -print0 | sort -z)

echo "== Device binary (aarch64, API $API)"
OBJS=()
i=0
for src in "${UNITS[@]}"; do
    obj="$BUILD/obj/$((i++))-$(basename "$src").o"
    "$CXX" "${COMMON[@]}" "${CXXSTD[@]}" "${INCLUDES[@]}" -c "$src" -o "$obj"
    OBJS+=("$obj")
done
# The device's libbinder_ndk.so imports libutils symbols (RefBase, String8, VectorImpl ...); the
# linker would export this binary's static copies to satisfy them, and the process's
# libbinder_ndk/libbinder would then run on them instead of the device's libutils.so. Nothing
# is exported: every symbol stays local, the libraries keep their own libutils and libc++.
printf '{\n  local: *;\n};\n' > "$BUILD/yerel.map"
mkdir -p "$OUT"
"$CXX" --target="aarch64-linux-android$API" -pie -static-libstdc++ \
    -Wl,--version-script="$BUILD/yerel.map" -Wl,--exclude-libs,ALL \
    -Wl,--build-id=sha1 -Wl,-z,relro,-z,now -Wl,-z,max-page-size=16384 -Wl,--gc-sections \
    -Wl,--no-undefined -Wl,--as-needed \
    -o "$BUILD/$NAME.unstripped" "${OBJS[@]}" "$BUILD/lib/libbinder_ndk.so" "$BUILD/lib/liblog.so"
"$LLVM/bin/llvm-strip" --strip-all -o "$BUILD/$NAME" "$BUILD/$NAME.unstripped"

echo "== Checks"
READELF="$LLVM/bin/llvm-readelf"
hdr="$("$READELF" -h "$BUILD/$NAME")"
grep -q 'Class:.*ELF64' <<<"$hdr" && grep -q 'Machine:.*AArch64' <<<"$hdr" && grep -q 'Type:.*DYN' <<<"$hdr" \
    || { echo "not an AArch64 PIE: $hdr" >&2; exit 1; }
interp="$("$READELF" -l "$BUILD/$NAME" | sed -n 's/.*Requesting program interpreter: \(.*\)]/\1/p')"
[[ "$interp" == /system/bin/linker64 ]] || { echo "interpreter $interp" >&2; exit 1; }
needed="$("$READELF" -d "$BUILD/$NAME" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p' | sort | tr '\n' ' ')"
[[ "$needed" == "libbinder_ndk.so libc.so libdl.so liblog.so " ]] \
    || { echo "unexpected DT_NEEDED: $needed" >&2; exit 1; }
# Every undefined symbol must be exported, with the same version, by one of the device's
# libraries (mica's /system/lib64 and runtime APEX bionic).
# (libutils' weak CallStack hooks stay undefined-weak inside the binary and resolve to null at
# link time, which RefBase checks for: utils/CallStack.h, CALLSTACK_WEAK.)
python3 -I araclar/sembol-denetimi.py "$READELF" "$BUILD/$NAME" "$BUILD/lib"

cp "$BUILD/$NAME" "$OUT/$NAME.tmp"
mv "$OUT/$NAME.tmp" "$OUT/$NAME"
./stamp.sh > "$OUT/STAMP"
sha256sum "$OUT/$NAME"
