#!/usr/bin/env python3
"""
Cross-device acceptance test of the Android build tools apply_patches.py and its modules call, on the
tablet's Linux environment (Debian packages + the SDK jars the kit carries) against the computer's
Android SDK build-tools 37.0.0, with the pristine mica release's own files as input:

  python3 -I guncelleyici/testler/android-araclari-esdegerlik.py --kanit DIR

  dexdump -d   every classes*.dex of framework.jar, services.jar, desktop-services.jar and
               AL_Camera.apk (apply_patches.dex_methods / dex_method_code, kamera_jpeg). Debian's
               dexdump (ART 14) differs from build-tools 37's in two ways only, both undone by
               normalize() before comparing: it still prints zero-length local variable ranges, and
               it renders an octal escape inside a string literal digit by digit as ASCII codes
               ("\\004" -> "\\484852"). The normalized output must be identical; every method whose
               text as the pipeline parses it (dex_methods) differs must be one no patch reads.
  zipalign     -f -p 4 of a jar rewritten as apply_patches.rewrite_jar_entries does: same entries,
               same data, every non-empty stored entry 4-byte aligned (Debian's zipalign 10 also
               pads zero-length entries, so the bytes differ)
  apksigner    sign (apply_patches.sign_apk's flags, a throwaway test key) + verify: same bytes;
               the tablet runs SDK lib/apksigner.jar with Debian's JDK (Debian's apksigner package
               would pull a desktop JRE)
  aapt2        dump packagename (yamalar/aosp_terminal.package_name): same answer
  javac + d8   kamera_jpeg.framework_replacements on the pristine framework.jar (javac --release 17,
               d8.jar dex merging, dexdump): the dex entries it returns must be the same bytes
SDK jars (d8.jar, apksigner.jar, android.jar) are copied to the tablet for the run and removed after.
Writes DIR/android-araclari.json. Exits non-zero on any difference.
"""
import argparse
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, KIT)
from t870_cihaz import seri  # noqa: E402
TABLET_DIR = "/var/tmp/gbos-android-araclari-esdegerlik"
LOCK = "/var/tmp/gbos-dogal-yon/TABLET-ANA-OTURUMDA"
SDK = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK") or os.path.expanduser("~/Android/Sdk")
BT = "build-tools/37.0.0"
PLATFORM = "platforms/android-37.0/android.jar"
IMAGES = os.path.join(KIT, "mica-imaj", "guncel")
DUMP_EROFS = os.path.join(os.environ.get("EROFS_BIN", os.path.expanduser("~/.local/bin")), "dump.erofs")
INPUTS = [("system_a.erofs", "/system/framework/framework.jar"),
          ("system_a.erofs", "/system/framework/services.jar"),
          ("system_a.erofs", "/system/framework/desktop-services.jar"),
          ("product_a.erofs", "/app/AL_Camera/AL_Camera.apk")]
KEY_PASS = "gbos-test-anahtari"

