"""Googlebook Terminal and its Linux environment, installed into the image trees.

    from yamalar import terminal
    terminal.install(SYS_DIR, SYSEXT_DIR, PROD_DIR)

Everything lands in the system partition (system_ext and product are full and need nothing):

    system/bin/gbos-linuxd, gbos-linux             root service and its client
    system/priv-app/Terminal/Terminal.apk          the app (com.gbos.terminal)
    system/etc/init/gbos-linux.rc                  starts the service, creates its socket
    system/etc/gbos-linux/                         linux.conf, packages, setup/adduser/droidadd/
                                                   locale scripts, bin/googlebook-open (xdg-open),
                                                   sbin/ (root tools copied into the environment:
                                                   googlebook-imaj-araclari, systemctl),
                                                   debs/ (bindfs for the shared storage view)
    system/etc/permissions/, default-permissions/  privapp allowlist, notification grant
    system/etc/selinux/plat_file_contexts          + terminal/selinux/file_contexts

The policy itself (types, domains, rules) is not added here:
yamalar/selinux.py rewrites plat_sepolicy.cil from the pristine copy and appends
selinux/politika/plat/*.cil, where it lives as 60-gbos-linux.cil (selinux-port's charge).

The binaries and the APK come from terminal/out/<abi>/ (terminal/build.sh, reproducible); the
Debian root filesystem is not in the image, the device installs it on first use (BENIOKU.md).
Running install() again gives the same tree (idempotent).
"""
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hazir_ikili  # noqa: E402

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERMINAL = os.path.join(TOOLS_DIR, "terminal")

# Built files, relative to terminal/out/<abi>/system and to the image's system/.
BUILT = [
    "bin/gbos-linuxd",
    "bin/gbos-linux",
    "etc/gbos-linux/bin/googlebook-open",
    "priv-app/Terminal/Terminal.apk",
]
# Source files, relative to terminal/system and to the image's system/.
SOURCES = [
    "etc/init/gbos-linux.rc",
    "etc/gbos-linux/linux.conf",
    "etc/gbos-linux/packages",
    "etc/gbos-linux/setup.sh",
    "etc/gbos-linux/adduser.sh",
    "etc/gbos-linux/droidadd.sh",
    "etc/gbos-linux/locale.sh",
    "etc/gbos-linux/sbin/googlebook-imaj-araclari",
    "etc/permissions/privapp-permissions-gbos-terminal.xml",
    "etc/default-permissions/default-permissions-gbos-terminal.xml",
]
# Vendored third-party files (terminal/third_party/fetch.sh pins them), image path: source.
THIRD_PARTY = {
    # The environment's systemctl: systemd cannot be PID 1 here (no PID namespace on 4.19), and
    # packages pull in systemd's own systemctl, which then fails ("not booted with systemd")
    # where scripts expect it to start services (Antigravity's SSH setup). gbos-linuxd copies
    # sbin/ to /usr/local/sbin, ahead of /usr/bin for root and sudo.
    "etc/gbos-linux/sbin/systemctl": "third_party/systemctl/systemctl3.py",
}
# Debian packages the environment installs from the image (setup.sh, droidadd.sh), per ABI.
DEBIAN_ARCH = {"arm64": "arm64", "x86_64": "amd64"}
DEBS = {
    # The shared storage view (daemon Container::ensure_storage_view) needs bindfs with libfuse
    # 3; Debian 13 has 1.14.7 (libfuse 2). Fetched from Debian's archive by fetch.sh.
    "etc/gbos-linux/debs/bindfs.deb": "third_party/debian/bindfs_1.18.4-0.1_{arch}.deb",
}
FILE_CONTEXTS = "etc/selinux/plat_file_contexts"
FC_BLOCK = re.compile(r"\n?# --- GBOS_TERMINAL BEGIN.*?# --- GBOS_TERMINAL END ---\n", re.S)


def _copy(src, dst, mode):
    if not os.path.isfile(src):
        raise FileNotFoundError(f"{src} is missing (run terminal/build.sh)")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, mode)


def _replace_block(path, pattern, block):
    with open(path) as f:
        text = f.read()
    # The pattern takes the newline before the block with it, so removing it leaves no blank
    # line behind: a re-run then gives the same file as a run on the pristine tree.
    text = pattern.sub("", text).rstrip("\n") + "\n"
    with open(path, "w") as f:
        f.write(text + "\n" + block)


# Downloaded by third_party/fetch.sh, not kept in the published repository (binaries of other
# projects, pinned there by version and SHA-256): fetched when one is missing.
FETCHED = ["third_party/androidx/annotation-jvm-1.11.0.jar",
           *(src.format(arch=a) for src in DEBS.values() for a in DEBIAN_ARCH.values())]


def fetch():
    """Runs third_party/fetch.sh when a file it downloads is missing. On the tablet (GBOS_KIT) the
    kit carries what install() copies and nothing is downloaded."""
    if os.environ.get("GBOS_KIT") or all(os.path.isfile(os.path.join(TERMINAL, f)) for f in FETCHED):
        return
    subprocess.run([os.path.join(TERMINAL, "third_party", "fetch.sh")], check=True)


def build(abi="arm64"):
    """Builds terminal/out/<abi>/ unless it already holds the build of the current sources
    (yamalar/hazir_ikili.py; the tablet's kit carries out/ only). terminal/build.sh builds arm64,
    the image's ABI; the x86_64 test build is made by hand (--abi x86_64)."""
    fetch()
    out = os.path.join(TERMINAL, "out", abi)
    if abi != "arm64":
        return
    hazir_ikili.derle(TERMINAL, os.path.join(out, "STAMP"),
                      [os.path.join(out, "system", rel) for rel in BUILT]
                      + [os.path.join(out, "kopru", "VmTerminalApp.apk")],
                      "terminal", kitte_kaynak=False)


def install(sys_dir, sysext_dir=None, prod_dir=None, abi="arm64"):
    """Installs the Terminal into the system tree at sys_dir (system-as-root: sys_dir/system).
    sysext_dir and prod_dir are accepted for the common interface; nothing goes there."""
    del sysext_dir, prod_dir
    build(abi)
    system = os.path.join(sys_dir, "system")
    built = os.path.join(TERMINAL, "out", abi, "system")
    for rel in BUILT:
        mode = 0o644 if rel.endswith(".apk") else 0o755
        _copy(os.path.join(built, rel), os.path.join(system, rel), mode)
    for rel in SOURCES:
        _copy(os.path.join(TERMINAL, "system", rel), os.path.join(system, rel), 0o644)
    for rel, src in THIRD_PARTY.items():
        _copy(os.path.join(TERMINAL, src), os.path.join(system, rel), 0o644)
    for rel, src in DEBS.items():
        _copy(os.path.join(TERMINAL, src.format(arch=DEBIAN_ARCH[abi])), os.path.join(system, rel), 0o644)

    with open(os.path.join(TERMINAL, "selinux", "file_contexts")) as f:
        _replace_block(os.path.join(system, FILE_CONTEXTS), FC_BLOCK, f.read())
    print("[+] Installed the Terminal and the Linux environment service (terminal/BENIOKU.md)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: terminal.py SYS_DIR [ABI]   (normally called from apply_patches.py)")
    install(sys.argv[1], abi=sys.argv[2] if len(sys.argv) == 3 else "arm64")
