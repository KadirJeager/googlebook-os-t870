#!/usr/bin/env bash
# Builds out/gbos-vpd-executor, the SM-T870 implementation of mica's
# vendor.google.desktop.vpd_executor.IVpdExecutor/default (see README.md).
#
#   ./build.sh          generate the AIDL NDK code, run the host tests, build and check the binary
#
# tests/emu/emu-test.sh builds the same sources for an x86_64 emulator with
#   BUILD_ARCH=x86_64 DEVICE_LIB_DIR=<the emulator's /system/lib64> OUT_DIR=<dir> ./build.sh
#
# Inputs: vpd/aidl, vpd/src, vpd/test, third_party/libbinder_ndk (AOSP android-17.0.0_r1 headers),
# the NDK, build-tools' aidl and the pristine mica system image (its libbinder_ndk.so and liblog.so
# are what the binary links and is checked against). The build is reproducible: same inputs, same
# bytes. out/STAMP records the hash of the inputs; yamalar/mica_servisleri.py rebuilds when it
# no longer matches.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$(dirname "$HERE")"
BUILD_ARCH="${BUILD_ARCH:-aarch64}"
OUT="${OUT_DIR:-$HERE/out}"
SDK="${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}"
NDK="${ANDROID_NDK:-$SDK/ndk/30.0.16248370}"
BT="${ANDROID_BUILD_TOOLS:-$SDK/build-tools/37.0.0}"
DUMP_EROFS="${DUMP_EROFS:-${EROFS_BIN:-$HOME/.local/bin}/dump.erofs}"
SYSTEM_IMG="$TOOLS_DIR/mica-imaj/guncel/system_a.erofs"
API=37
HASH=11049698b211cb439aad0e72f4c391331ed7063b  # IVpdExecutor.HASH of the frozen version 1

LLVM="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
CXX="$LLVM/bin/clang++"
for tool in "$CXX" "$LLVM/bin/llvm-strip" "$LLVM/bin/llvm-readelf" "$LLVM/bin/llvm-nm" \
        "$BT/aidl" "$DUMP_EROFS" "$SYSTEM_IMG"; do
    [[ -e "$tool" ]] || { echo "missing: $tool" >&2; exit 1; }
done
HOST_CXX="${HOST_CXX:-$(command -v clang++ || command -v g++ || true)}"
[[ -n "$HOST_CXX" ]] || { echo "missing: host C++ compiler" >&2; exit 1; }

cd "$HERE"
rm -rf "$OUT"
mkdir -p "$OUT/gen" "$OUT/obj" "$OUT/lib" "$OUT/test-tmp"

echo "== AIDL (NDK backend, version 1, VINTF stability)"
"$BT/aidl" --lang=ndk --structured --stability=vintf --version=1 --hash="$HASH" \
    -I vpd/aidl -h "$OUT/gen/include" -o "$OUT/gen/src" \
    vpd/aidl/vendor/google/desktop/vpd_executor/*.aidl

echo "== Host tests"
"$HOST_CXX" -std=c++17 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined \
    -o "$OUT/host_test" vpd/test/host_test.cpp vpd/src/vpd_store.cpp vpd/src/ro_source.cpp
TEST_TMPDIR="$OUT/test-tmp" "$OUT/host_test"
rm -rf "$OUT/test-tmp" "$OUT/host_test"

if [[ -n "${DEVICE_LIB_DIR:-}" ]]; then
    echo "== Device libraries from $DEVICE_LIB_DIR"
    cp "$DEVICE_LIB_DIR/libbinder_ndk.so" "$DEVICE_LIB_DIR/liblog.so" "$OUT/lib/"
else
    echo "== Device libraries from $(readlink -f "$SYSTEM_IMG")"
    for lib in libbinder_ndk.so liblog.so; do
        "$DUMP_EROFS" --cat --path="/system/lib64/$lib" "$SYSTEM_IMG" > "$OUT/lib/$lib"
        [[ -s "$OUT/lib/$lib" ]] || { echo "cannot read /system/lib64/$lib" >&2; exit 1; }
    done
fi

echo "== Device binary ($BUILD_ARCH, API $API)"
CXXFLAGS=(
    --target="$BUILD_ARCH-linux-android$API" -std=c++17 -O2 -Wall -Wextra -Werror
    -fno-exceptions -fPIE -fstack-protector-strong -D_FORTIFY_SOURCE=2
    -DBINDER_STABILITY_SUPPORT
    -ffile-prefix-map="$HERE"=. -ffile-prefix-map="$NDK"=ndk
    -I "$OUT/gen/include"
    -I third_party/libbinder_ndk/include_cpp
    -I third_party/libbinder_ndk/include_ndk
    -I third_party/libbinder_ndk/include_platform
)
SOURCES=(vpd/src/main.cpp vpd/src/vpd_executor.cpp vpd/src/vpd_store.cpp vpd/src/ro_source.cpp)
while IFS= read -r -d '' f; do SOURCES+=("$f"); done < <(find "$OUT/gen/src" -name '*.cpp' -print0 | sort -z)
OBJS=()
for src in "${SOURCES[@]}"; do
    obj="$OUT/obj/$(echo "${src#"$OUT"/}" | tr '/' '_').o"
    "$CXX" "${CXXFLAGS[@]}" -c "$src" -o "$obj"
    OBJS+=("$obj")
done
"$CXX" --target="$BUILD_ARCH-linux-android$API" -pie -static-libstdc++ \
    -Wl,--build-id=sha1 -Wl,-z,relro,-z,now -Wl,-z,max-page-size=16384 -Wl,--gc-sections \
    -o "$OUT/gbos-vpd-executor.unstripped" "${OBJS[@]}" \
    "$OUT/lib/libbinder_ndk.so" "$OUT/lib/liblog.so"
"$LLVM/bin/llvm-strip" --strip-all -o "$OUT/gbos-vpd-executor" "$OUT/gbos-vpd-executor.unstripped"
rm -rf "$OUT/obj" "$OUT/gbos-vpd-executor.unstripped"

echo "== Checks"
needed=$("$LLVM/bin/llvm-readelf" -d "$OUT/gbos-vpd-executor" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p' | sort | tr '\n' ' ')
[[ "$needed" == "libbinder_ndk.so libc.so libdl.so liblog.so libm.so " ]] \
    || { echo "unexpected DT_NEEDED: $needed" >&2; exit 1; }
# Every binder/log symbol the binary uses must be exported by the device's libraries.
missing=0
for lib in libbinder_ndk.so liblog.so; do
    exported=$("$LLVM/bin/llvm-nm" -D --defined-only "$OUT/lib/$lib" | awk '{print $3}' | sed 's/@.*//' | sort -u)
    prefix=$([[ $lib == liblog.so ]] && echo '__android_log' || echo 'A[A-Za-z]*_')
    while read -r sym; do
        grep -qx "$sym" <<<"$exported" || { echo "not exported by device $lib: $sym" >&2; missing=1; }
    done < <("$LLVM/bin/llvm-nm" -D --undefined-only "$OUT/gbos-vpd-executor" | awk '{print $2}' | sed 's/@.*//' | grep -E "^$prefix" | sort -u)
done
[[ $missing == 0 ]] || exit 1
rm -rf "$OUT/lib" "$OUT/gen"

[[ -n "${DEVICE_LIB_DIR:-}${OUT_DIR:-}" || "$BUILD_ARCH" != aarch64 ]] || "$HERE/stamp.sh" > "$OUT/STAMP"
sha256sum "$OUT/gbos-vpd-executor"
