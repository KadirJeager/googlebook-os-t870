#!/usr/bin/env python3
"""Copies the LLNDK libraries the natural-orientation HALs use out of the pristine mica images.

pil-sagligi/araclar/cihaz-kutuphaneleri.py with this module's list. The launchers run in
linkerconfig's "product" section, the HAL libraries in a namespace cloned from its "sphal"
namespace; both reach the system partition only through the LLNDK link (libbinder_ndk, liblog,
libcamera_metadata, libc, libm, libdl, libdl_android: all in sphal's and the vendor section's
link.system.shared_libs on the tablet, dogal-yon/hal/BENIOKU.md).

    sistem-kutuphaneleri.py OUT_DIR     writes the libraries and OUT_DIR/KAYNAK
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
COPIER = os.path.join(TOOLS_DIR, "pil-sagligi", "araclar", "cihaz-kutuphaneleri.py")

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("gbos_cihaz_kutuphaneleri", COPIER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SYSTEM_LIBS = ("libbinder_ndk.so", "liblog.so", "libcamera_metadata.so")
    mod.BIONIC_LIBS = ("libc.so", "libm.so", "libdl.so", "libdl_android.so")
    sys.exit(mod.main())
