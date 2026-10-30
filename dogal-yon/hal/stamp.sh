#!/usr/bin/env bash
# Prints the hash of everything out/ is built from, so a stale build is noticed
# (yamalar/dogal_yon_hal.py). The upstream sources enter through their pinned commits and content
# hashes in kaynaklar.lock, the vendor libraries through the LineageOS zip's name and SHA-256 as
# lineage-hazirla.py recorded them in the work directory (the tablet's kit carries that record),
# the panel rotation through yamalar/dogal_yon.py. As for the other
# prebuilt binaries of the pipeline, neither the mica release nor a machine path is part of it:
# the NDK and build-tools versions are pinned in build.sh, which is hashed, and the release being
# patched is checked on its own (dogal_yon_hal.uyum_denetle: araclar/denetim.py against that
# release's LLNDK libraries).
set -euo pipefail
# The file order must not depend on the machine's locale (sort): the tablet's kit runs in C.
export LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
{
    find build.sh stamp.sh kaynaklar.lock araclar kaynak-yamalari src -type f ! -name '*.pyc' -print0 \
        | sort -z | xargs -0 sha256sum
    sha256sum ../../pil-sagligi/araclar/kaynak-getir.py ../../pil-sagligi/araclar/hidl-gen-kur.sh \
        ../../pil-sagligi/araclar/cihaz-kutuphaneleri.py ../../pil-sagligi/araclar/sembol-denetimi.py \
        ../../lineage-dosya-cikar.py
    python3 -I -c "import sys; sys.path.insert(0, '../../yamalar'); import dogal_yon; print('PANEL_ROTATION', dogal_yon.PANEL_ROTATION)"
    K="${GBOS_WORK:-/var/tmp/mica_work}/lineage/bolumler/KAYNAK"
    [[ -f "$K" ]] || { echo "$K missing: run lineage-hazirla.py first" >&2; exit 1; }
    cut -d' ' -f1,3 "$K"
} | sha256sum | cut -d' ' -f1
