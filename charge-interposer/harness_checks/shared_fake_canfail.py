#!/usr/bin/env python3
"""The shared TWAI fake's can-fail for host_bridge: the suite on the shared
fake catches a difference in the fake.

Tester, 2026-11-02 (the charge-interposer reviewer's order, after the
user's ruling on the entries written before AGENTS.md's committed-checks
rule): port step 1b's acceptance E compared host_bridge's transcript on our
own fake with its transcript on the shared fake (shared/twai_port/test) and
found them identical, and this was its can-fail as committed harness.

Port step 2b (tester, 2026-11-08; the charge-interposer reviewer's rulings
for 2b): our own fake is retired, and host_bridge builds only on the shared
fake, with the real twai_port and can_port (run.sh). So the reference is no
longer our own fake: it is the suite on the shared fake unchanged. Run by
the acceptance runner as host_bridge's own can-fail mode:

    [TWAI_FAKE_DIR=<checkout>/shared/twai_port/test] \\
        bash charge-interposer/fw_harness/host_bridge/run.sh --can-fail

The shared folders are TWAI_FAKE_DIR's checkout if it is set, else the
pinned checkout (shared_pin.folder). It runs host_bridge (run.sh, with gcc
and g++: madhouse) on copies of that checkout's shared/twai_port and
shared/can_port, each in a folder of its own in a scratch_dir run folder,
with TWAI_FAKE_DIR naming the copy:
  control    the fake unchanged, twice, each on its own copy: the two
             transcripts must be the same (otherwise a difference below
             could be the suite's own, and the mutants prove nothing)
  mutants    one behaviour broken at a time, with the text of the shared
             fake's own mutate.py (MUTANTS, by name; each text must occur
             exactly once, checked before anything runs): each transcript
             must differ from the first control's
The comparison is the acceptance adapter's masked-log comparer
(accept_adapter.py), the one an acceptance list compares runs with. The
mutants are the three that acceptance E's suite killed: recovery ends
running, recovery keeps the TX queue, a full RX queue not counted.

Exit 0: the controls were the same and every mutant differed. 1: not. 2:
refused, nothing judged (no shared folders, a name mutate.py does not hold,
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

RUN_SH = os.path.join(CODE, "fw_harness", "host_bridge", "run.sh")
NAMES = ("recovery ends running", "recovery keeps the TX queue",
         "full RX queue not counted")
FAKE_FILE = "twai_fake.c"
TOOL = "shared-fake-canfail"

# switched off one at a time by twai_fake_option_test.py --can-fail
RULES = {k: True for k in ("known_names", "exactly_once", "control_same",
                           "mutant_differs", "fresh_copy", "copy_used")}


class Refused(Exception):
    pass


def load_mutants(fake_dir):
    """{name: (old, new)} from the shared fake's own mutate.py."""
    p = os.path.join(fake_dir, "mutate.py")
    if not os.path.isfile(p):
        raise Refused("%s has no mutate.py" % fake_dir)
    spec = importlib.util.spec_from_file_location("_shared_fake_mutate", p)
    mod = importlib.util.module_from_spec(spec)
    # no bytecode written beside it: the fake's folder is the pinned
    # read-only checkout's, whose digest a __pycache__ would change
    # (2026-11-09: a run without -B left one there)
    was = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
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


def components(fake_dir):
    """(twai_port, can_port): the folders of the fake's checkout."""
    tp = os.path.dirname(os.path.abspath(fake_dir))
    cp = os.path.join(os.path.dirname(tp), "can_port")
    if not os.path.isdir(cp):
        raise Refused("%s has no can_port beside its twai_port (%s)" % (fake_dir, cp))
    return tp, cp


def fake_copy(fake_dir, dest, mutant=None):
    """twai_port and can_port copied to dest/shared/, the mutant (if any)
    applied to the copy's fake; the copy's fake folder."""
    tp, cp = components(fake_dir)
    ign = shutil.ignore_patterns("__pycache__")
    shutil.copytree(tp, os.path.join(dest, "shared", "twai_port"), ignore=ign)
    shutil.copytree(cp, os.path.join(dest, "shared", "can_port"), ignore=ign)
    d = os.path.join(dest, "shared", "twai_port", os.path.basename(
        os.path.normpath(fake_dir)))
    if mutant:
        p = os.path.join(d, FAKE_FILE)
        with open(p, encoding="utf-8", newline="") as f:
            src = f.read()
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(src.replace(mutant[1], mutant[2], 1))
    return d


def judge(fake_dir, work, runner=run_suite, out=print):
    """0 / 1 as the module says; Refused before any run."""
    muts = chosen(fake_dir)
    components(fake_dir)
    ok = True
    runs = [("control", None), ("control again", None)] + [(m[0], m) for m in muts]
    reused = None
    ref = None
    for i, (name, m) in enumerate(runs):
        if RULES["fresh_copy"] or reused is None:
            d = fake_copy(fake_dir, os.path.join(work, "copy-%d" % i), m)
            reused = reused or d
        else:
            d = reused
        log = os.path.join(work, "run-%d.txt" % i)
        rc = runner({"TWAI_FAKE_DIR": d if RULES["copy_used"] else fake_dir}, log)
        if rc == 2:
            raise Refused("run.sh refused (%s, rc 2): see %s" % (name, log))
        if ref is None:
            ref = log
            out("control (the fake unchanged): run.sh rc=%d" % rc)
            continue
        diffs = compare(ref, log)
        if m is None:
            good = not diffs or not RULES["control_same"]
            out("control again (the fake unchanged, another copy): rc=%d, %d line(s) "
                "differ from the first -- %s" % (rc, len(diffs),
                                                  "the same" if not diffs else "NOT THE SAME"))
        else:
            good = bool(diffs) or not RULES["mutant_differs"]
            out("mutant %r: rc=%d, %d line(s) differ -- %s"
                % (name, rc, len(diffs), "caught" if diffs else "NOT CAUGHT"))
        for d_ in diffs[:3]:
            out("    %s" % d_["line"][:160])
        ok = ok and good
    out("shared fake can-fail: %s" % ("PASS: the controls are the same and every mutant "
                                      "is caught" if ok else "FAIL"))
    return 0 if ok else 1


def fake_folder():
    """TWAI_FAKE_DIR, else the pinned checkout's shared/twai_port/test."""
    d = os.environ.get("TWAI_FAKE_DIR", "").strip()
    if d:
        return d
    import shared_pin
    try:
        return os.path.join(shared_pin.folder("shared/twai_port"), "test")
    except shared_pin.SharedPinError as e:
        raise Refused("no TWAI_FAKE_DIR, and the pinned shared/twai_port: %s" % e)


def main(argv=None, runner=run_suite, out=print):
    if argv:
        out("usage: shared_fake_canfail.py (no arguments; TWAI_FAKE_DIR names the fake, "
            "else the pinned checkout's)")
        return 2
    code = os.environ.get("INTERPOSER_CODE")
    if code and os.path.realpath(code) != os.path.realpath(CODE):
        out("INTERPOSER_CODE=%s is not this tree's charge-interposer/ (%s)" % (code, CODE))
        return 2
    import scratch_dir
    try:
        fake_dir = fake_folder()
        if not os.path.isfile(os.path.join(fake_dir, FAKE_FILE)):
            raise Refused("%s is not the shared fake's folder (shared/twai_port/test): "
                          "it has no %s" % (fake_dir, FAKE_FILE))
        with scratch_dir.run(TOOL) as work:
            return judge(fake_dir, work, runner, out)
    except Refused as e:
        out("REFUSED: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
