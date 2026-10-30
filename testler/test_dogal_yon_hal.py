"""Sensor and camera layers of the landscape natural orientation (dogal-yon/hal,
yamalar/dogal_yon_hal.py): the frame change itself, its coverage of every AOSP sensor type, the
tablet's real readings through the vendor HAL and the rebuilt one, the build against the tablet's
own libraries, the camera struct layout, the init and SELinux integration.

Real inputs: dogal-yon/hal/out (built by its build.sh), its pinned sources (kaynaklar.lock, the
module cache), the LineageOS zip (vendor libraries, binaries, rc files), the pristine mica images,
the working tree's product file_contexts, prod.meta and policy (read only), the live measurements
in dogal-yon/kanit/<date>/ and AOSP android-17.0.0_r1 sources (network)."""
import base64
import glob
import math
import os
import random
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from conftest import (ARACLAR, EROFS_BIN, GERCEK_WORK, LINEAGE_ZIP, MICA, arac_gerekli, disk_payi,
                      gerekli, oku, reflink, sha256, sil)

sys.path.insert(0, ARACLAR)
from yamalar import dogal_yon, dogal_yon_hal  # noqa: E402

MODUL = dogal_yon_hal.SRC
NDK = os.environ.get("ANDROID_NDK", os.path.expanduser("~/Android/Sdk/ndk/30.0.16248370"))
LLVM = os.path.join(NDK, "toolchains/llvm/prebuilt/linux-x86_64/bin")
READELF = os.path.join(LLVM, "llvm-readelf")
OBJDUMP = os.path.join(LLVM, "llvm-objdump")
KANIT = os.path.join(ARACLAR, "dogal-yon", "kanit")
R = dogal_yon.PANEL_ROTATION
AOSP = "https://android.googlesource.com/platform/{repo}/+/refs/tags/android-17.0.0_r1/{path}?format=TEXT"


def _aosp(repo, path):
    try:
        with urllib.request.urlopen(AOSP.format(repo=repo, path=path), timeout=60) as r:
            return base64.b64decode(r.read()).decode()
    except urllib.error.HTTPError as e:
        if e.code == 429 or e.code >= 500:
            pytest.skip(f"atlandı: gitiles {e.code}")
        pytest.fail(f"{repo}/{path}: HTTP {e.code}")
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        pytest.skip(f"atlandı: ağ yok ({e})")


