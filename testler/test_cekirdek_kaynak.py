"""h) Pins the kernel behaviour acilis-loglari/recovery-20261022-fastbootd/KOK-NEDEN.md rests on,
in the LineageOS kernel the tablet runs (LineageOS/android_kernel_samsung_sm8250, lineage-24.0):

- sec_debug.c: "userrequested…" maps to PON_RESTART_REASON_NORMALBOOT; a reason no entry matches
  (a bare `adb reboot` says "shell") returns without writing the PON, so the bootloader reuses the
  previous reason (recovery).
- msm-poweroff.c: "recovery…" writes PON_RESTART_REASON_RECOVERY.
tablete-yaz.py --sisteme-ac relies on both through SYSTEM_BOOT_REASON = "userrequested,recovery".
The sources are fetched from GitHub on each run; without network the tests are skipped."""
import re
import socket
import urllib.error
import urllib.request

import pytest

from conftest import arac_modulu

REPO = "LineageOS/android_kernel_samsung_sm8250"
DAL = "lineage-24.0"
RAW = f"https://raw.githubusercontent.com/{REPO}/{DAL}/"


@pytest.fixture(scope="module")
def kaynak():
    cache = {}

    def get(path):
        if path not in cache:
            try:
                with urllib.request.urlopen(RAW + path, timeout=30) as r:
                    cache[path] = r.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                if e.code in (403, 429) or e.code >= 500:
                    pytest.skip(f"atlandı: GitHub {e.code} ({RAW + path})")
                pytest.fail(f"{REPO}@{DAL}: {path} alınamadı (HTTP {e.code}); dal ya da dosya "
                            "taşınmış olabilir, KOK-NEDEN.md kaynağı yeniden doğrulanmalı")
            except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
                pytest.skip(f"atlandı: ağ yok ({e})")
        return cache[path]
    return get


@pytest.fixture(scope="module")
def ty():
    return arac_modulu("tablete-yaz.py", "tablete_yaz")


def _govde(src, imza):
    """Body of the C function whose definition starts with `imza` (brace matching)."""
    i = src.index(imza)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        depth += {"{": 1, "}": -1}.get(src[k], 0)
        if depth == 0:
            return src[j:k + 1]
    raise AssertionError(f"{imza}: unbalanced braces")


def _magic(src):
    body = _govde(src, "void sec_debug_update_restart_reason(")
    entries = re.findall(r'\{\s*"([^"]+)",\s*(PON_RESTART_REASON_\w+),\s*(RESTART_REASON_\w+),',
                         body)
    assert len(entries) > 10, "magic[] table not found"
    return body, entries


def _eslesen(entries, cmd):
    """First entry the loop takes: strncmp(cmd, magic[i].cmd, strlen(magic[i].cmd)) == 0."""
    return next((e for e in entries if cmd.startswith(e[0])), None)


def test_userrequested_normalboot(kaynak, ty):
    """The first magic[] entry matching SYSTEM_BOOT_REASON is "userrequested" with
    PON_RESTART_REASON_NORMALBOOT, and the loop's match path writes the PON (__done)."""
    body, entries = _magic(kaynak("drivers/samsung/debug/sec_debug.c"))
    e = _eslesen(entries, ty.SYSTEM_BOOT_REASON)
    assert e and e[0] == "userrequested" and e[1] == "PON_RESTART_REASON_NORMALBOOT", e
    assert re.search(r"pon_rr = magic\[i\]\.pon_rr;.*?goto __done;", body, re.S)
    assert re.search(r"__done:\s*if \(pon_rr != PON_RESTART_REASON_NOT_HANDLE\)", body)


def test_eslesmeyen_neden_pon_yazmaz(kaynak):
    """"shell" (bare adb reboot) matches no entry, and the no-match path sets only the Samsung
    restart reason and returns before __done, leaving the PON unchanged."""
    body, entries = _magic(kaynak("drivers/samsung/debug/sec_debug.c"))
    assert _eslesen(entries, "shell") is None
    loop_end = re.search(r"goto __done;\s*\}\s*__sec_debug_set_restart_reason\(RESTART_REASON_REBOOT\);"
                         r"\s*return;\s*__done:", body)
    assert loop_end, "no-match path changed"


def test_recovery_dali_pon_recovery(kaynak, ty):
    """msm_restart_prepare: "recovery" (after "recovery-update") writes
    PON_RESTART_REASON_RECOVERY; SYSTEM_BOOT_REASON hits none of its branches, and
    sec_debug_update_restart_reason runs after them and decides."""
    body = _govde(kaynak("drivers/power/reset/msm-poweroff.c"), "static void msm_restart_prepare(")
    assert re.search(r'strncmp\(cmd, "recovery-update", 15\).*?strncmp\(cmd, "recovery", 8\)\)\s*\{'
                     r"\s*qpnp_pon_set_restart_reason\(\s*PON_RESTART_REASON_RECOVERY\);", body, re.S)
    branches = re.findall(r'(strn?cmp)\(cmd, "([^"]+)"(?:, (\d+))?\)', body)
    assert branches
    for fn, s, n in branches:
        hit = ty.SYSTEM_BOOT_REASON.startswith(s[:int(n)]) if fn == "strncmp" else ty.SYSTEM_BOOT_REASON == s
        assert not hit, f"{ty.SYSTEM_BOOT_REASON} now matches the {s!r} branch"
    last_branch = max(body.rfind(f'"{s}"') for _, s, _ in branches)
    assert body.find("sec_debug_update_restart_reason(cmd", last_branch) > last_branch
