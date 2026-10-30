#!/usr/bin/env python3
"""Checks that the device's libraries resolve every dynamic symbol a binary imports.

    sembol-denetimi.py [--yalniz-ice] READELF BINARY LIB_DIR

Each undefined symbol of BINARY (with the version it requires, e.g. AServiceManager_addService
@LIBBINDER_NDK29) must be defined and exported by one of its DT_NEEDED libraries in LIB_DIR
(copies of the device's own, araclar/cihaz-kutuphaneleri.py) under that version. Weak
undefined symbols are reported too: the HAL must not depend on one being absent. The binary must
not export any symbol of its own either: it links libutils, libbase and libhidlbase statically while
the process also loads the device's shared copies (through libbinder_ndk), and an exported copy
would interpose on them. --yalniz-ice checks only the imports: that is the release compatibility
check (yamalar/hazir_ikili.py), the export rule belongs to the binary's own build. Exit code 0 and a summary line when all hold, 1 with the list otherwise.
"""
import os
import re
import subprocess
import sys

# GNU readelf prints bionic's STT_GNU_IFUNC (memcpy, strlen, ...) as "<OS specific>: 10", the
# OSABI of Android binaries being SYSV; llvm-readelf prints IFUNC.
ROW = re.compile(r"^\s*\d+:\s+[0-9a-f]+\s+\d+\s+(<[^>]*>:\s*\d+|\w+)\s+(\w+)\s+(\w+)\s+(\S+)\s+(\S+)")
# llvm-readelf (computer, NDK) and GNU readelf (tablet, binutils) print the same rows otherwise; GNU
# readelf translates its labels ("Shared library") in a non-C locale, so it always runs in C.
C_LOCALE = dict(os.environ, LC_ALL="C")


def dynsyms(readelf, path):
    out = subprocess.run([readelf, "--dyn-syms", "--wide", path], check=True, capture_output=True,
                         text=True, env=C_LOCALE).stdout
    rows = []
    for line in out.splitlines():
        m = ROW.match(line)
        if m:
            typ, bind, vis, ndx, name = m.groups()
            rows.append((typ, bind, vis, ndx, name))
    return rows


def split_version(name):
    if "@@" in name:
        base, ver = name.split("@@", 1)
    elif "@" in name:
        base, ver = name.split("@", 1)
    else:
        base, ver = name, None
    return base, ver


def needed(readelf, path):
    out = subprocess.run([readelf, "-d", path], check=True, capture_output=True, text=True,
                         env=C_LOCALE).stdout
    return re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", out)


def main():
    args = sys.argv[1:]
    imports_only = args[:1] == ["--yalniz-ice"]
    if imports_only:
        args = args[1:]
    if len(args) != 3:
        raise SystemExit(__doc__)
    readelf, binary, lib_dir = args
    exported = {}
    for lib in needed(readelf, binary):
        path = os.path.join(lib_dir, lib)
        if not os.path.isfile(path):
            raise SystemExit(f"{lib}: no device copy in {lib_dir}")
        for typ, bind, vis, ndx, name in dynsyms(readelf, path):
            if ndx == "UND" or vis not in ("DEFAULT", "PROTECTED") or bind == "LOCAL":
                continue
            base, ver = split_version(name)
            exported.setdefault((base, ver), lib)
            exported.setdefault((base, None), lib)
    missing, resolved, own = [], {}, []
    for typ, bind, vis, ndx, name in dynsyms(readelf, binary):
        if not name:
            continue
        if ndx != "UND":
            if not imports_only:
                own.append(name)
            continue
        base, ver = split_version(name)
        lib = exported.get((base, ver))
        if lib is not None:
            resolved[lib] = resolved.get(lib, 0) + 1
        else:
            missing.append(f"{name} ({bind})")
    if missing or own:
        if missing:
            print("not exported by the device's libraries:\n  " + "\n  ".join(sorted(missing)),
                  file=sys.stderr)
        if own:
            print("exported by the binary itself:\n  " + "\n  ".join(sorted(own)), file=sys.stderr)
        return 1
    print("symbols resolved: " + ", ".join(f"{lib} {n}" for lib, n in sorted(resolved.items()))
          + ("" if imports_only else "; exported by the binary: 0"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
