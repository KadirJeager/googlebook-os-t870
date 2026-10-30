#!/usr/bin/env bash
# Builds AOSP's e2fsdroid for the tablet's Linux environment (Debian trixie) and installs it as
# ${E2FSDROID_HEDEF:-/usr/local/bin/e2fsdroid}. Debian no longer ships it (android-sdk-ext4-utils
# was removed); make_ext4_images.py runs it as  e2fsdroid -e -T -f -C -S -a IMAGE.
#
# The sources are the ones the computer's e2fsdroid is built from (Arch android-tools 37.0.0.p1
# vendors exactly these android17-release commits), taken from the official AOSP repositories:
#   external/e2fsprogs  lib, misc, contrib/android    libext2fs, com_err, quota, create_inode, e2fsdroid
#   system/core         libcutils                     canned_fs_config.cpp (-C), fs_config.cpp
#   external/selinux    libselinux, libsepol/include  file_contexts lookup (-S; label_file + pcre2)
# Source lists are those of the pinned Android.bp files; libselinux gets the source subset and the
# defines android-tools builds it with. Generic helpers that do not decide what goes into the image
# come from Debian: android-libbase (strings, logging), android-liblog, android-libsparse (sparse
# output only, never used with -e), libpcre2-8, zlib.
#
# Idempotent: nothing is done while the installed binary carries the stamp of these pins and of
# this script. Build needs (ortam-kur.sh installs them): curl tar gzip gcc g++ libc6-dev
# libpcre2-dev zlib1g-dev android-libbase-dev android-liblog-dev android-libsparse-dev.
#
#   e2fsdroid-kur.sh            build and install unless current
#   e2fsdroid-kur.sh --damga    print the stamp the installed binary must carry
set -euo pipefail

E2FSPROGS=3eb17241db7cd4408cc1bce9d25b602465ace8ff  # android17-release (e2fsprogs 1.47.2)
CORE=545d2487e38192a2ce25040897ced877cf6b4f53       # android17-release
SELINUX=a8c5e74f85f869bf236a01ba24181b78f5e39a52    # android17-release (also selinux/araclar/kur.sh)
# sha256 over the "sha256sum" lines of every extracted file followed by "path -> target" of every
# symlink, both sorted by path (gitiles regenerates the .tar.gz, so only its contents are stable)
declare -A TREE_SHA256=(
  [external/e2fsprogs/lib]=c0610e03b6eaeb619fbf9e3341025039c0d3241eea37a78ffb49ab0804d030fe
  [external/e2fsprogs/misc]=31e512281cb444c43532290a9c40a6aa0a12fb6964571918635c035e6058ea58
  [external/e2fsprogs/contrib/android]=ca28c226d4bb8712878bfae09a2977e672ff7d6695057fbe25d111076663d9d5
  [system/core/libcutils]=0cd07342f373997185c7c7784bf0cf2a78790b646e9f0bee70fa8cc15857028e
  [external/selinux/libselinux]=ae77ba3ce08b18417976ad670e0aaa80bab073038a0f31499eea8d203187dfb5
  [external/selinux/libsepol/include]=d4dd1a2039bf5931be9a8b03a836d00a678cdc78f300a318b706bbc044f82ade
)
# single files (gitiles serves them base64-encoded): plain sha256
declare -A FILE_SHA256=(
  [external/e2fsprogs/version.h]=de6c5488459254b38ad040db8b7fd383bc5b3854386c3def756242a1d35481b5
)

# Android.bp srcs at the pinned commits (windows_io.c replaces unix_io.c on Windows only)
EXT2FS_SRCS=(ext2_err alloc alloc_sb alloc_stats alloc_tables atexit badblocks bb_inode bitmaps
  bitops blkmap64_ba blkmap64_rb blknum block bmap check_desc crc16 crc32c csum closefs dblist
  dblist_dir digest_encode dirblock dirhash dir_iterate dupfs expanddir ext_attr extent fallocate
  fileio finddev flushb freefs gen_bitmap gen_bitmap64 get_num_dirs get_pathname getenv
  getsectsize getsize hashmap i_block icount imager ind_block initialize inline inline_data inode
  io_manager ismounted link llseek lookup mmp mkdir mkjournal namei native newdir nls_utf8 openfs
  orphan progress punch qcow2 rbtree read_bb read_bb_file res_gdt rw_bitmaps sha256 sha512 swapfs
  symlink undo_io unix_io sparse_io unlink valid_blk version test_io)
COM_ERR_SRCS=(error_message et_name init_et com_err com_right)
E2P_SRCS=(encoding errcode feature fgetflags fsetflags fgetproject fsetproject fgetversion
  fsetversion getflags getversion hashstr iod ljs ls mntopts parse_num pe pf ps setflags setversion
  uuid ostype percent)
