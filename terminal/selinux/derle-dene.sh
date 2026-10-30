#!/usr/bin/env bash
# Compiles the tablet's policy stack the way init does (mica plat/system_ext/product from the
# work tree, LineageOS vendor/odm from $GBOS_WORK/lineage/bolumler, lineage-hazirla.py) with the
# Linux environment's policy added ($CIL, default selinux/politika/plat/60-gbos-linux.cil, selinux-port's file),
# using selinux-port's secilc (~/.cache/gbos-selinux/bin, built by selinux/araclar/kur.sh).
# Exit 0 = the CIL compiles against the real policy. Output policy: $OUT (default: a temp file).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$(cd "$HERE/../.." && pwd)"
WORK="${GBOS_WORK:-/var/tmp/mica_work}"
SECILC="${SECILC:-$HOME/.cache/gbos-selinux/bin/secilc}"
LIN="$WORK/lineage/bolumler"
CIL="${CIL:-$TOOLS/selinux/politika/plat/60-gbos-linux.cil}"
SYS="$WORK/sys/system/etc/selinux"
EXT="$WORK/sysext/etc/selinux"
PROD="$WORK/prod/etc/selinux"
V="$(head -1 "$LIN/vendor/etc/selinux/plat_sepolicy_vers.txt")"
G="$(head -1 "$LIN/vendor/etc/selinux/genfs_labels_version.txt")"
tmp="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-cil.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
# The image's plat policy is rebuilt from pristine by yamalar/selinux.py; for this check the
# work tree's copy (any earlier inline additions included) is good enough as the base.
cp "$SYS/plat_sepolicy.cil" "$tmp/plat.cil"
printf '\n' >> "$tmp/plat.cil"
cat "$CIL" >> "$tmp/plat.cil"
args=("$tmp/plat.cil" -m -M true -G -N -c 30 "$SYS/mapping/$V.cil")
for f in "$SYS/mapping/$V.compat.cil" "$EXT/system_ext_sepolicy.cil" "$EXT/mapping/$V.cil" \
         "$EXT/mapping/$V.compat.cil" "$PROD/product_sepolicy.cil" "$PROD/mapping/$V.cil" \
         "$LIN/vendor/etc/selinux/plat_pub_versioned.cil" "$LIN/vendor/etc/selinux/vendor_sepolicy.cil" \
         "$LIN/odm/etc/selinux/odm_sepolicy.cil" "$SYS/plat_sepolicy_genfs_$G.cil"; do
    [[ -e "$f" ]] && args+=("$f")
done
"$SECILC" "${args[@]}" -o "${OUT:-$tmp/policy}" -f /dev/null
echo "ok: $(basename "$CIL") compiles with the tablet's policy stack"
