#!/bin/sh
# Googlebook OS (SM-T870): the whole image chain after a change, each step only when the previous
# one passed: apply_patches.py -> yama-dogrula.py (0 differences) -> testler/ (pytest) ->
# dex-dogrula.py (the tablet's ART verifier over the patched jars; the tablet must be in Android,
# GBOS_DEX_DOGRULA=atla skips it on purpose) -> the image set ($GBOS_WORK/denetim/imaj) and, on
# request, a set that reports an older mica build (surum-dusur-imaj.py, for testing the updater).
# Heavy steps go through the kaynak gate when the machine has one (kapi.sh).
# Logs: $GBOS_WORK/denetim/gunluk/ (GBOS_WORK defaults to /var/tmp/mica_work).
#   T870-araclar/imaj-uret.sh                 # the image set
#   T870-araclar/imaj-uret.sh surum-dusur     # also the older-build set (denetim/surum-dusur)
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${GBOS_WORK:-/var/tmp/mica_work}
export GBOS_WORK="$WORK"
LOG=$WORK/denetim/gunluk
K="$HERE/kapi.sh"
mkdir -p "$LOG"
cd "$HERE"
# every set records the commit it was built from: a set built before a fix was committed looks
# identical otherwise (24 Oct: a "fixed" .273 went to the tablet without the fix)
KAYNAK=$(git rev-parse HEAD)
[ -z "$(git status --porcelain)" ] || KAYNAK="$KAYNAK+kirli"
python3 -I guncelleyici/yerel/derle.py > "$LOG/yerel-derle.log" 2>&1
guncelleyici/build.sh > "$LOG/guncelleyici-build.log" 2>&1
python3 -I apply_patches.py > "$LOG/apply.log" 2>&1
python3 -I yama-dogrula.py > "$LOG/yama-dogrula.log" 2>&1
# each suite has its own conftest.py (tests import it by name), so they run separately
python3 -I -m pytest testler -q > "$LOG/testler.log" 2>&1
python3 -I -m pytest guncelleyici/testler -q > "$LOG/testler-guncelleyici.log" 2>&1
if [ "${GBOS_DEX_DOGRULA:-}" = atla ]; then
    echo "dex-dogrula ATLANDI (GBOS_DEX_DOGRULA=atla): yamalı jar'lar tablette doğrulanmadı" | tee "$LOG/dex-dogrula.log"
else
    python3 -I dex-dogrula.py > "$LOG/dex-dogrula.log" 2>&1 || { cat "$LOG/dex-dogrula.log"; exit 1; }
fi
# SHA256SUMS, KAYNAK and hazir.conf (the updater's ready-set file, version from product build.prop)
muhurle() {
    (cd "$1" && sha256sum system.img system_ext.img product.img > SHA256SUMS
     echo "$KAYNAK" > KAYNAK
     bp=$(debugfs -R "cat /etc/build.prop" product.img 2>/dev/null)
     inc=$(printf '%s\n' "$bp" | sed -n 's/^ro.product.build.version.incremental=//p')
     id=$(printf '%s\n' "$bp" | sed -n 's/^ro.product.build.id=//p')
     [ -n "$inc" ] && [ -n "$id" ] || { echo "product build.prop okunamadı: $1" >&2; exit 1; }
     { echo "surum ${inc}_${id}"
       for p in system system_ext product; do echo "bolum $p $(grep " $p.img\$" SHA256SUMS | cut -d' ' -f1)"; done
       echo "kaynak bilgisayar $KAYNAK"; } > hazir.conf)
}
# a set about to be rebuilt is replaced (the fallback sets live in denetim/bilinen-iyi-*); removing
# it first keeps the gate's disk floor reachable, and btrfs frees the extents asynchronously
yer_ac() {
    rm -f "$1"/*.img "$1"/SHA256SUMS "$1"/hazir.conf "$1"/KAYNAK
    sync; for i in $(seq 1 30); do [ "$(df --output=avail -B1G "$1" | tail -1)" -ge 30 ] && break; sleep 2; done
}
if [ "${1:-}" = surum-dusur ]; then
    mkdir -p "$WORK/denetim/surum-dusur"
    yer_ac "$WORK/denetim/surum-dusur"
    "$K" --gb 2 --tur derleme --ad "GooglebookOS: eski sürüm ext4 imajları" --disk-gb 5 --yol "$WORK" \
        -- python3 -I surum-dusur-imaj.py > "$LOG/surum-dusur.log" 2>&1
    muhurle "$WORK/denetim/surum-dusur"
fi
mkdir -p "$WORK/denetim/imaj"
yer_ac "$WORK/denetim/imaj"
"$K" --gb 2 --tur derleme --ad "GooglebookOS: ext4 imajları" --disk-gb 5 --yol "$WORK" \
    -- python3 -I make_ext4_images.py > "$LOG/ext4-273.log" 2>&1
cd "$WORK"
mv system.img system_ext.img product.img denetim/imaj/
for p in system system_ext product; do mv "${p}_new_file_labels.txt" denetim/imaj/; done
muhurle "$WORK/denetim/imaj"
echo "İMAJLAR HAZIR: $WORK/denetim/imaj"
