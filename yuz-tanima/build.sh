#!/usr/bin/env bash
# Builds face unlock from source: the HAL (sense-face-hal.jar), the Sense app (FaceUnlock.apk) and
# its Settings overlay (FaceUnlockOverlay.apk).
#
#   ./getir.sh            once: fetches FaceUnlock/ (Evolution-X + yamalar/), kotlinc and dex2jar
#   ./build.sh            build into out/
#   ./build.sh --stage    also copy the HAL and the app into the system image tree (SYS_TREE)
#
# The AIDL interfaces are generated from aosp/ (frozen AOSP API versions) and FaceUnlock/
# interfaces/ with build-tools 35.0.0's aidl; the platform classes the code compiles against come
# from the Googlebook OS image (sinif-yolu.sh). Resources and manifests are compiled with aapt2,
# Megvii's native libraries are packed from FaceUnlock/lib as shipped. Fixed timestamps keep the
# outputs byte-for-byte reproducible.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

SDK="${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}"
BT="${ANDROID_BUILD_TOOLS:-$SDK/build-tools/37.0.0}"
AIDL="${AIDL:-$SDK/build-tools/35.0.0/aidl}"
ANDROID_JAR="${ANDROID_JAR:-$SDK/platforms/android-37.2/android.jar}"
SYS_TREE="${SYS_TREE:-${GBOS_WORK:-/var/tmp/mica_work}/sys/system}"
KOTLINC="$HERE/tools/kotlinc/bin/kotlinc"
FACE="$HERE/FaceUnlock"
FIXED_DATE="2008-01-01T00:00:00Z"
# The project's face unlock app key (anahtar-olustur.sh; outside the project, never published)
SENSE_KS="${GBOS_ANAHTAR:-$HOME/.android-certs/googlebookos-t870}/sense.keystore"

for tool in "$BT/aapt2" "$BT/d8" "$BT/zipalign" "$BT/apksigner" "$AIDL" "$ANDROID_JAR" "$KOTLINC" \
        "$FACE/AndroidManifest.xml" "$FACE/lib/vendorImplPrebuilt.jar" "$SENSE_KS"; do
    [[ -e "$tool" ]] || { echo "missing: $tool (sources and compilers come from ./getir.sh)" >&2; exit 1; }
done
command -v javac >/dev/null || { echo "missing: javac" >&2; exit 1; }

"$HERE/sinif-yolu.sh"
PLATFORM_CP="out/classpath/core-oj.jar:out/classpath/core-libart.jar:out/classpath/framework.jar"
CP="$PLATFORM_CP:out/aidl_classes.jar:$FACE/lib/vendorImplPrebuilt.jar"

echo "== AIDL"
rm -rf out/aidl_gen out/stub_aidl out/aidl_classes
mkdir -p out/aidl_gen out/stub_aidl/android/view out/aidl_classes
# The face HAL's frames carry an android.view.Surface, a Java-only parcelable of the framework
printf 'package android.view;\n@VintfStability @JavaOnlyStableParcelable parcelable Surface;\n' \
    > out/stub_aidl/android/view/Surface.aidl
api() {  # api DIR NAME VERSION: the frozen API directory of an AOSP AIDL interface
    echo "aosp/$1/aidl_api/$2/$3"
}
COMMON=$(api common_aidl android.hardware.common 2)
FMQ=$(api common_fmq_aidl android.hardware.common.fmq 1)
KEYMASTER=$(api keymaster_aidl android.hardware.keymaster 4)
BIO_COMMON=$(api biometrics_common_aidl android.hardware.biometrics.common 4)
BIO_FACE=$(api biometrics_face_aidl android.hardware.biometrics.face 4)
gen_frozen() {  # gen_frozen API_DIR [INCLUDE...]: Java for every type of a frozen VINTF API
    local dir="$1" version="${1##*/}" hash inc=() i
    shift
    for i in "$dir" "$@"; do inc+=(-I "$i"); done
    hash="$(tail -n1 "$dir/.hash")"
    find "$dir" -name '*.aidl' -print0 | sort -z | while IFS= read -r -d '' f; do
        "$AIDL" --lang=java --min_sdk_version=35 --structured --stability=vintf \
            --version="$version" --hash="$hash" "${inc[@]}" -o out/aidl_gen "$f"
    done
}
gen_frozen "$COMMON"
gen_frozen "$FMQ" "$COMMON"
gen_frozen "$KEYMASTER"
gen_frozen "$BIO_COMMON" "$COMMON" "$KEYMASTER"
gen_frozen "$BIO_FACE" "$BIO_COMMON" "$KEYMASTER" "$COMMON" "$FMQ" out/stub_aidl
SENSE_AIDL="$FACE/interfaces/biometrics/face/aidl/src"
find "$SENSE_AIDL" -name '*.aidl' -print0 | sort -z | while IFS= read -r -d '' f; do
    "$AIDL" --lang=java --min_sdk_version=35 -I "$SENSE_AIDL" -o out/aidl_gen "$f"
done
mapfile -t aidl_java < <(find out/aidl_gen -name '*.java' | sort)
javac -source 17 -target 17 -Xlint:-options,-dep-ann -cp "$PLATFORM_CP" -d out/aidl_classes \
    "${aidl_java[@]}"
rm -f out/aidl_classes.jar
jar --create --date="$FIXED_DATE" --file out/aidl_classes.jar -C out/aidl_classes .

