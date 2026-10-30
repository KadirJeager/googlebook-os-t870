#!/usr/bin/env python3
"""Fetches the pinned upstream sources dogal-yon/hal/build.sh compiles (../kaynaklar.lock).

The same fetcher as pil-sagligi (pil-sagligi/araclar/kaynak-getir.py: sparse, blobless, depth 1
checkout of exactly the pinned commit, content hash checked against the lock), run on this
module's lock and into its own cache:
    $GBOS_DOGAL_YON_CACHE/src/<name>-<commit>/      (default ~/.cache/gbos-dogal-yon)

    kaynak-getir.py                 fetch/verify all, print "name<TAB>directory" per project
    kaynak-getir.py --kilitle       print the lock with freshly computed hashes
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
FETCHER = os.path.join(TOOLS_DIR, "pil-sagligi", "araclar", "kaynak-getir.py")
CACHE_ROOT = os.environ.get("GBOS_DOGAL_YON_CACHE", os.path.expanduser("~/.cache/gbos-dogal-yon"))


def load():
    spec = importlib.util.spec_from_file_location("gbos_kaynak_getir", FETCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LOCK = os.path.join(os.path.dirname(HERE), "kaynaklar.lock")
    mod.CACHE = os.path.join(CACHE_ROOT, "src")
    return mod


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(load().main())
