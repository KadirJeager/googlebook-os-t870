#!/usr/bin/env bash
# Fetches what the face unlock build needs from its upstream sources, pinned by commit/version and
# checked against SHA-256 sums. Nothing of it is kept in this repository: the Sense app source and
# Megvii's prebuilt face libraries come from Evolution-X, the compilers from their own releases.
# Rerun only to reproduce or to move a pin; build.sh never needs the network.
#
#   FaceUnlock/              Evolution-X packages_apps_FaceUnlock (Apache-2.0, from Paranoid
#                            Android) at FACEUNLOCK_COMMIT, with yamalar/*.patch applied. Its lib/
#                            holds Megvii's face detection libraries and vendorImplPrebuilt.jar,
#                            prebuilt binaries under Megvii's terms, used as shipped.
#   tools/kotlinc/           Kotlin compiler (Apache-2.0) from JetBrains' GitHub release
#   tools/dex-tools-v2.4/    dex2jar (Apache-2.0) from pxb1988/dex2jar's GitHub release: turns the
#                            image's framework and core library dex into compile-time class jars
#
# The AIDL compiler comes from the Android SDK's build-tools 35.0.0 (sdkmanager
# "build-tools;35.0.0"): newer ones generate different, equally valid Java, and the build pins
# the generated code.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FACEUNLOCK_COMMIT=817e02a974215b100915814267fabbfdb5961f88   # branch cnb, 2025-11-11
# SHA-256 over "sha256  path" lines of every file (sorted), after the patches are applied:
# GitHub may regenerate archive bytes, the file contents stay the same.
FACEUNLOCK_TREE_SHA256=0645d7d608b95d0d5ec2544e5f133a6ba476b0fe8ce3115a8ad01c9c7fd8de9d
KOTLIN_VERSION=1.9.24
KOTLIN_SHA256=eb7b68e01029fa67bc8d060ee54c12018f2c60ddc438cf21db14517229aa693b
DEX2JAR_VERSION=2.4
DEX2JAR_SHA256=ee7c45eb3c1d2474a6145d8d447e651a736a22d9664b6d3d3be5a5a817dda23a

work="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-yuz-getir.XXXXXX")"
trap 'rm -rf "$work"' EXIT

tree_sha256() {  # tree_sha256 DIR
    (cd "$1" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum |
        cut -d' ' -f1)
}

check() {  # check WHAT EXPECTED ACTUAL
    if [[ "$2" != "$3" ]]; then
        echo "$1: SHA-256 mismatch: expected $2, got $3" >&2
        exit 1
    fi
}

echo "== packages_apps_FaceUnlock $FACEUNLOCK_COMMIT"
curl -fsSL -o "$work/faceunlock.tar.gz" \
    "https://codeload.github.com/Evolution-X/packages_apps_FaceUnlock/tar.gz/$FACEUNLOCK_COMMIT"
mkdir "$work/FaceUnlock"
tar -xzf "$work/faceunlock.tar.gz" -C "$work/FaceUnlock" --strip-components=1
for patch in "$HERE"/yamalar/*.patch; do
    patch -d "$work/FaceUnlock" -p1 --batch --forward --silent --no-backup-if-mismatch < "$patch"
done
check FaceUnlock "$FACEUNLOCK_TREE_SHA256" "$(tree_sha256 "$work/FaceUnlock")"
rm -rf "$HERE/FaceUnlock"
mv "$work/FaceUnlock" "$HERE/FaceUnlock"

mkdir -p "$HERE/tools"

echo "== kotlinc $KOTLIN_VERSION"
curl -fsSL -o "$work/kotlin.zip" \
    "https://github.com/JetBrains/kotlin/releases/download/v$KOTLIN_VERSION/kotlin-compiler-$KOTLIN_VERSION.zip"
check kotlinc "$KOTLIN_SHA256" "$(sha256sum "$work/kotlin.zip" | cut -d' ' -f1)"
unzip -q "$work/kotlin.zip" -d "$work/kotlin"
rm -rf "$HERE/tools/kotlinc"
mv "$work/kotlin/kotlinc" "$HERE/tools/kotlinc"

echo "== dex2jar $DEX2JAR_VERSION"
curl -fsSL -o "$work/dex2jar.zip" \
    "https://github.com/pxb1988/dex2jar/releases/download/v$DEX2JAR_VERSION/dex-tools-v$DEX2JAR_VERSION.zip"
check dex2jar "$DEX2JAR_SHA256" "$(sha256sum "$work/dex2jar.zip" | cut -d' ' -f1)"
unzip -q "$work/dex2jar.zip" -d "$work/dex2jar"
rm -rf "$HERE/tools/dex-tools-v$DEX2JAR_VERSION"
mv "$work/dex2jar/dex-tools-v$DEX2JAR_VERSION" "$HERE/tools/dex-tools-v$DEX2JAR_VERSION"

echo "ok"
