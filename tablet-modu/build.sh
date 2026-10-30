#!/usr/bin/env bash
# Builds the "Tablet modu" quick settings app, reproducibly (fixed timestamps, no network).
#
#   ./build.sh       -> out/system/priv-app/GbosTabletModu/GbosTabletModu.apk
#
# yamalar/tablet_modu.py copies it and cihaz/system/ into the image tree. Signed with the same
# app key as the updater (anahtar-olustur.sh, outside the project); without it the
# build stops, nothing is signed with another key.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

SDK="${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}"
BT="${ANDROID_BUILD_TOOLS:-$SDK/build-tools/37.0.0}"
ANDROID_JAR="${ANDROID_JAR:-$SDK/platforms/android-37.2/android.jar}"
API=35
FIXED_DATE="2008-01-01T00:00:00Z"
KEYS="${GBOS_ANAHTAR:-$HOME/.android-certs/googlebookos-t870}"
APP_KS="$KEYS/uygulama.p12"
APP_PASS=gbos-uygulama   # anahtar-olustur.sh

for tool in "$BT/aapt2" "$BT/d8" "$BT/zipalign" "$BT/apksigner" "$ANDROID_JAR" "$APP_KS"; do
    [[ -e "$tool" ]] || { echo "missing: $tool" >&2; exit 1; }
done
command -v javac >/dev/null || { echo "missing: javac" >&2; exit 1; }

OUT="$HERE/out"
OBJ="$OUT/obj"
rm -rf "$OUT"
mkdir -p "$OBJ/res" "$OBJ/gen" "$OBJ/classes" "$OBJ/dex" "$OUT/system/priv-app/GbosTabletModu"

APP="$HERE/uygulama"
"$BT/aapt2" compile --dir "$APP/res" -o "$OBJ/res/app.zip"
"$BT/aapt2" link -I "$ANDROID_JAR" --manifest "$APP/AndroidManifest.xml" \
    --min-sdk-version $API --target-sdk-version 37 --version-code 1 --version-name 1.0 \
    --java "$OBJ/gen" -o "$OBJ/res.apk" "$OBJ/res/app.zip"
mapfile -t java_src < <(find "$APP/src" "$OBJ/gen" -name '*.java' | LC_ALL=C sort)
javac -source 17 -target 17 -encoding UTF-8 -Xlint:all -Xlint:-options -Werror \
    -cp "$ANDROID_JAR" -d "$OBJ/classes" "${java_src[@]}"
jar --create --date="$FIXED_DATE" --file "$OBJ/classes.jar" -C "$OBJ/classes" .
"$BT/d8" --release --min-api $API --lib "$ANDROID_JAR" --output "$OBJ/dex" "$OBJ/classes.jar"
python3 -I - "$OBJ/res.apk" "$OBJ/dex/classes.dex" "$OBJ/unsigned.apk" <<'EOF'
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
EOF
"$BT/zipalign" -f -P 16 4 "$OBJ/unsigned.apk" "$OBJ/aligned.apk"
APK="$OUT/system/priv-app/GbosTabletModu/GbosTabletModu.apk"
# v2 + v3 only: RSA PKCS#1 v1.5 carries no timestamp, so the same input gives the same APK
"$BT/apksigner" sign --ks "$APP_KS" --ks-key-alias uygulama --ks-pass "pass:$APP_PASS" \
    --key-pass "pass:$APP_PASS" --v1-signing-enabled false --v2-signing-enabled true \
    --v3-signing-enabled true --v4-signing-enabled false --out "$APK" "$OBJ/aligned.apk"
"$BT/apksigner" verify "$APK"
rm -f "$APK.idsig"

rm -rf "$OBJ"
bash stamp.sh > "$OUT/STAMP"
(cd "$OUT" && find system -type f | LC_ALL=C sort | xargs sha256sum)
