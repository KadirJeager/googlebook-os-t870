#!/usr/bin/env bash
# Prints the hash of everything out/<abi>/ is built from (build.sh and the sources it compiles,
# the vendored third-party code included), so a stale build is noticed (yamalar/terminal.py).
# The SDK/NDK versions are pinned in build.sh, which is hashed; the signing key is not.
set -euo pipefail
# The file order must not depend on the machine's locale (sort).
export LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
{
    find build.sh stamp.sh app daemon kopru third_party -type f ! -name '*.pyc' -print0 \
        | sort -z | xargs -0 sha256sum
} | sha256sum | cut -d' ' -f1
