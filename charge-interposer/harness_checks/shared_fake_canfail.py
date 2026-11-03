#!/usr/bin/env python3
"""The shared TWAI fake's can-fail for host_bridge: the comparison of the
suite on our own fake with the suite on the shared fake catches a
difference in the shared fake.

Tester, 2026-11-02 (the charge-interposer reviewer's order, after the
user's ruling on the entries written before AGENTS.md's committed-checks
rule): port step 1b's acceptance E compared host_bridge's transcript on our
own fake with its transcript on the shared fake (shared/twai_port/test) and
found them identical. Its can-fail was a script in scratch. This is that
can-fail as committed harness, run by the acceptance runner as
host_bridge's own can-fail mode:

    TWAI_FAKE=shared TWAI_FAKE_DIR=<shared/twai_port/test> \\
        bash charge-interposer/firmware/test/host_bridge/run.sh --can-fail

It runs host_bridge (run.sh, with g++: madhouse) on our own fake, then on
copies of the shared fake in a scratch_dir run folder:
  control   the shared fake unchanged: the comparison must find NO
            difference (otherwise the mutants below prove nothing)
  mutants   one behaviour broken at a time, with the text of the shared
            fake's own mutate.py (MUTANTS, by name; each text must occur
            exactly once, checked before anything runs): the comparison
            must find a difference for every one
The comparison is the acceptance adapter's masked-log comparer
(accept_adapter.py), the one an acceptance list compares the two runs with.
The mutants are the three that acceptance E's suite killed: recovery ends
running, recovery keeps the TX queue, a full RX queue not counted.

Exit 0: the control was the same and every mutant differed. 1: not. 2:
refused, nothing judged (no TWAI_FAKE_DIR, a name mutate.py does not hold,
a text not found exactly once, a run.sh refusal).

Tests: ../twai_fake_option_test.py (with --can-fail).
"""

import importlib.util
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
if CODE not in sys.path:
    sys.path.insert(0, CODE)

RUN_SH = os.path.join(CODE, "firmware", "test", "host_bridge", "run.sh")
NAMES = ("recovery ends running", "recovery keeps the TX queue",
         "full RX queue not counted")
FAKE_FILE = "twai_fake.c"
TOOL = "shared-fake-canfail"

# switched off one at a time by twai_fake_option_test.py --can-fail
RULES = {k: True for k in ("known_names", "exactly_once", "control_same",
                           "mutant_differs", "fresh_copy", "shared_mode")}


class Refused(Exception):
    pass


def load_mutants(fake_dir):
    """{name: (old, new)} from the shared fake's own mutate.py."""
    p = os.path.join(fake_dir, "mutate.py")
    if not os.path.isfile(p):
        raise Refused("%s has no mutate.py" % fake_dir)
    spec = importlib.util.spec_from_file_location("_shared_fake_mutate", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {name: (old, new) for name, old, new in mod.MUTANTS}


def chosen(fake_dir, names=NAMES):
    """[(name, old, new)] for names, checked against the fake before any run."""
    muts = load_mutants(fake_dir)
    with open(os.path.join(fake_dir, FAKE_FILE), encoding="utf-8", newline="") as f:
        src = f.read()
    out = []
    for n in names:
        if n not in muts:
            if RULES["known_names"]:
                raise Refused("the shared fake's mutate.py holds no mutant %r" % n)
            continue
        old, new = muts[n]
        k = src.count(old)
        if k != 1 and RULES["exactly_once"]:
            raise Refused("mutant %r: its text occurs %d times in %s, not once"
                          % (n, k, FAKE_FILE))
        out.append((n, old, new))
    return out


def run_suite(env, log):
    """run.sh with env added to this process's own, its output to log; its
    exit code."""
    with open(log, "w", encoding="utf-8", newline="\n") as f:
        return subprocess.run(["bash", RUN_SH], env=dict(os.environ, **env),
                              stdout=f, stderr=subprocess.STDOUT).returncode


def compare(a, b):
    import accept_adapter
    return accept_adapter.comparer("masked-log")({"log": a}, {"log": b}, None, None)


def fake_copy(fake_dir, dest, mutant=None):
    shutil.copytree(fake_dir, dest)
    if mutant:
        p = os.path.join(dest, FAKE_FILE)
        with open(p, encoding="utf-8", newline="") as f:
            src = f.read()
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(src.replace(mutant[1], mutant[2], 1))
    return dest


def judge(fake_dir, work, runner=run_suite, out=print):
    """0 / 1 as the module says; Refused before any run."""
    muts = chosen(fake_dir)
    own = os.path.join(work, "own.txt")
    rc = runner({"TWAI_FAKE": "own"}, own)
    out("own fake: run.sh rc=%d" % rc)
    if rc == 2:
        raise Refused("run.sh refused on our own fake (rc 2): see %s" % own)
    shared = "shared" if RULES["shared_mode"] else "own"
    ok = True
    runs = [("control", None)] + [(m[0], m) for m in muts]
    control_dir = None
    for i, (name, m) in enumerate(runs):
        if RULES["fresh_copy"] or control_dir is None:
            d = fake_copy(fake_dir, os.path.join(work, "fake-%d" % i), m)
            control_dir = control_dir or d
        else:
            d = control_dir
        log = os.path.join(work, "run-%d.txt" % i)
        rc = runner({"TWAI_FAKE": shared, "TWAI_FAKE_DIR": d}, log)
        if rc == 2:
            raise Refused("run.sh refused on the shared fake (%s, rc 2)" % name)
        diffs = compare(own, log)
        if m is None:
            good = not diffs or not RULES["control_same"]
            out("control (the shared fake unchanged): rc=%d, %d line(s) differ from our "
                "own fake -- %s" % (rc, len(diffs), "the same" if good else "NOT THE SAME"))
        else:
            good = bool(diffs) or not RULES["mutant_differs"]
            out("mutant %r: rc=%d, %d line(s) differ -- %s"
                % (name, rc, len(diffs), "caught" if diffs else "NOT CAUGHT"))
        for d_ in diffs[:3]:
            out("    %s" % d_["line"][:160])
        ok = ok and good
    out("shared fake can-fail: %s" % ("PASS: the control is the same and every mutant is "
                                      "caught" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None, runner=run_suite, out=print):
    if argv:
        out("usage: shared_fake_canfail.py (no arguments; TWAI_FAKE_DIR names the fake)")
        return 2
    code = os.environ.get("INTERPOSER_CODE")
    if code and os.path.realpath(code) != os.path.realpath(CODE):
        out("INTERPOSER_CODE=%s is not this tree's charge-interposer/ (%s)" % (code, CODE))
        return 2
    fake_dir = os.environ.get("TWAI_FAKE_DIR", "").strip()
    if not fake_dir or not os.path.isfile(os.path.join(fake_dir, FAKE_FILE)):
        out("TWAI_FAKE_DIR must name the shared fake's folder (shared/twai_port/test), "
            "with %s" % FAKE_FILE)
        return 2
    import scratch_dir
    try:
        with scratch_dir.run(TOOL) as work:
            return judge(fake_dir, work, runner, out)
    except Refused as e:
        out("REFUSED: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
