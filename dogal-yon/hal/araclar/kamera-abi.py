#!/usr/bin/env python3
"""Reads the camera HAL struct layout the vendor camera provider was built with.

    kamera-abi.py OBJDUMP READELF VENDOR_LIB      (camera.device-impl.samsung.so)

The provider talks to the Samsung camera module (camera.qcom.so) through C structs whose layout
LineageOS sets per device with soong config flags (hardware/samsung aidl/camera/
libhardware_headers: CAMERA_NEEDS_SEC_*, CAMERA_NEEDS_FPS_FIELD, CAMERA_64BIT_USAGE). The rebuilt
provider must use the vendor provider's layout. This reads it from the vendor library's machine
code and prints three lines that build.sh turns into static_asserts:

  torch <offset>    camera_module_t::set_torch_mode_strength, loaded and compared with 0 in
                    SamsungCameraModule::isSetTorchModeStrengthSupported()
  stream <size>     sizeof(camera3_stream_t): offset of Camera3Stream::mId, the field copied to
                    HalStream::id (offset 0) in convertToAidl(const Camera3Stream*, HalStream*)
  usage <bytes>     sizeof(camera3_stream_t::usage), from the width of its load in that function
"""
import os
import re
import subprocess
import sys

C_LOCALE = dict(os.environ, LC_ALL="C")  # tool labels in C, whatever the user's locale

TORCH = "_ZN7android8hardware6camera6common6helper19SamsungCameraModule31isSetTorchModeStrengthSupportedEv"
STREAM = ("_ZN7android8hardware6camera6device14implementation13convertToAidlEPKNS3_13Camera3Stream"
          "EPN4aidl7android8hardware6camera6device9HalStreamE")


def function(objdump, readelf, lib, symbol):
    syms = subprocess.run([readelf, "--dyn-syms", "-W", lib], check=True, capture_output=True,
                          text=True, env=C_LOCALE).stdout
    m = re.search(rf"^\s*\d+:\s+([0-9a-f]+)\s+(\d+)\s+FUNC\s+GLOBAL\s+DEFAULT\s+\d+\s+{symbol}$", syms, re.M)
    if not m:
        raise SystemExit(f"{symbol} not exported by {lib}")
    start, size = int(m.group(1), 16), int(m.group(2))
    out = subprocess.run([objdump, "-d", "--no-show-raw-insn", f"--start-address={start:#x}",
                          f"--stop-address={start + size:#x}", lib], check=True,
                         capture_output=True, text=True, env=C_LOCALE).stdout
    return [line.split(":", 1)[1].strip() for line in out.splitlines()
            if re.match(r"^\s+[0-9a-f]+:", line)]


def main():
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    objdump, readelf, lib = sys.argv[1:]

    asm = function(objdump, readelf, lib, TORCH)
    torch = None
    for a, b, c in zip(asm, asm[1:], asm[2:]):
        m1 = re.fullmatch(r"ldr\s+(x\d+), \[x\d+, #0x[0-9a-f]+\]", a)
        m2 = re.fullmatch(r"ldr\s+(x\d+), \[(x\d+), #(0x[0-9a-f]+)\]", b)
        if m1 and m2 and m2.group(2) == m1.group(1) and re.fullmatch(rf"cmp\s+{m2.group(1)}, #0x0", c):
            torch = int(m2.group(3), 16)
    if torch is None:
        raise SystemExit(f"no mModule->set_torch_mode_strength load in {TORCH}:\n" + "\n".join(asm))

    asm = function(objdump, readelf, lib, STREAM)
    stream = usage = None
    src = None
    for a, b in zip(asm, asm[1:]):
        m1 = re.fullmatch(r"ldr\s+([wx])(\d+), \[x0, #(0x[0-9a-f]+)\]", a)
        if m1 and re.fullmatch(rf"str\s+{m1.group(1)}{m1.group(2)}, \[x1\]", b):
            stream = int(m1.group(3), 16)
        m = re.fullmatch(r"mov\s+(x\d+), x0", a)
        if m:
            src = m.group(1)
    for a in asm:  # usage sits after stream_type, width, height, format: offset 0x10
        m = re.fullmatch(rf"ldr\s+([wx])\d+, \[{src}, #0x10\]", a) if src else None
        if m:
            usage = 4 if m.group(1) == "w" else 8
    if stream is None or usage is None:
        raise SystemExit(f"no Camera3Stream::mId / usage load in {STREAM}:\n" + "\n".join(asm))
    print(f"torch {torch:#x}\nstream {stream:#x}\nusage {usage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
