#!/usr/bin/env bash
# Builds the natural-orientation layers of the SM-T870's sensor and camera HALs (BENIOKU.md):
#
#   out/bin/hw/android.hardware.sensors-service.samsung-multihal-gbos        launcher
#   out/lib64/gbos-dogal-yon/android.hardware.sensors-service.samsung-multihal-gbos.so
#   out/bin/hw/android.hardware.camera.provider-service.samsung-gbos         launcher
#   out/lib64/gbos-dogal-yon/android.hardware.camera.provider-service.samsung-gbos.so
#
#   ./build.sh
#
# Each library is the LineageOS vendor HAL itself (hardware/samsung aidl/sensors resp.
# aidl/camera on hardware/interfaces), rebuilt from the pinned sources (kaynaklar.lock) with
# kaynak-yamalari/*.patch, compiled the way Soong compiles a vendor module (platform libc++
# headers, __ANDROID_VENDOR__, vendor API level) and linked against the vendor partition's own
# shared libraries, read out of the LineageOS OTA zip the tablet runs (araclar/vendor-dosyalari.py).
# The launcher (src/baslatici.c) runs from /product and loads it into a linker namespace cloned
# from "sphal", after the library's vendor dependencies. Same inputs, same bytes; out/STAMP is the
# hash of the inputs (stamp.sh).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$(cd "$HERE/../.." && pwd)"
OUT="$HERE/out"
CACHE="${GBOS_DOGAL_YON_CACHE:-$HOME/.cache/gbos-dogal-yon}"
SDK="${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}"
NDK="${ANDROID_NDK:-$SDK/ndk/30.0.16248370}"
BT="${ANDROID_BUILD_TOOLS:-$SDK/build-tools/37.0.0}"
API=37
VENDOR_API=202604   # ro.board.api_level / ro.llndk.api_level of the tablet (LineageOS 24.0)
LLVM="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
CC="$LLVM/bin/clang"
CXX="$LLVM/bin/clang++"
READELF="$LLVM/bin/llvm-readelf"
for tool in "$CXX" "$LLVM/bin/llvm-strip" "$READELF" "$BT/aidl"; do
    [[ -x "$tool" ]] || { echo "missing: $tool" >&2; exit 1; }
done
command -v patch >/dev/null || { echo "missing: patch" >&2; exit 1; }

SENSORS=android.hardware.sensors-service.samsung-multihal-gbos
CAMERA=android.hardware.camera.provider-service.samsung-gbos
LIBDIR=lib64/gbos-dogal-yon

cd "$HERE"
# The one value everything derives from (yamalar/dogal_yon.py PANEL_ROTATION).
PANEL_ROTATION="$(python3 -I -c "import sys; sys.path.insert(0, '$TOOLS_DIR/yamalar'); import dogal_yon; print(dogal_yon.PANEL_ROTATION)")"
[[ "$PANEL_ROTATION" =~ ^(0|90|180|270)$ ]] || { echo "PANEL_ROTATION $PANEL_ROTATION" >&2; exit 1; }

echo "== Sources (kaynaklar.lock)"
SOURCES_TSV="$(python3 -I araclar/kaynak-getir.py)"
declare -A S
while IFS=$'\t' read -r name dir; do S[$name]="$dir"; done <<<"$SOURCES_TSV"
HIDL_GEN="$(GBOS_PIL_CACHE="$CACHE" bash "$TOOLS_DIR/pil-sagligi/araclar/hidl-gen-kur.sh" <<<"$SOURCES_TSV")"
echo "== Vendor libraries (LineageOS zip)"
V="$(python3 -I araclar/vendor-dosyalari.py)"

BUILD="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-dogal-yon-hal.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT
mkdir -p "$BUILD/src" "$BUILD/obj" "$BUILD/lib" "$BUILD/gen/hidl" "$BUILD/gen/aidl" "$BUILD/out"

echo "== System LLNDK libraries (mica)"
python3 -I araclar/sistem-kutuphaneleri.py "$BUILD/lib"

