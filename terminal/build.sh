#!/usr/bin/env bash
# Builds the Googlebook Terminal and its Linux environment service, reproducibly (fixed
# timestamps, no network: third-party sources are vendored by third_party/fetch.sh).
#
#   ./build.sh                 arm64 (the tablet) into out/arm64/
#   ./build.sh --abi x86_64    for the x86_64 emulator (tests) into out/x86_64/
#
# Output (out/<abi>/):
#   system/bin/gbos-linuxd                     root service (init: gbos-linux.rc)
#   system/bin/gbos-linux                      client: Terminal tabs, `gbos-linux run` for root
#   system/etc/gbos-linux/bin/googlebook-open  xdg-open inside the environment (static)
#   system/priv-app/Terminal/Terminal.apk      the app (native lib stored uncompressed)
#   kopru/VmTerminalApp.apk                    com.android.virtualization.terminal, the
#                                              compatibility package (kopru/); goes into the
#                                              com.android.virt APEX (yamalar/aosp_terminal.py)
# yamalar/terminal.py copies the system/ files plus system/ and selinux/ into the image trees.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

ABI=arm64
while [[ $# -gt 0 ]]; do
    case "$1" in
        --abi) ABI="$2"; shift 2 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
case "$ABI" in
    arm64) TRIPLE=aarch64-linux-android; APK_ABI=arm64-v8a ;;
    x86_64) TRIPLE=x86_64-linux-android; APK_ABI=x86_64 ;;
    *) echo "unsupported ABI $ABI" >&2; exit 2 ;;
esac

SDK="${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}"
NDK="${ANDROID_NDK:-$SDK/ndk/30.0.16248370}"
BT="${ANDROID_BUILD_TOOLS:-$SDK/build-tools/37.0.0}"
ANDROID_JAR="${ANDROID_JAR:-$SDK/platforms/android-37.2/android.jar}"
API=35                       # minimum platform of the binaries (the image is API 37)
LLVM="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
CXX="$LLVM/clang++ --target=$TRIPLE$API"
CC="$LLVM/clang --target=$TRIPLE$API"
STRIP="$LLVM/llvm-strip"
FIXED_DATE="2008-01-01T00:00:00Z"
# The project's Terminal key (anahtar-olustur.sh; outside the project, never published)
KEYS="${GBOS_ANAHTAR:-$HOME/.android-certs/googlebookos-t870}"
KEYSTORE="$KEYS/terminal.keystore"

for tool in "$LLVM/clang++" "$BT/aapt2" "$BT/d8" "$BT/zipalign" "$BT/apksigner" "$ANDROID_JAR" \
            third_party/nlohmann/json.hpp third_party/termux/COMMIT \
            third_party/androidx/annotation-jvm-1.11.0.jar "$KEYSTORE" "$KEYS/terminal.pass"; do
    [[ -e "$tool" ]] || { echo "missing: $tool" >&2; exit 1; }
done
command -v javac >/dev/null || { echo "missing: javac" >&2; exit 1; }

OUT="$HERE/out/$ABI"
OBJ="$OUT/obj"
rm -rf "$OUT"
mkdir -p "$OBJ" "$OUT/system/bin" "$OUT/system/etc/gbos-linux/bin" "$OUT/system/priv-app/Terminal" "$OUT/kopru"

# Paths never leak into the binaries; build ids derive from the content.
COMMON=(-O2 -Wall -Wextra -Werror -fPIE -ffile-prefix-map="$HERE"=. -fno-ident
        -fstack-protector-strong -D_FORTIFY_SOURCE=2)
CXXFLAGS=("${COMMON[@]}" -std=c++20 -fexceptions -Ithird_party/nlohmann -Idaemon)
LDFLAGS=(-pie -static-libstdc++ -Wl,--build-id=sha1 -Wl,-z,relro,-z,now -Wl,--gc-sections)

echo "== native ($ABI)"
for src in util config container dns installer linuxd client; do
    $CXX "${CXXFLAGS[@]}" -c "daemon/$src.cpp" -o "$OBJ/$src.o"
