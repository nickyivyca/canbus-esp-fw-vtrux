"""A mutant run from a read-only source (tests/_ota_mutants.py's
writable_copy): a pinned checkout ../shared-<tag> is read-only (AGENTS.md,
from 2026-11-17), and copytree keeps the mode bits, so before the fix every
mutant of the OTA flasher's tests stopped with PermissionError when they ran
from one (the charge-interposer tester's re-pin to 2026.11.17; the
charge-interposer reviewer's order of 2026-11-17). No hardware.

    python -B shared/bench/tests/test_readonly_copy.py            every test
    python -B shared/bench/tests/test_readonly_copy.py --can-fail  the
        control first (every test unchanged, which must pass), then each
        test again with the rule it guards switched off; each must fail

A made-up vtrux_bench (one module, m.py, whose f() returns 1) and a test of
it (FAIL when f() is not 1), in this run's own scratch folder
(tests/_scratch.py), made read-only, every folder and file; then
_ota_mutants.run_mutants from it, with one mutant (f returns 2). The
control must pass, the mutant be killed, and the source stay read-only and
unchanged. The run folder is removed at the end; its read-only files are
made writable first (_scratch.remove_tree).
"""

import contextlib
import io
import os
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _ota_mutants as OM                            # noqa: E402
import _scratch                                      # noqa: E402

BASE = _scratch.run_dir("readonly_copy")
N = [0]

TEST = ("import os, sys\n"
        "sys.path.insert(0, os.environ['OTA_TEST_PKG'])\n"
        "from vtrux_bench import m\n"
        "print('ok    f' if m.f() == 1 else 'FAIL  f returned %r' % m.f())\n")


def tree(root):
    out = {}
    for d, _dirs, files in os.walk(root):
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = (f.read(), os.stat(p).st_mode)
        out[os.path.relpath(d, root) + "/"] = (None, os.stat(d).st_mode)
    return out


def read_only(root):
    for d, _dirs, files in os.walk(root, topdown=False):
        for p in [os.path.join(d, n) for n in files] + [d]:
            os.chmod(p, os.stat(p).st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def test_mutant_from_read_only():
    """A mutant from a read-only package: the control passes, the mutant is
    killed (1 of 1), run_mutants returns 0, and the source is still
    read-only and byte for byte unchanged."""
    N[0] += 1
    d = os.path.join(BASE, "t%d" % N[0])
    pkg = os.path.join(d, "src", "vtrux_bench")
    os.makedirs(pkg)
    for name, text in (("__init__.py", ""), ("m.py", "def f():\n    return 1\n")):
        with open(os.path.join(pkg, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    test = os.path.join(d, "test_m.py")
    with open(test, "w", encoding="utf-8", newline="\n") as f:
        f.write(TEST)
    read_only(os.path.join(d, "src"))
    before = tree(os.path.join(d, "src"))
    assert not os.access(os.path.join(pkg, "m.py"), os.W_OK) or os.geteuid() == 0, \
        "the source is not read-only"
    old = OM.PKG
    OM.PKG = pkg
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = OM.run_mutants(test, [("f returns 2", "m.py", "return 1", "return 2")],
                                "readonly_copy_m")
    finally:
        OM.PKG = old
    text = out.getvalue()
    assert rc == 0, (rc, text[-1500:])
    assert "PASS     m0" in text and "KILLED   m1" in text and "1 of 1 killed" in text, text
    assert tree(os.path.join(d, "src")) == before, "the read-only source changed"


TESTS = [test_mutant_from_read_only]
MUTANTS = [("writable_copy", test_mutant_from_read_only)]


def outcome_of(t):
    try:
        t()
        return None
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:300])


def main(argv):
    can_fail = "--can-fail" in argv
    bad = 0
    try:
        if not can_fail:
            for t in TESTS:
                out = outcome_of(t)
                print("%s  %-28s %s" % ("ok  " if out is None else "FAIL", t.__name__,
                                        out or ""), flush=True)
                bad += out is not None
            print("\n%d of %d passed" % (len(TESTS) - bad, len(TESTS)))
            return 1 if bad else 0
        control = [t.__name__ for t in TESTS if outcome_of(t) is not None]
        if control:
            print("REFUSED: the control fails (%s), so a mutant's failures would "
                  "mean nothing" % ", ".join(control))
            return 1
        missing = sorted(set(OM.RULES) - {k for k, _t in MUTANTS})
        if missing:
            print("FAIL  rules with no mutant: %s" % ", ".join(missing))
            bad += 1
        caught = 0
        for k, t in MUTANTS:
            OM.RULES[k] = False
            try:
                out = outcome_of(t)
            finally:
                OM.RULES[k] = True
            print("%s  %-16s off: %-28s %s" % (
                "ok  " if out else "FAIL", k, t.__name__,
                ("fails (" + out[:110] + ")") if out else
                "STILL PASSES -- the test checks nothing"), flush=True)
            caught += out is not None
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 1 if bad or caught < len(MUTANTS) else 0
    finally:
        _scratch.remove_tree(BASE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
