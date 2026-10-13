#!/usr/bin/env python3
"""Spec 12.3 item 3: each test-only switch's refusal rows, shown failing on an image
built with the switch on and passing on one built without it.

    python3 test_refusal_pairs.py --off <off.bin> --on SWITCH=<on.bin> [--on SWITCH=<on.bin> ...]

Both images of a pair should be built from the same commit, from a clean tree. Each
must have its .elf beside it. For each --on image, every row the switch's table entry
covers must FAIL. Where a row is shared by several switches, its detail must name this
switch, so the instrument table's row can't pass the proof on another instrument's
marker. On the --off image every refusal row of every test-only switch must PASS.
A NO REF anywhere is a failure of the proof, not a pass: a missing ELF proves nothing.

The rows per switch are the ones build_output.py writes for it. A switch missing from
ROWS fails the run, so a new test-only switch can't be skipped here by omission.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_output as BO                                          # noqa: E402
import build_switches as BS                                        # noqa: E402

INSTR_ROWS = ("no measurement instrument in the image", "no instrument symbols in the ELF")
# switch -> (the check function, the rows it must fail, whether the detail must name the switch)
ROWS = {
    "GI_INSTRUMENT_RXQ": (BO.check_no_instrument, INSTR_ROWS, True),
    "GI_INSTRUMENT_TXABORT": (BO.check_no_instrument, INSTR_ROWS, True),
    "GI_INSTRUMENT_ABORTWIN": (BO.check_no_instrument, INSTR_ROWS, True),
    "OTA_HEALTH_FAULT_INJECT": (BO.check_not_rollback_rehearsal,
                                ("no OTA fault injection in the image", "project name is not a rehearsal build"), False),
    "GI_TEST_RESTART_QUIESCE": (BO.check_no_restart_test,
                                ("no restart test feature in the image", "no restart test symbols in the ELF",
                                 "project name is not a restart test build"), False),
}


def run(fn, path):
    BO.rows.clear()
    fn(path)
    out = {name: (ok, detail) for name, ok, detail, _r in BO.rows}
    BO.rows.clear()
    return out


def main():
    args = sys.argv[1:]
    off = args[args.index("--off") + 1]
    ons = [args[k + 1].split("=", 1) for k, a in enumerate(args) if a == "--on"]
    checks = []
    tests = sorted(s for s, e in BS.SWITCHES.items() if e[0] == BS.TEST)
    checks.append(("every test-only switch in the table has rows here",
                   [s for s in tests if s not in ROWS] == [], "missing %s" % [s for s in tests if s not in ROWS]))
    for sw, path in ons:
        if sw not in ROWS:
            checks.append(("%s is a known test-only switch" % sw, False, "not in ROWS"))
            continue
        fn, names, named = ROWS[sw]
        got = run(fn, path)
        for n in names:
            ok, detail = got.get(n, (None, "row not produced"))
            good = ok is False and (not named or sw in detail)
            checks.append(("%s ON: %r fails%s" % (sw, n, " naming it" if named else ""), good, detail[:120]))
    seen_fns = []
    for sw, (fn, names, _n) in sorted(ROWS.items()):
        if fn in seen_fns:
            continue
        seen_fns.append(fn)
        got = run(fn, off)
        for n in sorted({x for s2, (f2, ns, _m) in ROWS.items() if f2 is fn for x in ns}):
            ok, detail = got.get(n, (None, "row not produced"))
            checks.append(("OFF: %r passes" % n, ok is True, detail[:120]))
    covered = {sw for sw, _p in ons}
    checks.append(("every test-only switch has an ON image in this run",
                   set(tests) <= covered, "no ON image for %s" % sorted(set(tests) - covered)))
    fails = 0
    for name, ok, detail in checks:
        print("  %-4s %s%s" % ("ok" if ok else "FAIL", name, "" if ok else "  -- " + detail))
        fails += 0 if ok else 1
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