echo "== Patched LineageOS sources"
HS="$BUILD/src/hardware_samsung"
mkdir -p "$HS"
cp -r "${S[hardware_samsung]}/aidl" "$HS/"
for p in kaynak-yamalari/hardware_samsung/*.patch; do
    patch --quiet --no-backup-if-mismatch --fuzz=0 -p1 -d "$HS" < "$p"
done
# The tree's symlinks into the platform source (camera.h, the common helper sources) point at the
# pinned copies, so a quoted include in a linked header still resolves next to the link, as in
# the platform tree (camera.h's "camera_common.h" must be LineageOS's Samsung variant).
while IFS= read -r -d '' link; do
    target="$(readlink "$link")"
    case "$target" in
        */hardware/libhardware/include/*) ln -sfn "${S[libhardware]}/include_all/${target##*/hardware/libhardware/include/}" "$link" ;;
        */hardware/interfaces/*) ln -sfn "${S[hardware_interfaces]}/${target##*/hardware/interfaces/}" "$link" ;;
        *) echo "unexpected link $link -> $target" >&2; exit 1 ;;
    esac
    [[ -e "$link" ]] || { echo "dangling $link -> $(readlink "$link")" >&2; exit 1; }
done < <(find "$HS" -type l -print0)
# hardware/interfaces: only the patched directory is copied; its include path comes first.
HIP="$BUILD/src/hardware_interfaces"
mkdir -p "$HIP/sensors/aidl/default/multihal"
cp -r "${S[hardware_interfaces]}/sensors/aidl/default/multihal/include" "$HIP/sensors/aidl/default/multihal/"
for p in kaynak-yamalari/hardware_interfaces/*.patch; do
    patch --quiet --no-backup-if-mismatch --fuzz=0 -p1 -d "$HIP" < "$p"
done

HI="${S[hardware_interfaces]}"
echo "== HIDL headers (hidl-gen)"
for pkg in android.hardware.sensors@1.0 android.hardware.sensors@2.0 android.hardware.sensors@2.1 \
           android.hardware.graphics.common@1.0 android.hardware.graphics.common@1.1 \
           android.hardware.graphics.common@1.2 android.hardware.graphics.mapper@4.0 \
           android.hidl.base@1.0 android.hidl.manager@1.0; do
    "$HIDL_GEN" -o "$BUILD/gen/hidl" -Lc++-headers \
        -randroid.hardware:"$HI" -randroid.hidl:"${S[libhidl]}/transport" "$pkg"
done

echo "== AIDL headers (NDK backend, frozen versions the vendor libraries implement)"
# interface  directory  version: the -V<n>-ndk.so libraries on the vendor partition
AIDL_IFACES=(
    "android.hardware.sensors sensors/aidl 3"
    "android.hardware.common common/aidl 2"
    "android.hardware.common.fmq common/fmq/aidl 1"
    "android.hardware.camera.common camera/common/aidl 1"
    "android.hardware.camera.device camera/device/aidl 1"
    "android.hardware.camera.provider camera/provider/aidl 1"
    "android.hardware.camera.metadata camera/metadata/aidl 1"
    "android.hardware.graphics.common graphics/common/aidl 7"
)
AIDL_INC=()
for spec in "${AIDL_IFACES[@]}"; do
    read -r iface dir ver <<<"$spec"
    AIDL_INC+=(-I "$HI/$dir/aidl_api/$iface/$ver")
done
for spec in "${AIDL_IFACES[@]}"; do
    read -r iface dir ver <<<"$spec"
    api="$HI/$dir/aidl_api/$iface/$ver"
    mapfile -d '' files < <(find "$api" -name '*.aidl' -print0 | sort -z)
    "$BT/aidl" --lang=ndk --structured --stability=vintf --version="$ver" \
        --hash="$(tail -n1 "$api/.hash")" "${AIDL_INC[@]}" \
        -h "$BUILD/gen/aidl/include" -o "$BUILD/gen/aidl/src" "${files[@]}"
done

SC="${S[system_core]}"
FN="${S[frameworks_native]}"
CL="${S[clang_prebuilts]}/clang-r584948b"
COMMON_INC=(
    -I "$HERE/src" -I "$BUILD/gen/aidl/include" -I "$BUILD/gen/hidl"
    -I "${S[libhidl]}/base/include" -I "${S[libhidl]}/transport/include" -I "${S[libhwbinder]}/include"
    -I "${S[libfmq]}/include" -I "${S[libfmq]}/base" -I "$HI/common/support/include"
    -I "$SC/libutils/include" -I "$SC/libutils/binder/include" -I "$SC/libcutils/include"
    -I "$SC/libsystem/include" -I "${S[libbase]}/include" -I "${S[logging]}/liblog/include"
    -I "$FN/libs/binder/ndk/include_cpp" -I "$FN/libs/binder/ndk/include_ndk"
    -I "$FN/libs/binder/ndk/include_platform"
)
# A vendor module as Soong compiles it (build/soong cc/config/global.go, cc/compiler.go at
# android-17.0.0_r1): the platform libc++ headers of the release's clang, the vendor macros and
# API level, no exceptions, no RTTI, gnu++20.
CXXFLAGS=(
    --target="aarch64-linux-android$API" -nostdinc++
    -isystem "$CL/android_libc++/platform/aarch64/include/c++/v1" -isystem "$CL/include/c++/v1"
    -std=gnu++20 -O2 -fPIC -fno-exceptions -fno-rtti -ffunction-sections -fdata-sections
    -fno-short-enums -funwind-tables -fstack-protector-strong -fvisibility-inlines-hidden
    -fno-strict-aliasing -ffp-contract=off -D_FORTIFY_SOURCE=2
    -DANDROID -DNDEBUG -UDEBUG -D__ANDROID_VNDK__ -D__ANDROID_VENDOR__
    -D__ANDROID_VENDOR_API__="$VENDOR_API" -D__ANDROID_UNAVAILABLE_SYMBOLS_ARE_WEAK__
    -D_LIBCPP_ENABLE_THREAD_SAFETY_ANNOTATIONS -DGBOS_PANEL_ROTATION="$PANEL_ROTATION"
    -Wall -Wno-unused -Wno-c99-designator -Wno-unused-command-line-argument
    -ffile-prefix-map="$BUILD"=build -ffile-prefix-map="$CACHE/src"=src -ffile-prefix-map="$HERE"=dogal-yon/hal
    -ffile-prefix-map="$NDK"=ndk
)

compile() {  # compile <object prefix> <LOG_TAG or -> <extra flags...> -- <sources...>
    local prefix="$1" tag="$2"
    shift 2
    local extra=()
    while [[ "$1" != "--" ]]; do extra+=("$1"); shift; done
    shift
    local src obj tagflag=()
    [[ "$tag" != "-" ]] && tagflag=(-DLOG_TAG="\"$tag\"")
    for src in "$@"; do
        obj="$BUILD/obj/$prefix-$(printf '%02d' "${#OBJS[@]}")-$(basename "$src").o"
        "$CXX" "${CXXFLAGS[@]}" "${tagflag[@]}" "${extra[@]}" "${COMMON_INC[@]}" -c "$src" -o "$obj"
        OBJS+=("$obj")
    done
}

link_hal() {  # link_hal <name> <vendor libraries...>
    local name="$1"
    shift
    local libs=()
    for l in "$@"; do libs+=("$V/$l"); done
    printf '{\n  global: gbos_hal_main;\n  local: *;\n};\n' > "$BUILD/$name.map"
    "$CXX" --target="aarch64-linux-android$API" -shared -nostdlib++ \
        -Wl,-soname,"$name.so" -Wl,--version-script="$BUILD/$name.map" -Wl,--exclude-libs,ALL \
        -Wl,--build-id=sha1 -Wl,-z,relro,-z,now -Wl,-z,max-page-size=16384 -Wl,--gc-sections \
        -Wl,--no-undefined -Wl,--as-needed -Wl,--hash-style=gnu \
        -o "$BUILD/$name.unstripped.so" "${OBJS[@]}" "${libs[@]}" \
        "$BUILD/lib/libbinder_ndk.so" "$BUILD/lib/liblog.so" "$BUILD/lib/libcamera_metadata.so"
    "$LLVM/bin/llvm-strip" --strip-all -o "$BUILD/out/$name.so" "$BUILD/$name.unstripped.so"
}

echo "== Sensors multi-HAL ($SENSORS.so)"
OBJS=()
SENSOR_INC=(
    -I "$HS/aidl/sensors"
    -I "$HIP/sensors/aidl/default/multihal/include" -I "$HI/sensors/common/default/2.X/multihal/include"
    -I "$HI/sensors/common/utils" -I "$HI/sensors/1.0/default/include"
    -I "$HI/sensors/1.0/default/include/sensors" -I "$HI/sensors/common/convert/include"
    -I "${S[libhardware]}/include_all" -I "${S[libhardware_legacy]}/include"
)
# Android.bp: android.hardware.sensors@2.X-multihal (LOG_TAG SensorsMultiHal), @aidl-multihal,
# @1.0-convert, sensors_common_convert, libaidlcommonsupport, the service itself.
compile sensors SensorsMultiHal "${SENSOR_INC[@]}" -- \
    "$HI/sensors/common/default/2.X/multihal/HalProxy.cpp" \
    "$HI/sensors/common/default/2.X/multihal/HalProxyCallback.cpp"
compile sensors - "${SENSOR_INC[@]}" -- \
    "$HI/sensors/aidl/default/multihal/HalProxyAidl.cpp" \
    "$HI/sensors/aidl/default/multihal/ConvertUtils.cpp" \
    "$HI/sensors/1.0/default/convert.cpp" "$HI/sensors/common/convert/convert.cpp" \
    "$HI/common/support/NativeHandle.cpp" \
    "$HS/aidl/sensors/HalProxySamsung.cpp" "$HS/aidl/sensors/service.cpp" "$HERE/src/surum.cpp"
link_hal "$SENSORS" \
    android.hardware.sensors@1.0.so android.hardware.sensors@2.0.so \
    android.hardware.sensors@2.0-ScopedWakelock.so android.hardware.sensors@2.1.so \
    android.hardware.sensors-V3-ndk.so android.hardware.common-V2-ndk.so \
    android.hardware.common.fmq-V1-ndk.so libbase.so libcutils.so libfmq.so libhidlbase.so \
    libpower.so libutils.so libc++.so

echo "== Camera provider ($CAMERA.so)"
OBJS=()
SM="${S[system_media]}"
CAMERA_INC=(
    # LineageOS's Samsung camera3.h/camera_common.h before libhardware's (samsung_camera3_defaults)
    -I "$HS/aidl/camera/libhardware_headers/include"
    -I "$HS/aidl/camera/device" -I "$HS/aidl/camera/provider" -I "$HS/aidl/camera/common/include_samsung"
    -I "$HI/camera/common/default/include" -I "${S[libhardware]}/include_all"
    -I "$SM/camera/include" -I "$SM/private/camera/include"
    -I "$FN/libs/ui/include" -I "$FN/libs/ui/include_types" -I "$FN/libs/nativewindow/include"
    -I "$FN/libs/nativebase/include" -I "$FN/libs/arect/include" -I "$FN/libs/gralloc/types/include"
    -I "$FN/libs/math/include" -I "$FN/include"
)
# sm8250-common common.mk: soong_config samsungCameraVars needs_sec_reserved_field=true.
CAMERA_DEFS=(-DCAMERA_NEEDS_SEC_RESERVED_FIELD)
# The C structs shared with the camera module must have the vendor provider's layout.
read -r _ TORCH_OFFSET _ STREAM_SIZE _ USAGE_BYTES <<<"$(python3 -I araclar/kamera-abi.py \
    "$LLVM/bin/llvm-objdump" "$READELF" "$V/camera.device-impl.samsung.so" | tr '\n' ' ')"
cat > "$BUILD/kamera-abi.cpp" <<ABI
#include <stddef.h>
#include <hardware/camera3.h>
#include <hardware/camera_common.h>
static_assert(offsetof(camera_module_t, set_torch_mode_strength) == $TORCH_OFFSET,
              "camera_module_t differs from the vendor provider's");
static_assert(sizeof(camera3_stream_t) == $STREAM_SIZE, "camera3_stream_t differs from the vendor provider's");
static_assert(sizeof(camera3_stream_t::usage) == $USAGE_BYTES, "camera3_stream_t::usage differs");
ABI
"$CXX" "${CXXFLAGS[@]}" "${CAMERA_DEFS[@]}" "${CAMERA_INC[@]}" "${COMMON_INC[@]}" -fsyntax-only "$BUILD/kamera-abi.cpp"
echo "camera_module_t.set_torch_mode_strength $TORCH_OFFSET, sizeof(camera3_stream_t) $STREAM_SIZE, usage $USAGE_BYTES bytes: as the vendor provider"
# Android.bp: android.hardware.camera.common-helper.samsung (sources it links), camera.device-
# impl.samsung, the provider service; each source sets its own LOG_TAG.
compile camera - "${CAMERA_DEFS[@]}" "${CAMERA_INC[@]}" -- \
    "$HI/camera/common/default/CameraModule.cpp" "$HI/camera/common/default/CameraMetadata.cpp" \
    "$HI/camera/common/default/VendorTagDescriptor.cpp" "$HI/camera/common/default/HandleImporter.cpp" \
    "$HI/camera/common/default/SimpleThread.cpp" "$HS/aidl/camera/common/SamsungCameraModule.cpp" \
    "$HS/aidl/camera/device/CameraDevice.cpp" "$HS/aidl/camera/device/CameraDeviceSession.cpp" \
    "$HS/aidl/camera/device/convert.cpp" "$HS/aidl/camera/provider/CameraProvider.cpp" \
    "$HS/aidl/camera/provider/service.cpp" "$HI/common/support/NativeHandle.cpp" \
    "$HERE/src/camera_natural_orientation.cpp" "$HERE/src/surum.cpp"
link_hal "$CAMERA" \
    android.hardware.camera.common-V1-ndk.so android.hardware.camera.device-V1-ndk.so \
    android.hardware.camera.provider-V1-ndk.so android.hardware.camera.metadata-V1-ndk.so \
    android.hardware.graphics.common-V7-ndk.so android.hardware.common-V2-ndk.so \
    android.hardware.common.fmq-V1-ndk.so libgralloctypes.so libui.so libhardware.so \
    libbase.so libcutils.so libfmq.so libhidlbase.so libutils.so libc++.so

echo "== Launchers"
# The libraries' DT_NEEDED entries that are not LLNDK: loaded into "sphal" before the library.
LLNDK=" libc.so libm.so libdl.so libdl_android.so liblog.so libbinder_ndk.so libcamera_metadata.so "
for name in "$SENSORS" "$CAMERA"; do
    preload=""
    for lib in $("$READELF" -d "$BUILD/out/$name.so" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p'); do
        [[ "$LLNDK" == *" $lib "* ]] || preload+="${preload:+,}\"$lib\""
    done
    "$CC" --target="aarch64-linux-android$API" -std=c17 -O2 -fPIE -pie -fstack-protector-strong \
        -D_FORTIFY_SOURCE=2 -Wall -Wextra -Werror -I "${S[bionic]}/libc/platform" \
        -DGBOS_ON_YUKLEME="$preload" -ffile-prefix-map="$HERE"=dogal-yon/hal \
        -Wl,--build-id=sha1 -Wl,-z,relro,-z,now -Wl,-z,max-page-size=16384 -Wl,--no-undefined \
        -Wl,--as-needed -o "$BUILD/$name.unstripped" src/baslatici.c \
        "$BUILD/lib/libdl_android.so" "$BUILD/lib/liblog.so"
    "$LLVM/bin/llvm-strip" --strip-all -o "$BUILD/out/$name" "$BUILD/$name.unstripped"
done

echo "== Checks"
python3 -I araclar/denetim.py "$READELF" "$BUILD/out" "$V" "$BUILD/lib" "$PANEL_ROTATION"

rm -rf "$OUT.tmp"
mkdir -p "$OUT.tmp/bin/hw" "$OUT.tmp/$LIBDIR"
for name in "$SENSORS" "$CAMERA"; do
    cp "$BUILD/out/$name" "$OUT.tmp/bin/hw/$name"
    cp "$BUILD/out/$name.so" "$OUT.tmp/$LIBDIR/$name.so"
done
./stamp.sh > "$OUT.tmp/STAMP"
rm -rf "$OUT"
mv "$OUT.tmp" "$OUT"
(cd "$OUT" && sha256sum bin/hw/* "$LIBDIR"/*)
