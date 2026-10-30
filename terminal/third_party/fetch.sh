#!/usr/bin/env bash
# Fetches the third-party sources the Terminal build uses, pinned by commit/version and checked
# against SHA-256 sums, into third_party/. The results are kept next to this script, so a build
# never needs the network; rerun this only to reproduce or to move a pin.
#
#   termux/         terminal-emulator + terminal-view (Apache-2.0) from termux/termux-app
#   nlohmann/       json.hpp (MIT) for the daemon's OCI registry client
#   androidx/       androidx.annotation (Apache-2.0), compile-only for the Termux sources
#   systemctl/      systemctl3.py (EUPL-1.2) from gdraheim/docker-systemctl-replacement: the
#                   environment's systemctl (/usr/local/sbin) where systemd cannot be PID 1
#   debian/         bindfs 1.18.4-0.1 (GPL-2+) .debs from Debian's archive (snapshot.debian.org):
#                   the shared storage view (daemon Container::ensure_storage_view) needs libfuse
#                   3's "/dev/fd/N" mount point; Debian 13 ships bindfs 1.14.7 (libfuse 2). Its
#                   dependencies (libc6 >= 2.38, libfuse3-4 >= 3.17.2, fuse3) are Debian 13's.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TERMUX_COMMIT=8629e632fcb95da272221be327db653fb24befe9   # termux-app master, 2026-09-26
# SHA-256 over "sha256  path" lines of every extracted file (sorted): GitHub may regenerate
# archive bytes, the file contents stay the same.
TERMUX_TREE_SHA256=ffcdf3a2eb7752ed7d187207591653655119c53a45fc986fdb8d0404e3dc0451
NLOHMANN_VERSION=v3.12.0
NLOHMANN_SHA256=aaf127c04cb31c406e5b04a63f1ae89369fccde6d8fa7cdda1ed4f32dfc5de63
ANDROIDX_ANNOTATION_VERSION=1.11.0
ANDROIDX_ANNOTATION_SHA256=a83b827cc17fd6b41c7bf5e7a2a2b40a0895b00b3e30fdfdb820b49dc7b667f3
SYSTEMCTL_VERSION=v1.7.1097
SYSTEMCTL_COMMIT=8bd65bec50650fcc740e2d55c32162ba7bafdfc8   # tag v1.7.1097, 2026-03-08
SYSTEMCTL_SHA256=5f5a47f321c7a8881dfd01f774106dc4012d664bee238eada86a7112674fea93
BINDFS_VERSION=1.18.4-0.1
BINDFS_SNAPSHOT=20260129T203345Z
BINDFS_ARM64_SHA256=9282efd9ba312fda276582eb544a5d5afb0a24c4e3175c692386eed73352e116
BINDFS_AMD64_SHA256=177138b55fb3e3ac00e89b6aaf63aab137788e210063b6553b8c8fc2a0451621
SYSTEMCTL_LICENSE_SHA256=67e6b5e4f3f4c6b3c4fbca47f0202823f38344f03f1b54e3dce9aef17406b0a1

work="$(mktemp -d "${TMPDIR:-/var/tmp}/gbos-terminal-fetch.XXXXXX")"
trap 'rm -rf "$work"' EXIT

