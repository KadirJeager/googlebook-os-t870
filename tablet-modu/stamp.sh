#!/usr/bin/env bash
# Prints the hash of everything out/ is built from, so a stale build is noticed
# (yamalar/tablet_modu.py). The SDK version is pinned in build.sh, which is hashed; the signing
# key is not.
set -euo pipefail
# The file order must not depend on the machine's locale (sort).
export LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
{
    find build.sh stamp.sh uygulama -type f -print0 | sort -z | xargs -0 sha256sum
} | sha256sum | cut -d' ' -f1