echo "== HAL"
rm -rf out/hal_classes out/hal_dex
mkdir -p out/hal_classes out/hal_dex
javac -source 17 -target 17 -Xlint:-options -cp "$CP" -d out/hal_classes \
    hal_src/co/aospa/sense/hal/SenseFaceHalMain.java
rm -f out/hal_classes.jar
jar --create --date="$FIXED_DATE" --file out/hal_classes.jar -C out/hal_classes .
"$BT/d8" --min-api 35 --output out/hal_dex out/hal_classes.jar out/aidl_classes.jar
rm -f out/sense-face-hal.jar
jar --create --date="$FIXED_DATE" --file out/sense-face-hal.jar -C out/hal_dex classes.dex

echo "== App resources"
rm -rf out/app_res out/app_gen
mkdir -p out/app_res out/app_gen
"$BT/aapt2" compile --dir "$FACE/res" -o out/app_res/res.zip
"$BT/aapt2" link -I "$ANDROID_JAR" --manifest "$FACE/AndroidManifest.xml" \
    --min-sdk-version 35 --target-sdk-version 35 --auto-add-overlay \
    --java out/app_gen -o out/FaceUnlock-res.apk out/app_res/res.zip

echo "== App"
rm -rf out/app_classes out/app_dex
mkdir -p out/app_classes out/app_dex
mapfile -t gen_java < <(find out/app_gen -name '*.java')
javac -source 17 -target 17 -Xlint:-options -cp "$CP" -d out/app_classes "${gen_java[@]}"
mapfile -t kt_src < <(find "$FACE/src" -name '*.kt')
"$KOTLINC" -jvm-target 17 -nowarn -cp "$CP:out/app_classes" -d out/app_classes "${kt_src[@]}"
rm -f out/app_classes.jar
jar --create --date="$FIXED_DATE" --file out/app_classes.jar -C out/app_classes .
"$BT/d8" --min-api 35 --output out/app_dex out/app_classes.jar out/aidl_classes.jar \
    "$FACE/lib/vendorImplPrebuilt.jar" tools/kotlinc/lib/kotlin-stdlib.jar

# Resources, the dex and the native libraries (stored uncompressed, so they load from the APK)
python3 -I - out/FaceUnlock-res.apk out/app_dex/classes.dex "$FACE/lib" out/FaceUnlock-tmp.apk <<'EOF'
import os, sys, zipfile
res_apk, dex, lib_dir, out = sys.argv[1:5]
EPOCH = (2008, 1, 1, 0, 0, 0)

def put(zout, name, data, compress):
    ni = zipfile.ZipInfo(name, date_time=EPOCH)
    ni.compress_type = compress
    ni.external_attr = 0o644 << 16
    zout.writestr(ni, data)

libs = sorted(n for n in os.listdir(lib_dir) if n.endswith(".so"))
if not libs:
    raise SystemExit(f"{lib_dir}: no native libraries")
with zipfile.ZipFile(res_apk) as zres, zipfile.ZipFile(out, "w") as zout:
    for info in zres.infolist():
        put(zout, info.filename, zres.read(info.filename), info.compress_type)
    with open(dex, "rb") as f:
        put(zout, "classes.dex", f.read(), zipfile.ZIP_DEFLATED)
    for name in libs:
        with open(os.path.join(lib_dir, name), "rb") as f:
            put(zout, f"lib/arm64-v8a/{name}", f.read(), zipfile.ZIP_STORED)
EOF
"$BT/zipalign" -f -p 4 out/FaceUnlock-tmp.apk out/FaceUnlock-aligned.apk
"$BT/apksigner" sign --ks "$SENSE_KS" --ks-pass pass:android --key-pass pass:android \
    --out out/FaceUnlock.apk out/FaceUnlock-aligned.apk
"$BT/apksigner" verify out/FaceUnlock.apk
rm -f out/FaceUnlock-tmp.apk out/FaceUnlock-aligned.apk

echo "== Settings overlay"
rm -rf out/overlay_res
mkdir -p out/overlay_res
"$BT/aapt2" compile --dir "$FACE/overlay/res" -o out/overlay_res/res.zip
"$BT/aapt2" link -I "$ANDROID_JAR" --manifest "$FACE/overlay/AndroidManifest.xml" \
    -o out/FaceUnlockOverlay-unsigned.apk out/overlay_res/res.zip
"$BT/zipalign" -f -p 4 out/FaceUnlockOverlay-unsigned.apk out/FaceUnlockOverlay-aligned.apk
"$BT/apksigner" sign --ks "$SENSE_KS" --ks-pass pass:android --key-pass pass:android \
    --out out/FaceUnlockOverlay.apk out/FaceUnlockOverlay-aligned.apk
"$BT/apksigner" verify out/FaceUnlockOverlay.apk
rm -f out/FaceUnlockOverlay-unsigned.apk out/FaceUnlockOverlay-aligned.apk

bash stamp.sh > out/STAMP
sha256sum out/sense-face-hal.jar out/FaceUnlock.apk out/FaceUnlockOverlay.apk

if [[ "${1:-}" == "--stage" ]]; then
    echo "== Staging into $SYS_TREE"
    install -m 644 out/sense-face-hal.jar "$SYS_TREE/framework/sense-face-hal.jar"
    install -m 644 out/FaceUnlock.apk "$SYS_TREE/priv-app/FaceUnlock/FaceUnlock.apk"
fi
