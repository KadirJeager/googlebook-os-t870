#!/usr/bin/env bash
# Prepares the tablet's Linux environment (gbos-linux, Debian trixie, run as root) for motor.py and
# the image pipeline it runs (mica-indir.py, yeni-surum-hazirla.py, apply_patches.py with yamalar/,
# make_ext4_images.py, guncelleyici/yerel/derle.py), then checks every tool by using it.
#
#   ortam-kur.sh            install what is missing, check everything; exit 0 only when all works
#   ortam-kur.sh --denetle  check only, install nothing
#
# Re-runnable: packages already installed and tools already built are left alone (apt-get is not
# even called then). The log goes to stdout; a failed check is listed at the end and makes the exit
# code 1 (2: not root / not Debian).
#
# What is installed, and why (measured against the pipeline's calls on 2026-10-24,
# guncelleyici/kanit/ortam-20261024):
#   erofs-utils               fsck.erofs (mica-indir, apply_patches --extract), dump.erofs, mkfs.erofs
#   e2fsprogs                 mke2fs, e2fsck, debugfs (make_ext4_images, motor's denetle step)
#   e2fsdroid                 not in Debian: AOSP android17-release build, e2fsdroid-kur.sh
#   openjdk-21-jdk-headless   javac (yamalar/kamera_jpeg), java for the kit's SDK d8.jar/apksigner.jar
#   zipalign dexdump          apply_patches (zipalign, dexdump), kamera_jpeg (dexdump)
#   binutils                  nm (yerel/derle.py), readelf (hazir_ikili, dogal_yon_hal, sembol-denetimi)
#   gcc libc6-dev make flex   cc (yerel/derle.py), selinux/araclar/kur.sh (libsepol: make, flex)
#   curl ca-certificates      kur.sh and e2fsdroid-kur.sh downloads
#   g++ libpcre2-dev zlib1g-dev android-lib{base,log,sparse}-dev   e2fsdroid-kur.sh
#   secilc, avc_kontrol       not Debian's (secilc 3.8.1 does not reproduce the device's policy):
#                             AOSP build by selinux/araclar/kur.sh, into the cache yamalar/selinux.py
#                             reads (GBOS_SELINUX_CACHE or $HOME/.cache/gbos-selinux)
# Deliberately not installed: aapt (aosp_terminal reads the manifest itself; Debian's aapt2 2.19
# does not open newer releases' APKs), apksigner (depends on a desktop JRE, 111 more packages; the kit's
# SDK apksigner.jar runs on the JDK above), secilc/checkpolicy (see above), brotli (only for the
# LineageOS zip, which stays on the computer).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT="$(cd "$HERE/../.." && pwd)"
SELINUX_KUR="$KIT/selinux/araclar/kur.sh"
PAKETLER=(erofs-utils e2fsprogs openjdk-21-jdk-headless zipalign dexdump binutils gcc libc6-dev
          make flex curl ca-certificates g++ libpcre2-dev zlib1g-dev android-libbase-dev
          android-liblog-dev android-libsparse-dev)
# Debian's base system, used by the scripts (cp, find, sort, xargs, sha256sum, tar, gzip, mktemp;
# bash runs the kit's own scripts, which the environment may not execute from /data/gbos-ota)
TEMEL=(coreutils findutils tar gzip python3 bash)
# Packages earlier kits installed and nothing uses any more: removed when present (aapt: the
# pipeline reads manifests itself since 24 Oct 2026, yamalar/guncelleyici.py manifest_package)
ARTIK=(aapt)
DENETLE_YALNIZ=0
[[ "${1:-}" == "--denetle" ]] && DENETLE_YALNIZ=1

HATALAR=()
log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }
basarisiz() { HATALAR+=("$1"); log "HATA: $1"; }

if [[ "$(id -u)" != 0 ]]; then
  log "ortam-kur.sh root olarak çalışmalı (gbos-linux run -- ...)"
  exit 2
fi
. /etc/os-release 2>/dev/null || true
if [[ "${ID:-}" != debian ]]; then
  log "Debian ortamı bekleniyor, bulunan: ${PRETTY_NAME:-bilinmiyor}"
  exit 2
fi
log "ortam: ${PRETTY_NAME:-debian} $(dpkg --print-architecture), kit: $KIT"