done
$CXX "${LDFLAGS[@]}" -o "$OBJ/gbos-linuxd" "$OBJ"/{util,config,container,dns,installer,linuxd}.o -llog -landroid
$CXX "${LDFLAGS[@]}" -o "$OBJ/gbos-linux" "$OBJ"/{util,config,client}.o -llog
# Runs inside Debian (glibc userland): static, no Android dynamic linker needed.
$CXX "${COMMON[@]}" -std=c++20 -static -Wl,--build-id=sha1 -o "$OBJ/googlebook-open" daemon/open.cpp
# libtermux.so: the pty/process JNI of Termux's terminal-emulator, built as upstream does.
$CC -std=c11 -Wall -Wextra -Werror -Os -fno-stack-protector -fPIC -shared \
    -ffile-prefix-map="$HERE"=. -Wl,--gc-sections -Wl,--build-id=sha1 -Wl,-z,max-page-size=16384 \
    -o "$OBJ/libtermux.so" third_party/termux/terminal-emulator/src/main/jni/termux.c
for bin in gbos-linuxd gbos-linux googlebook-open libtermux.so; do
    "$STRIP" --strip-unneeded "$OBJ/$bin"
done
install -m 755 "$OBJ/gbos-linuxd" "$OBJ/gbos-linux" "$OUT/system/bin/"
install -m 755 "$OBJ/googlebook-open" "$OUT/system/etc/gbos-linux/bin/"

