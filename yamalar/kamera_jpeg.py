#!/usr/bin/env python3
"""System-side JPEG for sessions that also record video (framework.jar).

The tablet's CamX HAL freezes video when a JPEG stream is configured next to a hardware video
encoder stream (T870-araclar/acilis-loglari/kamera-video-20261022/KOK-NEDEN.md). The fix every
camera app gets, without touching any app: android.hardware.camera2.impl.ICameraDeviceUserWrapper
(the thin class every binder call to cameraserver goes through) routes createStream, deleteStream,
endConfigure, submitRequestList and disconnect through gbos.CameraBridge, and gbos.CameraJpegShim
swaps the HAL JPEG stream for a YUV stream and produces the JPEG itself (kamera-jpeg/src).

apply_patches.py calls framework_replacements(zip) while it rebuilds framework.jar from the
pristine image; this module returns the dex entries to write:
  classes2.dex  the pristine dex with gbos.CameraBridge merged in (d8 dex merging; the dex is
                within a dozen method ids of the 65 536 limit, so only the five-method bridge goes
                here), its hidden-API flags put back member by member (d8 drops the
                hiddenapi_class_data section, without it ART would treat every member of this dex as
                public SDK), and the wrapper's five binder calls redirected to the bridge (same-size
                instruction rewrites inside the methods' try blocks);
  classes7.dex  gbos.CameraJpegShim and its inner classes, a new dex of the same jar.

Command line (from T870-araclar): python3 yamalar/kamera_jpeg.py prova   builds and checks against
the pristine framework.jar in a temporary directory.
"""
import glob
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_DIR = os.path.join(TOOLS_DIR, "kamera-jpeg")
SDK = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK") or os.path.expanduser("~/Android/Sdk")
BUILD_TOOLS = os.path.join(SDK, "build-tools", "37.0.0")
ANDROID_JAR = os.path.join(SDK, "platforms", "android-37.0", "android.jar")
D8 = os.environ.get("D8", os.path.join(BUILD_TOOLS, "d8"))
JAVAC = os.environ.get("JAVAC", "javac")
DEXDUMP = os.environ.get("DEXDUMP", os.path.join(BUILD_TOOLS, "dexdump"))

WRAPPER = "Landroid/hardware/camera2/impl/ICameraDeviceUserWrapper;"
REMOTE = "Landroid/hardware/camera2/ICameraDeviceUser;"
BRIDGE = "Lgbos/CameraBridge;"
SHIM = "Lgbos/CameraJpegShim;"
HOOKS = ("createStream", "deleteStream", "endConfigure", "configureStreams", "submitRequestList", "disconnect")
WRAPPER_DEX = "classes2.dex"
SHIM_DEX = "classes7.dex"
# hiddenapi flag of the bridge's members: 0x2 = blocked (as dexdump prints BLOCKED for the
# wrapper's own endConfigure); apps never reach it, the wrapper instance is not obtainable.
BRIDGE_FLAG = 2
MAP_HIDDENAPI, MAP_LIST = 0xF000, 0x1000

DEX_INSN = re.compile(r"^([0-9a-f]{6}): [0-9a-f ]+?\s*\|([0-9a-f]{4}): (.*)$")
DEX_METHOD = re.compile(r"^[0-9a-f]{6}:\s+\|\[[0-9a-f]+\] (.*)$")


# ------------------------------------------------------------------ building the Java side

