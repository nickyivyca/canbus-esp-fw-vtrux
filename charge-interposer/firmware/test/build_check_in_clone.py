"""Run build_check.py in a real git checkout of the run's commit.

Why (charge-interposer tester, 2026-10-19). The chain runner ships
madhouse a plain copy of charge-interposer/, with no .git. A build there
gets no manifest row -- manifest.py refuses a row whose git state it
cannot read (reviewer, 2026-10-15) -- and spec 8.2 gives a build on
another host OS its own row, so build_check's manifest check could never
pass on the shipped copy: emulated run e7d4 failed exactly so, with every
other build check passing. The user ruled (2026-10-19) that build-check
runs in a real git checkout instead, at exactly the run's commit, from a
clean tree whose source digest equals the shipped copy's.

    build_check_in_clone.py --bundle B --commit C --shipped DIR --dest DIR
                            [-- build_check.py arguments]

  --bundle   a git bundle holding C (the suite ships one per run)
  --commit   the run's commit, full SHA
  --shipped  the shipped copy of charge-interposer/ the rest of the run uses
  --dest     where to clone; must not exist (the run's work folder)

Refused (exit 2, nothing built) unless: the clone is a git work tree,
HEAD is exactly C, `git status --porcelain` is empty, and the source
digest of the checkout's firmware/ equals the shipped copy's. Otherwise
build_check.py runs in the checkout with the arguments given, and its exit
code is this script's.
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import build_identity                               # noqa: E402

REFUSED = 2


def _git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                       text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def prepare_tree(bundle, commit, dest):
    """Clone the bundle to dest and check out commit -> problems."""
    if os.path.exists(dest):
        return ["%s already exists; a checkout is made fresh each run" % dest]
    r = subprocess.run(["git", "clone", "-q", "--no-hardlinks", "--no-checkout",
                        bundle, dest], capture_output=True, text=True)
    if r.returncode != 0:
        return ["cannot clone %s: %s" % (bundle, r.stderr.strip()[-300:])]
    rc, _o, err = _git(dest, "checkout", "-q", "--detach", commit)
    if rc != 0:
        return ["cannot check out %s: %s" % (commit, err[-300:])]
    return []


def verify_tree(dest, commit):
    """The checkout is a clean git work tree at exactly commit -> problems."""
    rc, out, err = _git(dest, "rev-parse", "--is-inside-work-tree")
    if rc != 0 or out != "true":
        return ["%s is not a git work tree (%s)" % (dest, err or out)]
    rc, head, err = _git(dest, "rev-parse", "HEAD")
    if rc != 0 or head != commit:
        return ["HEAD is %s, not the run's commit %s" % (head or err, commit)]
    rc, status, err = _git(dest, "status", "--porcelain")
    if rc != 0 or status:
        return ["the checkout is not clean: %s" % (status or err)[:300]]
    return []


def same_sources(shipped, dest):
    """The checkout's firmware sources are the shipped copy's -> problems."""
    a = build_identity.source_digest(os.path.join(shipped, "firmware"))
    b = build_identity.source_digest(
        os.path.join(dest, "charge-interposer", "firmware"))
    if a != b:
        return ["source digest of the checkout is %s, of the shipped copy %s"
                % (b[:12], a[:12])]
    return []


def main(argv):
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    else:
        rest = []
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--shipped", required=True)
    ap.add_argument("--dest", required=True)
    a = ap.parse_args(argv)
    if len(a.commit) != 40:
        print("REFUSED: --commit must be a full SHA, got %r" % a.commit)
        return REFUSED
    probs = prepare_tree(a.bundle, a.commit, a.dest)
    if not probs:
        probs = verify_tree(a.dest, a.commit)
    if not probs:
        probs = same_sources(a.shipped, a.dest)
    if probs:
        print("REFUSED, nothing built:")
        for p in probs:
            print("  - " + p)
        return REFUSED
    work = os.path.join(a.dest, "charge-interposer")
    print("build_check in a git checkout: %s at %s, clean, source digest "
          "equal to the shipped copy's" % (work, a.commit[:10]))
    sys.stdout.flush()
    return subprocess.run([sys.executable, "-B",
                           os.path.join(work, "firmware", "test",
                                        "build_check.py")] + rest,
                          cwd=work).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
