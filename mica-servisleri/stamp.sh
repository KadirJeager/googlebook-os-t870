#!/usr/bin/env bash
# Prints the hash of everything out/gbos-vpd-executor is built from, so a stale build is noticed.
# The mica release and the machine's paths are not part of it: the same out/ serves every release
# (yamalar/hazir_ikili.py checks the binary against each release's device libraries) and the
# tablet's kit. The NDK and build-tools versions are pinned in build.sh, which is hashed.
set -euo pipefail
# The file order must not depend on the machine's locale (sort): the tablet's kit runs in C.
export LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
{
    find build.sh stamp.sh vpd/aidl vpd/src vpd/test third_party -type f -print0 | sort -z | xargs -0 sha256sum
} | sha256sum | cut -d' ' -f1