def build(scratch):
    """Compiles kamera-jpeg/src: returns (path of gbos/CameraBridge.class, bytes of the shim dex)."""
    for tool in (D8, ANDROID_JAR, DEXDUMP):
        assert os.path.exists(tool), f"missing: {tool}"
    stubs, classes, shim_out = (os.path.join(scratch, d) for d in ("stubs", "classes", "shim"))
    for d in (stubs, classes, shim_out):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    stub_src = sorted(glob.glob(os.path.join(MODULE_DIR, "stubs", "**", "*.java"), recursive=True))
    src = sorted(glob.glob(os.path.join(MODULE_DIR, "src", "gbos", "*.java")))
    assert stub_src and src, MODULE_DIR
    subprocess.run([JAVAC, "--release", "17", "-nowarn", "-Xlint:-options", "-d", stubs, "-cp", ANDROID_JAR, *stub_src],
                   check=True, stdout=subprocess.DEVNULL)
    # -XDstringConcat=inline: javac 17 would compile string concatenation to invokedynamic on
    # java.lang.invoke.StringConcatFactory, which Android does not have (d8 is told not to desugar)
    subprocess.run([JAVAC, "--release", "17", "-nowarn", "-Xlint:-options", "-XDstringConcat=inline", "-d", classes,
                    "-cp", f"{stubs}{os.pathsep}{ANDROID_JAR}", *src], check=True, stdout=subprocess.DEVNULL)
    shim_classes = sorted(glob.glob(os.path.join(classes, "gbos", "CameraJpegShim*.class")))
    bridge_class = os.path.join(classes, "gbos", "CameraBridge.class")
    assert shim_classes and os.path.exists(bridge_class)
    subprocess.run([D8, "--min-api", "34", "--no-desugaring", "--output", shim_out, *shim_classes],
                   check=True, stdout=subprocess.DEVNULL)
    produced = os.listdir(shim_out)
    assert produced == ["classes.dex"], produced
    with open(os.path.join(shim_out, "classes.dex"), "rb") as f:
        shim_dex = f.read()
    assert SHIM.encode() in shim_dex
    assert b"Ljava/lang/invoke/" not in shim_dex, "shim dex references java.lang.invoke (indy left by javac)"
    return bridge_class, shim_dex


def merge_bridge(dex, bridge_class, scratch):
    """d8 dex merging: the pristine dex plus gbos.CameraBridge, as one dex of the same version."""
    out = os.path.join(scratch, "merge")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    src = os.path.join(scratch, "wrapper-input.dex")
    with open(src, "wb") as f:
        f.write(dex)
    subprocess.run([D8, "--min-api", "34", "--no-desugaring", "--output", out, src, bridge_class],
                   check=True, stdout=subprocess.DEVNULL)
    produced = os.listdir(out)
    assert produced == ["classes.dex"], f"d8 split the dex: {produced} (method ids exhausted?)"
    with open(os.path.join(out, "classes.dex"), "rb") as f:
        merged = f.read()
    assert merged[:8] == dex[:8], "dex version changed"
    return merged


# ------------------------------------------------------------------ dex tables

def _uleb(d, off):
    v, shift = 0, 0
    while True:
        b = d[off]
        off += 1
        v |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return v, off


