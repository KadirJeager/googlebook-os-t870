"""selinux/reddedilenler.txt, the denials yamalar/selinux.py dogrula replays: it covers every denial
of the device logs (so dropping the logs, as the published repository does, replays the same) and
carries nothing but the policy fields.

Real inputs: the tracked logs under selinux/kanit, acilis-loglari and cihaz-kabul/kanit."""
import os
import re
import sys

from conftest import ARACLAR, gerekli

sys.path.insert(0, os.path.join(ARACLAR, "selinux", "araclar"))
sys.path.insert(0, ARACLAR)
import avc  # noqa: E402
from yamalar import selinux  # noqa: E402

SATIR = re.compile(r"^avc: denied \{ [a-z0-9_]+ \} for(?: name=\S+)? scontext=\S+ tcontext=\S+ "
                   r"tclass=[a-z0-9_]+$")


def test_kayitlarin_her_reddi_dosyada():
    """The file gives exactly the denials of the logs plus its own: `topla` is up to date."""
    gerekli(selinux.REDDEDILENLER)
    logs = selinux.log_files()
    if not logs:
        import pytest
        pytest.skip("no device logs on this machine (published repository): nothing to compare")
    dosya = avc.parse_logs([selinux.REDDEDILENLER])
    kayitlar = avc.parse_logs(logs)
    eksik = {k: v - dosya.get(k, set()) for k, v in kayitlar.items() if v - dosya.get(k, set())}
    assert not eksik, f"{len(eksik)} denials missing; run python3 yamalar/selinux.py topla"


def test_yalniz_politika_alanlari():
    """Every line is the canonical form: no path, process name, pid or inode of the device."""
    gerekli(selinux.REDDEDILENLER)
    with open(selinux.REDDEDILENLER) as f:
        satirlar = [l.rstrip("\n") for l in f if not l.startswith("#")]
    assert satirlar
    kotu = [l for l in satirlar if not SATIR.match(l)]
    assert not kotu, kotu[:5]
    assert satirlar == sorted(set(satirlar))
