#!/usr/bin/env python3
"""Fetches the pinned upstream sources pil-sagligi/build.sh compiles (../kaynaklar.lock).

Each lock line names a project, its git URL, a commit and the paths taken from it. The paths are
checked out sparsely (blobless, depth 1) at exactly that commit and copied into
    $GBOS_PIL_CACHE/src/<name>-<commit>/        (default ~/.cache/gbos-pil-sagligi)
The content hash of every copy (sha256 over "sha256  path" lines of every file, sorted by path,
symlinks as "link -> target") must equal the lock's value, otherwise the copy is discarded and
the build stops. A copy that is already there and still matches is reused without network.

    kaynak-getir.py                 fetch/verify all, print "name<TAB>directory" per project
    kaynak-getir.py --kilitle       print the lock file with freshly computed hashes (to pin a
                                    new commit: edit the commit, run this, review, replace)
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(os.path.dirname(HERE), "kaynaklar.lock")
CACHE = os.path.join(os.environ.get("GBOS_PIL_CACHE", os.path.expanduser("~/.cache/gbos-pil-sagligi")), "src")


def read_lock():
    entries = []
    with open(LOCK) as f:
        for n, line in enumerate(f, 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            fields = line.split()
            if len(fields) != 5:
                raise SystemExit(f"{LOCK}:{n}: expected 'name url commit paths sha256'")
            name, url, commit, paths, digest = fields
            entries.append((name, url, commit, paths.split(","), digest))
    return entries


def tree_hash(root):
    lines = []
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for name in fn + [d for d in dn if os.path.islink(os.path.join(dp, d))]:
            p = os.path.join(dp, name)
            rel = os.path.relpath(p, root)
            if os.path.islink(p):
                lines.append((rel, f"link {rel} -> {os.readlink(p)}"))
            else:
                with open(p, "rb") as f:
                    lines.append((rel, f"{hashlib.sha256(f.read()).hexdigest()}  {rel}"))
    lines.sort()
    return hashlib.sha256("".join(l + "\n" for _, l in lines).encode()).hexdigest()


def git(*args, cwd):
    subprocess.run(["git", "-c", "advice.detachedHead=false", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def fetch(name, url, commit, paths, dest):
    os.makedirs(CACHE, exist_ok=True)
    work = tempfile.mkdtemp(prefix=f".{name}-", dir=CACHE)
    try:
        repo = os.path.join(work, "repo")
        os.mkdir(repo)
        git("init", "-q", cwd=repo)
        git("remote", "add", "origin", url, cwd=repo)
        git("sparse-checkout", "set", "--no-cone", *[f"/{p.strip('/')}/" if p != "." else "/*" for p in paths], cwd=repo)
        git("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit, cwd=repo)
        git("checkout", "-q", commit, cwd=repo)
        out = os.path.join(work, "out")
        for p in paths:
            src = os.path.join(repo, p)
            if not os.path.exists(src):
                raise SystemExit(f"{name}: {p} not found at {commit}")
            if p == ".":
                shutil.copytree(repo, out, symlinks=True, ignore=shutil.ignore_patterns(".git"))
            else:
                shutil.copytree(src, os.path.join(out, p), symlinks=True)
        os.replace(out, dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kilitle", action="store_true")
    args = ap.parse_args()
    result = []
    for name, url, commit, paths, digest in read_lock():
        dest = os.path.join(CACHE, f"{name}-{commit}")
        if os.path.isdir(dest) and not args.kilitle and tree_hash(dest) == digest:
            result.append((name, url, commit, paths, digest, dest))
            continue
        shutil.rmtree(dest, ignore_errors=True)
        try:
            fetch(name, url, commit, paths, dest)
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"{name}: git {' '.join(e.cmd[3:5])} failed: {e.stderr.decode()[-400:]}")
        got = tree_hash(dest)
        if args.kilitle:
            digest = got
        elif got != digest:
            shutil.rmtree(dest)
            raise SystemExit(f"{name}@{commit}: content hash {got}, kaynaklar.lock says {digest}")
        result.append((name, url, commit, paths, digest, dest))
    for name, url, commit, paths, digest, dest in result:
        if args.kilitle:
            print(f"{name} {url} {commit} {','.join(paths)} {digest}")
        else:
            print(f"{name}\t{dest}")


if __name__ == "__main__":
    sys.exit(main())