def _uleb_bytes(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


class Dex:
    """The id tables and class members of a dex file, enough to key hidden-API flags and find
    method indices."""

    def __init__(self, data):
        self.d = data
        h = struct.unpack_from("<12I", data, 0x38)
        self.n_string, self.off_string, self.n_type, self.off_type, self.n_proto, self.off_proto, \
            self.n_field, self.off_field, self.n_method, self.off_method, self.n_class, self.off_class = h
        self.map_off = struct.unpack_from("<I", data, 0x34)[0]
        self.data_size, self.data_off = struct.unpack_from("<II", data, 0x68)
        self.strings = [self._string(i) for i in range(self.n_string)]
        self.types = [self.strings[struct.unpack_from("<I", data, self.off_type + 4 * i)[0]] for i in range(self.n_type)]
        self.protos = [self._proto(i) for i in range(self.n_proto)]
        self.fields = [self._field(i) for i in range(self.n_field)]
        self.methods = [self._method(i) for i in range(self.n_method)]

    def _string(self, i):
        off = struct.unpack_from("<I", self.d, self.off_string + 4 * i)[0]
        _n, off = _uleb(self.d, off)
        end = self.d.index(b"\0", off)
        return self.d[off:end].decode("utf-8", errors="surrogateescape")

    def _proto(self, i):
        _shorty, ret, params_off = struct.unpack_from("<III", self.d, self.off_proto + 12 * i)
        params = []
        if params_off:
            n = struct.unpack_from("<I", self.d, params_off)[0]
            params = [self.types[struct.unpack_from("<H", self.d, params_off + 4 + 2 * k)[0]] for k in range(n)]
        return "(" + "".join(params) + ")" + self.types[ret]

    def _field(self, i):
        cls, typ, name = struct.unpack_from("<HHI", self.d, self.off_field + 8 * i)
        return self.types[cls], self.types[typ], self.strings[name]

    def _method(self, i):
        cls, proto, name = struct.unpack_from("<HHI", self.d, self.off_method + 8 * i)
        return self.types[cls], self.protos[proto], self.strings[name]

    def method_index(self, cls, name, proto):
        hits = [i for i, m in enumerate(self.methods) if m == (cls, proto, name)]
        assert len(hits) == 1, f"{cls}.{name}{proto}: {len(hits)} method ids"
        return hits[0]

    def class_defs(self):
        """[(class descriptor, class_data_off)] in class_def order."""
        out = []
        for i in range(self.n_class):
            cls_idx, _acc, _sup, _ifaces, _src, _ann, data_off, _statics = \
                struct.unpack_from("<8I", self.d, self.off_class + 32 * i)
            out.append((self.types[cls_idx], data_off))
        return out

    def members(self, data_off):
        """The members of one class_data_item, in the order hiddenapi flags are stored:
        [('F', name, type)...static, instance] + [('M', name, proto)...direct, virtual]."""
        if not data_off:
            return []
        d = self.d
        off = data_off
        counts = []
        for _ in range(4):
            n, off = _uleb(d, off)
            counts.append(n)
        out = []
        for kind, n in zip(("F", "F", "M", "M"), counts):
            idx = 0
            for _ in range(n):
                diff, off = _uleb(d, off)
                idx += diff
                _flags, off = _uleb(d, off)
                if kind == "M":
                    _code, off = _uleb(d, off)
                    cls, proto, name = self.methods[idx]
                    out.append(("M", name, proto))
                else:
                    cls, typ, name = self.fields[idx]
                    out.append(("F", name, typ))
        return out

    def map_items(self):
        n = struct.unpack_from("<I", self.d, self.map_off)[0]
        return [struct.unpack_from("<HHII", self.d, self.map_off + 4 + 12 * i) for i in range(n)]


# ------------------------------------------------------------------ hidden-API flags

def hiddenapi_decode(dex):
    """{(class descriptor, member key): flag} from the dex's hiddenapi_class_data section."""
    x = Dex(dex)
    items = [m for m in x.map_items() if m[0] == MAP_HIDDENAPI]
    assert len(items) == 1, "no hiddenapi_class_data section"
    item_off = items[0][3]
    size = struct.unpack_from("<I", dex, item_off)[0]
    flags = {}
    for i, (cls, data_off) in enumerate(x.class_defs()):
        off = struct.unpack_from("<I", dex, item_off + 4 + 4 * i)[0]
        members = x.members(data_off)
        if off == 0:
            assert not members or True  # a class whose members all carry flag 0 may have no data
            for m in members:
                flags[(cls, m)] = 0
            continue
        p = item_off + off
        for m in members:
            v, p = _uleb(dex, p)
            flags[(cls, m)] = v
        assert p <= item_off + size
    return flags


def hiddenapi_section(x, flags, default):
    """The hiddenapi_class_data_item bytes for dex x with the given per-member flags."""
    defs = x.class_defs()
    body = bytearray()
    offsets = []
    header_len = 4 + 4 * len(defs)
    for cls, data_off in defs:
        members = x.members(data_off)
        values = [flags.get((cls, m), default) for m in members]
        if not values or all(v == 0 for v in values):
            offsets.append(0)
            continue
        offsets.append(header_len + len(body))
        for v in values:
            body += _uleb_bytes(v)
    size = header_len + len(body)
    return struct.pack("<I", size) + b"".join(struct.pack("<I", o) for o in offsets) + bytes(body)


def hiddenapi_encode(dex, flags, default):
    """Appends a hiddenapi_class_data section to a dex that has none (d8 output): the section
    takes the place of the map list, which moves to the end; header and map are updated."""
    x = Dex(dex)
    items = x.map_items()
    assert not any(m[0] == MAP_HIDDENAPI for m in items), "dex already has hiddenapi data"
    map_item = [m for m in items if m[0] == MAP_LIST]
    assert len(map_item) == 1 and map_item[0][3] == x.map_off
    map_len = 4 + 12 * len(items)
    assert x.map_off + map_len == len(dex), "map list is not the last section"
    section = hiddenapi_section(x, flags, default)
    out = bytearray(dex[:x.map_off])
    assert len(out) % 4 == 0
    out += section
    while len(out) % 4:
        out += b"\0"
    new_map_off = len(out)
    new_items = [m for m in items if m[0] != MAP_LIST] + [(MAP_HIDDENAPI, 0, 1, x.map_off), (MAP_LIST, 0, 1, new_map_off)]
    new_items.sort(key=lambda m: m[3])
    out += struct.pack("<I", len(new_items))
    for t, _u, s, o in new_items:
        out += struct.pack("<HHII", t, 0, s, o)
    struct.pack_into("<I", out, 0x20, len(out))          # file_size
    struct.pack_into("<I", out, 0x34, new_map_off)       # map_off
    struct.pack_into("<I", out, 0x68, len(out) - x.data_off)  # data_size
    return fix_dex_header(bytes(out))


def hiddenapi_roundtrip_ok(dex):
    """Decoding the pristine section and re-encoding it gives the pristine bytes (the test of
    every layout assumption above)."""
    x = Dex(dex)
    item_off = [m for m in x.map_items() if m[0] == MAP_HIDDENAPI][0][3]
    size = struct.unpack_from("<I", dex, item_off)[0]
    return hiddenapi_section(x, hiddenapi_decode(dex), 0) == dex[item_off:item_off + size]


# ------------------------------------------------------------------ the wrapper's binder calls

def _dexdump(dex, scratch):
    p = os.path.join(scratch, "dexdump-input.dex")
    with open(p, "wb") as f:
        f.write(dex)
    return subprocess.run([DEXDUMP, "-d", p], check=True, capture_output=True).stdout.decode(errors="replace")


def _wrapper_methods(dump):
    """{"name:proto": [(offset, text), ...]} of the wrapper's methods from a dexdump -d."""
    methods, current, in_class = {}, None, False
    for line in dump.splitlines():
        if line.startswith("Class #"):
            in_class = False
        elif line.strip() == f"Class descriptor  : '{WRAPPER}'":
            in_class = True
        m = DEX_METHOD.match(line)
        if m and in_class:
            name = m.group(1)  # android.hardware.camera2.impl.ICameraDeviceUserWrapper.createStream:(...)I
            current = methods.setdefault(name.split(".")[-1], [])
            continue
        m = DEX_INSN.match(line)
        if m and in_class and current is not None:
            # (file offset, text, code-unit address inside the method)
            current.append((int(m.group(1), 16), m.group(3), int(m.group(2), 16)))
    return methods


def _encode_invoke_static(method_idx, regs):
    """invoke-static {regs}, method@idx in format 35c (up to 5 registers, each v0..v15)."""
    assert 1 <= len(regs) <= 5 and all(0 <= r <= 15 for r in regs), regs
    g = regs[4] if len(regs) == 5 else 0
    c, d, e, f = (regs + [0, 0, 0, 0])[:4]
    return bytes([0x71, (len(regs) << 4) | g]) + struct.pack("<H", method_idx) + bytes([(d << 4) | c, (f << 4) | e])


def patch_wrapper(dex, scratch):
    """Redirects the wrapper's five binder calls to gbos.CameraBridge. Each site is
    `iget-object vR, vThis, mRemoteDevice` ... `invoke-interface {vR, args}, ICameraDeviceUser.X`;
    the invoke becomes `invoke-static {vThis, args}, CameraBridge.X` (same size) and, when the
    iget is right before it, the iget becomes two nops. Returns (patched dex, log lines)."""
    x = Dex(dex)
    methods = _wrapper_methods(_dexdump(dex, scratch))
    out = bytearray(dex)
    log = []
    for hook in HOOKS:
        insns = methods.get(f"{hook}:" + "", None)
        key = [k for k in methods if k.startswith(hook + ":")]
        assert len(key) == 1, f"{WRAPPER}.{hook}: {len(key)} methods"
        insns = methods[key[0]]
        proto = key[0].split(":", 1)[1]
        igets = [(i, o, t) for i, (o, t, _u) in enumerate(insns)
                 if re.match(rf"iget-object v\d+, v\d+, {re.escape(WRAPPER)}\.mRemoteDevice:", t)]
        calls = [(i, o, t) for i, (o, t, _u) in enumerate(insns)
                 if re.match(rf"invoke-interface \{{[v\d, ]+\}}, {re.escape(REMOTE)}\.{hook}:", t)]
        assert len(igets) == 1 and len(calls) == 1, f"{hook}: {len(igets)} igets, {len(calls)} calls"
        (ii, iget_off, iget_text), (ci, call_off, call_text) = igets[0], calls[0]
        remote_reg, this_reg = map(int, re.match(r"iget-object v(\d+), v(\d+),", iget_text).groups())
        regs = [int(r) for r in re.findall(r"v(\d+)", call_text.split("}")[0])]
        assert regs[0] == remote_reg, (hook, call_text)
        assert dex[call_off] == 0x72 and dex[iget_off] == 0x54, hook
        # the bridge method takes the wrapper first, then the interface method's parameters
        bridge_proto = "(" + WRAPPER + proto[1:]
        idx = x.method_index(BRIDGE, hook, bridge_proto)
        new_call = _encode_invoke_static(idx, [this_reg] + regs[1:])
        out[call_off:call_off + 6] = new_call
        if call_off == iget_off + 4:
            out[iget_off:iget_off + 4] = bytes(4)  # two nops; v{remote} is no longer needed
            where = f"{iget_off:#x}"
        else:
            where = f"{call_off:#x} (iget kept)"
        log.append(f"{hook}{proto} -> CameraBridge.{hook} method@{idx:#x} at {where}")
    return bytes(out), log


def fix_dex_header(dex):
    import hashlib
    import zlib
    dex = bytearray(dex)
    dex[12:32] = hashlib.sha1(bytes(dex[32:])).digest()
    struct.pack_into("<I", dex, 8, zlib.adler32(bytes(dex[12:])) & 0xFFFFFFFF)
    return bytes(dex)


# ------------------------------------------------------------------ entry point for apply_patches

def framework_replacements(z, scratch, log=print):
    """{entry: bytes} to write into framework.jar: the merged and patched wrapper dex and the shim
    dex. `z` is the pristine framework.jar (zipfile.ZipFile)."""
    names = z.namelist()
    assert WRAPPER_DEX in names and SHIM_DEX not in names, names
    pristine = z.read(WRAPPER_DEX)
    assert WRAPPER.encode() in pristine, f"{WRAPPER} is not in {WRAPPER_DEX} (new release?)"
    assert hiddenapi_roundtrip_ok(pristine), "hiddenapi layout assumption broken"
    flags = hiddenapi_decode(pristine)
    bridge_class, shim_dex = build(scratch)
    merged = merge_bridge(pristine, bridge_class, scratch)
    merged = hiddenapi_encode(merged, flags, BRIDGE_FLAG)
    x = Dex(merged)
    assert x.n_method <= 0xFFFF
    # every pristine member keeps its flag, the bridge's members are blocked
    after = hiddenapi_decode(merged)
    missing = [k for k in flags if k not in after]
    assert not missing, f"members lost in the merge: {missing[:5]}"
    changed = [k for k in flags if after[k] != flags[k]]
    assert not changed, f"hiddenapi flags changed: {changed[:5]}"
    assert all(v == BRIDGE_FLAG for k, v in after.items() if k[0] == BRIDGE and k[1][1] != "<init>")
    patched, lines = patch_wrapper(merged, scratch)
    for line in lines:
        log(f"[+] framework.jar!{WRAPPER_DEX}: {line}")
    log(f"[+] framework.jar!{SHIM_DEX}: gbos.CameraJpegShim ({len(shim_dex)} bytes); {WRAPPER_DEX} method ids "
        f"{Dex(pristine).n_method} -> {x.n_method}, hiddenapi flags restored for {len(after)} members")
    return {WRAPPER_DEX: fix_dex_header(patched), SHIM_DEX: shim_dex}


def prova():
    """Builds and checks against the pristine framework.jar of mica-imaj/guncel in a temp dir."""
    sys.path.insert(0, TOOLS_DIR)
    import apply_patches  # noqa: E402
    tmp = tempfile.mkdtemp(prefix="gbos-kamera-jpeg-", dir="/var/tmp")
    try:
        jar = os.path.join(tmp, "framework.jar")
        with open(jar, "wb") as f:
            f.write(apply_patches.pristine_file("system/framework/framework.jar"))
        with zipfile.ZipFile(jar) as z:
            rep = framework_replacements(z, tmp)
        for name, data in rep.items():
            print(f"{name}: {len(data)} bytes")
        print("PROVA OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "prova":
        prova()
    else:
        print(__doc__)
