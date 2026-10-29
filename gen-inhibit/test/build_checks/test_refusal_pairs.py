#!/usr/bin/env python3
"""Spec 12.3 item 3: each test-only switch's refusal rows, and each stock feature's
(off in this product, class added 2026-10-16), shown failing on an image built with
the switch on and passing on one built without it.

    python3 test_refusal_pairs.py --off <off.bin> --on SWITCH=<on.bin> [--on ...] [--control SWITCH=<img> ...]

--control: for a switch that cannot be built on alone, the image that differs from
its --on image by that switch only; the switch's rows must PASS on it. AutoPID needs
ELM327 and MQTT (wican_features.h), so its --on is MQTT+ELM327+AUTOPID and its
--control MQTT+ELM327, which the generic --off image (everything off) cannot stand in
for: there the AutoPID row passing would not show that AutoPID's switch is what
moves it.
So the control must also be shown to carry what the switch needs (NEEDS_CONTROL):
on it, MQTT's and ELM327's rows must FAIL, naming them.

Both images of a pair should be built from the same commit, from a clean tree. Each
must have its .elf beside it. For each --on image, every row the switch's table entry
covers must FAIL. Where a row is shared by several switches, its detail must name this
switch, so the instrument table's row can't pass the proof on another instrument's
marker. On the --off image every refusal row of every test-only switch must PASS.
A NO REF anywhere is a failure of the proof, not a pass: a missing ELF proves nothing.

The rows per switch are the ones build_output.py writes for it. A switch missing from
ROWS fails the run, so a new test-only switch can't be skipped here by omission.

The switches are the test-only and stock-feature entries of the project's table and,
since port step 1a, of each shared component's own table (shared/tools R20): the stock
features are vtrux/wican's. The component tables are read from the copies the --off
image's build downloaded (build_output.shared_folders()); a shared component with no
copy there fails the run, since its switches would otherwise drop out unseen.
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
    # Stock features, off in this product (2026-10-16). The three with symbols and
    # strings name their switch in the detail, image and ELF; RealDash's row counts
    # the start-up's can_enable call sites, which any boot branch adds to, so its
    # detail cannot name it, and it has no image row.
    "WICAN_FEATURE_MQTT": (BO.check_no_stock_features,
                           ("no MQTT feature in the image", "no MQTT feature in the ELF"), True),
    "WICAN_FEATURE_ELM327": (BO.check_no_stock_features,
                             ("no ELM327 feature in the image", "no ELM327 feature in the ELF"), True),
    "WICAN_FEATURE_AUTOPID": (BO.check_no_stock_features,
                              ("no AutoPID feature in the image", "no AutoPID feature in the ELF"), True),
    "WICAN_FEATURE_REALDASH": (BO.check_no_stock_features, (BO.REALDASH_ROW,), False),
}
# switch -> the switches its --control image must carry: their rows must FAIL on it,
# naming them, so the product image (which passes AutoPID's rows too) cannot stand in.
NEEDS_CONTROL = {"WICAN_FEATURE_AUTOPID": ("WICAN_FEATURE_MQTT", "WICAN_FEATURE_ELM327")}


def table_switches(off):
    """({switch: class} of every test-only and stock-feature entry, the project's table's
    and each shared component's, from the copies the build of `off` downloaded;
    [problems])."""
    out = {s: e[0] for s, e in BS.SWITCHES.items() if e[0] in (BS.TEST, BS.STOCK)}
    problems = []
    project = BO.project_path(os.path.dirname(os.path.abspath(off)))
    shared = BO.shared_folders(project)
    if not shared:
        problems.append("%s names no shared component" % os.path.join(project, BO.BUILD_FILE))
    for name, folder in sorted(shared.items()):
        p = os.path.join(folder, os.path.basename(BS.TABLE_FILE))
        if not os.path.isdir(folder):
            problems.append("no copy of %s at %s" % (name, folder))
            continue
        if not os.path.isfile(p):
            continue                      # a component that tests no switch has no table
        try:
            sw, _tr = BS.load_table(p)
        except (OSError, ValueError) as e:
            problems.append("%s: %s" % (p, e))
            continue
        out.update({s: e[0] for s, e in sw.items() if e[0] in (BS.TEST, BS.STOCK)})
    return out, problems


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
    controls = [args[k + 1].split("=", 1) for k, a in enumerate(args) if a == "--control"]
    checks = []
    classes, problems = table_switches(off)
    checks.append(("every shared component's table could be read", not problems, "; ".join(problems)))
    tests = sorted(classes)
    checks.append(("every test-only switch and stock feature in the table has rows here",
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
    for sw, path in controls:
        if sw not in ROWS:
            checks.append(("%s is a known switch" % sw, False, "not in ROWS"))
            continue
        fn, names, _named = ROWS[sw]
        got = run(fn, path)
        for n in names:
            ok, detail = got.get(n, (None, "row not produced"))
            checks.append(("%s CONTROL: %r passes" % (sw, n), ok is True, detail[:120]))
        for dep in NEEDS_CONTROL.get(sw, ()):
            dfn, dnames, dnamed = ROWS[dep]
            dgot = got if dfn is fn else run(dfn, path)
            for n in dnames:
                ok, detail = dgot.get(n, (None, "row not produced"))
                checks.append(("%s CONTROL carries %s: %r fails%s" % (sw, dep, n, " naming it" if dnamed else ""),
                               ok is False and (not dnamed or dep in detail), detail[:120]))
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
    checks.append(("every test-only switch and stock feature has an ON image in this run",
                   set(tests) <= covered, "no ON image for %s" % sorted(set(tests) - covered)))
    need = set(NEEDS_CONTROL) - {sw for sw, _p in controls}
    checks.append(("every switch that cannot be built alone has a CONTROL image in this run",
                   not need, "no --control for %s" % sorted(need)))
    fails = 0
    for name, ok, detail in checks:
        print("  %-4s %s%s" % ("ok" if ok else "FAIL", name, "" if ok else "  -- " + detail))
        fails += 0 if ok else 1
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