tree_sha256() {  # tree_sha256 DIR
    (cd "$1" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
}

check() {  # check WHAT EXPECTED ACTUAL
    if [[ "$2" != "$3" ]]; then
        echo "$1: SHA-256 mismatch: expected $2, got $3" >&2
        exit 1
    fi
}

echo "== termux-app $TERMUX_COMMIT"
curl -fsSL --retry 3 --retry-all-errors --retry-delay 15 -o "$work/termux.tar.gz" \
    "https://codeload.github.com/termux/termux-app/tar.gz/$TERMUX_COMMIT"
mkdir "$work/termux"
tar -xzf "$work/termux.tar.gz" -C "$work/termux" --strip-components=1 \
    "termux-app-$TERMUX_COMMIT/LICENSE.md" \
    "termux-app-$TERMUX_COMMIT/terminal-emulator/src/main" \
    "termux-app-$TERMUX_COMMIT/terminal-view/src/main"
check termux "$TERMUX_TREE_SHA256" "$(tree_sha256 "$work/termux")"
rm -rf "$HERE/termux"
mv "$work/termux" "$HERE/termux"
echo "$TERMUX_COMMIT" > "$HERE/termux/COMMIT"

echo "== nlohmann/json $NLOHMANN_VERSION"
mkdir -p "$HERE/nlohmann"
curl -fsSL --retry 3 --retry-all-errors --retry-delay 15 -o "$work/json.hpp" \
    "https://github.com/nlohmann/json/releases/download/$NLOHMANN_VERSION/json.hpp"
check json.hpp "$NLOHMANN_SHA256" "$(sha256sum "$work/json.hpp" | cut -d' ' -f1)"
mv "$work/json.hpp" "$HERE/nlohmann/json.hpp"
curl -fsSL --retry 3 --retry-all-errors --retry-delay 15 -o "$HERE/nlohmann/LICENSE.MIT" \
    "https://raw.githubusercontent.com/nlohmann/json/$NLOHMANN_VERSION/LICENSE.MIT"

echo "== androidx.annotation $ANDROIDX_ANNOTATION_VERSION"
mkdir -p "$HERE/androidx"
jar="annotation-jvm-$ANDROIDX_ANNOTATION_VERSION.jar"
curl -fsSL --retry 3 --retry-all-errors --retry-delay 15 -o "$work/$jar" \
    "https://dl.google.com/android/maven2/androidx/annotation/annotation-jvm/$ANDROIDX_ANNOTATION_VERSION/$jar"
check "$jar" "$ANDROIDX_ANNOTATION_SHA256" "$(sha256sum "$work/$jar" | cut -d' ' -f1)"
rm -f "$HERE"/androidx/annotation-jvm-*.jar
mv "$work/$jar" "$HERE/androidx/$jar"

echo "== docker-systemctl-replacement $SYSTEMCTL_VERSION"
mkdir -p "$HERE/systemctl"
base="https://raw.githubusercontent.com/gdraheim/docker-systemctl-replacement/$SYSTEMCTL_COMMIT"
curl -fsSL --retry 3 --retry-all-errors --retry-delay 15 -o "$work/systemctl3.py" "$base/files/docker/systemctl3.py"
check systemctl3.py "$SYSTEMCTL_SHA256" "$(sha256sum "$work/systemctl3.py" | cut -d' ' -f1)"
curl -fsSL --retry 3 --retry-all-errors --retry-delay 15 -o "$work/EUPL-LICENSE.md" "$base/EUPL-LICENSE.md"
check EUPL-LICENSE.md "$SYSTEMCTL_LICENSE_SHA256" "$(sha256sum "$work/EUPL-LICENSE.md" | cut -d' ' -f1)"
mv "$work/systemctl3.py" "$work/EUPL-LICENSE.md" "$HERE/systemctl/"
echo "$SYSTEMCTL_COMMIT" > "$HERE/systemctl/COMMIT"

echo "== bindfs $BINDFS_VERSION (Debian)"
mkdir -p "$HERE/debian"
for arch in arm64 amd64; do
    deb="bindfs_${BINDFS_VERSION}_$arch.deb"
    want=BINDFS_${arch^^}_SHA256
    curl -fsSL --retry 3 --retry-all-errors --retry-delay 15 -o "$work/$deb" \
        "https://snapshot.debian.org/archive/debian/$BINDFS_SNAPSHOT/pool/main/b/bindfs/$deb"
    check "$deb" "${!want}" "$(sha256sum "$work/$deb" | cut -d' ' -f1)"
    mv "$work/$deb" "$HERE/debian/$deb"
done

echo "ok"
