#!/usr/bin/env python3
"""Checks the natural-orientation HAL build against the tablet's own libraries.

    denetim.py READELF OUT_DIR VENDOR_DIR SYSTEM_LIB_DIR PANEL_ROTATION

OUT_DIR holds <name> (launcher) and <name>.so (HAL library) for each HAL in HALS. For every
library:
  - AArch64 shared object, 16 KiB segment alignment, BIND_NOW, no interpreter;
  - DT_NEEDED: each entry is a library the vendor HAL process already loads (the vendor binary's
    and, for the camera, camera.device-impl.samsung.so's DT_NEEDED), from the vendor partition
    (VENDOR_DIR, read out of the LineageOS zip) or LLNDK (SYSTEM_LIB_DIR, out of mica);
  - every undefined symbol is defined by one of those libraries (with its version for the
    versioned LLNDK libraries): nothing resolves to a stub or fails at dlopen;
  - the only exported symbol is gbos_hal_main (no interposition on the vendor libraries' symbols);
  - the marker string carries PANEL_ROTATION.
For every launcher: AArch64 PIE for /system/bin/linker64, DT_NEEDED only libdl_android, liblog,
libc, libdl (LLNDK, visible in the product section), symbols resolved, nothing exported, and its
preload list equal to the library's non-LLNDK DT_NEEDED. Exit 0 with a summary per binary.
"""
import importlib.util
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
_spec = importlib.util.spec_from_file_location(
    "gbos_sembol_denetimi", os.path.join(TOOLS_DIR, "pil-sagligi", "araclar", "sembol-denetimi.py"))
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)

# name: (vendor binaries whose process this library replaces)
HALS = {
    "android.hardware.sensors-service.samsung-multihal-gbos":
        ("android.hardware.sensors-service.samsung-multihal",),
    "android.hardware.camera.provider-service.samsung-gbos":
        ("android.hardware.camera.provider-service.samsung", "camera.device-impl.samsung.so"),
}
LLNDK = {"libc.so", "libm.so", "libdl.so", "libdl_android.so", "liblog.so", "libbinder_ndk.so",
         "libcamera_metadata.so"}
LAUNCHER_NEEDED = ["libc.so", "libdl.so", "libdl_android.so", "liblog.so"]


def readelf(tool, *args):
    # LLVM readelf (computer) or GNU readelf (the tablet's kit), always with C labels
    return subprocess.run([tool, *args], check=True, capture_output=True, text=True,
                          env=sd.C_LOCALE).stdout


def resolve(tool, binary, dirs):
    """(missing undefined symbols, {library: resolved count}, own exported symbol names)"""
    exported = {}
    for lib in sd.needed(tool, binary):
        path = next((os.path.join(d, lib) for d in dirs if os.path.isfile(os.path.join(d, lib))), None)
        if path is None:
            return [f"{lib}: no copy"], {}, []
        for typ, bind, vis, ndx, name in sd.dynsyms(tool, path):
            if ndx == "UND" or vis not in ("DEFAULT", "PROTECTED") or bind == "LOCAL":
                continue
            base, ver = sd.split_version(name)
            exported.setdefault((base, ver), lib)
            exported.setdefault((base, None), lib)
    missing, resolved, own = [], {}, []
    for typ, bind, vis, ndx, name in sd.dynsyms(tool, binary):
        if not name:
            continue
        if ndx != "UND":
            own.append(name)
            continue
        base, ver = sd.split_version(name)
        lib = exported.get((base, ver))
        if lib is None:
            missing.append(f"{name} ({bind})")
        else:
            resolved[lib] = resolved.get(lib, 0) + 1
    return missing, resolved, own


def elf_problems(tool, path, pie):
    problems = []
    hdr = readelf(tool, "-h", path)
    if not (re.search(r"Class:\s+ELF64", hdr) and re.search(r"Machine:\s+AArch64", hdr)
            and re.search(r"Type:\s+DYN", hdr)):
        problems.append("not an AArch64 shared object/PIE")
    seg = readelf(tool, "-lW", path)
    interp = re.search(r"Requesting program interpreter: (\S+)\]", seg)
    if pie and (interp is None or interp.group(1) != "/system/bin/linker64"):
        problems.append(f"interpreter {interp and interp.group(1)}")
    if not pie and interp is not None:
        problems.append("library with an interpreter")
    for align in re.findall(r"^\s+LOAD\s+.*\s(0x[0-9a-f]+)$", seg, re.M):
        if int(align, 16) < 16384:
            problems.append(f"LOAD alignment {align}")
    dyn = readelf(tool, "-d", path)
    if not re.search(r"\bBIND_NOW\b|\bNOW\b", dyn):
        problems.append("no BIND_NOW")
    return problems


def main():
    if len(sys.argv) != 6:
        raise SystemExit(__doc__)
    tool, out, vendor, system, rotation = sys.argv[1:]
    errors = []
    for name, vendor_bins in HALS.items():
        lib = os.path.join(out, f"{name}.so")
        launcher = os.path.join(out, name)
        errors += [f"{name}.so: {p}" for p in elf_problems(tool, lib, pie=False)]
        errors += [f"{name}: {p}" for p in elf_problems(tool, launcher, pie=True)]

        allowed = set()
        for b in vendor_bins:
            allowed |= set(sd.needed(tool, os.path.join(vendor, b)))
        allowed -= {"camera.device-impl.samsung.so"}
        needed = sd.needed(tool, lib)
        extra = sorted(set(needed) - allowed)
        if extra:
            errors.append(f"{name}.so needs {extra}, which the vendor HAL process does not load")
        for n in needed:
            where = system if n in LLNDK else vendor
            if not os.path.isfile(os.path.join(where, n)):
                errors.append(f"{name}.so: {n} not in {'LLNDK' if n in LLNDK else 'the vendor partition'}")
        missing, resolved, own = resolve(tool, lib, [system, vendor])
        if missing:
            errors.append(f"{name}.so: unresolved {missing[:20]}")
        if own != ["gbos_hal_main"]:
            errors.append(f"{name}.so exports {own[:20]}, expected only gbos_hal_main")
        with open(lib, "rb") as f:
            if f"gbos-dogal-yon PANEL_ROTATION={rotation}\0".encode() not in f.read():
                errors.append(f"{name}.so is not built for PANEL_ROTATION={rotation}")
        print(f"{name}.so: DT_NEEDED {len(needed)}, symbols resolved: "
              + ", ".join(f"{l} {c}" for l, c in sorted(resolved.items())))

        lneeded = sorted(sd.needed(tool, launcher))
        if lneeded != LAUNCHER_NEEDED:
            errors.append(f"{name}: DT_NEEDED {lneeded}")
        lmissing, lresolved, lown = resolve(tool, launcher, [system])
        if lmissing or lown:
            errors.append(f"{name}: unresolved {lmissing} / exported {lown}")
        with open(launcher, "rb") as f:
            data = f.read()
        for n in needed:
            if n not in LLNDK and f"{n}\0".encode() not in data:
                errors.append(f"{name}: does not preload {n}")
        print(f"{name}: launcher, symbols resolved: "
              + ", ".join(f"{l} {c}" for l, c in sorted(lresolved.items())))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main())
