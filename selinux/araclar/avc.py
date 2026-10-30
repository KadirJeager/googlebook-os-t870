#!/usr/bin/env python3
"""Checks SELinux AVC denials from a boot log against a compiled policy.

    avc.py --policy sepolicy.bin [--service-contexts FILE]... LOG [LOG ...]

Every distinct (scontext, tcontext, tclass, permission) seen in the logs (dmesg "avc: denied"
lines and logcat auditd lines alike) is asked of the policy through avc_kontrol. For
service_manager denials the target is relabelled from the given service_contexts files first,
the way servicemanager would label the service with them installed (later files win, as with
plat < system_ext < product < vendor < odm). The report lists what the policy still refuses,
grouped by source type, target type and class; exit status 1 means something is refused.
"""
import argparse
import collections
import os
import re
import subprocess
import sys

AVC_RE = re.compile(
    r"avc:\s+denied\s+\{\s*([^}]*?)\s*\}(.*?)scontext=(\S+)\s+tcontext=(\S+)\s+tclass=(\S+)")
NAME_RE = re.compile(r"\bname=(\S+)")
# pstore/last_kmsg copies carry bit flips; such lines are dropped rather than misread.
CONTEXT_RE = re.compile(r"^u:(?:r|object_r):[a-z0-9_.-]+:s0(?::c\d+(?:,c\d+)*)?$")
WORD_RE = re.compile(r"^[a-z0-9_]+$")


def load_service_contexts(paths):
    exact, prefix = {}, []
    for path in paths:
        with open(path, errors="replace") as f:
            for line in f:
                fields = line.split("#", 1)[0].split()
                if len(fields) < 2:
                    continue
                name, ctx = fields[0], fields[1]
                if name.endswith("*"):
                    prefix.append((name[:-1], ctx))
                else:
                    exact[name] = ctx
    return exact, prefix


def service_label(name, exact, prefix):
    if name in exact:
        return exact[name]
    best = None
    for p, ctx in prefix:  # the "*" catch-all maps to default_android_service
        if name.startswith(p) and (best is None or len(p) > len(best[0])):
            best = (p, ctx)
    return best[1] if best else None


def parse_logs(paths):
    """Returns {(scon, tcon, tclass, perm): set of service names (service_manager only)}."""
    seen = collections.defaultdict(set)
    for path in paths:
        with open(path, errors="replace") as f:
            for line in f:
                m = AVC_RE.search(line)
                if not m:
                    continue
                perms, middle, scon, tcon, tclass = m.groups()
                if not (CONTEXT_RE.match(scon) and CONTEXT_RE.match(tcon) and WORD_RE.match(tclass)
                        and all(WORD_RE.match(p) for p in perms.split())):
                    continue
                name = None
                if tclass == "service_manager":
                    n = NAME_RE.search(middle)
                    name = n.group(1) if n else None
                for perm in perms.split():
                    seen[(scon, tcon, tclass, perm)].add(name)
    return seen


def canonical_lines(paths):
    """The denials of the logs, one line per (scontext, tcontext, tclass, permission, service
    name), in the "avc: denied" form parse_logs reads: the same replay without the logs' paths,
    process names, inodes and everything else they carry."""
    out = set()
    for (scon, tcon, tclass, perm), names in parse_logs(paths).items():
        for name in names:
            mid = f" for name={name}" if name else " for"
            out.add(f"avc: denied {{ {perm} }}{mid} scontext={scon} tcontext={tcon} tclass={tclass}\n")
    return sorted(out)


