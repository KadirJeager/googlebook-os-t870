"""i) patch_camera_app: the CameraX JpegHalCorruptImageQuirk patch of the Googlebook camera app
(AL_Camera.apk) against the real pristine APK, and the working tree.

The back camera froze on video: CameraX opened preview + encoder + JPEG and the CamX HAL's
"Video With Liveshot" usecase filled no video buffer (acilis-loglari/kamera-video-20261022/
KOK-NEDEN.md). The patch turns CameraX's own quirk on for the back camera: List.contains(Build.DEVICE) in
the quirk loader becomes LENS_FACING == LENS_FACING_BACK, same size. Here the function runs into a scratch PROD_DIR
on the real pristine APK and the result is checked instruction by instruction, then the APK as a
whole (only that dex changed, dex entries still stored and aligned, project signature), then the
working tree carries the unpatched app (the patch is kept but not applied, see test_calisan_agac_orijinal)."""
import os
import re
import subprocess
import zipfile

import pytest

from conftest import GERCEK_WORK, gerekli, oku

QUIRK = "Landroidx/camera/camera2/compat/quirk/JpegHalCorruptImageQuirk;"


@pytest.fixture(scope="module")
def yamali(ap, gecici_kok):
    """Runs patch_camera_app once with PROD_DIR in a scratch dir; returns (ap, pristine apk bytes,
    patched apk path)."""
    gerekli(ap.APP_KEYSTORE, ap.APKSIGNER, ap.ZIPALIGN, ap.DEXDUMP)
    tree, rel, image, _dev = ap.CAMERA_APK
    assert tree == "prod"
    prod_dir = os.path.join(gecici_kok, "kamera-prod")
    eski = ap.PROD_DIR
    ap.PROD_DIR = prod_dir
    try:
        ap.patch_camera_app()
    finally:
        ap.PROD_DIR = eski
    return ap, ap.pristine_file(rel, image), os.path.join(prod_dir, rel)


def _dexler(apk_bytes_or_path):
    src = apk_bytes_or_path
    if isinstance(src, bytes):
        import io
        src = io.BytesIO(src)
    with zipfile.ZipFile(src) as z:
        return {e: z.read(e) for e in z.namelist() if re.fullmatch(r"classes\d*\.dex", e)}, \
            {e: (z.getinfo(e).compress_type, z.read(e)) for e in z.namelist()
             if not re.fullmatch(r"classes\d*\.dex", e)}


def _site(ap, dex):
    insns = ap.dex_methods(ap.dexdump(dex))
    sites = ap.camerax_jpeg_quirk_sites(insns)
    return insns, sites


def test_tek_yer_ve_sadece_o_baytlar(yamali):
    """Exactly one dex holds the loader, exactly one site in it; the patched dex differs from the
    pristine one only in those 8 bytes and the header's checksum/signature."""
    ap, pristine, patched_path = yamali
    p_dex, _ = _dexler(pristine)
    y_dex, _ = _dexler(patched_path)
    assert set(p_dex) == set(y_dex)
    degisen = [e for e in p_dex if p_dex[e] != y_dex[e]]
    assert len(degisen) == 1, degisen
    entry = degisen[0]
    insns, sites = _site(ap, p_dex[entry])
    assert len(sites) == 1, sites
    method, i = sites[0]
    off = insns[method][i - 7][0]  # the device-list sget-object that opens the 20-unit sequence
    a, b = p_dex[entry], y_dex[entry]
    assert len(a) == len(b)
    farkli = [k for k in range(len(a)) if a[k] != b[k]]
    assert all(8 <= k < 32 or off <= k < off + 40 for k in farkli), \
        f"bytes outside the site changed: {[hex(k) for k in farkli if not (8 <= k < 32 or off <= k < off + 40)]}"
    assert any(off <= k < off + 40 for k in farkli)


