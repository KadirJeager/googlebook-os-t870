#!/usr/bin/env python3
"""
The adb serial of the Galaxy Tab S7 (SM-T870, gts7lwifi) the tools talk to.

  t870_cihaz.py            # prints the serial (exit 1 with the reason if there is none)

$ANDROID_SERIAL wins when it is set (adb itself honours it too). Otherwise every attached
device in the device or recovery state is asked for its vendor device name; exactly one must be
gts7lwifi. Googlebook OS reports itself as mica (Dell XPS Googlebook) in ro.product.*, but the
vendor partition stays LineageOS's, so ro.product.vendor.device is gts7lwifi there and in
LineageOS recovery alike. Another phone or tablet on the same computer is therefore never
picked by accident.

Python tools: `from t870_cihaz import seri`. Shell scripts:
  ANDROID_SERIAL=${ANDROID_SERIAL:-$(python3 -I "$TOOLS/t870_cihaz.py")} || exit 1
  export ANDROID_SERIAL
"""
import functools
import os
import subprocess
import sys

DEVICE = "gts7lwifi"
PROPS = ("ro.product.vendor.device", "ro.product.device")


def _adb(*args, serial=None, timeout=20):
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def adaylar():
    """[(serial, state)] of the attached devices adb can run a shell on."""
    r = _adb("devices")
    if r.returncode != 0:
        raise SystemExit(f"adb devices failed: {r.stderr.strip()}")
    out = []
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("device", "recovery"):
            out.append((parts[0], parts[1]))
    return out


def tablet_mi(serial):
    for prop in PROPS:
        try:
            r = _adb("shell", "getprop", prop, serial=serial)
        except subprocess.TimeoutExpired:
            return False
        if r.returncode == 0 and r.stdout.strip() == DEVICE:
            return True
    return False


@functools.cache
def seri():
    env = os.environ.get("ANDROID_SERIAL")
    if env:
        return env
    found = [s for s, _ in adaylar() if tablet_mi(s)]
    if len(found) == 1:
        return found[0]
    if not found:
        raise SystemExit(f"no {DEVICE} (SM-T870) attached over adb in Android or recovery; "
                         "connect it, allow USB debugging, or set ANDROID_SERIAL")
    raise SystemExit(f"several {DEVICE} attached ({', '.join(found)}); set ANDROID_SERIAL")


if __name__ == "__main__":
    print(seri())
