#!/usr/bin/env python3
"""Self-test: prod_build.py verifies the components the reconfigure fetched.

    python tools/test_prod_build_fetch.py [--script PATH]

Spec 12.3 item 3: a prod build refuses if a downloaded component differs from
its own CHECKSUMS.json or its .component_hash from dependencies.lock. On a
fresh clone `managed_components/` does not exist until `idf.py reconfigure`
downloads it, so the check has to run after the reconfigure and before the
build (reviewer's ruling 2026-10-16, from the tester's prod build of e3d9437,
where the check ran first, found "none on disk" and passed).

Each case makes a fresh clone of this repo's HEAD with no managed_components/,
checks out gen-inhibit/main there, and runs prod_build.main() against it with
`idf.py` replaced by a fake. The fake's reconfigure "fetches" the components;
its build only records that it was asked to build. Nothing is compiled, and no
IDF environment is needed.

  tampered  the reconfigure fetches mdns with one byte appended to mdns.c:
            the build must not be asked for.
  missing   the reconfigure fetches nothing although the lock names mdns:
            the build must not be asked for.
  good      the reconfigure fetches mdns as this checkout has it (verified
            against its own CHECKSUMS.json and the lock first): the build
            must be asked for. Without this control a script that refused
            everything would pass the other two.

--script runs another copy of prod_build.py (an older commit's, say) against
the same cases; the clone still comes from this repo. On the ordering this
test was written against, tampered and missing fail.

Implementor self-test, not evidence. Runs on Windows or Linux with git on PATH.
"""

import argparse
import contextlib
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
COMPONENT = "espressif__mdns"
# AGENTS.md "Files and folders": this worktree's scratch/, never the system
# temp folder. One folder per run, so two runs at once never share one.
SCRATCH = os.path.join(REPO, "scratch", "gen-inhibit-prodbuild")
WORK = None                             # this run's, made by main()


def git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def rmtree(path):
    def writable(fn, p, _exc):          # git's object files are read-only on Windows
        os.chmod(p, stat.S_IWRITE)
        fn(p)
    if os.path.exists(path):
        shutil.rmtree(path, onerror=writable)


def load(script):
    spec = importlib.util.spec_from_file_location("prod_build_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fresh_clone(head):
    rmtree(WORK)
    git(["clone", "-q", "--no-checkout", REPO, WORK], REPO)
    git(["checkout", "-q", "-b", "gen-inhibit/main", head], WORK)
    assert not os.path.exists(os.path.join(WORK, "managed_components"))
    return WORK


def run_case(script, head, fetch):
    """prod_build.main() on a fresh clone; returns (build asked for, output)."""
    clone = fresh_clone(head)
    mod = load(script)
    mod.REPO = clone
    mod.check_idf_env = lambda: True
    calls = []
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "idf.py":
            calls.append(list(cmd))
            if "reconfigure" in cmd:
                fetch(clone)
            return subprocess.CompletedProcess(cmd, 1 if "build" in cmd else 0)
        return real_run(cmd, *a, **kw)

    out = io.StringIO()
    mod.subprocess.run = fake_run
    argv, sys.argv = sys.argv, [script]     # main() parses its own arguments
    try:
        with contextlib.redirect_stdout(out):
            rc = mod.main()
    finally:
        mod.subprocess.run = real_run
        sys.argv = argv
    built = any("build" in c for c in calls)
    return built, rc, calls, out.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default=os.path.join(HERE, "prod_build.py"))
    args = ap.parse_args()

    source = os.path.join(REPO, "managed_components", COMPONENT)
    if not os.path.isdir(source):
        print("CANNOT RUN: %s is not on disk, so there is no good component for "
              "the control. Build this checkout once first." % source)
        return 2
    # The control's component must itself pass, or the control proves nothing.
    current = load(os.path.join(HERE, "prod_build.py"))
    current.REPO = REPO
    with contextlib.redirect_stdout(io.StringIO()):
        if not current.check_components({}):
            print("CANNOT RUN: this checkout's %s does not pass refusal 3 itself."
                  % COMPONENT)
            return 2

    global WORK
    os.makedirs(SCRATCH, exist_ok=True)
    WORK = tempfile.mkdtemp(prefix="fetch-", dir=SCRATCH)
    head = git(["rev-parse", "HEAD"], REPO)

    def fetch_good(clone):
        shutil.copytree(source, os.path.join(clone, "managed_components", COMPONENT))

    def fetch_tampered(clone):
        fetch_good(clone)
        with open(os.path.join(clone, "managed_components", COMPONENT, "mdns.c"),
                  "ab") as fh:
            fh.write(b" ")

    def fetch_nothing(clone):
        pass

    cases = [
        ("tampered", fetch_tampered, False),
        ("missing", fetch_nothing, False),
        ("good", fetch_good, True),
    ]
    print("script under test: %s" % args.script)
    print("clone of %s at %s, in %s" % (REPO, head[:7], WORK))
    failed = 0
    for name, fetch, want_build in cases:
        built, rc, calls, out = run_case(args.script, head, fetch)
        ok = (built == want_build)
        failed += not ok
        print("[%s] %-9s build %s (want %s); idf.py calls: %s"
              % ("PASS" if ok else "FAIL", name,
                 "asked for" if built else "not asked for",
                 "asked for" if want_build else "not asked for",
                 " | ".join(" ".join(c[1:]) for c in calls) or "none"))
        if not ok:
            for ln in out.splitlines():
                if "component" in ln or "REFUSED" in ln or "NOT BUILT" in ln:
                    print("         " + ln.strip())
    rmtree(WORK)
    print("%d of %d cases failed" % (failed, len(cases)) if failed
          else "all %d cases pass" % len(cases))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
