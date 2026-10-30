#!/usr/bin/env python3
"""Turns AVC denials (logcat / dmesg text on stdin) that involve the Linux environment's types
into CIL allow rules, one per (source, target, class), permissions merged and sorted.

    adb logcat -b all -d | grep avc: | python3 selinux/avc2cil.py [--kabul] > avc.cil

--kabul leaves out the refusals that T870-araclar/selinux/kabul.txt keeps refused on purpose
(read with yamalar/selinux.py's own parser, so the rules mean the same as in its check).

Only denials whose source or target is one of ours (gbos_linux*) are kept: everything else is
the image's business. The output is a review aid for selinux/politika/plat/60-gbos-linux.cil, not policy by itself.
"""
import os
import re
import sys
from collections import defaultdict

AVC = re.compile(r"avc:\s+denied\s+\{ ([^}]+) \}.*?scontext=u:r:([\w]+):s0(?::[\w,]+)?\s+"
                 r"tcontext=u:(?:object_r|r):([\w]+):s0(?::[\w,]+)?\s+tclass=(\w+)")
OURS = re.compile(r"^gbos_linux")


def accepted():
    if "--kabul" not in sys.argv[1:]:
        return lambda key: False
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "yamalar"))
    import selinux
    kabul = selinux.Kabul()
    return lambda key: key in kabul


def main():
    is_accepted = accepted()
    rules = defaultdict(set)
    for line in sys.stdin:
        m = AVC.search(line)
        if not m:
            continue
        perms, src, tgt, cls = m.group(1).split(), m.group(2), m.group(3), m.group(4)
        if not (OURS.match(src) or OURS.match(tgt)):
            continue
        perms = [p for p in perms if not is_accepted((src, tgt, cls, p))]
        if not perms:
            continue
        if src == tgt:
            tgt = "self"
        rules[(src, tgt, cls)].update(perms)
    for (src, tgt, cls) in sorted(rules):
        print(f"(allow {src} {tgt} ({cls} ({' '.join(sorted(rules[(src, tgt, cls)]))})))")


if __name__ == "__main__":
    main()
