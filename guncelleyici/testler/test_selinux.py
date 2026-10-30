"""selinux/70-gbos-guncelleyici.cil against the policy the tablet compiles (yamalar/selinux.py's
init_inputs over the current work tree, the tablet's LineageOS vendor/odm policy):

- it compiles with secilc exactly as init does
- it violates no neverallow rule of the platform (compiled once more without -N, and every
  neverallow failure that names one of the updater's types is an error)
- the accesses the design depends on are allowed, and the ones it must never have are not
  (avc_kontrol on the compiled policy)

    python3 -I -m pytest guncelleyici/testler -q
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GUNCELLEYICI = os.path.dirname(HERE)
TOOLS = os.path.dirname(GUNCELLEYICI)
CIL = os.path.join(GUNCELLEYICI, "selinux", "70-gbos-guncelleyici.cil")
WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")
OUR_TYPES = ("gbos_guncelleyici", "gbos_ota_data_file", "gbos_ota_cache_file",
             "gbos_guncelleyici_socket", "gbos_guncelleyici_exec")


@pytest.fixture(scope="module")
def policy(tmp_path_factory):
    plat = os.path.join(WORK, "sys", "system", "etc", "selinux", "plat_sepolicy.cil")
    if not os.path.exists(plat):
        pytest.skip(f"{plat} yok (apply_patches.py çalışma ağacı)")
    sys.path.insert(0, TOOLS)
    from yamalar import selinux as sel
    secilc, checker = sel._tools()
    tmp = str(tmp_path_factory.mktemp("politika"))
    # system root with our CIL appended to the work tree's plat policy (what selinux.py would do
    # with the file in selinux/politika/plat/)
    system = os.path.join(tmp, "system")
    src = os.path.join(WORK, "sys", "system", "etc", "selinux")
    dst = os.path.join(system, "etc", "selinux")
    os.makedirs(dst)
    for name in os.listdir(src):
        if name != "plat_sepolicy.cil":
            os.symlink(os.path.join(src, name), os.path.join(dst, name))
    with open(plat) as f, open(CIL) as g, open(os.path.join(dst, "plat_sepolicy.cil"), "w") as out:
        text, ours = f.read(), g.read()
        if "(type gbos_guncelleyici)" in text:
            # apply_patches.py already merged selinux/politika/plat/70-gbos-guncelleyici.cil: the
            # tree must carry exactly this file's rules, and is then tested as it is
            if ours.strip() not in text:
                pytest.fail("çalışma ağacındaki güncelleyici kuralları guncelleyici/selinux ile aynı değil")
            out.write(text)
        else:
            out.write(text.rstrip("\n") + "\n\n" + ours)
    lin = sel._lineage_layout(WORK)
    inputs = sel.init_inputs(system, os.path.join(WORK, "sysext"), os.path.join(WORK, "prod"),
                             lin["vendor"], lin["odm"])
    return {"sel": sel, "secilc": secilc, "checker": checker, "inputs": inputs, "tmp": tmp}


def test_init_gibi_derlenir(policy):
    out = os.path.join(policy["tmp"], "gbos.pol")
    policy["sel"].compile_policy(policy["secilc"], policy["inputs"], out)
    assert os.path.getsize(out) > 1 << 20
    policy["pol"] = out


def test_neverallow_ihlali_yok(policy):
    inputs = [a for a in policy["inputs"] if a != "-N"]
    res = subprocess.run([policy["secilc"], *inputs, "-o", os.devnull, "-f", os.devnull],
                         capture_output=True, text=True)
    text = res.stderr + res.stdout
    # Report blocks start with "neverallow check failed at ..." and list the offending allows.
    blocks = re.split(r"(?=neverallow check failed)", text)
    ours = [b for b in blocks if b.startswith("neverallow") and
            any(re.search(rf"\b{t}\b", b) for t in OUR_TYPES)]
    assert not ours, "\n".join(ours)[:4000]


ALLOWED = [
    ("gbos_guncelleyici", "super_block_device", "blk_file", "read"),
    ("gbos_guncelleyici", "userdata_block_device", "blk_file", "read"),
    ("gbos_guncelleyici", "cache_recovery_file", "file", "create"),
    ("gbos_guncelleyici", "powerctl_prop", "property_service", "set"),
    ("gbos_guncelleyici", "uncrypt", "process", "transition"),
    ("gbos_guncelleyici", "vdc", "process", "transition"),
    ("uncrypt", "gbos_ota_data_file", "file", "ioctl"),
    ("uncrypt", "gbos_ota_cache_file", "file", "create"),
    ("uncrypt", "gbos_ota_cache_file", "file", "rename"),
    ("priv_app", "gbos_guncelleyici", "unix_stream_socket", "connectto"),
    ("shell", "gbos_guncelleyici", "unix_stream_socket", "connectto"),
    ("init", "gbos_guncelleyici", "process", "transition"),
]
REFUSED = [
    # it never writes a block device, misc, nor talks to vold itself
    ("gbos_guncelleyici", "super_block_device", "blk_file", "write"),
    ("gbos_guncelleyici", "userdata_block_device", "blk_file", "write"),
    ("gbos_guncelleyici", "misc_block_device", "blk_file", "write"),
    ("gbos_guncelleyici", "vold_service", "service_manager", "find"),
    # apps cannot reach the images
    ("priv_app", "gbos_ota_data_file", "file", "read"),
    ("untrusted_app", "gbos_guncelleyici", "unix_stream_socket", "connectto"),
]


def ask(policy, rows):
    pol = policy.get("pol") or policy["sel"].compile_policy(
        policy["secilc"], policy["inputs"], os.path.join(policy["tmp"], "gbos.pol"))
    role = lambda c: "r" if c in ("process", "unix_stream_socket", "fd") else "object_r"
    # apps carry MLS categories (levelFrom); the MLS constraints are part of the question
    level = lambda s: "s0:c512,c768" if s.endswith("_app") else "s0"
    stdin = "".join(f"u:r:{s}:{level(s)} u:{role(c)}:{t}:s0 {c} {p}\n" for s, t, c, p in rows)
    out = subprocess.run([policy["checker"], pol], input=stdin, capture_output=True, text=True,
                         check=True).stdout.splitlines()
    return dict(zip(rows, (line.split()[0] for line in out)))


def test_gereken_erisimler(policy):
    got = ask(policy, ALLOWED)
    assert all(v == "ALLOW" for v in got.values()), {k: v for k, v in got.items() if v != "ALLOW"}


def test_olmamasi_gereken_erisimler(policy):
    got = ask(policy, REFUSED)
    assert not any(v == "ALLOW" for v in got.values()), {k: v for k, v in got.items() if v == "ALLOW"}
