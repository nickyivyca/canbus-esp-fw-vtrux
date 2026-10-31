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

The tables' own rules (known anomaly 8, 2026-10-30): besides the rows build_output.py
writes, each switch's refusal rules in every table that lists it are judged as
shared/tools R20 reads them (build_output.judge_rule). On the switch's --on image every
rule must FAIL, on --off and on its --control every rule must PASS. A project_row rule
must name a row this run produced, failing on the --on image (judge_rule asks only that
it was produced). A switch whose only rows are its table's (WICAN_SLCAN_COMMANDS: no row
of build_output.py names it) is in ROWS with no check function. A test-only switch or
stock feature with no rules in a table that lists it fails the run.
"""

import os
import subprocess
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
    # vtrux/wican's SLCAN command parser (W6): no row of build_output.py's names it, so
    # its rows are its table's rule alone, judged below (known anomaly 8, 2026-10-30).
    "WICAN_SLCAN_COMMANDS": (None, (), False),
}
# switch -> the switches its --control image must carry: their rows must FAIL on it,
# naming them, so the product image (which passes AutoPID's rows too) cannot stand in.
NEEDS_CONTROL = {"WICAN_FEATURE_AUTOPID": ("WICAN_FEATURE_MQTT", "WICAN_FEATURE_ELM327")}


def tables(off):
    """([(label, {switch: entry})] for the project's table and each shared component's
    that has one, from the copies the build of `off` downloaded; [problems]). An entry
    is build_switches.load_table()'s (class, kind, reason, refusal rules)."""
    out = [("project table", BS.SWITCHES)]
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
        out.append(("%s table" % name, sw))
    return out, problems


def table_switches(off):
    """({switch: class} of every test-only and stock-feature entry, the project's table's
    and each shared component's, from the copies the build of `off` downloaded;
    [problems])."""
    tabs, problems = tables(off)
    out = {}
    for _label, sw in tabs:
        out.update({s: e[0] for s, e in sw.items() if e[0] in (BS.TEST, BS.STOCK)})
    return out, problems


def table_rules(tabs):
    """{switch: [(table label, rule)]} of every test-only and stock-feature entry in the
    tables; a switch with no rules in a table that lists it gets (label, None)."""
    out = {}
    for label, sw in tabs:
        for s, e in sorted(sw.items()):
            if e[0] not in (BS.TEST, BS.STOCK):
                continue
            rules = e[3] if isinstance(e[3], list) and e[3] else [None]
            out.setdefault(s, []).extend((label, r) for r in rules)
    return out


def rule_label(rule):
    if not (isinstance(rule, dict) and len(rule) == 1):
        return "malformed %r" % (rule,)
    (k, v), = rule.items()
    return "%s %s" % (k, v if isinstance(v, str) else ", ".join(map(str, v)))


def image_facts(path):
    """(image bytes, the ELF's defined symbols, project name), each None where it cannot
    be read; judge_rule then has no reference, which is never a pass here."""
    try:
        img = open(path, "rb").read()
    except OSError:
        return None, None, None
    d = BO.app_desc(path)
    syms = None
    elf = os.path.splitext(path)[0] + ".elf"
    from elf_checks import find_nm
    exe = find_nm()
    if exe and os.path.exists(elf):
        r = subprocess.run((exe, "--defined-only", elf), capture_output=True, text=True)
        if r.returncode == 0:
            syms = BO.defined_symbols(r.stdout)
    return img, syms, d[1] if d else None


def judge_table_rule(rule, facts, produced, want):
    """(good, detail) for one table rule on one image: want False, the rule must FAIL
    (the switch's own image); want True, it must PASS. produced: {row: ok} of the rows
    this run's checks wrote on the image."""
    if not (isinstance(rule, dict) and len(rule) == 1):
        return False, "no rule, or a malformed one: %r" % (rule,)
    (k, v), = rule.items()
    if k == "project_row":
        ok = produced.get(v)
        return ok is want, "row %r %s" % (v, {True: "passes", False: "fails", None: "was not produced"}[ok])
    img, syms, project = facts
    ok, detail = BO.judge_rule(rule, img, syms, project, produced)
    return ok is want, detail


def run(fn, path):
    """{row: (ok, detail)} that fn writes on path; none for a switch proved by its
    table rules alone (fn None, e.g. WICAN_SLCAN_COMMANDS)."""
    if fn is None:
        return {}
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
    tabs, problems = tables(off)
    classes, _p = table_switches(off)
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
        if fn is None or fn in seen_fns:
            continue
        seen_fns.append(fn)
        got = run(fn, off)
        for n in sorted({x for s2, (f2, ns, _m) in ROWS.items() if f2 is fn for x in ns}):
            ok, detail = got.get(n, (None, "row not produced"))
            checks.append(("OFF: %r passes" % n, ok is True, detail[:120]))
    # The tables' own rules (known anomaly 8): each FAILs on its switch's --on image and
    # PASSes on --off and on the switch's --control.
    rules = table_rules(tabs)
    cache = {}

    def facts(path):
        if path not in cache:
            produced = {}
            for fn in {f for f, _n, _m in ROWS.values() if f is not None}:
                produced.update({n: ok for n, (ok, _d) in run(fn, path).items()})
            cache[path] = (image_facts(path), produced)
        return cache[path]

    def judge_all(sw, path, want, what):
        f, produced = facts(path)
        for label, rule in rules.get(sw, []):
            good, detail = judge_table_rule(rule, f, produced, want)
            checks.append((what % ("%s rule %s" % (label, rule_label(rule))), good, str(detail)[:120]))

    for sw, path in ons:
        judge_all(sw, path, False, sw + " ON: %s fails")
    for sw, path in controls:
        judge_all(sw, path, True, sw + " CONTROL: %s passes")
    for sw in sorted(rules):
        judge_all(sw, off, True, "OFF: " + sw + " %s passes")
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
