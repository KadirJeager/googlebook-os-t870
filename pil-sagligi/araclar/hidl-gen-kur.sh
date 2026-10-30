#!/usr/bin/env bash
# Builds AOSP's hidl-gen for the host into ${GBOS_PIL_CACHE:-~/.cache/gbos-pil-sagligi}/bin.
#
# The health HAL's BatteryMonitor (system/core/healthd) still fills the HIDL health@2.1 structs
# (AOSP TODO b/177269435), so the build needs the C++ headers hidl-gen generates from
# hardware/interfaces/health/{1.0,2.0,2.1}/types.hal. The SDK does not ship hidl-gen; this script
# compiles it from the pinned system/tools/hidl, libbase, liblog and fmtlib (kaynaklar.lock) the
# way Soong's cc_binary_host "hidl-gen" does. Host libraries: jsoncpp and OpenSSL's libcrypto
# (only SHA-256 for current.txt hashes, which the header generator does not use).
#
#   hidl-gen-kur.sh <name<TAB>dir lines of kaynak-getir.py on stdin>
# Prints the path of the binary. Returns at once when the binary of these inputs exists.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="${GBOS_PIL_CACHE:-$HOME/.cache/gbos-pil-sagligi}"
declare -A SRC
while IFS=$'\t' read -r name dir; do SRC[$name]="$dir"; done
for need in hidl_tools libbase logging fmtlib libhwbinder system_core; do
    [[ -n "${SRC[$need]:-}" ]] || { echo "hidl-gen-kur.sh: no source for $need" >&2; exit 1; }
done
STAMP="$(sha256sum "$HERE/hidl-gen-kur.sh" | cut -c1-16) $(for n in hidl_tools libbase logging fmtlib libhwbinder system_core; do basename "${SRC[$n]}"; done | tr '\n' ' ')"
BIN="$CACHE/bin"
if [[ -x "$BIN/hidl-gen" && "$(cat "$BIN/hidl-gen.VERSION" 2>/dev/null)" == "$STAMP" ]]; then
    echo "$BIN/hidl-gen"
    exit 0
fi

CXX="${HOST_CXX:-$(command -v clang++ || command -v g++)}"
for tool in "$CXX" bison flex pkg-config; do
    command -v "$tool" >/dev/null || { echo "hidl-gen-kur.sh: missing $tool" >&2; exit 1; }
done
pkg-config --exists jsoncpp libcrypto || { echo "hidl-gen-kur.sh: missing jsoncpp or libcrypto (pkg-config)" >&2; exit 1; }

H="${SRC[hidl_tools]}"
B="${SRC[libbase]}"
L="${SRC[logging]}/liblog"
mkdir -p "$CACHE"
BUILD="$(mktemp -d "$CACHE/.hidl-gen-build.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT

# Soong's yacc rule: header next to the parser, location.hh/position.hh generated.
bison -Wno-other -d -o "$BUILD/hidl-gen_y.cpp" --defines="$BUILD/hidl-gen_y.h" "$H/hidl-gen_y.yy"
flex -o "$BUILD/hidl-gen_l.cpp" "$H/hidl-gen_l.ll"

INC=(-I"$BUILD" -I"$H" -I"$H/utils/include" -I"$H/utils/include/hidl-util"
     -I"$H/host_utils/include" -I"$H/host_utils/include/hidl-util" -I"$H/hashing/include"
     -I"$B/include" -I"$L/include" -I"${SRC[fmtlib]}/include" -I"${SRC[libhwbinder]}/include"
     -I"${SRC[system_core]}/libutils/include" -I"${SRC[system_core]}/libcutils/include"
     -I"${SRC[system_core]}/libsystem/include")
SOURCES=("$BUILD/hidl-gen_y.cpp" "$BUILD/hidl-gen_l.cpp")
for f in Annotation ArrayType CompoundType ConstantExpression DeathRecipientType DocComment EnumType \
         FmqType HandleType HidlTypeAssertion Interface Location MemoryType Method NamedType \
         PointerType ScalarType Scope StringType Type TypeDef VectorType \
         AST Coordinator generateCpp generateCppImpl generateDependencies generateFormattedHidl \
         generateInheritanceHierarchy generateJava generateJavaImpl generateVts main; do
    SOURCES+=("$H/$f.cpp")
done
SOURCES+=("$H/utils/FQName.cpp" "$H/utils/FqInstance.cpp" "$H/host_utils/Formatter.cpp"
          "$H/host_utils/StringHelper.cpp" "$H/hashing/Hash.cpp")
for f in chrono_utils cmsg file hex logging mapped_file parsebool parsenetaddress posix_strerror_r \
         process properties result stringprintf strings threads errors_unix; do
    SOURCES+=("$B/$f.cpp")
done
for f in log_event_list log_event_write logger_name logger_read logger_write logprint properties \
         event_tag_map; do
    SOURCES+=("$L/$f.cpp")
done
SOURCES+=("${SRC[fmtlib]}/src/format.cc")

mkdir "$BUILD/obj"
OBJS=()
i=0
for src in "${SOURCES[@]}"; do
    obj="$BUILD/obj/$((i++))-$(basename "$src").o"
    # AOSP builds host tools against its libc++, which pulls <cstdint>/<cstring> in transitively;
    # the host's libstdc++ does not. hidl-gen's own sources get the two standard headers forced
    # (no source change); libbase must not (posix_strerror_r.cpp needs string.h without _GNU_SOURCE).
    force=()
    [[ "$src" == "$H"/* || "$src" == "$BUILD"/* ]] && force=(-include cstdint -include cstring)
    "$CXX" -std=gnu++23 -O1 -w "${force[@]}" "${INC[@]}" $(pkg-config --cflags jsoncpp) -c "$src" -o "$obj"
    OBJS+=("$obj")
done
mkdir -p "$BIN"
"$CXX" -o "$BIN/hidl-gen.tmp" "${OBJS[@]}" $(pkg-config --libs jsoncpp libcrypto) -lpthread
mv "$BIN/hidl-gen.tmp" "$BIN/hidl-gen"
echo "$STAMP" > "$BIN/hidl-gen.VERSION"
echo "$BIN/hidl-gen"
