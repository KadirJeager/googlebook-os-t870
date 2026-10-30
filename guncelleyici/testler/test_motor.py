#!/usr/bin/env python3
"""
Tests of motor.py's own machinery with real processes and files (no network):

  python3 -I test_motor.py

- state file: atomic replace, readable by others (0644), fields merged
- download progress parsing against mica-indir.py's real log line
- a second engine is refused while one holds the lock (exit 75)
- SIGTERM during a step stops the step's whole process group and records "duraklatildi"
- a failed step reports the tail of its output
- a sparse image is rewritten with every block allocated and the same bytes (uncrypt maps blocks)
- a build's work directory is resumed only while its kit, header and images are still there
"""
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MOTOR = os.path.join(os.path.dirname(HERE), "motor", "motor.py")


def load_motor(work):
    os.environ["GBOS_GUNCELLEYICI"] = work
    import importlib.util
    spec = importlib.util.spec_from_file_location("motor_t", MOTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MotorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = self.tmp.name
        self.m = load_motor(self.work)
        os.makedirs(self.m.LOGS, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_state_file(self):
        self.m.update_state(durum="hazirlaniyor", ilerleme=5)
        self.m.update_state(ilerleme=7)
        st = os.stat(self.m.STATE)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o644)
        with open(self.m.STATE) as f:
            data = json.load(f)
        self.assertEqual((data["durum"], data["ilerleme"], data["surum"]), ("hazirlaniyor", 7, 1))
        self.assertFalse(os.path.exists(self.m.STATE + ".tmp"))

    def _work_dir(self, identity, steps, images):
        work = os.path.join(self.m.BUILDS, "16560795_CL3B.260622.276.R2")
        os.makedirs(work)
        with open(os.path.join(work, self.m.IDENTITY), "w") as f:
            f.write(identity)
        with open(os.path.join(work, "ADIMLAR"), "w") as f:
            f.write("".join(f"{s}\n" for s in steps))
        for p in images:
            with open(os.path.join(work, f"{p}.img"), "wb") as f:
                f.write(b"\0" * 4096)
        return work

    def test_stale_work_after_hand_over(self):
        """The tablet's state of 29-30 Oct 2026: .276 handed over on 28 Oct, the set then replaced
        by a manual install; the work directory still said "derle" with no images in it."""
        identity = "kit abc\nbaslik googlebookos-2\n"
        work = self._work_dir(identity, ["agac", "yamala 1793153751", "derle"], [])
        self.m.drop_stale_work(work, identity)
        self.assertFalse(os.path.exists(work))

    def test_resumable_work_kept(self):
        identity = "kit abc\nbaslik googlebookos-2\n"
        work = self._work_dir(identity, ["agac", "yamala 1793153751", "derle"], self.m.PARTITIONS)
        self.m.drop_stale_work(work, identity)
        self.assertTrue(os.path.isdir(work))
        self.m.remove_tree(work)
        work = self._work_dir(identity, ["agac"], [])  # interrupted in yamala: no images yet
        self.m.drop_stale_work(work, identity)
        self.assertTrue(os.path.isdir(work))

    def test_other_kit_work_dropped(self):
        work = self._work_dir("kit eski\nbaslik googlebookos-2\n", ["agac", "derle"], self.m.PARTITIONS)
        self.m.drop_stale_work(work, "kit yeni\nbaslik googlebookos-2\n")
        self.assertFalse(os.path.exists(work))

    def test_download_progress(self):
        line = "  2048 MiB indirildi, açılan 4096 / 8192 MiB (35.2 MiB/s)\n"
        self.assertEqual(self.m.download_progress(line), 50)
        self.assertIsNone(self.m.download_progress("zip: veri 1, sıkıştırılmış 2, açık 3\n"))

    def test_build_incremental(self):
        self.assertEqual(self.m.build_incremental("16471258_CL3B.260622.273.R1"), 16471258)
        with self.assertRaises(ValueError):
            self.m.build_incremental("CL3B")

    def _engine(self, script_body):
        """Starts motor.py with a kit whose mica-indir.py is the given script."""
        kit = os.path.join(self.work, "kit")
        os.makedirs(kit, exist_ok=True)
        with open(os.path.join(kit, "mica-indir.py"), "w") as f:
            f.write(textwrap.dedent(script_body))
        env = dict(os.environ, GBOS_GUNCELLEYICI=self.work, GBOS_MEVCUT_ARTIMLI="1",
                   PATH=os.environ["PATH"])
        return subprocess.Popen([sys.executable, "-I", "-c", textwrap.dedent(f"""
            import importlib.util, os, sys, signal
            spec = importlib.util.spec_from_file_location("m", {MOTOR!r})
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            import fcntl
            lock = open(m.LOCK, "w"); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            signal.signal(signal.SIGTERM, m.on_signal)
            m.update_state(durum="hazirlaniyor", asama="indir")
            m.run_step("indir", [sys.executable, os.path.join(m.KIT, "mica-indir.py")], m.tool_env())
            """)], env=env)

    def test_lock_and_sigterm_stop_process_group(self):
        marker = os.path.join(self.work, "torun.pid")
        proc = self._engine(f"""
            import os, subprocess, time
            child = subprocess.Popen(["sleep", "60"])
            open({marker!r}, "w").write(str(child.pid))
            print("  10 MiB indirildi, açılan 10 / 100 MiB (1.0 MiB/s)", flush=True)
            time.sleep(60)
        """)
        for _ in range(100):
            if os.path.exists(marker) and os.path.getsize(marker):
                break
            time.sleep(0.1)
        with open(marker) as f:
            grandchild = int(f.read())

        second = subprocess.run([sys.executable, "-I", MOTOR, "hazirla"],
                                env=dict(os.environ, GBOS_GUNCELLEYICI=self.work,
                                         GBOS_MEVCUT_ARTIMLI="1"), capture_output=True, text=True)
        self.assertEqual(second.returncode, 75, second.stdout + second.stderr)

        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=10), 128 + signal.SIGTERM)
        for _ in range(50):
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(grandchild, signal.SIGKILL)
            self.fail("adımın alt süreci durdurulmadı")
        with open(self.m.STATE) as f:
            self.assertEqual(json.load(f)["durum"], "duraklatildi")

    def test_failed_step_keeps_tail_of_output(self):
        kit = os.path.join(self.work, "kit")
        os.makedirs(kit)
        script = os.path.join(kit, "boz.py")
        with open(script, "w") as f:
            f.write("print('satir 1'); raise AssertionError('desen bulunamadi')\n")
        with self.assertRaises(self.m.Failure) as ctx:
            self.m.run_step("yamala", [sys.executable, script], self.m.tool_env())
        self.assertIn("AssertionError", ctx.exception.detail)
        self.assertIn("başarısız", ctx.exception.message)

    def test_dense_fills_holes(self):
        path = os.path.join(self.work, "seyrek.img")
        with open(path, "wb") as f:
            f.write(b"bas")
            f.seek(64 * 1024 * 1024)
            f.write(b"son")
        before = os.stat(path)
        if before.st_blocks * 512 >= before.st_size:
            self.skipTest("this filesystem does not keep holes")
        want = self.m.sha256(path)
        self.assertFalse(self.m.allocated(path))
        self.m.dense(path)
        self.assertTrue(self.m.allocated(path))
        self.assertEqual(self.m.sha256(path), want)
        self.assertEqual(os.path.getsize(path), before.st_size)
        self.m.dense(path)  # already dense: nothing to do
        self.assertEqual(self.m.sha256(path), want)


if __name__ == "__main__":
    unittest.main(verbosity=2)