def test_komutlar_lens_facing(yamali):
    """In the pristine dex the site computes List.contains(Build.DEVICE); in the patched one the
    same 20 code units read LENS_FACING from the camera's characteristics and yield 1 only for
    LENS_FACING_BACK (every instruction decoded by dexdump, both branch targets inside the
    sequence), the const-class that follows is untouched, and the quirk's compiled-in device list
    is still there."""
    ap, pristine, patched_path = yamali
    p_dex, _ = _dexler(pristine)
    y_dex, _ = _dexler(patched_path)
    entry = next(e for e in p_dex if p_dex[e] != y_dex[e])
    p_insns, p_sites = _site(ap, p_dex[entry])
    method, i = p_sites[0]
    assert p_insns[method][i][1].startswith("invoke-interface ")
    reg = int(re.match(r"move-result v(\d+)$", p_insns[method][i + 1][1]).group(1))
    start = p_insns[method][i - 7][0]
    y_insns = ap.dex_methods(ap.dexdump(y_dex[entry]))
    assert not ap.camerax_jpeg_quirk_sites(y_insns), "patched dex still has the contains() gate"
    y = y_insns[method]
    j = next(k for k, (o, _t) in enumerate(y) if o == start)
    seq = [t for _o, t in y[j:j + 12]]
    beklenen = [
        rf"sget-object v{reg}, Landroid/hardware/camera2/CameraCharacteristics;\.LENS_FACING:",
        rf"invoke-interface \{{v\d+, v{reg}\}}, L[^;]+;\.[^:]+:\(Landroid/hardware/camera2/CameraCharacteristics\$Key;\)Ljava/lang/Object;",
        rf"move-result-object v{reg}$",
        rf"check-cast v{reg}, Ljava/lang/Integer;",
        rf"if-eqz v{reg}, ([0-9a-f]{{4}})",
        rf"invoke-virtual \{{v{reg}\}}, Ljava/lang/Integer;\.intValue:\(\)I",
        rf"move-result v{reg}$",
        r"const/4 v(\d+), #int 1\b",
        rf"if-ne v{reg}, v(\d+), ([0-9a-f]{{4}})",
        rf"const/4 v{reg}, #int 1\b",
        r"goto ([0-9a-f]{4})",
        rf"const/4 v{reg}, #int 0\b",
    ]
    for t, b in zip(seq, beklenen):
        assert re.match(b, t), (t, b)
    tmp = re.match(beklenen[7], seq[7]).group(1)
    assert re.match(beklenen[8], seq[8]).group(1) == tmp
    sifir = y[j + 11][0]  # const/4 vR, #0
    son = y[j + 12][0]    # the const-class after the sequence
    assert son == start + 40
    # branch targets are printed as code-unit addresses within the method; convert through the
    # first instruction's address
    base = y[0][0]
    hedef = lambda t: base + int(t, 16) * 2  # noqa: E731
    assert hedef(re.match(beklenen[4], seq[4]).group(1)) == sifir
    assert hedef(re.match(beklenen[8], seq[8]).group(2)) == sifir
    assert hedef(re.match(beklenen[10], seq[10]).group(1)) == son
    assert f", {QUIRK} //" in y[j + 12][1]
    clinit = "androidx.camera.camera2.compat.quirk.JpegHalCorruptImageQuirk.<clinit>:()V"
    liste = lambda insns: [t for _o, t in insns[clinit] if t.startswith("const-string ")]  # noqa: E731
    assert liste(p_insns) == liste(y_insns) and len(liste(y_insns)) == 3


def test_apk_butunu(yamali):
    """Every non-dex entry is byte-identical with the same compression (apksigner drops Google's
    Play source stamp, stamp-cert-sha256, which belongs to the old signature), the dex entries
    stay stored (uncompressed, 4-aligned), and the APK verifies with the project's key, not
    Google's."""
    ap, pristine, patched_path = yamali
    _, p_rest = _dexler(pristine)
    _, y_rest = _dexler(patched_path)
    atla = ("META-INF/", "stamp-cert-sha256")
    p_rest = {e: v for e, v in p_rest.items() if not e.startswith(atla)}
    y_rest = {e: v for e, v in y_rest.items() if not e.startswith(atla)}
    assert p_rest == y_rest
    with zipfile.ZipFile(patched_path) as z:
        for info in z.infolist():
            if re.fullmatch(r"classes\d*\.dex", info.filename):
                assert info.compress_type == zipfile.ZIP_STORED, info.filename
    r = subprocess.run([ap.ZIPALIGN, "-c", "-p", "4", patched_path], capture_output=True, text=True)
    assert r.returncode == 0, "not zipaligned: " + r.stdout
    r = subprocess.run([ap.APKSIGNER, "verify", "--print-certs", patched_path],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "O=Googlebook OS T870" in r.stdout and "Google Inc." not in r.stdout
    assert oct(os.stat(patched_path).st_mode & 0o777) == "0o644"


def test_calisan_agac_orijinal(yamali):
    """The working tree carries the camera app exactly as Google ships it: patch_camera_app is
    kept but not called (the framework fix of yamalar/kamera_jpeg.py covers every app and the
    re-signed app would lose its Play updates, Kadir 23 Oct 2026)."""
    ap, pristine, _patched_path = yamali
    calisan = os.path.join(GERCEK_WORK, "prod", ap.CAMERA_APK[1])
    gerekli(calisan)
    assert oku(calisan, "rb") == pristine
    src = oku(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apply_patches.py"))
    assert "\n    patch_camera_app()\n" not in src