@pytest.fixture(scope="module")
def kaynaklar():
    """name -> directory of the pinned sources (fetched and hash-checked by kaynak-getir.py)."""
    r = subprocess.run([sys.executable, "-I", os.path.join(MODUL, "araclar", "kaynak-getir.py")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"atlandı: kaynaklar alınamadı: {r.stderr[-300:]}")
    return dict(line.split("\t") for line in r.stdout.splitlines())


@pytest.fixture(scope="module")
def vendor():
    """The LineageOS vendor files the build links and compares against (vendor-dosyalari.py)."""
    gerekli(LINEAGE_ZIP)
    arac_gerekli("brotli")
    r = subprocess.run([sys.executable, "-I", os.path.join(MODUL, "araclar", "vendor-dosyalari.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    return r.stdout.strip()


@pytest.fixture(scope="module")
def cikti():
    """out/ of dogal-yon/hal; it must be the build of the current inputs."""
    gerekli(dogal_yon_hal.STAMP, READELF)
    assert oku(dogal_yon_hal.STAMP).strip() == dogal_yon_hal.current_stamp(), \
        "dogal-yon/hal/out is stale: run dogal-yon/hal/build.sh"
    return dogal_yon_hal.OUT


# ------------------------------------------------------------------ the transform, on the host

KINDS = ["kUnchanged", "kVector3", "kUncalibrated3", "kLimitedAxes", "kLimitedAxesUncal",
         "kQuaternion", "kDeviceOrientation", "kHeading", "kAdditionalInfo", "kHidden"]
HOST_MAIN = r"""
#include <natural_orientation.h>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
using namespace gbos::natural_orientation;
int main() {
    std::string op;
    while (std::cin >> op) {
        int r; std::cin >> r;
        if (op == "v") { float x, y; std::cin >> x >> y; rotate_vector(&x, &y, r); std::printf("%.9g %.9g\n", x, y); }
        else if (op == "q") { float q[4]; for (float& f : q) std::cin >> f; rotate_quaternion(q, r);
            std::printf("%.9g %.9g %.9g %.9g\n", q[0], q[1], q[2], q[3]); }
        else if (op == "d") { float d; std::cin >> d; std::printf("%.9g\n", rotate_device_orientation(d, r)); }
        else if (op == "h") { float h; std::cin >> h; std::printf("%.9g\n", rotate_heading(h, r)); }
        else if (op == "p") { float m[12]; for (float& f : m) std::cin >> f; rotate_placement(m, r);
            for (float f : m) { std::printf("%.9g ", f); }
            std::printf("\n"); }
        else if (op == "k") { std::printf("%d\n", static_cast<int>(kind_of(r))); }
        else if (op == "o") { int o, facing; std::cin >> o >> facing; std::printf("%d\n", rotate_camera_orientation(o, facing, r)); }
        else if (op == "e") { int kind; std::cin >> kind; float d[16]; int32_t type = 0;
            for (int i = 0; i < 16; i++) std::cin >> d[i];
            if (static_cast<Kind>(kind) == Kind::kAdditionalInfo) { type = static_cast<int32_t>(d[0]); std::memcpy(&d[0], &type, 4); }
            bool ok = rotate_payload(static_cast<Kind>(kind), d, r);
            if (static_cast<Kind>(kind) == Kind::kAdditionalInfo) { std::memcpy(&type, &d[0], 4); d[0] = type; }
            std::printf("%d", ok ? 1 : 0);
            for (float f : d) { std::printf(" %.9g", f); }
            std::printf("\n"); }
        else if (op == "f") { std::printf("%d\n", framework_preferred(r) ? 1 : 0); }
        else if (op == "P") { std::printf("%d\n", kPanelRotation); }
    }
}
"""


@pytest.fixture(scope="module")
def donusum(gecici_kok):
    """Runs natural_orientation.h, compiled for the host with the pipeline's PANEL_ROTATION."""
    arac_gerekli("c++")
    exe = os.path.join(gecici_kok, "dogal-yon-donusum")
    src = exe + ".cpp"
    with open(src, "w") as f:
        f.write(HOST_MAIN)
    subprocess.run(["c++", "-std=gnu++20", "-O2", "-Wall", "-Werror", f"-DGBOS_PANEL_ROTATION={R}",
                    "-I", os.path.join(MODUL, "src"), src, "-o", exe], check=True)

    def run(lines):
        out = subprocess.run([exe], input="".join(l + "\n" for l in lines), capture_output=True,
                             text=True, check=True).stdout.splitlines()
        return [[float(v) for v in line.split()] for line in out]
    return run


def rz(r):
    c, s = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[r % 360]
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def mat_vec(m, v):
    return [sum(m[i][j] * v[j] for j in range(3)) for i in range(3)]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def transpose(m):
    return [list(r) for r in zip(*m)]


def quat_matrix(q):
    """SensorManager.getRotationMatrixFromVector / CameraCharacteristics#LENS_POSE_ROTATION."""
    x, y, z, w = q
    return [[1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * z * w, 2 * x * z + 2 * y * w],
            [2 * x * y + 2 * z * w, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * x * w],
            [2 * x * z - 2 * y * w, 2 * y * z + 2 * x * w, 1 - 2 * x * x - 2 * y * y]]


def close(a, b, tol):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_panel_donusu_tek_kaynaktan(donusum):
    assert donusum(["P 0"]) == [[R]]
    assert R in (0, 90, 180, 270)


def test_vektor_ve_ters_donusum(donusum):
    rnd = random.Random(870)
    for r in (0, 90, 180, 270):
        for _ in range(50):
            x, y = rnd.uniform(-20, 20), rnd.uniform(-20, 20)
            (nx, ny), = donusum([f"v {r} {x!r} {y!r}"])
            assert close([nx, ny, 0], mat_vec(rz(r), [x, y, 0]), 1e-4)
            (bx, by), = donusum([f"v {-r} {nx!r} {ny!r}"])
            assert close([bx, by], [x, y], 1e-4)
    # the panel's right edge (old +X) is the new top (+Y) for PANEL_ROTATION 90
    if R == 90:
        assert donusum(["v 90 1 0"]) == [[0.0, 1.0]]


def test_kuaterniyon_ayni_dunya_vektorlerini_verir(donusum):
    rnd = random.Random(7)
    for r in (0, 90, 180, 270):
        for _ in range(100):
            q = [rnd.gauss(0, 1) for _ in range(4)]
            n = math.sqrt(sum(c * c for c in q))
            q = [c / n for c in q]
            (q2,) = donusum([f"q {r} " + " ".join(repr(c) for c in q)])
            assert q2[3] >= -1e-7, "hemisphere w >= 0"
            assert abs(sum(c * c for c in q2) - 1) < 1e-5
            v_new = [rnd.uniform(-1, 1) for _ in range(3)]
            v_old = mat_vec(rz(-r), v_new)
            assert close(mat_vec(quat_matrix(q2), v_new), mat_vec(quat_matrix(q), v_old), 1e-5)
            (back,) = donusum([f"q {-r} " + " ".join(repr(c) for c in q2)])
            sign = 1 if back[3] * q[3] >= 0 else -1
            assert close([sign * c for c in back], q, 1e-5) or close([-sign * c for c in back], q, 1e-5)


def test_cihaz_yonu_yukari_eksenden(donusum):
    # value -> the device axis pointing up (SensorType.aidl DEVICE_ORIENTATION)
    up = {0: [0, 1, 0], 1: [1, 0, 0], 2: [0, -1, 0], 3: [-1, 0, 0]}
    for r in (0, 90, 180, 270):
        for d in range(4):
            u_new = mat_vec(rz(r), up[d])
            want = next(k for k, v in up.items() if v == u_new)
            assert donusum([f"d {r} {d}"]) == [[want]], (r, d)


def test_framework_hesapladigi_sensor_sunulmaz(kaynaklar, donusum):
    """framework_preferred marks exactly DEVICE_ORIENTATION: the vendor sensorhub's own decision
    is dropped (getSensorsList leaves it out) so the framework's accel judge decides instead,
    with its low-pass filter, tilt tolerance and 23 degree hysteresis. Every other AOSP type
    passes through the HAL as before."""
    aidl = oku(os.path.join(kaynaklar["hardware_interfaces"],
                            "sensors/aidl/aidl_api/android.hardware.sensors/3/android/hardware/sensors/SensorType.aidl"))
    types = {n: int(v, 0) for n, v in re.findall(r"^\s+([A-Z0-9_]+) = (0x[0-9a-f]+|\d+),", aidl, re.M)}
    got = [n for n, (p,) in zip(types, donusum([f"f {v}" for v in types.values()])) if p == 1]
    assert got == ["DEVICE_ORIENTATION"], got
    # the transform itself stays (injected DEVICE_ORIENTATION data is re-expressed like any event)
    assert donusum(["k 27"]) == [[KINDS.index("kDeviceOrientation")]]


def test_baslik_yeni_y_ekseninin_azimutu(donusum):
    for r in (0, 90, 180, 270):
        for h in (0.0, 10.5, 359.0):
            # the new +Y in old device coordinates, at angle phi counter-clockwise from old +X;
            # azimuth (clockwise from north) of an axis at phi is h + (90 - phi)
            ex, ey, _ = mat_vec(rz(-r), [0, 1, 0])
            phi = math.degrees(math.atan2(ey, ex))
            want = (h + 90 - phi) % 360
            (got,), = donusum([f"h {r} {h}"])
            assert abs((got - want + 180) % 360 - 180) < 1e-3, (r, h, got, want)


def test_sensor_yerlesimi_aosp_ornegiyle(donusum):
    # AdditionalInfo.aidl AINFO_SENSOR_PLACEMENT example: R takes device vectors to the sensor's
    # frame, t is the sensor's centre in the device frame.
    m = [0, 1, 0, 0, -1, 0, 0, 10, 0, 0, 1, -2.5]
    rot = [m[0:3], m[4:7], m[8:11]]
    t = [m[3], m[7], m[11]]
    rnd = random.Random(3)
    for r in (0, 90, 180, 270):
        (m2,) = donusum([f"p {r} " + " ".join(str(v) for v in m)])
        rot2, t2 = [m2[0:3], m2[4:7], m2[8:11]], [m2[3], m2[7], m2[11]]
        for _ in range(10):
            v_new = [rnd.uniform(-1, 1) for _ in range(3)]
            assert close(mat_vec(rot2, v_new), mat_vec(rot, mat_vec(rz(-r), v_new)), 1e-5)
        assert close(t2, mat_vec(rz(r), t), 1e-5)


def test_kamera_yonu_jpeg_yonunu_korur(donusum):
    # CameraCharacteristics#SENSOR_ORIENTATION, getJpegOrientation(): jpeg = O + dev (back),
    # O - dev (front), dev = the device's clockwise rotation from its natural orientation as
    # OrientationEventListener reports it. One physical pose has dev_new = dev_old + R (the new
    # natural orientation is the old ROTATION_<R> pose, where the old dev is 360 - R).
    for facing in (0, 1):
        for o in (0, 90, 180, 270):
            (o2,), = donusum([f"o {R} {o} {facing}"])
            for dev_old in (0, 90, 180, 270):
                dev_new = (dev_old + R) % 360
                sign = -1 if facing == 0 else 1
                assert (o + sign * dev_old) % 360 == (int(o2) + sign * dev_new) % 360
    # external cameras keep theirs
    assert donusum([f"o {R} 90 2"]) == [[90]]


def test_tablo_aosp_sensor_turlerinin_hepsini_adiyla_anar(kaynaklar, donusum):
    """Every SensorType of the frozen AIDL interface the vendor HAL serves (V3) is named in
    natural_orientation.h, so no type is unchanged by omission; the kinds of the device-frame
    types are the ones their payload layout needs (ConvertUtils.cpp)."""
    hi = kaynaklar["hardware_interfaces"]
    aidl = oku(os.path.join(hi, "sensors/aidl/aidl_api/android.hardware.sensors/3/android/hardware/sensors/SensorType.aidl"))
    types = {n: int(v, 0) for n, v in re.findall(r"^\s+([A-Z0-9_]+) = (0x[0-9a-f]+|\d+),", aidl, re.M)}
    assert len(types) > 40
    header = oku(os.path.join(MODUL, "src", "natural_orientation.h"))
    missing = [n for n in types if not re.search(rf"\b{n}\b", header)]
    assert missing == [], f"not considered in natural_orientation.h: {missing}"
    kinds = {n: KINDS[int(k)] for n, (k,) in zip(types, donusum([f"k {v}" for v in types.values()]))}
    want = {
        "ACCELEROMETER": "kVector3", "MAGNETIC_FIELD": "kVector3", "GYROSCOPE": "kVector3",
        "GRAVITY": "kVector3", "LINEAR_ACCELERATION": "kVector3",
        "MAGNETIC_FIELD_UNCALIBRATED": "kUncalibrated3", "GYROSCOPE_UNCALIBRATED": "kUncalibrated3",
        "ACCELEROMETER_UNCALIBRATED": "kUncalibrated3",
        "ACCELEROMETER_LIMITED_AXES": "kLimitedAxes", "GYROSCOPE_LIMITED_AXES": "kLimitedAxes",
        "ACCELEROMETER_LIMITED_AXES_UNCALIBRATED": "kLimitedAxesUncal",
        "GYROSCOPE_LIMITED_AXES_UNCALIBRATED": "kLimitedAxesUncal",
        "ROTATION_VECTOR": "kQuaternion", "GAME_ROTATION_VECTOR": "kQuaternion",
        "GEOMAGNETIC_ROTATION_VECTOR": "kQuaternion", "DEVICE_ORIENTATION": "kDeviceOrientation",
        "HEADING": "kHeading", "ADDITIONAL_INFO": "kAdditionalInfo",
        "ORIENTATION": "kHidden", "POSE_6DOF": "kHidden",
    }
    for n, k in kinds.items():
        assert k == want.get(n, "kUnchanged"), (n, k)
    info = oku(os.path.join(hi, "sensors/aidl/aidl_api/android.hardware.sensors/3/android/hardware/sensors/AdditionalInfo.aidl"))
    body = info[info.index("enum AdditionalInfoType"):]
    value, placement = None, None
    for name, explicit in re.findall(r"^\s+(AINFO_[A-Z0-9_]+)(?: = (0x[0-9a-f]+|\d+))?,", body, re.M):
        value = int(explicit, 0) if explicit else value + 1
        if name == "AINFO_SENSOR_PLACEMENT":
            placement = value
    assert placement is not None and f"kAinfoSensorPlacement = {placement:#x}" in header


def test_olay_yukleri_duzeni(donusum):
    """rotate_payload on the 16-word EventPayload: each kind changes exactly its words."""
    d = [float(i + 1) for i in range(16)]
    line = lambda kind, r, data: f"e {r} {KINDS.index(kind)} " + " ".join(repr(v) for v in data)
    res = dict(zip(KINDS, donusum([line(k, 90, d if k != "kAdditionalInfo" else [0x10003] + d[1:]) for k in KINDS])))
    assert res["kUnchanged"] == [1] + d
    assert res["kHidden"][0] == 0
    assert res["kVector3"][1:] == [-2.0, 1.0] + d[2:]
    assert res["kUncalibrated3"][1:] == [-2.0, 1.0, 3.0, -5.0, 4.0] + d[5:]
    assert res["kLimitedAxes"][1:] == [-2.0, 1.0, 3.0, 5.0, 4.0] + d[5:]
    assert res["kLimitedAxesUncal"][1:] == [-2.0, 1.0, 3.0, -5.0, 4.0, 6.0, 8.0, 7.0] + d[8:]
    assert res["kHeading"][1:3] == [91.0, 2.0]
    placed = res["kAdditionalInfo"][1:]
    assert placed[0:2] == [0x10003, 2.0]  # type and serial stay
    # an AdditionalInfo frame of another type passes untouched
    (other,) = donusum([line("kAdditionalInfo", 90, [0x10002] + d[1:])])
    assert other[1:] == [0x10002] + d[1:]


# ------------------------------------------------------------------ real readings of the tablet

def _olcum():
    files = sorted(glob.glob(os.path.join(KANIT, "*", "sensor-olcum.txt")))
    gerekli(*(files[-1:] or [os.path.join(KANIT, "<tarih>", "sensor-olcum.txt")]))
    sections, cur = {}, None
    for line in oku(files[-1]).splitlines():
        m = re.match(r"== ([ABC]):", line)
        if m:
            cur = sections.setdefault(m.group(1), {})
            continue
        m = re.match(r"(\d+) (.+?) n=(\d+) v= (.*)$", line)
        if m and cur is not None and int(m.group(3)) > 0:
            cur[(int(m.group(1)), m.group(2))] = [float(v) for v in m.group(4).split()]
    return files[-1], sections


def test_tablet_olcumu_vendor_ile_donusum_tutarli():
    """The same physical pose read through the vendor HAL (A, C) and the rebuilt HAL (B,
    dogal-yon/hal BENIOKU "Ölçüm"): the vectors are the vendor's rotated by PANEL_ROTATION, the
    device orientation is shifted, and the rebuilt HAL's quaternions put gravity where its own
    accelerometer measures it."""
    path, s = _olcum()
    a, b, c = s.get("A", {}), s.get("B", {}), s.get("C", {})
    assert a and b and c, f"{path}: A/B/C sections"
    tol = {1: 0.25, 9: 0.25, 2: 3.0, 14: 3.0, 4: 0.05, 16: 0.05, 35: 0.25, 10: 0.3}
    checked = 0
    for key, va in a.items():
        typ = key[0]
        if key not in b or key not in c:
            continue
        vb, vc = b[key], c[key]
        if typ in (1, 2, 4, 9, 10):
            want = mat_vec(rz(R), va[:3])
            # the pose must not have changed between A and C
            if not close(va[:3], vc[:3], tol[typ]):
                pytest.skip(f"atlandı: {key} A ve C arasında hareket var")
            assert close(vb[:3], want, tol[typ]), (key, va, vb, want)
            checked += 1
        elif typ in (14, 16, 35):
            want = mat_vec(rz(R), va[:3]) + mat_vec(rz(R), va[3:6])
            assert close(vb[:6], want, tol[typ] * 2), (key, va, vb)
            checked += 1
        elif typ == 27:
            assert round(vb[0]) == (round(va[0]) - R // 90) % 4, (va, vb)
            checked += 1
    assert checked >= 5, f"{path}: only {checked} sensors compared"
    accel = next(v for k, v in b.items() if k[0] == 1)
    g = [x / math.sqrt(sum(y * y for y in accel[:3])) for x in accel[:3]]
    for typ in (11, 15):
        q = next((v for k, v in b.items() if k[0] == typ), None)
        if q is None:
            continue
        down = transpose(quat_matrix(q[:4]))  # world -> device
        up_device = mat_vec(down, [0, 0, 1])
        assert close(up_device, g, 0.05), (typ, up_device, g)


def _kamera_dokumu(path):
    """{camera id: (facing, orientation, poseRotation)} from a dumpsys media.camera text."""
    out, cam, key = {}, None, None
    for line in oku(path).splitlines():
        m = re.match(r"== Camera HAL device device@[\d.]+/internal/(\d+) \(v[\d.]+\) static information", line)
        if m:
            cam = out.setdefault(m.group(1), {})
            continue
        if line.startswith("== "):  # any other section: dynamic info, dumpState, error traces
            cam, key = None, None
            continue
        m = re.match(r"\s+Orientation: (\d+)$", line)
        if m and cam is not None:
            cam["api1"] = int(m.group(1))
            continue
        m = re.match(r"\s+android\.(lens\.facing|sensor\.orientation|lens\.poseRotation|lens\.poseTranslation) \(", line)
        if m and cam is not None:
            key = m.group(1)
            continue
        if key and cam is not None and line.strip().startswith("["):
            cam[key] = line.strip().strip("[]").split()
            key = None
    return out


def test_tablet_kamera_dokumu_donusum_tutarli():
    """dumpsys media.camera with the vendor provider and with the rebuilt one: orientation and
    lens pose of every camera are the vendor's re-expressed (front +R, back -R; q * q_z(-R),
    R_z t), and the API1 orientation cameraserver derives follows."""
    before = sorted(glob.glob(os.path.join(KANIT, "*", "kamera-vendor-dumpsys.txt")))
    after = sorted(glob.glob(os.path.join(KANIT, "*", "kamera-gbos-dumpsys.txt")))
    gerekli(*(before[-1:] or [os.path.join(KANIT, "<tarih>", "kamera-vendor-dumpsys.txt")]))
    gerekli(*(after[-1:] or [os.path.join(KANIT, "<tarih>", "kamera-gbos-dumpsys.txt")]))
    v, g = _kamera_dokumu(before[-1]), _kamera_dokumu(after[-1])
    assert v.keys() == g.keys() and len(v) >= 2
    for cam in v:
        facing = {"FRONT": 0, "BACK": 1, "EXTERNAL": 2}[v[cam]["lens.facing"][0]]
        o = int(v[cam]["sensor.orientation"][0])
        want = (o + R) % 360 if facing == 0 else (o - R) % 360 if facing == 1 else o
        assert int(g[cam]["sensor.orientation"][0]) == want, cam
        assert v[cam]["api1"] == o and g[cam]["api1"] == want, cam
        q = [float(x) for x in v[cam]["lens.poseRotation"]]
        q2 = [float(x) for x in g[cam]["lens.poseRotation"]]
        rnd = random.Random(int(cam))
        for _ in range(5):
            vn = [rnd.uniform(-1, 1) for _ in range(3)]
            assert close(mat_vec(quat_matrix(q2), vn), mat_vec(quat_matrix(q), mat_vec(rz(-R), vn)), 1e-3), cam
        t = [float(x) for x in v[cam]["lens.poseTranslation"]]
        assert close([float(x) for x in g[cam]["lens.poseTranslation"]], mat_vec(rz(R), t), 1e-4), cam


# ------------------------------------------------------------------ build and linking

def test_ikililer_cihazin_kutuphaneleriyle_baglanir(cikti, vendor, gecici):
    """denetim.py on out/ with fresh copies of the tablet's libraries: AArch64, 16 KiB, BIND_NOW;
    the libraries need only what the vendor HAL processes already load, every symbol resolves,
    only gbos_hal_main is exported and the marker carries PANEL_ROTATION; the launchers need only
    LLNDK visible in the product section and preload exactly the libraries' vendor DT_NEEDED."""
    gerekli(os.path.join(MICA, "system_a.erofs"), os.path.join(EROFS_BIN, "dump.erofs"))
    lib = os.path.join(gecici, "lib")
    subprocess.run([sys.executable, "-I", os.path.join(MODUL, "araclar", "sistem-kutuphaneleri.py"), lib],
                   check=True)
    flat = os.path.join(gecici, "out")
    os.makedirs(flat)
    for h in dogal_yon_hal.HALS:
        for src, rel, _ in h.files[:2]:
            os.symlink(src, os.path.join(flat, os.path.basename(rel)))
    r = subprocess.run([sys.executable, "-I", os.path.join(MODUL, "araclar", "denetim.py"), READELF,
                        flat, vendor, lib, str(R)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]


def test_kamera_yapi_duzeni_vendor_saglayiciyla_ayni(vendor):
    """camera_module_t and camera3_stream_t as the vendor provider's machine code uses them
    (kamera-abi.py); build.sh static_asserts the rebuilt provider against these values."""
    gerekli(OBJDUMP)
    r = subprocess.run([sys.executable, "-I", os.path.join(MODUL, "araclar", "kamera-abi.py"), OBJDUMP,
                        READELF, os.path.join(vendor, "camera.device-impl.samsung.so")],
                       capture_output=True, text=True, check=True)
    vals = dict(line.split() for line in r.stdout.splitlines())
    assert set(vals) == {"torch", "stream", "usage"}
    # sm8250-common: needs_sec_reserved_field only, so camera3_stream_t ends with 0x28 reserved bytes
    assert int(vals["stream"], 16) == 0x88 and vals["usage"] == "4"
    assert "-DCAMERA_NEEDS_SEC_RESERVED_FIELD" in oku(os.path.join(MODUL, "build.sh"))
    assert "kamera-abi.py" in oku(os.path.join(MODUL, "build.sh"))


def test_vendor_ile_ayni_kaynak_ve_dosyalar(cikti, vendor):
    """The vendor rc files, VINTF fragments and hals.conf the patch reads ($WORK/lineage/bolumler)
    are what the zip ships and what the port was checked against (lineage.lock); the vendor binaries predate the pinned hardware/samsung commit's successors
    (no device-orientation workaround property) and the rebuilt HALs carry the same upstream code
    paths (hals.conf locations, Samsung proximity fix, torch strength)."""
    kaynak = oku(os.path.join(MODUL, "lineage.lock"))
    assert f"sha256={sha256(LINEAGE_ZIP)}" in kaynak
    for rel in ("etc/init/android.hardware.camera.provider-service.samsung.rc",
                "etc/init/android.hardware.sensors-service.samsung-multihal.rc",
                "etc/vintf/manifest/android.hardware.camera.provider-service.samsung.xml",
                "etc/vintf/manifest/android.hardware.sensors-samsung-multihal.xml",
                "etc/sensors/hals.conf"):
        name = os.path.basename(rel)
        gerekli(os.path.join(dogal_yon_hal.LINEAGE_VENDOR, rel))
        assert sha256(os.path.join(vendor, name)) == sha256(os.path.join(dogal_yon_hal.LINEAGE_VENDOR, rel)), name
        assert f"vendor/{rel}  {sha256(os.path.join(vendor, name))}" in kaynak, name
    vbin = oku(os.path.join(vendor, "android.hardware.sensors-service.samsung-multihal"), "rb")
    ours = oku(os.path.join(cikti, dogal_yon_hal.LIBDIR, dogal_yon_hal.HALS[0].name + ".so"), "rb")
    assert b"device_orientation_workaround" not in vbin
    for s in (b"/vendor/etc/sensors/hals.conf\0", b"/odm/etc/sensors/hals.conf\0", b"Fixing %s\0",
              b"com.samsung.permission.SSENSOR\0"):
        assert s in vbin and s in ours, s
    vcam = oku(os.path.join(vendor, "camera.device-impl.samsung.so"), "rb")
    ocam = oku(os.path.join(cikti, dogal_yon_hal.LIBDIR, dogal_yon_hal.HALS[1].name + ".so"), "rb")
    assert b"camera_module->set_torch_mode_strength\0" in vcam and b"camera_module->set_torch_mode_strength\0" in ocam
    lock = oku(os.path.join(MODUL, "kaynaklar.lock"))
    assert re.search(r"^hardware_samsung \S+ 4c4e16f32a19c2cc706b5d2052e22d5ea5fa94f5 ", lock, re.M)


def test_yamalar_sabitlenmis_kaynaga_uygulanir(kaynaklar, gecici):
    """kaynak-yamalari apply to the pinned trees without fuzz, and the patched sensor and camera
    code calls the transforms where every event and every metadata buffer leaves the HAL."""
    arac_gerekli("patch")
    hs = os.path.join(gecici, "hs")
    subprocess.run(["cp", "-r", kaynaklar["hardware_samsung"], hs], check=True)
    for p in sorted(glob.glob(os.path.join(MODUL, "kaynak-yamalari", "hardware_samsung", "*.patch"))):
        subprocess.run(["patch", "--quiet", "--no-backup-if-mismatch", "--fuzz=0", "-p1", "-d", hs,
                        "-i", p], check=True)
    sens = oku(os.path.join(hs, "aidl/sensors/HalProxySamsung.cpp"))
    assert "HalProxy::postEventsToMessageQueue(rotated" in sens
    assert "-orientation::kPanelRotation" in sens and "HalProxyAidl::injectSensorData(panelEvent)" in sens
    conv = oku(os.path.join(hs, "aidl/camera/device/convert.cpp"))
    assert "rotate_camera_metadata(src, &dest->metadata)" in conv
    # characteristics and results are re-expressed; the request template, which apps send back
    # and the HAL echoes in its results, is not (it would be re-expressed twice)
    dev = oku(os.path.join(hs, "aidl/camera/device/CameraDevice.cpp"))
    ses = oku(os.path.join(hs, "aidl/camera/device/CameraDeviceSession.cpp"))
    assert dev.count("convertToAidlNaturalOrientation(") == 2
    assert ses.count("convertToAidlNaturalOrientation(") == 3
    assert "convertToAidl(metaBuffer, outMetadata);" in ses
    for f in ("aidl/sensors/service.cpp", "aidl/camera/provider/service.cpp"):
        assert 'extern "C" int gbos_hal_main()' in oku(os.path.join(hs, f))
    hi = os.path.join(gecici, "hi")
    os.makedirs(os.path.join(hi, "sensors/aidl/default/multihal"))
    subprocess.run(["cp", "-r", os.path.join(kaynaklar["hardware_interfaces"], "sensors/aidl/default/multihal/include"),
                    os.path.join(hi, "sensors/aidl/default/multihal/")], check=True)
    for p in sorted(glob.glob(os.path.join(MODUL, "kaynak-yamalari", "hardware_interfaces", "*.patch"))):
        subprocess.run(["patch", "--quiet", "--no-backup-if-mismatch", "--fuzz=0", "-p1", "-d", hi,
                        "-i", p], check=True)


# ------------------------------------------------------------------ init, SELinux, product image

def test_servis_tanimlari_ve_vendor_politikasi():
    assert dogal_yon_hal.vendor_policy_problems() == []
    for h in dogal_yon_hal.HALS:
        assert dogal_yon_hal.service_problems(h) == [], h.layer


def test_kamera_tembel_hal_kapali():
    """The camera rc relies on cameraserver not waiting for the provider on demand: that only
    happens for lazy providers (ro.camera.enableLazyHal), which no build.prop of the image sets."""
    stage = os.path.join(GERCEK_WORK, "lineage", "vendor_dogal_yon", "build.prop")
    props = [stage, os.path.join(GERCEK_WORK, "sys", "system", "build.prop"),
             os.path.join(GERCEK_WORK, "prod", "etc", "build.prop")]
    gerekli(*props)
    for p in props:
        assert dogal_yon.read_prop(oku(p), "ro.camera.enableLazyHal") in (None, "false"), p


def test_install_iki_kez_ayni_ve_dogal_yon_katmanlari(cikti, gecici):
    """install() on a copy of the real product file_contexts and build.prop: idempotent, the
    pristine part of file_contexts stays byte for byte, both layers verified; dogal_yon.
    install_all() then writes the display layer and dogrula_tumu() holds; a removed HAL file is
    caught."""
    gerekli(os.path.join(GERCEK_WORK, "sys", "system", "etc", "init"), os.path.join(GERCEK_WORK, "sysext"),
            os.path.join(GERCEK_WORK, "prod", dogal_yon_hal.PRODUCT_FILE_CONTEXTS),
            os.path.join(GERCEK_WORK, "lineage", "vendor_dogal_yon", "build.prop"))
    prod = os.path.join(gecici, "prod")
    sys_dir = os.path.join(gecici, "sys")
    fc_rel = dogal_yon_hal.PRODUCT_FILE_CONTEXTS
    reflink(os.path.join(GERCEK_WORK, "prod", fc_rel), os.path.join(prod, fc_rel))
    reflink(os.path.join(GERCEK_WORK, "prod", "etc", "build.prop"), os.path.join(prod, "etc", "build.prop"))
    reflink(os.path.join(GERCEK_WORK, "sys", "system", "build.prop"), os.path.join(sys_dir, "system", "build.prop"))
    reflink(os.path.join(GERCEK_WORK, "sys", dogal_yon_hal.PLAT_FILE_CONTEXTS),
            os.path.join(sys_dir, dogal_yon_hal.PLAT_FILE_CONTEXTS))
    subprocess.run(["cp", "-a", "--reflink=auto", os.path.join(GERCEK_WORK, "sys", "system", "etc", "init"),
                    os.path.join(sys_dir, "system", "etc", "init")], check=True)
    sysext = os.path.join(GERCEK_WORK, "sysext")
    asil = oku(os.path.join(prod, fc_rel))
    if dogal_yon_hal.FC_BEGIN in asil:
        b = asil.index(dogal_yon_hal.FC_BEGIN)
        asil = asil[:b] + asil[asil.index(dogal_yon_hal.FC_END) + len(dogal_yon_hal.FC_END):]
    for bp in (os.path.join(sys_dir, "system", "build.prop"), os.path.join(prod, "etc", "build.prop")):
        text = oku(bp)
        with open(bp, "w") as f:
            f.write("\n".join(l for l in text.splitlines() if not l.startswith(dogal_yon.BOOTANIM_PREFIX)) + "\n")
    assert dogal_yon_hal.install(sys_dir, sysext, prod) == {"sensor": True, "kamera": True}
    rels = [fc_rel] + [r for h in dogal_yon_hal.HALS for _, r, _ in h.files]
    bir = {rel: sha256(os.path.join(prod, rel)) for rel in rels}
    dogal_yon.install_all(sys_dir, sysext, prod, log=lambda *_: None)
    assert {rel: sha256(os.path.join(prod, rel)) for rel in rels} == bir
    assert oku(os.path.join(prod, fc_rel)) == asil.rstrip("\n") + "\n" + dogal_yon_hal.FC_BEGIN + \
        dogal_yon_hal.FC_LINES + dogal_yon_hal.FC_END
    assert dogal_yon.read_prop(oku(os.path.join(prod, "etc", "build.prop")), dogal_yon.DISPLAY_PROP) == \
        dogal_yon.ORIENTATION_VALUE
    assert dogal_yon.dogrula_tumu(sys_dir, sysext, prod)
    os.remove(os.path.join(prod, dogal_yon_hal.HALS[1].files[1][1]))
    with pytest.raises(dogal_yon.DogalYonHatasi, match="kamera"):
        dogal_yon.dogrula_tumu(sys_dir, sysext, prod)


def test_politika_gecisler_ve_erisimler():
    gerekli(os.path.join(GERCEK_WORK, "sys", "system", "etc", "selinux", "plat_sepolicy.cil"))
    assert dogal_yon_hal.politika(GERCEK_WORK) >= 49


def test_product_imajinda_etiket_ve_sahiplik(cikti, gecici):
    """make_ext4_images.py product on a small tree (install() output + the real prod.meta and the
    real plat/system_ext/product file_contexts): launchers get the vendor HALs' exec types,
    root:shell 0755; the libraries system_lib_file 0644; the rc files system_file."""
    arac_gerekli("mke2fs", "e2fsdroid", "e2fsck", "debugfs")
    disk_payi(0.5)
    gerekli(os.path.join(GERCEK_WORK, "prod.meta"),
            os.path.join(GERCEK_WORK, "sys/system/etc/selinux/plat_file_contexts"),
            os.path.join(GERCEK_WORK, "sysext/etc/selinux/system_ext_file_contexts"),
            os.path.join(GERCEK_WORK, "prod", dogal_yon_hal.PRODUCT_FILE_CONTEXTS))
    work = os.path.join(gecici, "work")
    for rel in ("prod.meta", "sys/system/etc/selinux/plat_file_contexts",
                "sysext/etc/selinux/system_ext_file_contexts",
                os.path.join("prod", dogal_yon_hal.PRODUCT_FILE_CONTEXTS)):
        reflink(os.path.join(GERCEK_WORK, rel), os.path.join(work, rel))
    os.makedirs(os.path.join(work, "sys", "system", "etc", "init"))
    os.makedirs(os.path.join(work, "sysext", "etc", "init"))
    dogal_yon_hal.install(os.path.join(work, "sys"), os.path.join(work, "sysext"), os.path.join(work, "prod"))
    img = os.path.join(work, "product.img")
    try:
        r = subprocess.run([sys.executable, "-I", os.path.join(ARACLAR, "make_ext4_images.py"), "product"],
                           env=dict(os.environ, GBOS_WORK=work), cwd=work, capture_output=True, text=True)
        assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
        want = {}
        for h in dogal_yon_hal.HALS:
            want[f"/bin/hw/{h.name}"] = (f"u:object_r:{h.exec_type}:s0", 0o755, 0, 2000)
            want[f"/{dogal_yon_hal.LIBDIR}/{h.name}.so"] = ("u:object_r:system_lib_file:s0", 0o644, 0, 0)
            want[f"/etc/init/{h.name}.rc"] = ("u:object_r:system_file:s0", 0o644, 0, 0)
        want[f"/{dogal_yon_hal.LIBDIR}"] = ("u:object_r:system_lib_file:s0", 0o755, 0, 0)
        for path, (label, mode, uid, gid) in want.items():
            out = subprocess.run(["debugfs", "-R", f'ea_get -V "{path}" security.selinux', img],
                                 capture_output=True, text=True, check=True).stdout
            assert out.strip().rstrip("\x00") == label, path
            st = subprocess.run(["debugfs", "-R", f'stat "{path}"', img], capture_output=True,
                                text=True, check=True).stdout
            m = re.search(r"Mode:\s+(\d+).*?User:\s+(\d+)\s+Group:\s+(\d+)", st, re.S)
            assert m and int(m.group(1), 8) == mode and (int(m.group(2)), int(m.group(3))) == (uid, gid), (path, st[:300])
    finally:
        sil(img)


# ------------------------------------------------------------------ the AOSP behaviour relied on

def test_aosp_init_arayuz_ve_yeniden_baglanma():
    """init allows one service per interface (the camera rc cannot copy the vendor's interface
    line); sensorservice reconnects to a restarted HAL only if the sensor handles are unchanged
    (the rebuilt HAL offers the vendor's list); cameraserver waits for a provider on demand only
    when it is lazy; SENSOR_ORIENTATION drives the preview transform."""
    parser = _aosp("system/core", "init/service_parser.cpp")
    assert "redefined in" in parser and "but is already defined by" in parser
    dev = _aosp("frameworks/native", "services/sensorservice/SensorDevice.cpp")
    assert 'LOG_ALWAYS_FATAL("Sensor handles changed, cannot re-enable sensors.");' in dev
    prov = _aosp("frameworks/av", "services/camera/libcameraservice/common/aidl/AidlProviderInfo.cpp")
    i_lazy = prov.index("if (!kEnableLazyHal) {")
    i_get = prov.index("mManager->mAidlServiceProxy->getService(mProviderName)")
    assert i_lazy < i_get
    util = _aosp("frameworks/av", "camera/CameraUtils.cpp")
    assert "ANDROID_SENSOR_ORIENTATION" in util and "ANDROID_LENS_FACING_FRONT" in util


def test_tablet_kamera_akisi_ve_sonuc_pozu():
    """araclar/kamera-olcum (NDK Camera2, as an app) with the vendor provider and the rebuilt one:
    every camera opens and streams through the rebuilt provider, and the lens pose in its capture
    results is the vendor's re-expressed once, equal to its own static characteristics."""
    files = sorted(glob.glob(os.path.join(KANIT, "*", "kamera-olcum.txt")))
    gerekli(*(files[-1:] or [os.path.join(KANIT, "<tarih>", "kamera-olcum.txt")]))
    runs, cur = {}, None
    for line in oku(files[-1]).splitlines():
        m = re.match(r"== (vendor|gbos) provider", line)
        if m:
            cur = runs.setdefault(m.group(1), {})
            continue
        m = re.match(r"(\d+) facing=(\d) orientation=(\d+) frames=(\d+) pose=(.*)$", line)
        if m and cur is not None:
            pose = None if m.group(5) == "none" else [float(x) for x in m.group(5).split()]
            cur[m.group(1)] = (int(m.group(2)), int(m.group(3)), int(m.group(4)), pose)
    v, g = runs.get("vendor", {}), runs.get("gbos", {})
    assert v and v.keys() == g.keys()
    rnd = random.Random(5)
    for cam, (facing, o, frames, pose) in v.items():
        gf, go, gframes, gpose = g[cam]
        assert gf == facing and gframes > 0, cam
        assert go == ((o + R) % 360 if facing == 0 else (o - R) % 360), cam
        assert pose is not None and gpose is not None, cam
        for _ in range(5):
            vn = [rnd.uniform(-1, 1) for _ in range(3)]
            assert close(mat_vec(quat_matrix(gpose), vn), mat_vec(quat_matrix(pose), mat_vec(rz(-R), vn)), 2e-3), cam