def load_policy_rules(checker, policy):
    """(attributes {name: member types}, types, allow rules [(src, tgt, class, perms)]) of a
    compiled policy, read from its CIL form (avc_kontrol --cil)."""
    text = subprocess.run([checker, "--cil", policy], capture_output=True, text=True, check=True).stdout
    attrs = {m.group(1): set(m.group(2).split())
             for m in re.finditer(r"^\(typeattributeset (\S+) \(([^()]*)\)\)$", text, re.M)}
    types = set(re.findall(r"^\(type (\S+)\)$", text, re.M))
    rules = [(s, t, c, set(p.split())) for s, t, c, p in
             re.findall(r"^\(allow (\S+) (\S+) \((\S+) \(([^()]*)\)\)\)$", text, re.M)]
    return attrs, types, rules


def expand_rules(attrs, rules, sources):
    """{(source type, target type, class): perms} for the allow rules whose source covers one
    of the given types."""
    out = collections.defaultdict(set)
    for s, t, c, perms in rules:
        for a in attrs.get(s, {s}) & sources:
            for b in ({a} if t == "self" else attrs.get(t, {t})):
                out[(a, b, c)] |= perms
    return out


def vendor_domain_losses(checker, baseline, ours):
    """What the vendor (non-core) domains of the baseline policy may do there but not in ours,
    for targets that exist in ours: {(source, target, class): missing perms}."""
    b_attrs, _, b_rules = load_policy_rules(checker, baseline)
    o_attrs, o_types, o_rules = load_policy_rules(checker, ours)
    vendor = b_attrs.get("domain", set()) - b_attrs.get("coredomain", set())
    before = expand_rules(b_attrs, b_rules, vendor)
    after = expand_rules(o_attrs, o_rules, vendor)
    lost = {}
    for key, perms in before.items():
        if key[1] in o_types:
            missing = perms - after.get(key, set())
            if missing:
                lost[key] = missing
    return lost


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", required=True, help="compiled binary policy (secilc -o)")
    ap.add_argument("--service-contexts", action="append", default=[],
                    help="a service_contexts file; repeat it, lowest priority first")
    ap.add_argument("--checker", default=os.path.join(os.environ.get("GBOS_SELINUX_CACHE", os.path.expanduser("~/.cache/gbos-selinux")), "bin", "avc_kontrol"))
    ap.add_argument("--all", action="store_true", help="also list the allowed tuples")
    ap.add_argument("logs", nargs="+")
    args = ap.parse_args()

    exact, prefix = load_service_contexts(args.service_contexts)
    queries = []  # (scon, tcon, tclass, perm, service name)
    for (scon, tcon, tclass, perm), names in parse_logs(args.logs).items():
        for name in sorted(names, key=lambda n: n or ""):
            t = tcon
            if name and args.service_contexts:
                t = service_label(name, exact, prefix) or tcon
            queries.append((scon, t, tclass, perm, name))
    queries = sorted(set(queries), key=lambda q: (q[0], q[1], q[2], q[3], q[4] or ""))

    stdin = "".join(f"{s} {t} {c} {p}\n" for s, t, c, p, _ in queries)
    out = subprocess.run([args.checker, args.policy], input=stdin, capture_output=True, text=True, check=True)
    verdicts = [line.split()[0] for line in out.stdout.splitlines()]
    assert len(verdicts) == len(queries), out.stderr

    groups = collections.OrderedDict()
    counts = collections.Counter()
    for (s, t, c, p, name), v in zip(queries, verdicts):
        counts[v] += 1
        if v == "ALLOW" and not args.all:
            continue
        key = (v, s.split(":")[2], t.split(":")[2], c)
        g = groups.setdefault(key, [set(), set()])
        g[0].add(p)
        if name:
            g[1].add(name)
    for (v, st, tt, c), (perms, names) in groups.items():
        extra = f"  [{', '.join(sorted(names))}]" if names else ""
        print(f"{v:16} {st} {tt}:{c} {{ {' '.join(sorted(perms))} }}{extra}")
    print(f"# {len(queries)} tuples: " + ", ".join(f"{k}={counts[k]}" for k in sorted(counts)), file=sys.stderr)
    return 1 if any(k != "ALLOW" for k in counts) else 0


if __name__ == "__main__":
    sys.exit(main())