UUID_SRCS=(clear compare copy gen_uuid isnull pack parse unpack unparse uuid_time)
QUOTA_SRCS=(devname dict mkquota parse_qtype plausible profile profile_helpers prof_err quotaio
  quotaio_tree quotaio_v2 cstring)
BLKID_SRCS=(cache dev devname devno getsize llseek probe read resolve save tag version)
MISC_SRCS=(create_inode create_inode_libarchive)
E2FSDROID_SRCS=(e2fsdroid block_range fsmap block_list base_fs perms basefs_allocator)
CUTILS_SRCS=(canned_fs_config fs_config)
# android-tools 37.0.0p1 vendor/CMakeLists.fastboot.txt
SELINUX_SRCS=(booleans callbacks freecon hashtab label_backends_android label label_support
  matchpathcon setrans_client sha1 label_file regex)

HEDEF="${E2FSDROID_HEDEF:-/usr/local/bin/e2fsdroid}"
DAMGA_DOSYASI="${E2FSDROID_DAMGA:-/usr/local/share/gbos-ortam/e2fsdroid.damga}"
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
DAMGA="e2fsprogs=$E2FSPROGS core=$CORE selinux=$SELINUX betik=$(sha256sum "$SELF" | cut -d' ' -f1)"

if [[ "${1:-}" == "--damga" ]]; then
  echo "$DAMGA"
  exit 0
fi
if [[ -x "$HEDEF" && "$(cat "$DAMGA_DOSYASI" 2>/dev/null)" == "$DAMGA" ]]; then
  echo "e2fsdroid güncel: $HEDEF"
  exit 0
fi

tree_sha256() {
  (cd "$1" && {
    find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum
    find . -type l -printf '%p -> %l\n' | LC_ALL=C sort
  } | sha256sum | cut -d' ' -f1)
}

BUILD="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-e2fsdroid.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT

fetch() {  # fetch REPO COMMIT PATH  ->  $BUILD/src/<last part of REPO>/PATH
  local repo="$1" commit="$2" path="$3" dest want got
  dest="$BUILD/src/${repo##*/}/$path"
  want="${TREE_SHA256[$repo/$path]}"
  mkdir -p "$dest"
  curl -fsSL --retry 3 -o "$BUILD/dl.tar.gz" \
    "https://android.googlesource.com/platform/$repo/+archive/$commit/$path.tar.gz"
  tar -xzf "$BUILD/dl.tar.gz" -C "$dest"
  rm -f "$BUILD/dl.tar.gz"
  got="$(tree_sha256 "$dest")"
  if [[ "$got" != "$want" ]]; then
    echo "e2fsdroid-kur.sh: $repo/$path@$commit içerik özeti $got, beklenen $want" >&2
    exit 1
  fi
  echo "  $repo/$path @ ${commit:0:12}: içerik özeti doğrulandı"
}

fetch_file() {  # fetch_file REPO COMMIT PATH  ->  $BUILD/src/<last part of REPO>/PATH
  local repo="$1" commit="$2" path="$3" dest want got
  dest="$BUILD/src/${repo##*/}/$path"
  want="${FILE_SHA256[$repo/$path]}"
  mkdir -p "$(dirname "$dest")"
  curl -fsSL --retry 3 "https://android.googlesource.com/platform/$repo/+/$commit/$path?format=TEXT" \
    | base64 -d >"$dest"
  got="$(sha256sum "$dest" | cut -d' ' -f1)"
  if [[ "$got" != "$want" ]]; then
    echo "e2fsdroid-kur.sh: $repo/$path@$commit sha256 $got, beklenen $want" >&2
    exit 1
  fi
  echo "  $repo/$path @ ${commit:0:12}: sha256 doğrulandı"
}

echo "e2fsdroid AOSP kaynağından derleniyor"
fetch external/e2fsprogs "$E2FSPROGS" lib
fetch external/e2fsprogs "$E2FSPROGS" misc
fetch external/e2fsprogs "$E2FSPROGS" contrib/android
fetch_file external/e2fsprogs "$E2FSPROGS" version.h
fetch system/core "$CORE" libcutils
fetch external/selinux "$SELINUX" libselinux
fetch external/selinux "$SELINUX" libsepol/include

E2FS="$BUILD/src/e2fsprogs"
CUTILS="$BUILD/src/core/libcutils"
SE="$BUILD/src/selinux"
OBJ="$BUILD/obj"
ANDROID_LIB="/usr/lib/$(gcc -dumpmachine)/android"
ANDROID_INC="/usr/include/android"

# android-tools' global flags; -w: third-party sources, their warnings are not acted upon here
COMMON=(-O2 -g0 -w -D_FILE_OFFSET_BITS=64 -D_LARGEFILE64_SOURCE -ftrivial-auto-var-init=zero)

