#!/usr/bin/env python3
"""Tests that refusal_pairs_runs.py and baseline_builds.ps1 have an image for every
switch the 12.3 refusal proof needs one for (known anomaly 8, gen-inhibit tester,
2026-10-30).

    py -3.14 gen-inhibit/tools/bench/test_refusal_pairs_runs.py

The switches are the test-only and stock-feature entries of the project's table
(firmware/vtrux_checks.json) and of each pinned shared component's, read from the
read-only checkout of the version firmware/main/idf_component.yml pins it at
(../shared-<version>/<path>/ beside the worktree). Every one needs an entry in
refusal_pairs_runs.ON, and every name in ON and CONTROL a build in baseline_builds.ps1
that sets its switch (a CONTROL build must leave it off). Until this test, the pinned
vtrux/wican table's WICAN_SLCAN_COMMANDS had neither, and nothing said so until a
post-port refusal-pair run failed on it.

Each check is also run on a broken input, which it must fail: a check that cannot
fail is not a check.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import refusal_pairs_runs as RP                                   # noqa: E402

PROJECT = os.path.dirname(os.path.dirname(HERE))                  # gen-inhibit/
WORKTREE = os.path.dirname(PROJECT)
MANIFEST = os.path.join(PROJECT, "firmware", "main", "idf_component.yml")
PS1 = os.path.join(HERE, "baseline_builds.ps1")
REFUSED = ("test-only", "stock feature off in this product")


def pins(text):
    """{component: (path, version)} from an idf_component.yml's vtrux/* dependencies."""
    out, name = {}, None
    for ln in text.splitlines():
        m = re.match(r"^  (vtrux/[\w.-]+):\s*$", ln)
        if m:
            name = m.group(1)
            out[name] = [None, None]
            continue
        if re.match(r"^  \S", ln):
            name = None
        m = re.match(r"""^\s{4}(path|version):\s*["']?([^"'\s#]+)""", ln)
        if name and m:
            out[name][0 if m.group(1) == "path" else 1] = m.group(2)
    return {n: tuple(v) for n, v in out.items()}


def refused(path):
    """The test-only and stock-feature switches of the table at path."""
    with open(path, encoding="utf-8") as fh:
        sw = json.load(fh).get("build_switches", {})
    return {s for s, e in sw.items() if e.get("class") in REFUSED}


def tables():
    """([(label, table path)], [problems]): the project's table, then each pinned
    component's that has one."""
    out = [("the project's table", os.path.join(PROJECT, "firmware", "vtrux_checks.json"))]
    problems = []
    for name, (path, version) in sorted(pins(open(MANIFEST, encoding="utf-8").read()).items()):
        if not path or not version:
            problems.append("%s: no path or version in %s" % (name, MANIFEST))
            continue
        folder = os.path.join(os.path.dirname(WORKTREE), "shared-%s" % version, path)
        if not os.path.isdir(folder):
            problems.append("%s: no checkout at %s" % (name, folder))
            continue
        t = os.path.join(folder, "vtrux_checks.json")
        if os.path.isfile(t):
            out.append(("%s's table" % name, t))
    return out, problems


def builds(text):
    """{name: [flags]} from baseline_builds.ps1's $defs."""
    out = {}
    for m in re.finditer(r'^\s*"(\w+)"\s*=\s*@\(([^)]*)\)', text, re.M):
        out[m.group(1)] = re.findall(r'"([^"]+)"', m.group(2))
    return out


def sets(flags, sw):
    return any(re.fullmatch(r"-D%s=(ON|1)" % re.escape(sw), f) for f in flags)


def missing_on(switches, on):
    return sorted(s for s in switches if s not in on)


def bad_builds(on, control, defs):
    """What is wrong with ON and CONTROL against baseline_builds.ps1's builds."""
    bad = []
    for sw, n in sorted(on.items()):
        if n not in defs:
            bad.append("ON %s: no build %r" % (sw, n))
        elif not sets(defs[n], sw):
            bad.append("ON %s: build %r does not set it" % (sw, n))
    for sw, n in sorted(control.items()):
        if n not in defs:
            bad.append("CONTROL %s: no build %r" % (sw, n))
        elif sets(defs[n], sw):
            bad.append("CONTROL %s: build %r sets it" % (sw, n))
    if "man" not in defs or defs["man"]:
        bad.append("no plain 'man' build (the OFF image)")
    return bad


def main():
    checks = []
    tabs, problems = tables()
    checks.append(("every pinned shared component has a checkout", not problems, problems))
    checks.append(("vtrux/wican's table is among them", any("vtrux/wican's" in l for l, _p in tabs),
                   [l for l, _p in tabs]))
    switches = set()
    for label, t in tabs:
        switches |= refused(t)
    checks.append(("WICAN_SLCAN_COMMANDS is a switch to prove", "WICAN_SLCAN_COMMANDS" in switches,
                   sorted(switches)))
    gone = missing_on(switches, RP.ON)
    checks.append(("every test-only switch and stock feature has an ON image name", not gone, gone))
    stale = sorted(set(RP.ON) - switches)
    checks.append(("no ON entry for a switch no table lists", not stale, stale))
    defs = builds(open(PS1, encoding="utf-8").read())
    bad = bad_builds(RP.ON, RP.CONTROL, defs)
    checks.append(("every ON and CONTROL name is a baseline_builds.ps1 build that sets (or leaves off) "
                   "its switch", not bad, bad))
    # the same checks on broken inputs, each of which must fail
    on = dict(RP.ON)
    on.pop("WICAN_SLCAN_COMMANDS", None)
    checks.append(("broken: ON without SLCAN is caught", missing_on(switches, on) == ["WICAN_SLCAN_COMMANDS"],
                   missing_on(switches, on)))
    d2 = dict(defs)
    d2.pop(RP.ON.get("WICAN_SLCAN_COMMANDS", "fslcan"), None)
    checks.append(("broken: no SLCAN build is caught", any("WICAN_SLCAN_COMMANDS" in b for b in
                                                           bad_builds(RP.ON, RP.CONTROL, d2)), ""))
    d3 = dict(defs, fslcan=["-DWICAN_FEATURE_MQTT=ON"])
    checks.append(("broken: an SLCAN build that sets another switch is caught",
                   any("does not set it" in b for b in bad_builds(RP.ON, RP.CONTROL, d3)), ""))
    d4 = dict(defs, fmqttelm=defs.get("fmqttelm", []) + ["-DWICAN_FEATURE_AUTOPID=ON"])
    checks.append(("broken: a CONTROL build that sets its switch is caught",
                   any("CONTROL" in b for b in bad_builds(RP.ON, RP.CONTROL, d4)), ""))
    p = pins('dependencies:\n  vtrux/x:\n    git: g\n    path: shared/x\n    version: "1.2"\n  idf:\n'
             '    version: ">=4.1.0"\n')
    checks.append(("pins() reads a component's path and version and stops at the next entry",
                   p == {"vtrux/x": ("shared/x", "1.2")}, p))
    fails = 0
    for label, ok, detail in checks:
        print("  %-4s %s%s" % ("ok" if ok else "FAIL", label, "" if ok else "  -- " + str(detail)[:160]))
        fails += not ok
    print("PASS" if not fails else "FAIL: %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
