#!/usr/bin/env bash
# Prints the hash of everything out/ is built from, so a stale build is noticed (apply_patches.py):
# the build scripts, the HAL and AIDL sources, the patched FaceUnlock checkout (getir.sh pins it
# and the compilers) and the two files of the pristine mica image the class path comes from
# (sinif-yolu.sh). The SDK versions are pinned in build.sh, which is hashed; the key is not.
set -euo pipefail
# The file order must not depend on the machine's locale (sort).
export LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
SYSTEM_IMG="${MICA_IMAGES:-$(dirname "$HERE")/mica-imaj/guncel}/system_a.erofs"
DUMP_EROFS="${EROFS_BIN:-$HOME/.local/bin}/dump.erofs"
art="$("$DUMP_EROFS" --ls --path=/system/apex "$SYSTEM_IMG" |
       awk '$2 == 1 && $3 ~ /^com\..*\.art\.apex$/ {print $3}')"
{
    find build.sh stamp.sh sinif-yolu.sh getir.sh hal_src aosp yamalar FaceUnlock \
        -path FaceUnlock/.git -prune -o -type f -print0 | sort -z | xargs -0 sha256sum
    for f in /system/framework/framework.jar "/system/apex/$art"; do
        printf '%s  %s\n' "$("$DUMP_EROFS" --cat --path="$f" "$SYSTEM_IMG" | sha256sum | cut -d' ' -f1)" "$f"
    done
} | sha256sum | cut -d' ' -f1
