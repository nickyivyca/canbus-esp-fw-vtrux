#!/usr/bin/env python3
"""Mutants of b6_zero_deltas.py; test_b6_zero_deltas.py must fail on each BY A CHECK.
A crash is not a kill, and an edit that does not apply is an error.

    py -3.14 -B gen-inhibit/tools/bench/test_b6_zero_deltas_mutants.py

Each mutant is a copy of the script in the worktree's scratch/, one edit applied, run by
the test through ZERO_SCRIPT. The unmutated copy runs first, as the control: if it fails,
no mutant is judged and the run exits 1. A FAIL line the test writes for a crash it caught
("ran without raising") kills nothing, nor does any FAIL line beside a traceback.
Gen-inhibit tester, 2026-11-05.
"""
import os
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "b6_zero_deltas.py"
TEST = "test_b6_zero_deltas.py"
MUTANTS = [
    ("the new rule without req_start (both rules the old one)",
     '        new = XJ.per_pulse(errs, n, iv, [(t, x.get("drv_bus_error"), x.get("req_start")) for t, x in samples])\n',
     '        new = XJ.per_pulse(errs, n, iv, [(t, x.get("drv_bus_error")) for t, x in samples])\n'),
    ("the old rule with req_start (both rules the new one)",
     '        old = XJ.per_pulse(errs, n, iv, [(t, x.get("drv_bus_error")) for t, x in samples])\n',
     '        old = XJ.per_pulse(errs, n, iv, [(t, x.get("drv_bus_error"), x.get("req_start")) for t, x in samples])\n'),
    ("records without a witness file read too",
     '            if "_witness" not in p and L.witness(json.load(open(p)), p) is not None:\n',
     '            if "_witness" not in p:\n'),
    ("the witness file looked for only under the record's own name",
     '            if "_witness" not in p and L.witness(json.load(open(p)), p) is not None:\n',
     '            if "_witness" not in p and os.path.exists(p[:-len(".json")] + "_witness.json.gz"):\n'),
    ("the after reading is the first returned after the frame",
     '            aft = sorted((s for s in good if (s[1].get("req_start") or s[0]) > e1 + 0.05),\n',
     '            aft = sorted((s for s in good if s[0] > e1 + 0.05),\n'),
    ("a row only where the new delta is 0",
     '            if a != 0 and b != 0:\n', '            if a != 0:\n'),
    ("the uptime offset over every reading, not the quickly answered ones",
     ' and t - s["req_start"] <= FAST_S:\n', ':\n'),
    ("the verdict from the old delta",
     '                verdict = "stands" if a == 0 else "artifact"\n',
     '                verdict = "stands" if b == 0 else "artifact"\n'),
]


def main():
    base = gi_paths.scratch("mutants-b6_zero_deltas")
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read().replace("\r\n", "\n")
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        path = os.path.join(d, SRC)
        open(path, "w", encoding="utf-8", newline="").write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], capture_output=True, text=True,
                           env=dict(os.environ, ZERO_SCRIPT=path))
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr or "ran without raising" in r.stdout
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                       len(fails), ", CRASHED" if crashed else "",
                                                       ("; " + fails[0][:100]) if fails else ""))
            if not ok:
                gi_paths.remove_tree(base)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = (bool(fails) and not all("ran without raising" in f for f in fails)
                and "Traceback" not in r.stderr)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if crashed else "", ("; " + fails[0][:100]) if fails else ""))
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