# Runs on both machines: side.py STAGE OUT.json  (tools from the environment variables below)
SIDE = r'''
import hashlib, io, json, os, re, shutil, subprocess, sys, zipfile
stage, out_path = sys.argv[1:3]
DEXDUMP, ZIPALIGN, AAPT2, APKSIGNER = (os.environ[k] for k in ("DEXDUMP", "ZIPALIGN", "AAPT2", "APKSIGNER"))
sha = lambda b: hashlib.sha256(b).hexdigest()
work = os.path.join(stage, "is")
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
out = {"dexdump": {}, "araclar": {}}

# the pipeline's own dexdump parser (apply_patches.dex_methods and its regexes), taken from its source
import ast
src = open(os.path.join(stage, "kit", "apply_patches.py")).read()
ns = {"re": re}
for node in ast.parse(src).body:
    if (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in ("DEX_INSN", "DEX_METHOD")) or \
            (isinstance(node, ast.FunctionDef) and node.name == "dex_methods"):
        exec(compile(ast.Module([node], []), "apply_patches.py", "exec"), ns)

def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)

def version(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip().splitlines()[:2]

out["araclar"] = {"java": version(["java", "-version"]), "javac": version(["javac", "-version"]),
                  "aapt2": version([AAPT2, "version"]), "apksigner": version([APKSIGNER, "--version"]),
                  "dexdump": shutil.which(DEXDUMP) or DEXDUMP, "zipalign": shutil.which(ZIPALIGN) or ZIPALIGN}

# dexdump -d of every dex; the dumped file's own path is the only machine-specific text
LOCAL0 = re.compile(rb"^        0x0000 - 0x0000 reg=.*\n", re.M)
ESC = re.compile(rb"\\((?:4[89]|5[0-5])(?:4[89]|5[0-5])(?:4[89]|5[0-5]))")
def normalize(text):
    """build-tools 37 rendering: no zero-length local ranges, octal escapes as octal digits"""
    text = LOCAL0.sub(b"", text)
    return ESC.sub(lambda m: b"\\" + bytes(int(m.group(1)[i:i + 2]) for i in (0, 2, 4)), text)
def method_hashes(text):
    return {m: sha(json.dumps(i).encode())[:16] for m, i in ns["dex_methods"](text.decode(errors="replace")).items()}
# methods the patches read by name or scan by content (apply_patches: SystemServer.startOtherServices,
# RoSystemFeatures.maybeHasFeature, every hasSystemFeature call site; kamera_jpeg: the wrapper)
ANCHORS = re.compile(r"hasSystemFeature|SystemServer\.startOtherServices|RoSystemFeatures|ICameraDeviceUserWrapper"
                     r"|JpegHalCorruptImageQuirk")
def anchored(text):
    return sorted(m for m, insns in ns["dex_methods"](text.decode(errors="replace")).items()
                  if ANCHORS.search(m) or any(ANCHORS.search(i[1]) for i in insns))
dex_path = os.path.join(work, "x.dex")
for jar in sorted(os.listdir(os.path.join(stage, "girdi"))):
    with zipfile.ZipFile(os.path.join(stage, "girdi", jar)) as z:
        for name in sorted(n for n in z.namelist() if re.fullmatch(r"classes\d*\.dex", n)):
            with open(dex_path, "wb") as f:
                f.write(z.read(name))
            text = run([DEXDUMP, "-d", dex_path]).stdout.replace(dex_path.encode(), b"X.dex")
            norm = normalize(text)
            key = f"{jar}!{name}"
            out["dexdump"][key] = {"sha256": sha(text), "normal_sha256": sha(norm), "satir": text.count(b"\n")}
            out.setdefault("yontemler", {})[key] = method_hashes(text)
            out.setdefault("yontemler_normal", {})[key] = method_hashes(norm)
            out.setdefault("dayanak", {})[key] = anchored(text)

# zipalign: a jar rewritten the way rewrite_jar_entries writes it
src = os.path.join(stage, "girdi", "desktop-services.jar")
tmp, aligned = os.path.join(work, "yeniden.jar"), os.path.join(work, "hizali.jar")
with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
    for info in zin.infolist():
        ni = zipfile.ZipInfo(info.filename, date_time=info.date_time)
        ni.compress_type, ni.external_attr, ni.create_system = info.compress_type, info.external_attr, info.create_system
        zout.writestr(ni, zin.read(info.filename))
run([ZIPALIGN, "-f", "-p", "4", tmp, aligned])
def zip_view(path):
    """entries, their data and the 4-byte alignment of every non-empty stored entry"""
    import struct
    data = open(path, "rb").read()
    rows = []
    with zipfile.ZipFile(path) as z:
        for i in z.infolist():
            n, e = struct.unpack_from("<HH", data, i.header_offset + 26)
            start = i.header_offset + 30 + n + e
            rows.append([i.filename, i.compress_type, i.CRC, i.file_size,
                         start % 4 == 0 if i.compress_type == 0 and i.file_size else None])
    return rows
view = zip_view(aligned)
out["zipalign"] = {"sha256": sha(open(aligned, "rb").read()), "gorunum": sha(json.dumps(view).encode()),
                   "hizasiz": [r[0] for r in view if r[4] is False],
                   "denetim": subprocess.run([ZIPALIGN, "-c", "-p", "4", aligned], capture_output=True).returncode}

# apksigner: sign_apk's flags on the zipaligned camera app
apk_in, apk_out = os.path.join(work, "kamera-hizali.apk"), os.path.join(work, "kamera-imzali.apk")
run([ZIPALIGN, "-f", "-p", "4", os.path.join(stage, "girdi", "AL_Camera.apk"), apk_in])
ks, pw = os.path.join(stage, "test.p12"), "pass:" + os.environ["KEY_PASS"]
run([APKSIGNER, "sign", "--ks", ks, "--ks-key-alias", "test", "--ks-pass", pw, "--key-pass", pw,
     "--v1-signing-enabled", "false", "--v2-signing-enabled", "true", "--v3-signing-enabled", "true",
     "--v4-signing-enabled", "false", "--out", apk_out, apk_in])
verify = subprocess.run([APKSIGNER, "verify", "-v", apk_out], capture_output=True, text=True)
out["apksigner"] = {"sha256": sha(open(apk_out, "rb").read()), "verify_rc": verify.returncode,
                    "verify": [l for l in verify.stdout.splitlines() if l.startswith("Verified using")]}

# aapt2 dump packagename
out["aapt2"] = {apk: run([AAPT2, "dump", "packagename", os.path.join(stage, "girdi", apk)], text=True).stdout.strip()
                for apk in ("AL_Camera.apk", "VmTerminalApp.apk")}

# javac + d8 + dexdump through the pipeline's own module
sys.path.insert(0, os.path.join(stage, "kit"))
import kamera_jpeg
kamera_jpeg.MODULE_DIR = os.path.join(stage, "kit", "kamera-jpeg")
scratch = os.path.join(work, "kamera")
os.makedirs(scratch)
with zipfile.ZipFile(os.path.join(stage, "girdi", "framework.jar")) as z:
    repl = kamera_jpeg.framework_replacements(z, scratch, log=lambda m: None)
out["kamera_jpeg"] = {k: sha(v) for k, v in sorted(repl.items())}
with open(out_path, "w") as f:
    json.dump(out, f)
'''


