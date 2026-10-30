#!/usr/bin/env python3
"""Tests of yamalar/mica_servisleri.py on copies of the real image tree files it touches.

    python3 -I mica-servisleri/tests/test_install.py [WORK]

WORK is apply_patches.py's work tree (default /var/tmp/mica_work, or GBOS_WORK); only its
system/etc/{init,vintf,selinux/plat_file_contexts}, system_ext/etc and product/etc init and
VINTF directories are copied, into a temporary directory next to WORK (not /tmp).
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, TOOLS_DIR)
from yamalar import mica_servisleri as ms  # noqa: E402

WORK = os.environ.get("GBOS_WORK", "/var/tmp/mica_work")


def tree_digest(root):
    h = hashlib.sha256()
    for dp, dn, fn in sorted(os.walk(root)):
        dn.sort()
        for name in sorted(fn):
            p = os.path.join(dp, name)
            st = os.lstat(p)
            h.update(os.path.relpath(p, root).encode() + b"\0" + oct(st.st_mode).encode())
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mica-servisleri-test-", dir=os.path.dirname(WORK))
        self.sys = os.path.join(self.tmp, "sys")
        self.sysext = os.path.join(self.tmp, "sysext")
        self.prod = os.path.join(self.tmp, "prod")
        for rel in ("system/etc/init", "system/etc/vintf"):
            shutil.copytree(os.path.join(WORK, "sys", rel), os.path.join(self.sys, rel))
        os.makedirs(os.path.join(self.sys, "system/etc/selinux"))
        shutil.copy2(os.path.join(WORK, "sys", ms.PLAT_FILE_CONTEXTS),
                     os.path.join(self.sys, ms.PLAT_FILE_CONTEXTS))
        for tree, src in ((self.sysext, "sysext"), (self.prod, "prod")):
            for rel in ("etc/init", "etc/vintf"):
                s = os.path.join(WORK, src, rel)
                if os.path.isdir(s):
                    shutil.copytree(s, os.path.join(tree, rel))
            os.makedirs(tree, exist_ok=True)
        # The real tree may already be patched; start from the state before install().
        uds = os.path.join(self.sys, "system/etc/init/udsattestation.rc")
        if not os.path.exists(uds):
            with open(uds, "w") as f:
                f.write("service android.system.desktop.security.udsattestation "
                        "/system/bin/udsattestation\n    class main\n    oneshot\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_install_result(self):
        ms.install(self.sys, self.sysext, self.prod)
        rc = os.path.join(self.sys, "system/etc/init/gbos-vpd-executor.rc")
        with open(rc) as f:
            body = f.read()
        self.assertIn("interface aidl vendor.google.desktop.vpd_executor.IVpdExecutor/default", body)
        self.assertIn("disabled", body)
        self.assertIn("mkdir /data/misc/gbos_vpd 0700 system system", body)
        b = os.path.join(self.sys, "system/bin/gbos-vpd-executor")
        self.assertEqual(os.stat(b).st_mode & 0o7777, 0o755)
        with open(b, "rb") as f:
            self.assertEqual(f.read(4), b"\x7fELF")
        self.assertFalse(os.path.exists(os.path.join(self.sys, "system/etc/init/udsattestation.rc")))
        with open(os.path.join(self.sys, ms.PLAT_FILE_CONTEXTS)) as f:
            fc = f.read()
        self.assertEqual(fc.count("gbos_vpd_executor_exec"), 1)

    def test_idempotent(self):
        ms.install(self.sys, self.sysext, self.prod)
        first = tree_digest(self.tmp)
        ms.install(self.sys, self.sysext, self.prod)
        self.assertEqual(first, tree_digest(self.tmp))

    def test_check_detects_tampering(self):
        ms.install(self.sys, self.sysext, self.prod)
        b = os.path.join(self.sys, "system/bin/gbos-vpd-executor")
        with open(b, "ab") as f:
            f.write(b"x")
        with self.assertRaisesRegex(RuntimeError, "differs"):
            ms.check(self.sys, self.sysext, self.prod)

    def test_check_detects_second_declaration(self):
        ms.install(self.sys, self.sysext, self.prod)
        extra = os.path.join(self.sysext, "etc/init/other.rc")
        os.makedirs(os.path.dirname(extra), exist_ok=True)
        with open(extra, "w") as f:
            f.write("service x /x\n    interface aidl vendor.google.desktop.vpd_executor.IVpdExecutor/default\n")
        with self.assertRaisesRegex(RuntimeError, "declared by"):
            ms.check(self.sys, self.sysext, self.prod)

    def test_refuses_without_policy_types(self):
        saved = ms.POLICY_DIR
        ms.POLICY_DIR = os.path.join(self.tmp, "politika-yok")
        os.makedirs(ms.POLICY_DIR)
        try:
            with self.assertRaisesRegex(RuntimeError, "not declared"):
                ms.install(self.sys, self.sysext, self.prod)
        finally:
            ms.POLICY_DIR = saved
        self.assertFalse(os.path.exists(os.path.join(self.sys, "system/bin/gbos-vpd-executor")))

    def test_missing_tree(self):
        with self.assertRaises(FileNotFoundError):
            ms.install(os.path.join(self.tmp, "yok"), self.sysext, self.prod)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        WORK = sys.argv.pop(1)
    unittest.main(verbosity=2)