if [[ "${SKIP_APP:-0}" == 1 ]]; then
    sha256sum "$OUT"/system/bin/* "$OUT"/system/etc/gbos-linux/bin/*
    exit 0
fi

echo "== app resources"
APP="$HERE/app"
TV_RES="third_party/termux/terminal-view/src/main/res"
mkdir -p "$OBJ/res" "$OBJ/gen"
"$BT/aapt2" compile --dir "$TV_RES" -o "$OBJ/res/terminal-view.zip"
"$BT/aapt2" compile --dir "$APP/res" -o "$OBJ/res/app.zip"
"$BT/aapt2" link -I "$ANDROID_JAR" --manifest "$APP/AndroidManifest.xml" \
    --min-sdk-version $API --target-sdk-version 36 --version-code 1 --version-name 1.0 \
    --auto-add-overlay --extra-packages com.termux.view \
    --java "$OBJ/gen" -o "$OBJ/res.apk" "$OBJ/res/terminal-view.zip" "$OBJ/res/app.zip"

echo "== app code"
mkdir -p "$OBJ/classes" "$OBJ/dex"
mapfile -t java_src < <(find "$APP/src" third_party/termux/terminal-emulator/src/main/java \
    third_party/termux/terminal-view/src/main/java "$OBJ/gen" -name '*.java' | LC_ALL=C sort)
javac -source 17 -target 17 -encoding UTF-8 -Xlint:-options -nowarn \
    -cp "$ANDROID_JAR:third_party/androidx/annotation-jvm-1.11.0.jar" \
    -d "$OBJ/classes" "${java_src[@]}"
jar --create --date="$FIXED_DATE" --file "$OBJ/classes.jar" -C "$OBJ/classes" .
"$BT/d8" --release --min-api $API --lib "$ANDROID_JAR" \
    --classpath third_party/androidx/annotation-jvm-1.11.0.jar \
    --output "$OBJ/dex" "$OBJ/classes.jar"

echo "== apk"
python3 -I - "$OBJ/res.apk" "$OBJ/dex/classes.dex" "$OBJ/libtermux.so" "$APK_ABI" \
    "$OBJ/unsigned.apk" <<'EOF'
import sys, zipfile
res_apk, dex, lib, abi, out = sys.argv[1:6]
EPOCH = (2008, 1, 1, 0, 0, 0)

def put(zout, name, data, compress):
    info = zipfile.ZipInfo(name, date_time=EPOCH)
    info.compress_type = compress
    info.external_attr = 0o644 << 16
    zout.writestr(info, data)

with zipfile.ZipFile(res_apk) as zres, zipfile.ZipFile(out, "w") as zout:
    for info in zres.infolist():
        put(zout, info.filename, zres.read(info.filename), info.compress_type)
    with open(dex, "rb") as f:
        put(zout, "classes.dex", f.read(), zipfile.ZIP_DEFLATED)
    # Stored and page aligned (zipalign -P): loaded straight from the APK, nothing extracted.
    with open(lib, "rb") as f:
        put(zout, f"lib/{abi}/libtermux.so", f.read(), zipfile.ZIP_STORED)
EOF
"$BT/zipalign" -f -P 16 4 "$OBJ/unsigned.apk" "$OBJ/aligned.apk"
"$BT/apksigner" sign --ks "$KEYSTORE" --ks-pass file:"$KEYS/terminal.pass" \
    --v1-signing-enabled false --v2-signing-enabled true --v3-signing-enabled true \
    --out "$OUT/system/priv-app/Terminal/Terminal.apk" "$OBJ/aligned.apk"
"$BT/apksigner" verify "$OUT/system/priv-app/Terminal/Terminal.apk"
rm -f "$OUT/system/priv-app/Terminal/Terminal.apk.idsig"

echo "== kopru (com.android.virtualization.terminal)"
# Above any version code of Android's own terminal app (37 in Android 17): the package takes
# its place in the APEX and must not look like a downgrade to the package manager.
KOPRU_VERSION=1000000
KOBJ="$OBJ/kopru"
mkdir -p "$KOBJ/classes" "$KOBJ/dex"
"$BT/aapt2" compile --dir kopru/res -o "$KOBJ/res.zip"
"$BT/aapt2" link -I "$ANDROID_JAR" --manifest kopru/AndroidManifest.xml \
    --min-sdk-version $API --target-sdk-version 36 --version-code $KOPRU_VERSION --version-name gbos \
    -o "$KOBJ/res.apk" "$KOBJ/res.zip"
mapfile -t kopru_src < <(find kopru/src -name '*.java' | LC_ALL=C sort)
javac -source 17 -target 17 -encoding UTF-8 -Xlint:-options -nowarn -cp "$ANDROID_JAR" \
    -d "$KOBJ/classes" "${kopru_src[@]}"
jar --create --date="$FIXED_DATE" --file "$KOBJ/classes.jar" -C "$KOBJ/classes" .
"$BT/d8" --release --min-api $API --lib "$ANDROID_JAR" --output "$KOBJ/dex" "$KOBJ/classes.jar"
python3 -I - "$KOBJ/res.apk" "$KOBJ/dex/classes.dex" "$KOBJ/unsigned.apk" <<'EOF2'
import sys, zipfile
res_apk, dex, out = sys.argv[1:4]
EPOCH = (2008, 1, 1, 0, 0, 0)
with zipfile.ZipFile(res_apk) as zres, zipfile.ZipFile(out, "w") as zout:
    for info in zres.infolist():
        i = zipfile.ZipInfo(info.filename, date_time=EPOCH)
        i.compress_type = info.compress_type
        i.external_attr = 0o644 << 16
        zout.writestr(i, zres.read(info.filename))
    i = zipfile.ZipInfo("classes.dex", date_time=EPOCH)
    i.compress_type = zipfile.ZIP_DEFLATED
    i.external_attr = 0o644 << 16
    with open(dex, "rb") as f:
        zout.writestr(i, f.read())
EOF2
"$BT/zipalign" -f -P 16 4 "$KOBJ/unsigned.apk" "$KOBJ/aligned.apk"
# Same key as the Terminal: the Terminal trusts requests only from a package signed like itself.
"$BT/apksigner" sign --ks "$KEYSTORE" --ks-pass file:"$KEYS/terminal.pass" \
    --v1-signing-enabled false --v2-signing-enabled true --v3-signing-enabled true \
    --out "$OUT/kopru/VmTerminalApp.apk" "$KOBJ/aligned.apk"
"$BT/apksigner" verify "$OUT/kopru/VmTerminalApp.apk"
rm -f "$OUT/kopru/VmTerminalApp.apk.idsig"
bash stamp.sh > "$OUT/STAMP"

sha256sum "$OUT"/system/bin/* "$OUT"/system/etc/gbos-linux/bin/* "$OUT"/system/priv-app/Terminal/* "$OUT"/kopru/*