# --- packages -----------------------------------------------------------------------------------
eksik_paketler() {
  local p
  for p in "${PAKETLER[@]}" "${TEMEL[@]}"; do
    [[ "$(dpkg-query -W -f '${db:Status-Status}' "$p" 2>/dev/null)" == installed ]] || echo "$p"
  done
}
mapfile -t EKSIK < <(eksik_paketler)
if ((${#EKSIK[@]})); then
  if ((DENETLE_YALNIZ)); then
    basarisiz "eksik paketler: ${EKSIK[*]}"
  else
    log "kurulacak paketler: ${EKSIK[*]}"
    export DEBIAN_FRONTEND=noninteractive
    if ! apt-get update -q; then
      basarisiz "apt-get update başarısız"
    elif ! apt-get install -y -q --no-install-recommends "${EKSIK[@]}"; then
      basarisiz "apt-get install başarısız: ${EKSIK[*]}"
    fi
    mapfile -t EKSIK < <(eksik_paketler)
    ((${#EKSIK[@]})) && basarisiz "kurulamayan paketler: ${EKSIK[*]}"
  fi
else
  log "paketler kurulu (${#PAKETLER[@]} + temel ${#TEMEL[@]})"
fi
for p in "${PAKETLER[@]}"; do
  printf '  %-26s %s\n' "$p" "$(dpkg-query -W -f '${Version}' "$p" 2>/dev/null || echo yok)"
done
mapfile -t KALAN < <(for p in "${ARTIK[@]}"; do
  [[ "$(dpkg-query -W -f '${db:Status-Status}' "$p" 2>/dev/null)" == installed ]] && echo "$p"; done)
if ((${#KALAN[@]})) && ! ((DENETLE_YALNIZ)); then
  log "artık kullanılmayan paketler kaldırılıyor: ${KALAN[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get purge -y -q "${KALAN[@]}" && apt-get autoremove -y -q --purge ||
    basarisiz "kaldırılamayan paketler: ${KALAN[*]}"
fi

# --- built tools ------------------------------------------------------------------------------------
if ((DENETLE_YALNIZ)); then
  [[ "$(cat /usr/local/share/gbos-ortam/e2fsdroid.damga 2>/dev/null)" == "$(bash "$HERE/e2fsdroid-kur.sh" --damga)" ]] \
    || basarisiz "e2fsdroid kurulu değil ya da eski (e2fsdroid-kur.sh)"
else
  bash "$HERE/e2fsdroid-kur.sh" || basarisiz "e2fsdroid derlenemedi (e2fsdroid-kur.sh)"
fi
SELINUX_BIN="${GBOS_SELINUX_CACHE:-$HOME/.cache/gbos-selinux}/bin"
if [[ ! -f "$SELINUX_KUR" ]]; then
  basarisiz "kitte selinux/araclar/kur.sh yok: $SELINUX_KUR"
elif ((DENETLE_YALNIZ)); then
  :  # checked below with the SELinux tools' own test
else
  log "SELinux araçları ($SELINUX_KUR -> $SELINUX_BIN)"
  bash "$SELINUX_KUR" || basarisiz "selinux/araclar/kur.sh başarısız"
fi

# --- checks: every tool is used the way the pipeline uses it -----------------------------------
DENEME="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-ortam-denetim.XXXXXX")"
trap 'rm -rf "$DENEME"' EXIT
surum() {  # surum COMMAND ARGS...  : first line of the version output; fails if COMMAND is absent
  local out
  if ! command -v "$1" >/dev/null 2>&1; then
    basarisiz "$1 yok"
    return 1
  fi
  out="$("$@" 2>&1 | grep -v '^$' | head -n1)"
  printf '  %-13s %s\n' "$(basename "$1")" "$out"
}
paket_surum() {  # paket_surum COMMAND PACKAGE  : for tools without a version option
  if ! command -v "$1" >/dev/null 2>&1; then
    basarisiz "$1 yok"
    return 1
  fi
  printf '  %-13s %s (Debian paketi %s)\n' "$1" "$(dpkg-query -W -f '${Version}' "$2")" "$2"
}
log "araç sürümleri"
surum python3 -V
surum fsck.erofs --version
surum dump.erofs --version
surum mkfs.erofs --version
surum mke2fs -V
surum e2fsck -V
surum debugfs -V
if command -v e2fsdroid >/dev/null 2>&1; then
  printf '  %-13s %s: AOSP %s\n' e2fsdroid "$(command -v e2fsdroid)" \
    "$(cut -d' ' -f1-3 /usr/local/share/gbos-ortam/e2fsdroid.damga 2>/dev/null)"
else
  basarisiz "e2fsdroid yok"
fi
surum java -version
surum javac -version
paket_surum zipalign zipalign
paket_surum dexdump dexdump
surum nm --version
surum readelf --version
surum cc --version
surum g++ --version
surum make --version
surum flex --version
surum curl --version
if [[ -x "$SELINUX_BIN/secilc" && -x "$SELINUX_BIN/avc_kontrol" ]]; then
  printf '  %-13s %s (%s)\n' secilc "$SELINUX_BIN/secilc" "$(cat "$SELINUX_BIN/VERSION" 2>/dev/null)"
  printf '  %-13s %s\n' avc_kontrol "$SELINUX_BIN/avc_kontrol"
else
  printf '  %-13s yok (%s)\n' secilc "$SELINUX_BIN"
fi

log "erofs: mkfs.erofs -zlz4hc, fsck.erofs --extract --preserve --xattrs, dump.erofs --cat"
mkdir -p "$DENEME/erofs/agac/alt"
printf 'gbos\n' >"$DENEME/erofs/agac/alt/dosya"
if mkfs.erofs -zlz4hc -b 4096 -T 0 "$DENEME/erofs/i.erofs" "$DENEME/erofs/agac" >/dev/null 2>&1 &&
   fsck.erofs --extract="$DENEME/erofs/cikti" --preserve --xattrs "$DENEME/erofs/i.erofs" >/dev/null 2>&1 &&
   [[ "$(dump.erofs --cat --path=/alt/dosya "$DENEME/erofs/i.erofs" 2>/dev/null)" == gbos ]] &&
   cmp -s "$DENEME/erofs/agac/alt/dosya" "$DENEME/erofs/cikti/alt/dosya"; then
  log "  tamam"
else
  basarisiz "erofs-utils deneme imajında çalışmadı"
fi

log "ext4: mke2fs + e2fsdroid -e -T -f -C -S -a + debugfs + e2fsck (make_ext4_images bayrakları)"
E="$DENEME/ext4"
mkdir -p "$E/agac/bin" "$E/agac/etc"
printf '#!/system/bin/sh\n' >"$E/agac/bin/arac"
printf 'x\n' >"$E/agac/etc/ayar"
cat >"$E/fs_config" <<'FSC'
/ 0 0 0755
/system_ext 0 0 0755
/system_ext/bin 0 2000 0751
/system_ext/bin/arac 1000 2000 04750 capabilities=0x800000400
/system_ext/etc 0 0 0755
/system_ext/etc/ayar 0 0 0644
FSC
cat >"$E/file_contexts" <<'FC'
/system_ext(/.*)? u:object_r:system_file:s0
/system_ext/bin/arac u:object_r:gbos_deneme_exec:s0
FC
if E2FSPROGS_FAKE_TIME=1761300000 mke2fs -q -F -I 256 -N 64 -M /system_ext -m 0 -t ext4 -b 4096 \
     -O ^has_journal,^metadata_csum,^64bit "$E/i.img" 1024 &&
   E2FSPROGS_FAKE_TIME=1761300000 e2fsdroid -e -T 1761300000 -f "$E/agac" -C "$E/fs_config" \
     -S "$E/file_contexts" -a /system_ext "$E/i.img" &&
   e2fsck -fn "$E/i.img" >/dev/null 2>&1; then
  st="$(LC_ALL=C debugfs -R 'stat /bin/arac' "$E/i.img" 2>/dev/null)"
  if [[ "$st" == *"Mode:  04750"* && "$st" == *"User:  1000   Group:  2000"* &&
        "$st" == *'security.selinux (31) = "u:object_r:gbos_deneme_exec:s0\000"'* &&
        "$st" == *"security.capability (20) = 01 00 00 02 00 04 00 00 00 00 00 00 08 00 00 00 00 00 00 00"* ]]; then
    log "  tamam (sahip, kip, setuid, capability, security.selinux)"
  else
    basarisiz "e2fsdroid imaja beklenen sahip/kip/capability/etiketi yazmadı"
    printf '%s\n' "$st"
  fi
else
  basarisiz "mke2fs/e2fsdroid/e2fsck deneme imajında çalışmadı"
fi

log "zipalign -f -p 4 ve zipalign -c -p 4"
if python3 -I -c "
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'w') as z:
    z.writestr('a/', b'')
    z.writestr(zipfile.ZipInfo('b.txt'), b'x' * 3)
    z.writestr(zipfile.ZipInfo('classes.dex'), b'dex\n039\0' + bytes(100))
" "$DENEME/z.jar" && zipalign -f -p 4 "$DENEME/z.jar" "$DENEME/z-hizali.jar" &&
   zipalign -c -p 4 "$DENEME/z-hizali.jar" >/dev/null; then
  log "  tamam"
else
  basarisiz "zipalign çalışmadı"
fi

log "JDK: javac --release 17 + java"
mkdir -p "$DENEME/java"
printf 'public class Deneme { public static void main(String[] a) { System.out.println("gbos " + a.length); } }\n' \
  >"$DENEME/java/Deneme.java"
if javac --release 17 -d "$DENEME/java" "$DENEME/java/Deneme.java" &&
   [[ "$(java -cp "$DENEME/java" Deneme x)" == "gbos 1" ]]; then
  log "  tamam"
else
  basarisiz "javac/java çalışmadı"
fi

# The kit carries the SDK jars (build-tools d8.jar, apksigner.jar); checked when present.
SDK="${ANDROID_HOME:-${ANDROID_SDK:-$HOME/Android/Sdk}}"
BT="$SDK/build-tools/37.0.0"
if [[ -f "$BT/lib/d8.jar" ]]; then
  log "d8.jar + dexdump ($BT)"
  if java -cp "$BT/lib/d8.jar" com.android.tools.r8.D8 --min-api 34 --no-desugaring --output "$DENEME/java" \
       "$DENEME/java/Deneme.class" && dexdump -d "$DENEME/java/classes.dex" 2>/dev/null | grep -q "Class descriptor  : 'LDeneme;'"; then
    log "  tamam"
  else
    basarisiz "d8.jar ya da dexdump çalışmadı"
  fi
else
  log "d8.jar yok ($BT): kit taşıyınca denetlenir"
fi
if [[ -f "$BT/lib/apksigner.jar" ]]; then
  if out="$(java -jar "$BT/lib/apksigner.jar" --version 2>&1)"; then
    printf '  %-13s %s\n' apksigner "$out"
  else
    basarisiz "apksigner.jar çalışmadı: $out"
  fi
else
  log "apksigner.jar yok ($BT): kit taşıyınca denetlenir"
fi

log "SELinux: secilc (AOSP) en küçük CIL politikası + avc_kontrol"
cat >"$DENEME/min.cil" <<'CIL'
(class sinif (izin))
(classorder (sinif))
(sid kernel)
(sidorder (kernel))
(user k)
(role r)
(type t)
(type u)
(category c0)
(categoryorder (c0))
(sensitivity s0)
(sensitivityorder (s0))
(sensitivitycategory s0 (c0))
(allow t self (sinif (izin)))
(roletype r t)
(roletype r u)
(userrole k r)
(userlevel k (s0))
(userrange k ((s0)(s0 (c0))))
(sidcontext kernel (k r t ((s0)(s0))))
CIL
if [[ ! -x "$SELINUX_BIN/secilc" || ! -x "$SELINUX_BIN/avc_kontrol" ]]; then
  basarisiz "secilc/avc_kontrol yok: $SELINUX_BIN"
elif "$SELINUX_BIN/secilc" -M true "$DENEME/min.cil" -o "$DENEME/min.pol" -f /dev/null &&
   [[ "$(printf 'k:r:t:s0 k:r:t:s0 sinif izin\nk:r:t:s0 k:r:u:s0 sinif izin\n' |
         "$SELINUX_BIN/avc_kontrol" "$DENEME/min.pol" | cut -d' ' -f1 | tr '\n' ' ')" == "ALLOW DENY-TE " ]]; then
  log "  tamam"
else
  basarisiz "secilc/avc_kontrol çalışmadı ($SELINUX_BIN)"
fi

log "meta veri kütüphaneleri derleyicisi: cc + nm (guncelleyici/yerel/derle.py)"
printf '#include <dlfcn.h>\nint gbos_deneme(void){return dlopen(0, RTLD_NOW) != 0;}\n' >"$DENEME/d.c"
if cc -O2 -shared -fPIC -o "$DENEME/libd.so" "$DENEME/d.c" -ldl &&
   nm -D --defined-only "$DENEME/libd.so" | grep -q gbos_deneme &&
   readelf -d "$DENEME/libd.so" | grep -q NEEDED; then
  log "  tamam"
else
  basarisiz "cc/nm/readelf çalışmadı"
fi

if ((${#HATALAR[@]})); then
  log "ortam HAZIR DEĞİL (${#HATALAR[@]} hata):"
  printf '  - %s\n' "${HATALAR[@]}"
  exit 1
fi
log "ortam hazır"
