#!/usr/bin/env python3
"""Landscape natural orientation for the SM-T870: display, touchscreen, S Pen, boot animation and
side fingerprint location (T870-araclar/dogal-yon/CIHAZ-TESTI.md).

The panel scans out 1600x2560 (portrait). Googlebook OS, like mica's Dell laptops, the Pixel
Tablet and tablet apps, expects ROTATION_0 to be landscape. The landscape the tablet is used in
today is ROTATION_90 (front camera on the top long edge); this module makes that pose the
natural orientation:

  display   ro.surface_flinger.primary_display_orientation=ORIENTATION_90 (product build.prop).
            SurfaceFlinger composes every frame with (logical rotation + 90), so the framebuffer
            transform of the new ROTATION_0 equals today's ROTATION_90; it reports the display as
            2560x1600 and installOrientation 90 to DisplayManager (AOSP android17-release
            SurfaceFlinger::getPhysicalDisplayOrientation, DisplayDevice::setProjection,
            SurfaceFlinger::getStaticDisplayInfo). The composer's own physical orientation would
            take precedence, but this vendor's QTI composer reports none (dumpsys SurfaceFlinger:
            displaySpace orientation follows the logical rotation only).
  input     SurfaceFlinger hides the panel rotation from input (DisplayDevice::getFrontEndInfo),
            so the touch digitisers, whose raw axes follow the panel, are told with
            touch.orientation = ORIENTATION_90 (TouchInputMapper::computeInputTransforms step 2).
            The files go to /product/usr/idc, which InputDevice searches before /vendor/usr/idc.
            sec_e-pen keeps the vendor file's lines (it has no INPUT_PROP_DIRECT; without
            touch.deviceType = touchScreen it would not be a touchscreen and touch.orientation
            would be ignored). Only these two device names get the line: the Book Cover
            keyboard's touchpad (Vendor_04e8_Product_a035, POGO) is a relative pointer device
            that moves in the rotated display's coordinates and keeps its vendor file.
  bootanim  ro.bootanim.set_orientation_* rotate the animation away from the natural
            orientation; with a landscape natural orientation they must not exist.
  fingerprint  the side sensor location the vendor HAL reports (ro.vendor.fingerprint.
            sensor_location, natural display coordinates) is rotated with the display.

The two other layers: the accelerometer, gyroscope, magnetometer, fusion and Samsung's device
orientation sensor report axes relative to the portrait panel, and the camera HAL reports sensor
orientations 90/270 relative to it. They change with the display in the vendor HALs themselves,
rebuilt from source with the transform and run from /product (yamalar/dogal_yon_hal.py,
dogal-yon/hal). Without them this module would rotate auto-rotation and the camera preview by
PANEL_ROTATION: LAYERS_REQUIRED lists them and install() refuses to run unless the caller confirms
both. apply_patches.py calls install_all(), which installs and verifies the two layers first and
passes their verified state on.

Real inputs: the LineageOS vendor stage $WORK/lineage/vendor_dogal_yon, extracted from the OTA
zip the tablet runs:
  lineage-dosya-cikar.py <zip> vendor /usr/idc/sec_e-pen.idc /build.prop \
      --cikti $WORK/lineage/vendor_dogal_yon

Command line (from T870-araclar):  python3 -I yamalar/dogal_yon.py dogrula
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from yamalar import dogal_yon_hal  # noqa: E402

WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
STAGE = os.path.join(WORK, "lineage", "vendor_dogal_yon")

# The single value everything here derives from: how far the panel's own scan-out is rotated
# from the device's natural orientation, in degrees clockwise (SurfaceFlinger's meaning).
PANEL_ROTATION = 90
PANEL_SIZE = (1600, 2560)  # the panel's native width x height (getevent: ABS_MT_POSITION_X/Y max)

ORIENTATION_VALUE = f"ORIENTATION_{PANEL_ROTATION}"
DISPLAY_PROP = "ro.surface_flinger.primary_display_orientation"
FINGERPRINT_PROP = "ro.vendor.fingerprint.sensor_location"
BOOTANIM_PREFIX = "ro.bootanim.set_orientation_"
# Digitisers whose raw axes follow the panel (getevent -lp on the tablet, 23 Oct 2026).
TOUCH_DEVICES = ("sec_touchscreen", "sec_e-pen")
# Input devices that must keep their vendor configuration (relative pointers, keyboards).
UNTOUCHED_DEVICES = ("Vendor_04e8_Product_a035",)

BLOCK_BEGIN = "# --- GOOGLEBOOKOS_T870_DOGAL_YON ---"
BLOCK_END = "# --- GOOGLEBOOKOS_T870_DOGAL_YON end ---"
# The other layers a landscape natural orientation needs; see the module docstring.
LAYERS_REQUIRED = ("sensor axes (accelerometer, gyroscope, magnetometer, device orientation)",
                   "camera sensor orientation (characteristics, preview transform, API1)")


class DogalYonHatasi(Exception):
    pass


# ------------------------------------------------------------------ geometry

def rotate_natural_point(x, y, rotation=PANEL_ROTATION, panel=PANEL_SIZE):
    """A point in the panel's native coordinates -> the same physical point in the coordinates of
    the natural orientation SurfaceFlinger presents (content rotated by `rotation` clockwise from
    the panel; ROTATION_90 turns the panel's right edge into the top edge)."""
    w, h = panel
    r = rotation % 360
    if r == 0:
        return x, y
    if r == 90:
        return y, w - x
    if r == 180:
        return w - x, h - y
    if r == 270:
        return h - y, x
    raise DogalYonHatasi(f"rotation {rotation} is not a multiple of 90")


def rotate_fingerprint_location(value, rotation=PANEL_ROTATION, panel=PANEL_SIZE):
    """x|y|radius[|display];... (LineageOS Samsung fingerprint HAL format) rotated point by point
    and clamped onto the natural display boundary.

    The vendor specifies the side sensor slightly past the portrait right edge (x=1636 on a
    1600x2560 panel, so rotation=90 yields y=-36 on the top edge). SystemUI's SideFpsSensorInteractor
    sets isSensorVerticalInDefaultOrientation = (sensorLocationY != 0); a negative y on the top edge
    makes it treat the horizontal top-edge power button as a vertical right-edge sensor at x=2560.
    Clamping onto [0..nw] x [0..nh] puts a top-edge sensor at y=0 (horizontal) and a right-edge
    sensor at x=nw (vertical)."""
    w, h = panel
    nw, nh = (h, w) if (rotation % 360) % 180 else (w, h)
    out = []
    for sensor in value.split(";"):
        if not sensor:
            continue
        dim = sensor.split("|")
        if not 3 <= len(dim) <= 4 or not all(re.fullmatch(r"-?\d+", d) for d in dim[:3]):
            raise DogalYonHatasi(f"{FINGERPRINT_PROP}: invalid entry {sensor!r}")
        x, y = rotate_natural_point(int(dim[0]), int(dim[1]), rotation, panel)
        x = max(0, min(nw, x))
        y = max(0, min(nh, y))
        out.append("|".join([str(x), str(y), dim[2], *dim[3:]]))
    if not out:
        raise DogalYonHatasi(f"{FINGERPRINT_PROP}: empty value")
    return ";".join(out)


# ------------------------------------------------------------------ inputs

def read_prop(text, key):
    vals = [line.split("=", 1)[1].strip() for line in text.splitlines()
            if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#")]
    return vals[-1] if vals else None


def vendor_stage(stage=STAGE):
    """(vendor sec_e-pen.idc text, vendor build.prop text) from the LineageOS stage."""
    files = {n: os.path.join(stage, n) for n in ("sec_e-pen.idc", "build.prop", "KAYNAK")}
    missing = [p for p in files.values() if not os.path.isfile(p)]
    if missing:
        raise DogalYonHatasi(f"vendor stage missing {missing}; create it with lineage-dosya-cikar.py "
                             f"(module docstring)")
    with open(files["sec_e-pen.idc"]) as f:
        pen = f.read()
    with open(files["build.prop"]) as f:
        bp = f.read()
    return pen, bp


# ------------------------------------------------------------------ outputs

def idc_files(vendor_pen_idc):
    """{name: text} for /product/usr/idc."""
    def strip(text):
        kept = [l for l in text.splitlines() if not re.match(r"\s*touch\.orientation\s*=", l)]
        return "\n".join(kept).strip("\n")
    pen = strip(vendor_pen_idc)
    if not re.search(r"^\s*touch\.deviceType\s*=\s*touchScreen\s*$", pen, re.M):
        raise DogalYonHatasi("vendor sec_e-pen.idc no longer makes the pen a touchscreen; "
                             "touch.orientation would be ignored")
    header = ("# Googlebook OS (SM-T870): the natural orientation is landscape; the digitiser's raw\n"
              f"# axes follow the portrait panel, rotated {PANEL_ROTATION} degrees from it "
              "(yamalar/dogal_yon.py).\n")
    line = f"touch.orientation = {ORIENTATION_VALUE}\n"
    return {
        "sec_touchscreen.idc": header + line,
        "sec_e-pen.idc": header + "# Vendor lines (LineageOS /vendor/usr/idc/sec_e-pen.idc):\n"
                         + pen + "\n" + line,
    }


def prop_block(vendor_build_prop):
    loc = read_prop(vendor_build_prop, FINGERPRINT_PROP)
    lines = [BLOCK_BEGIN, f"{DISPLAY_PROP}={ORIENTATION_VALUE}"]
    if loc is not None:
        lines.append(f"{FINGERPRINT_PROP}={rotate_fingerprint_location(loc)}")
    lines.append(BLOCK_END)
    return "\n".join(lines) + "\n"


def with_block(text, block):
    """build.prop text with this module's block replaced (idempotent)."""
    pat = re.compile(re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.S)
    text = pat.sub("", text).rstrip("\n") + "\n"
    return text + block


def bootanim_lines(text):
    return [l for l in text.splitlines()
            if l.strip().startswith(BOOTANIM_PREFIX) and not l.lstrip().startswith("#")]


# ------------------------------------------------------------------ pipeline entry points

def install(sys_dir, prod_dir, sensor_layer_ready, camera_layer_ready, stage=STAGE, log=print):
    """Writes the product idc files and build.prop block. The two flags state that the image also
    carries the sensor-axis and camera-orientation layers (LAYERS_REQUIRED); without them the
    image would turn auto-rotation and the camera preview by PANEL_ROTATION, so nothing is
    written."""
    if not (sensor_layer_ready and camera_layer_ready):
        raise DogalYonHatasi("landscape natural orientation needs every layer: "
                             + "; ".join(LAYERS_REQUIRED))
    pen, vbp = vendor_stage(stage)
    idc_dir = os.path.join(prod_dir, "usr", "idc")
    os.makedirs(idc_dir, exist_ok=True)
    for name, text in idc_files(pen).items():
        p = os.path.join(idc_dir, name)
        with open(p, "w") as f:
            f.write(text)
        os.chmod(p, 0o644)
    bp_path = os.path.join(prod_dir, "etc", "build.prop")
    with open(bp_path) as f:
        bp = f.read()
    with open(bp_path, "w") as f:
        f.write(with_block(bp, prop_block(vbp)))
    log(f"[+] dogal_yon: {DISPLAY_PROP}={ORIENTATION_VALUE}, /product/usr/idc "
        f"{', '.join(TOUCH_DEVICES)}, fingerprint location rotated")
    dogrula(sys_dir, prod_dir, stage)


def install_all(sys_dir, sysext_dir, prod_dir, stage=STAGE, log=print):
    """apply_patches.py entry: the sensor and camera layers (yamalar/dogal_yon_hal.py) are
    installed and verified first; their verified state, not a caller's word, enables install()."""
    layers = dogal_yon_hal.install(sys_dir, sysext_dir, prod_dir)
    install(sys_dir, prod_dir, sensor_layer_ready=layers.get("sensor", False),
            camera_layer_ready=layers.get("kamera", False), stage=stage, log=log)
    dogrula_tumu(sys_dir, sysext_dir, prod_dir, stage)


def dogrula_tumu(sys_dir, sysext_dir, prod_dir, stage=STAGE):
    """dogrula() plus the sensor and camera layers: the whole landscape natural orientation."""
    problems = [f"{layer}: {p}" for layer, ps in
                dogal_yon_hal.layer_problems(sys_dir, sysext_dir, prod_dir).items() for p in ps]
    if problems:
        raise DogalYonHatasi("; ".join(problems))
    return dogrula(sys_dir, prod_dir, stage)


def dogrula(sys_dir, prod_dir, stage=STAGE):
    """Checks a patched tree for a consistent landscape natural orientation; raises on the first
    inconsistency."""
    pen, vbp = vendor_stage(stage)
    errors = []
    bps = {
        "system": os.path.join(sys_dir, "system", "build.prop"),
        "product": os.path.join(prod_dir, "etc", "build.prop"),
    }
    texts = {}
    for part, p in bps.items():
        with open(p) as f:
            texts[part] = f.read()
        stale = bootanim_lines(texts[part])
        if stale:
            errors.append(f"{part} build.prop still rotates the boot animation: {stale}")
    if read_prop(texts["product"], DISPLAY_PROP) != ORIENTATION_VALUE:
        errors.append(f"product build.prop: {DISPLAY_PROP} is not {ORIENTATION_VALUE}")
    for part in ("system",):
        if read_prop(texts[part], DISPLAY_PROP) not in (None, ORIENTATION_VALUE):
            errors.append(f"{part} build.prop sets a different {DISPLAY_PROP}")
    loc = read_prop(vbp, FINGERPRINT_PROP)
    if loc is not None and read_prop(texts["product"], FINGERPRINT_PROP) != rotate_fingerprint_location(loc):
        errors.append(f"product build.prop: {FINGERPRINT_PROP} is not the rotated vendor value")
    idc_dir = os.path.join(prod_dir, "usr", "idc")
    for name, text in idc_files(pen).items():
        p = os.path.join(idc_dir, name)
        if not os.path.isfile(p):
            errors.append(f"missing /product/usr/idc/{name}")
            continue
        with open(p) as f:
            if f.read() != text:
                errors.append(f"/product/usr/idc/{name} differs from the generated file")
    for name in UNTOUCHED_DEVICES:
        if os.path.exists(os.path.join(idc_dir, name + ".idc")):
            errors.append(f"/product/usr/idc/{name}.idc would replace the vendor file of a "
                          "device that must not rotate")
    if errors:
        raise DogalYonHatasi("; ".join(errors))
    return True


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dogrula":
        dogrula_tumu(os.path.join(WORK, "sys"), os.path.join(WORK, "sysext"), os.path.join(WORK, "prod"))
        print("DOGAL_YON_OK")
    else:
        print(__doc__)
