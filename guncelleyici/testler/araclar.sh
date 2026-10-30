#!/usr/bin/env bash
# Builds the shell and userland the tablet's recovery runs update-binary with, for the tests on
# this computer: mksh R59 (recovery: "MIRBSD KSH R59 2020/10/31 Android") and toybox 0.8.13
# (recovery: "0.8.13-android"), both read from the LineageOS recovery image in
# yedek-20261020/recovery.img. Sources are pinned by SHA-256 (toybox: landley.net tarball,
# identical to the GitHub tag 0.8.13; mksh: mirbsd.org).
#
#   testler/araclar.sh          # prints the bin directory (builds once)
#
# Output: ${XDG_CACHE_HOME:-~/.cache}/gbos-guncelleyici/bin/{sh,toybox,<applet links>}
set -euo pipefail

CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/gbos-guncelleyici"
SRC="$CACHE/kaynak"
BIN="$CACHE/bin"
TOYBOX_URL=https://landley.net/toybox/downloads/toybox-0.8.13.tar.gz
TOYBOX_SHA256=9d4c124d7d731a2db399f6278baa2b42c2e3511f610c6ad30cc3f1a52581334b
MKSH_URL=http://www.mirbsd.org/MirOS/dist/mir/mksh/mksh-R59.tgz
MKSH_SHA256=592a28ba67bea8a285f003d7a5d21b65e718546c8fcb375d7d696f3d5dd390ba
STAMP="$BIN/.surum"
WANT="toybox $TOYBOX_SHA256 +expr+tr mksh $MKSH_SHA256"
# toybox applets update-binary and the Android side call
APPLETS="cat cut date dd mkdir mv rm sha256sum sync blockdev readlink printf head tail tr sleep
         grep sed stat ls cmp wc od expr"

if [[ -f "$STAMP" && "$(cat "$STAMP")" == "$WANT" ]]; then
    echo "$BIN"
    exit 0
fi

fetch() {  # url sha file
    if [[ ! -f "$3" ]] || ! echo "$2  $3" | sha256sum -c --quiet - 2>/dev/null; then
        curl -fsSL --retry 3 -o "$3.tmp" "$1"
        mv "$3.tmp" "$3"
    fi
    echo "$2  $3" | sha256sum -c --quiet -
}

mkdir -p "$SRC" "$BIN"
fetch "$TOYBOX_URL" "$TOYBOX_SHA256" "$SRC/toybox-0.8.13.tar.gz"
fetch "$MKSH_URL" "$MKSH_SHA256" "$SRC/mksh-R59.tgz"
work="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-araclar.XXXXXX")"
trap 'rm -rf "$work"' EXIT

tar xzf "$SRC/toybox-0.8.13.tar.gz" -C "$work"
# defconfig plus the pending applets Android's toybox build enables and the scripts use
(cd "$work/toybox-0.8.13" && make defconfig >/dev/null &&
    sed -i -e 's/^# CONFIG_EXPR is not set$/CONFIG_EXPR=y/' -e 's/^# CONFIG_TR is not set$/CONFIG_TR=y/' .config &&
    grep -q '^CONFIG_EXPR=y$' .config && grep -q '^CONFIG_TR=y$' .config &&
    make -j"$(nproc)" toybox >/dev/null)
install -m 755 "$work/toybox-0.8.13/toybox" "$BIN/toybox"

tar xzf "$SRC/mksh-R59.tgz" -C "$work"
(cd "$work/mksh" && sh ./Build.sh -r >/dev/null 2>&1)
install -m 755 "$work/mksh/mksh" "$BIN/sh"

for a in $APPLETS; do
    ln -sf toybox "$BIN/$a"
done
echo "$WANT" > "$STAMP"
echo "$BIN"
