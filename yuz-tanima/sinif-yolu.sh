#!/usr/bin/env bash
# Compile-time class jars of the Googlebook OS image, for Java/Kotlin code that calls hidden
# platform APIs (the face unlock HAL and app, the mica-servisleri emulator test):
#
#   out/classpath/framework.jar     /system/framework/framework.jar
#   out/classpath/core-oj.jar       javalib/core-oj.jar of the image's ART APEX
#   out/classpath/core-libart.jar   javalib/core-libart.jar of the image's ART APEX
#
#   ./sinif-yolu.sh         build them unless out/classpath/KAYNAK already names the same inputs
#
# The inputs are read from the pristine system image (MICA_IMAGES/system_a.erofs, the release
# mica-indir.py fetched), not from the work tree apply_patches.py edits, and the dex is turned back
# into class files with dex2jar (getir.sh). Only API signatures and constants matter to the
# compilers, so the jars come from the very release the code runs on.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$(dirname "$HERE")"
MICA_IMAGES="${MICA_IMAGES:-$TOOLS/mica-imaj/guncel}"
SYSTEM_IMG="$MICA_IMAGES/system_a.erofs"
D2J="$HERE/tools/dex-tools-v2.4/d2j-dex2jar.sh"
VERSION_SWITCH="$HERE/tools/dex-tools-v2.4/d2j-class-version-switch.sh"
DUMP_EROFS="${EROFS_BIN:-$HOME/.local/bin}/dump.erofs"
OUT="$HERE/out/classpath"

for f in "$D2J" "$SYSTEM_IMG" "$DUMP_EROFS"; do
    [[ -e "$f" ]] || { echo "missing: $f (dex2jar comes from ./getir.sh)" >&2; exit 1; }
done

work="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-sinif-yolu.XXXXXX")"
trap 'rm -rf "$work"' EXIT

image_file() {  # image_file IMAGE PATH DEST: one file of an erofs or ext4 image, which must exist
    if [[ "$(od -An -tx1 -j 1080 -N 2 "$1" | tr -d ' ')" == "53ef" ]]; then
        debugfs -R "dump $2 $3" "$1" 2>/dev/null
    else
        "$DUMP_EROFS" --cat --path="$2" "$1" > "$3"
    fi
    [[ -s "$3" ]] || { echo "$1: no $2" >&2; exit 1; }
}

mapfile -t apexes < <("$DUMP_EROFS" --ls --path=/system/apex "$SYSTEM_IMG" |
                      awk '$2 == 1 && $3 ~ /^com\..*\.art\.apex$/ {print $3}')
[[ ${#apexes[@]} -eq 1 ]] || { echo "$SYSTEM_IMG: expected one ART APEX, found ${#apexes[@]}" >&2; exit 1; }
image_file "$SYSTEM_IMG" /system/framework/framework.jar "$work/framework.dex.jar"
image_file "$SYSTEM_IMG" "/system/apex/${apexes[0]}" "$work/art.apex"

stamp="$(cd "$work" && sha256sum framework.dex.jar art.apex)
$(cd "$HERE" && sha256sum "tools/dex-tools-v2.4/lib/"*.jar | sha256sum | cut -d' ' -f1)  dex2jar"
if [[ -f "$OUT/KAYNAK" && "$(cat "$OUT/KAYNAK")" == "$stamp" ]] &&
        [[ -f "$OUT/framework.jar" && -f "$OUT/core-oj.jar" && -f "$OUT/core-libart.jar" ]]; then
    exit 0
fi

unzip -q "$work/art.apex" apex_payload.img -d "$work"
for lib in core-oj core-libart; do
    image_file "$work/apex_payload.img" "/javalib/$lib.jar" "$work/$lib.dex.jar"
done

mkdir "$work/out"
for lib in framework core-oj core-libart; do
    # run in $work: dex2jar leaves a <name>-error.zip in the current directory on a failed class
    (cd "$work" && "$D2J" -f -o "$lib.v50.jar" "$lib.dex.jar" >/dev/null)
    [[ ! -e "$work/$lib.dex-error.zip" ]] || { echo "dex2jar could not convert all of $lib" >&2; exit 1; }
    # dex2jar writes class file version 50, where javac reads an interface's default methods
    # (Parcelable.getStability) as abstract; version 52 (Java 8) keeps them default
    "$VERSION_SWITCH" 8 "$work/$lib.v50.jar" "$work/out/$lib.jar" >/dev/null
done
printf '%s\n' "$stamp" > "$work/out/KAYNAK"
rm -rf "$OUT"
mkdir -p "$(dirname "$OUT")"
mv "$work/out" "$OUT"
echo "class jars from $SYSTEM_IMG -> $OUT"
