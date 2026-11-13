#!/usr/bin/env python3
"""shared/tools R20 for the interposer: each test-only switch's refusal rules
shown failing on builds with the switch on and passing on the release build
without it.

    py -3.14 -B charge-interposer/fw_harness/refusal_pairs.py \
        --off <truck release build folder> --on <label>=<build folder> [--on ...]

Tester, 2026-11-03 (port step 2a; the charge-interposer reviewer's
acceptance of the plan that day). A build folder is an ESP-IDF build folder
(the one holding project_description.json). The rules are the project's
table's, firmware/vtrux_checks.json, and they are judged by the pinned
shared/tools' own R20 code (vtrux_checks.switches: collect() reads the
build's image, ELF and project name, judge_rule() judges one rule, judge()
the whole check), from the checkout firmware/shared_pins.json pins, after
that pin's own check (vtrux_build.pin.check). Nothing here re-implements a
rule.

OFF is the release stand-in: interposer_truck, built by the release tooling
(charge-interposer/vtrux_tools.py release). On it the whole R20 check must
pass, and so must every refusal rule of every test-only switch, each listed.
The table is the OFF build's own project's (its project_path), so the rules
judged are the product commit's, not this harness tree's.

Each ON label is a build with a switch on, and EXPECT says which rules must
FAIL on it:
  bridge          the bench image: serial on, so INTP_NO_SERIAL's strings
                  and its "bridge" name token
  witness         bridge + INTP_ORDER_WITNESS: the strings, the "witness"
                  token, and the witness's ELF symbols
  selftest, diag  the other bench images: each its own name token
  trace           bridge + INTP_TX_TRACE (a link that overflowed DRAM,
                  -Wl,--noinhibit-exec: no runnable image, but an ELF and a
                  .bin): the trace's ELF symbols, and bridge's strings and token
  bridge-release  the release tooling's build of a commit whose CMake
                  default variant is bridge (user, 2026-11-03, release route
                  (B)): the "bridge" name token, the guard against a release
                  that comes out as another variant, and the strings
Every rule of every test-only switch must be expected to fail on at least
one label, every label must be given, and each ON build's project name must
be the variant its label says. A build that cannot be read (no ELF, no
image) is a failure of the proof, never a pass.

Exit 0 only if every check holds; the last line is PASS or FAIL: <count>.
refusal_pairs_test.py tests the judging on synthetic builds (--can-fail).
"""

import os
import sys

# A __pycache__ written into the pinned checkout would change its R7 digest.
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
FW = os.path.join(CODE, "firmware")
TABLE = os.path.join(FW, "vtrux_checks.json")

TEST_ONLY = "test-only"
STRINGS = "bin_strings_absent"
TOKEN = "project_name_token_absent"
SYMBOLS = "elf_symbols_absent"

# label -> (the variant its project name must say, {switch: [rule id]})
EXPECT = {
    "bridge": ("bridge", {"INTP_NO_SERIAL": [STRINGS, TOKEN + " bridge"]}),
    "witness": ("witness", {"INTP_NO_SERIAL": [STRINGS, TOKEN + " witness"],
                            "INTP_ORDER_WITNESS": [SYMBOLS]}),
    "selftest": ("selftest", {"INTP_NO_SERIAL": [TOKEN + " selftest"]}),
    "diag": ("diag", {"INTP_NO_SERIAL": [TOKEN + " diag"]}),
    "trace": ("bridge", {"INTP_TX_TRACE": [SYMBOLS],
                         "INTP_NO_SERIAL": [STRINGS, TOKEN + " bridge"]}),
    "bridge-release": ("bridge", {"INTP_NO_SERIAL": [TOKEN + " bridge",
                                                     STRINGS]}),
}
OFF_VARIANT = "truck"


def rule_id(rule):
    """A rule's id in EXPECT: its kind, and for a name token its token."""
    if not (isinstance(rule, dict) and len(rule) == 1):
        return "malformed %r" % (rule,)
    (k, v), = rule.items()
    return "%s %s" % (k, v.strip("_")) if k == TOKEN else k


def test_only_rules(table):
    """{switch: [(rule id, rule)]} of every test-only entry of a table
    ({switch: entry}); a test-only entry with no rules gets []."""
    out = {}
    for s, e in sorted(table.items()):
        if isinstance(e, dict) and e.get("class") == TEST_ONLY:
            out[s] = [(rule_id(r), r) for r in (e.get("refusal") or [])]
    return out