compile() {  # compile NAME DIR EXT "FLAGS..." SOURCE-STEMS...  ->  $OBJ/NAME/*.o
  local name="$1" dir="$2" ext="$3" cc=gcc std=-std=gnu11 stem
  local -a flags
  read -r -a flags <<<"$4"
  shift 4
  [[ "$ext" == cpp ]] && cc=g++ std=-std=gnu++20
  mkdir -p "$OBJ/$name"
  for stem in "$@"; do
    "$cc" "$std" "${COMMON[@]}" "${flags[@]}" -c "$dir/$stem.$ext" -o "$OBJ/$name/$stem.o"
  done
}
archive() {  # archive NAME  ->  $OBJ/libNAME.a
  ar rcs "$OBJ/lib$1.a" "$OBJ/$1"/*.o
}

E2FS_FLAGS="-I$E2FS/lib -I$E2FS/lib/ext2fs -I$E2FS/misc -I$ANDROID_INC -DHAVE_SYS_TYPES_H"
compile ext2fs "$E2FS/lib/ext2fs" c "$E2FS_FLAGS" "${EXT2FS_SRCS[@]}"
archive ext2fs
compile com_err "$E2FS/lib/et" c "$E2FS_FLAGS" "${COM_ERR_SRCS[@]}"
archive com_err
compile e2p "$E2FS/lib/e2p" c "$E2FS_FLAGS" "${E2P_SRCS[@]}"
archive e2p
compile uuid "$E2FS/lib/uuid" c "$E2FS_FLAGS" "${UUID_SRCS[@]}"
archive uuid
compile quota "$E2FS/lib/support" c "$E2FS_FLAGS" "${QUOTA_SRCS[@]}"
archive quota
compile blkid "$E2FS/lib/blkid" c "$E2FS_FLAGS" "${BLKID_SRCS[@]}"
archive blkid
compile misc "$E2FS/misc" c "$E2FS_FLAGS" "${MISC_SRCS[@]}"
archive misc

# glibc >= 2.38 has strlcpy, android-tools probes it the same way
SE_FLAGS="-I$SE/libselinux/include -I$SE/libsepol/include -DAUDITD_LOG_TAG=1003 -D_GNU_SOURCE
  -DBUILD_HOST -DUSE_PCRE2 -DNO_PERSISTENTLY_STORED_PATTERNS -DDISABLE_SETRANS -DDISABLE_BOOL
  -DNO_MEDIA_BACKEND -DNO_X_BACKEND -DNO_DB_BACKEND -DPCRE2_CODE_UNIT_WIDTH=8"
probe() { printf '#define _GNU_SOURCE\n#include <string.h>\n#include <stdlib.h>\nint main(void){%s;return 0;}\n' "$1" \
  | gcc -x c -o /dev/null - >/dev/null 2>&1; }
probe 'char b[2]; strlcpy(b, "a", 2)' && SE_FLAGS+=" -DHAVE_STRLCPY"
probe 'free(reallocarray(0, 1, 1))' && SE_FLAGS+=" -DHAVE_REALLOCARRAY"
compile selinux "$SE/libselinux/src" c "${SE_FLAGS//$'\n'/ }" "${SELINUX_SRCS[@]}"
archive selinux

compile cutils "$CUTILS" cpp "-I$CUTILS/include -I$ANDROID_INC -D_GNU_SOURCE" "${CUTILS_SRCS[@]}"
compile e2fsdroid "$E2FS/contrib/android" c "$E2FS_FLAGS -I$SE/libselinux/include -I$CUTILS/include" \
  "${E2FSDROID_SRCS[@]}"

g++ -o "$BUILD/e2fsdroid" "$OBJ"/e2fsdroid/*.o "$OBJ"/cutils/*.o \
  -Wl,--start-group "$OBJ"/libmisc.a "$OBJ"/libext2fs.a "$OBJ"/libquota.a "$OBJ"/libe2p.a \
  "$OBJ"/libblkid.a "$OBJ"/libuuid.a "$OBJ"/libcom_err.a "$OBJ"/libselinux.a -Wl,--end-group \
  -L"$ANDROID_LIB" -Wl,-rpath,"$ANDROID_LIB" -lbase -llog -lsparse -lpcre2-8 -lz -lpthread

# usage text (exit 1 with "invalid option") proves the binary loads its libraries
usage="$("$BUILD/e2fsdroid" -h 2>&1 || true)"
if [[ "$usage" != *"e2fsdroid [-B block_list]"* ]]; then
  echo "e2fsdroid-kur.sh: derlenen e2fsdroid çalışmıyor: $usage" >&2
  exit 1
fi
mkdir -p "$(dirname "$HEDEF")" "$(dirname "$DAMGA_DOSYASI")"
install -m 0755 "$BUILD/e2fsdroid" "$HEDEF.yeni"
mv -f "$HEDEF.yeni" "$HEDEF"
echo "$DAMGA" >"$DAMGA_DOSYASI.yeni"
mv -f "$DAMGA_DOSYASI.yeni" "$DAMGA_DOSYASI"
echo "e2fsdroid kuruldu: $HEDEF"
