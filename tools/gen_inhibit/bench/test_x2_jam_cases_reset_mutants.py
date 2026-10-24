#!/usr/bin/env python3
"""Mutants of x2_jam_cases.py's WiCAN reset (reset_to_off and its use in case
threshold) and of the witness controller state (kvaser_flags,
witness_state_summary; added 2026-10-16, item 4), and of the trip 6 judge (max_in_window's
edge, the latch bound; added 2026-10-20); test_x2_jam_cases.py must fail on each.

    py -3.14 tools/gen_inhibit/bench/test_x2_jam_cases_reset_mutants.py

Run from the reverse-it root (the test imports canre). Each mutant copies the
two files into a fresh folder under runs/, applies one edit, and runs the test
from the root with this folder on PYTHONPATH for everything else. An edit that
does not apply is an error, not a kill. Added 2026-10-16 with the fix: with
the WiCAN's USB unplugged, --reset-wican reset nothing and said nothing.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "x2_jam_cases.py"
TEST = "test_x2_jam_cases.py"

MUTANTS = [
    ("threshold arms whatever the reset said (the old behaviour)",
     'if pre_reset is not None and not pre_reset["ok"]:',
     'if False:'),
    ("a reset counts without a lower uptime",
     '                and p["uptime_ms"] < out["uptime_before_ms"]):',
     '                ):'),
    ("a reset counts while still latched",
     '    elif after.get("abort_latched"):',
     '    elif False:'),
    ("no uptime before still counts as shown",
     '                and out["uptime_before_ms"] is not None\n',
     ''),
    ("always POSTs, even with COM4 open",
     'if cc is not None and getattr(cc, "ser", None) is not None:',
     'if False:'),
    ("any mode after the reset is accepted",
     'if (p is not None and p.get("mode") == 0 and p.get("uptime_ms") is not None',
     'if (p is not None and p.get("uptime_ms") is not None'),
    # Witness controller state (2026-10-16, reviewer's item 4).
    ("bus_off read from the wrong bit",
     'KV_FLAGS = {0x1: "error_passive", 0x2: "bus_off",',
     'KV_FLAGS = {0x1: "error_passive", 0x20000: "bus_off",'),
    ("an unsampled span reads as healthy",
     '    if not s:\n        return None\n    names',
     '    if not s:\n        return {"bus_off": False}\n    names'),
    ("bus_off_from is the last bus_off sample",
     '"bus_off_from": off[0] if off else None,',
     '"bus_off_from": off[-1] if off else None,'),
    ("error_passive never reported",
     '"error_passive": bool(passive),',
     '"error_passive": False,'),
    # The trip 6 judge (2026-10-20, reviewer: the helper's edge and the latch bound).
    ("max_in_window's edge strict (<= to <)",
     "        while ts[i] - ts[j] > window_s + 1e-9:",
     "        while ts[i] - ts[j] >= window_s - 1e-9:"),
    ("no latch bound",
     "        if late:\n            return dict(out, verdict=\"FAIL\",\n                        why=\"trip 6 not latched",
     "        if False:\n            return dict(out, verdict=\"FAIL\",\n                        why=\"trip 6 not latched"),
    ("the bound is 1 s",
     "TRIP6_RECORD_S = 0.2\n",
     "TRIP6_RECORD_S = 1.0\n"),
    ("the bound reads the answer time, not the request",
     '        late = [t for t, s in before if (s.get("req_start") or t) >= due]',
     '        late = [t for t, s in before if t >= due]'),
]


def main():
    root = os.getcwd()
    base = gi_paths.scratch("mutants-x2_jam_reset_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        d = os.path.join(base, "m%d" % i)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        nl = "\r\n" if "\r\n" in src else "\n"
        o, n = old.replace("\n", nl), new.replace("\n", nl)
        if src.count(o) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (i, name, src.count(o)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(o, n))
        shutil.copy(os.path.join(HERE, TEST), d)
        env = dict(os.environ, PYTHONPATH=HERE)
        r = subprocess.run([sys.executable, os.path.join(d, TEST)], cwd=root, env=env,
                           capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
        dead = bool(fails)      # a crash is not a kill (2026-10-16)
        killed += dead
        print("%s m%d %s  (exit %d%s)" % ("KILLED  " if dead else "SURVIVED", i, name,
                                          r.returncode, ("; " + fails[0][:90]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