def adb_chroot(script, stdin=None):
    if os.path.exists(LOCK):
        raise SystemExit(f"{LOCK} var: tablet ana oturumda, ölçüm yapılmadı")
    return subprocess.run(["adb", "-s", seri(), "shell", "gbos-linux run -- sh -c " + shlex.quote(script)],
                          input=stdin, capture_output=True, check=False)


def stage(base):
    girdi = os.path.join(base, "girdi")
    os.makedirs(girdi)
    for image, path in INPUTS:
        with open(os.path.join(girdi, os.path.basename(path)), "wb") as f:
            subprocess.run([DUMP_EROFS, "--cat", f"--path={path}", os.path.join(IMAGES, image)],
                           stdout=f, check=True)
    shutil.copy2(os.path.join(KIT, "terminal", "out", "arm64", "kopru", "VmTerminalApp.apk"), girdi)
    kit = os.path.join(base, "kit")
    os.makedirs(kit)
    shutil.copy2(os.path.join(KIT, "yamalar", "kamera_jpeg.py"), kit)
    shutil.copy2(os.path.join(KIT, "apply_patches.py"), kit)
    shutil.copytree(os.path.join(KIT, "kamera-jpeg"), os.path.join(kit, "kamera-jpeg"),
                    ignore=shutil.ignore_patterns("out", "__pycache__"))
    sdk = os.path.join(base, "sdk")
    for rel in (f"{BT}/d8", f"{BT}/apksigner", f"{BT}/lib/d8.jar", f"{BT}/lib/apksigner.jar", PLATFORM):
        os.makedirs(os.path.dirname(os.path.join(sdk, rel)), exist_ok=True)
        shutil.copy2(os.path.join(SDK, rel), os.path.join(sdk, rel))
    subprocess.run(["keytool", "-genkeypair", "-keystore", os.path.join(base, "test.p12"), "-storetype", "PKCS12",
                    "-alias", "test", "-keyalg", "RSA", "-keysize", "2048", "-validity", "1",
                    "-dname", "CN=gbos-esdegerlik-testi", "-storepass", KEY_PASS, "-keypass", KEY_PASS],
                   check=True, capture_output=True)
    with open(os.path.join(base, "side.py"), "w") as f:
        f.write(SIDE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kanit", required=True)
    args = ap.parse_args()
    os.makedirs(args.kanit, exist_ok=True)
    base = tempfile.mkdtemp(prefix="gbos-android-araclari-", dir="/var/tmp")
    try:
        stage(base)
        env = dict(os.environ, KEY_PASS=KEY_PASS, ANDROID_SDK=SDK, D8=os.path.join(SDK, BT, "d8"),
                   DEXDUMP=os.path.join(SDK, BT, "dexdump"), ZIPALIGN=os.path.join(SDK, BT, "zipalign"),
                   AAPT2=os.path.join(SDK, BT, "aapt2"), APKSIGNER=os.path.join(SDK, BT, "apksigner"))
        r = subprocess.run([sys.executable, "-I", os.path.join(base, "side.py"), base,
                            os.path.join(base, "pc.json")], env=env, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit("computer:\n" + r.stderr[-4000:])
        with open(os.path.join(base, "pc.json")) as f:
            pc = json.load(f)

        t = TABLET_DIR
        tar = subprocess.run(["tar", "-cf", "-", "-C", base, "girdi", "kit", "sdk", "test.p12", "side.py"],
                             capture_output=True, check=True).stdout
        adb_chroot(f"rm -rf {t}")
        r = adb_chroot(f"mkdir -p {t} && tar -xf - -C {t}", stdin=tar)
        if r.returncode:
            raise SystemExit(r.stderr.decode())
        tenv = (f"KEY_PASS={KEY_PASS} ANDROID_SDK={t}/sdk D8={t}/sdk/{BT}/d8 DEXDUMP=/usr/bin/dexdump "
                f"ZIPALIGN=/usr/bin/zipalign AAPT2=/usr/bin/aapt2 APKSIGNER={t}/sdk/{BT}/apksigner")
        r = adb_chroot(f"cd {t} && env {tenv} python3 -I side.py {t} {t}/tablet.json >&2 && cat {t}/tablet.json")
        if r.returncode:
            raise SystemExit("tablet:\n" + r.stderr.decode()[-4000:])
        tab = json.loads(r.stdout)
        # methods whose text, as the pipeline parses it, differs between the two dexdumps
        raw_methods = {}
        for key, a in pc["yontemler"].items():
            b = tab["yontemler"].get(key, {})
            names = sorted(m for m in set(a) | set(b) if a.get(m) != b.get(m))
            if names:
                raw_methods[key] = names
        adb_chroot(f"rm -rf {t}")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    sources = "".join(open(p, errors="replace").read() for p in
                      [os.path.join(KIT, "apply_patches.py")] + sorted(glob.glob(os.path.join(KIT, "yamalar", "*.py"))))
    # The two dexdumps' method views differ only where normalize() changed a line, i.e. in string
    # literals holding non-printable characters. A patch could only notice that if one of its
    # const-string patterns contained such an escape.
    used = sorted({line.strip() for line in re.findall(r"const-string[^\n]*", sources)
                   if re.search(r"\\\\[0-7]{3}|\\[0-7]{3}", line)})
    anchored = sorted(f"{k}: {m}" for k, ms in raw_methods.items() for m in ms
                      if m in set(pc["dayanak"].get(k, [])) | set(tab["dayanak"].get(k, [])))
    checks = {
        f"dexdump: normalleştirilmiş çıktı aynı ({len(pc['dexdump'])} dex)":
            {k: v["normal_sha256"] for k, v in pc["dexdump"].items()} ==
            {k: v["normal_sha256"] for k, v in tab["dexdump"].items()},
        "dexdump: normalleştirilmiş yöntem/komut görünümü (dex_methods) aynı":
            pc["yontemler_normal"] == tab["yontemler_normal"],
        "dexdump: hattın const-string kalıplarında basılamayan karakter kaçışı yok": not used,
        "dexdump: ham görünümü farklı yöntemlerin hiçbiri yamaların okuduğu yöntem değil": not anchored,
        "zipalign: aynı girdiler, aynı veri, boş olmayan her saklı girdi 4 bayt hizalı":
            pc["zipalign"]["gorunum"] == tab["zipalign"]["gorunum"] and not tab["zipalign"]["hizasiz"],
        "zipalign -c geçti (tablet)": tab["zipalign"]["denetim"] == 0,
        "apksigner sign aynı bayt": pc["apksigner"]["sha256"] == tab["apksigner"]["sha256"],
        "apksigner verify geçti (tablet)": tab["apksigner"]["verify_rc"] == 0,
        "aapt2 dump packagename aynı": pc["aapt2"] == tab["aapt2"],
        "javac + d8 + dexdump: kamera_jpeg.framework_replacements aynı bayt": pc["kamera_jpeg"] == tab["kamera_jpeg"],
    }
    for side in (pc, tab):
        side.pop("yontemler")
        side.pop("yontemler_normal")
        side["dayanak_yontem_sayisi"] = {k: len(v) for k, v in side.pop("dayanak").items()}
    info = {"pc": pc, "tablet": tab, "dexdump_ham_gorunumu_farkli_yontemler": raw_methods,
            "kacisli_const_string_kaliplari": used, "farkli_dayanak_yontemleri": anchored,
            "zipalign_bayt_ayni": pc["zipalign"]["sha256"] == tab["zipalign"]["sha256"], "denetimler": checks}
    with open(os.path.join(args.kanit, "android-araclari.json"), "w") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    for name, ok in checks.items():
        print(f"{'GEÇTİ' if ok else 'KALDI'}  {name}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