def checks(table, off, ons, judge_rule, expect=None):
    """The checks, [(name, ok, detail)]. Pure: no files.

    table      the project's {switch: entry}
    off        {"outputs": ..., "bad": [R20 failures]} or {"error": text}
    ons        {label: {"outputs": ...} or {"error": text}}
    judge_rule shared/tools' (rule, outputs) -> (ok, detail)
    """
    expect = EXPECT if expect is None else expect
    out = []
    rules = test_only_rules(table)

    def add(name, ok, detail=""):
        out.append((name, bool(ok), str(detail)[:160]))

    # the table and EXPECT agree
    for s, rs in sorted(rules.items()):
        add("%s has refusal rules" % s, rs, "a test-only entry with none")
        for rid, _r in rs:
            where = [lb for lb, (_v, e) in sorted(expect.items())
                     if rid in e.get(s, [])]
            add("%s rule %s is expected to fail on an ON build" % (s, rid),
                where, "no label expects it")
    for lb, (_v, e) in sorted(expect.items()):
        for s, rids in sorted(e.items()):
            known = [rid for rid, _r in rules.get(s, [])]
            for rid in rids:
                add("%s: %s rule %s is in the table" % (lb, s, rid),
                    rid in known, "not a test-only rule of %s" % s)
    # every label given, no other
    for lb in sorted(expect):
        add("ON build %s given" % lb, lb in ons, "no --on %s=..." % lb)
    for lb in sorted(set(ons) - set(expect)):
        add("ON label %s is known" % lb, False, "not in EXPECT")
    # OFF
    if "error" in off or "outputs" not in off:
        add("OFF build read", False, off.get("error", "no outputs"))
    else:
        o = off["outputs"]
        name = "interposer_%s" % OFF_VARIANT
        add("OFF is %s" % name, o.get("project_name") == name,
            "its project name is %r" % o.get("project_name"))
        bad = off.get("bad")
        add("OFF passes the whole R20 check", bad == [],
            "; ".join("%s %s %s: %s" % b for b in (bad or [])[:3]) if bad
            else "no R20 result")
        for s, rs in sorted(rules.items()):
            for rid, r in rs:
                ok, detail = judge_rule(r, o)
                add("OFF: %s rule %s passes" % (s, rid), ok is True, detail)
    # each ON
    for lb in sorted(set(ons) & set(expect)):
        variant, e = expect[lb]
        b = ons[lb]
        if "error" in b or "outputs" not in b:
            add("ON %s read" % lb, False, b.get("error", "no outputs"))
            continue
        o = b["outputs"]
        name = "interposer_%s" % variant
        add("ON %s is %s" % (lb, name), o.get("project_name") == name,
            "its project name is %r" % o.get("project_name"))
        for s, rids in sorted(e.items()):
            byid = dict(rules.get(s, []))
            for rid in rids:
                if rid not in byid:
                    continue                  # reported above
                ok, detail = judge_rule(byid[rid], o)
                add("ON %s: %s rule %s fails" % (lb, s, rid), ok is False,
                    detail)
    return out


# ---- reading real builds through the pinned shared/tools ------------------
def load_switches():
    """vtrux_checks.switches from the pinned shared/tools, after the pin's
    own check. SystemExit naming what does not hold."""
    if CODE not in sys.path:
        sys.path.insert(0, CODE)
    import vtrux_tools
    tools = vtrux_tools.tools_dir()
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from vtrux_build import pin
    try:
        pin.check(FW, tools)
    except pin.PinError as e:
        raise SystemExit("refusal_pairs: the shared/tools pin: %s" % e)
    from vtrux_checks import switches
    return switches


def read_build(SW, build, whole=False):
    """{"outputs": ..., ["bad": R20 failures]} or {"error": text}."""
    try:
        facts = SW.collect(build)
    except Exception as e:                       # noqa: BLE001
        return {"error": "%s: %s: %s" % (build, type(e).__name__, e)}
    r = {"outputs": facts["outputs"], "root": facts["root"]}
    if whole:
        r["bad"] = SW.judge(facts)
    return r


def project_table(SW, root):
    """The table of the project the OFF build was made from (the product
    commit's own firmware/vtrux_checks.json, which its R20 check reads),
    not this harness tree's."""
    return SW.read_table(root, SW.PROJECT)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    off, ons = None, {}
    while args:
        a = args.pop(0)
        if a == "--off" and args:
            off = args.pop(0)
        elif a == "--on" and args and "=" in args[0]:
            lb, path = args.pop(0).split("=", 1)
            if lb in ons:
                raise SystemExit("refusal_pairs: --on %s given twice" % lb)
            ons[lb] = path
        else:
            raise SystemExit(__doc__.split("\n\n")[1])
    if off is None:
        raise SystemExit("refusal_pairs: no --off build")
    SW = load_switches()
    offb = read_build(SW, off, whole=True)
    if "root" not in offb:
        print("  FAIL OFF build read  -- %s" % offb.get("error"))
        print("FAIL: 1")
        return 1
    try:
        table = project_table(SW, offb["root"])
    except SW.SwitchesError as e:
        print("  FAIL the OFF build's project table read  -- %s" % e)
        print("FAIL: 1")
        return 1
    print("table: %s" % os.path.join(offb["root"], SW.DATA_FILE))
    res = checks(table, offb,
                 {lb: read_build(SW, p) for lb, p in ons.items()},
                 SW.judge_rule)
    fails = 0
    for name, ok, detail in res:
        print("  %-4s %s%s" % ("ok" if ok else "FAIL", name,
                               "" if ok else "  -- " + detail))
        fails += 0 if ok else 1
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
