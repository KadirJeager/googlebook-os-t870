#!/usr/bin/env bash
# Builds the host tools dogrula needs into ${GBOS_SELINUX_CACHE:-~/.cache/gbos-selinux}/bin:
#   secilc       AOSP's CIL compiler (external/selinux, android17-release), the program init runs
#                on the device; compiling the tablet's LineageOS policy with it reproduces
#                odm/etc/selinux/precompiled_sepolicy byte for byte
#   avc_kontrol  asks a compiled policy about access vectors (araclar/avc_kontrol.c)
# Both link AOSP's libsepol statically. AOSP's libsepol differs from upstream's in Android
# specifics (e.g. it always sets the netlink route/getneigh policy config bits).
set -euo pipefail

COMMIT=a8c5e74f85f869bf236a01ba24181b78f5e39a52  # android17-release, 2026-03-25
# sha256 over "sha256sum" lines of every extracted file, sorted by path (gitiles regenerates
# the .tar.gz, so the archive bytes are not stable but its contents are)
declare -A TREE_SHA256=(
  [libsepol]=76d3dbe17f9c6a828113672d302ce64b497d81415fd55d1c378de276a417dc85
  [secilc]=fe2db99eec5b01758864009fab234a3be5f63b3c85acdb30acec71577fd059cd
)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="${GBOS_SELINUX_CACHE:-$HOME/.cache/gbos-selinux}"
BIN="$CACHE/bin"
STAMP="aosp-$COMMIT"

if [[ -x "$BIN/secilc" && -x "$BIN/avc_kontrol" && "$BIN/avc_kontrol" -nt "$HERE/avc_kontrol.c" \
      && "$(cat "$BIN/VERSION" 2>/dev/null)" == "$STAMP" ]]; then
  exit 0
fi

BUILD="$(mktemp -d "${TMPDIR:-/tmp}/gbos-selinux-build.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT
mkdir -p "$BIN"
for pkg in libsepol secilc; do
  curl -fsSL -o "$BUILD/$pkg.tar.gz" \
    "https://android.googlesource.com/platform/external/selinux/+archive/$COMMIT/$pkg.tar.gz"
  mkdir "$BUILD/$pkg"
  tar -xzf "$BUILD/$pkg.tar.gz" -C "$BUILD/$pkg"
  got="$(cd "$BUILD/$pkg" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)"
  if [[ "$got" != "${TREE_SHA256[$pkg]}" ]]; then
    echo "kur.sh: $pkg@$COMMIT content hash $got, expected ${TREE_SHA256[$pkg]}" >&2
    exit 1
  fi
done

# AOSP builds libsepol with Soong; its upstream Makefile works once -Werror is off (an
# Android-only static helper in cil/src/android.c is unused outside Soong's build).
make -C "$BUILD/libsepol" -s -j"$(nproc)" DESTDIR="$BUILD/inst" \
  CFLAGS="-O2 -Wall -Wno-error -Wno-unused-function -fno-semantic-interposition" install >/dev/null
INC="$BUILD/inst/usr/include"
LIB="$(find "$BUILD/inst" -name libsepol.a | head -n1)"
gcc -O2 -I"$INC" -o "$BIN/secilc" "$BUILD/secilc/secilc.c" "$LIB"
gcc -O2 -Wall -Werror -I"$INC" -o "$BIN/avc_kontrol" "$HERE/avc_kontrol.c" "$LIB"
echo "$STAMP" > "$BIN/VERSION"
echo "selinux tools ($STAMP) -> $BIN"
