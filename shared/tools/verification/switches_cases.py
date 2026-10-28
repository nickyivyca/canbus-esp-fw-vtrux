#!/usr/bin/env python3
"""R20 build-switch check (vtrux_checks switches) on real builds, shown passing and failing.

    python -B switches_cases.py --root <short folder in scratch/> --out <run folder> --base <gen-inhibit commit>
                                [--cases a,b] [--jobs N]
    python -B switches_cases.py --self-test

Run from an exported ESP-IDF shell, with its Python, like release_cases.py, whose fixture
(gen-inhibit at --base, cloned under --root, plus vtrux_build.json and reproducible build on)
and plumbing it reuses; the fixture is made here if --root has none. --base must be a
gen-inhibit commit whose vtrux_checks.json holds its build_switches table and whose
build_output.py writes vtrux_project_rows.json, since the check reads both from the tree.

Each case builds, runs the project's own harness (test/build_checks/build_output.py --bin on
the image, which writes build/vtrux_project_rows.json) where the case needs project rows, then
runs the check and judges its exit code and the failures it names, (scope, symbol, kind),
against what the case planted. It never judges wording beyond those names. A case whose
trigger does not show without the check's reader (ninja's deps log, CMakeCache.txt, the
rows file) is VOID.

    release_pass      a release build of the fixture: passes
    stale_entry       the same build, the table with an entry nothing tests: fails, naming it
    component_table   the same build, a table planted in managed_components/espressif__mdns/
                      listing all but one symbol the build read there, plus one it does not
                      test: fails, naming both under that component
    shared_no_table   the same build, vtrux_build.json naming espressif/mdns as shared, no table
                      there: fails, naming it
    planted           a release build of a commit that adds `#if R20_PLANTED_C` to main/main.c,
                      `#ifdef R20_UNCOMPILED` to main/ftp.h (not compiled), and to
                      main/CMakeLists.txt `if(R20_PLANTED_CM)` and a `set(R20_OWN_CM 1)` before
                      `if(R20_OWN_CM)`: fails, naming R20_PLANTED_C and R20_PLANTED_CM only
    on_<switch>       a build with one test-only or stock-off switch on (idf.py -D, a bench
                      build: the check reads any build folder): fails on that switch's refusal
                      rules. Other entries may be named too, as refusals only, never as a pass
                      (R20 reading (a), gen-inhibit reviewer, 2026-10-24: a row may refuse more
                      images than its own switch's); the detail names them. The R20 "shown
                      failing ... and passing" evidence; release_pass is the image built without
    on_autopid, control_autopid
                      AutoPID cannot be built without MQTT and ELM327: its ON build has all
                      three; the control (MQTT and ELM327) must fail on those two and must not
                      name AutoPID

Before each case it checks the bench lock, the "No builds" line and running simulators, as
release_cases.py does.
"""

import json
import os
import platform
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_cases as RC  # noqa: E402

TOOLS = RC.TOOLS
_FAIL = re.compile(r"^    (.+?): (\S+) (unlisted|stale|refusal|no table): ", re.M)
SWITCH_ON = {   # case -> (-D defines, the switches whose refusal rules must fail)
    "on_rxq": (("GI_INSTRUMENT_RXQ=1",), ("GI_INSTRUMENT_RXQ",)),
    "on_txabort": (("GI_INSTRUMENT_TXABORT=1",), ("GI_INSTRUMENT_TXABORT",)),
    "on_abortwin": (("GI_INSTRUMENT_ABORTWIN=1",), ("GI_INSTRUMENT_ABORTWIN",)),
    "on_otafault": (("OTA_HEALTH_FAULT_INJECT=1",), ("OTA_HEALTH_FAULT_INJECT",)),
    "on_restart": (("GI_TEST_RESTART_QUIESCE=1",), ("GI_TEST_RESTART_QUIESCE",)),
    "on_mqtt": (("WICAN_FEATURE_MQTT=ON",), ("WICAN_FEATURE_MQTT",)),
    "on_elm327": (("WICAN_FEATURE_ELM327=ON",), ("WICAN_FEATURE_ELM327",)),
    "on_realdash": (("WICAN_FEATURE_REALDASH=ON",), ("WICAN_FEATURE_REALDASH",)),
    "on_autopid": (("WICAN_FEATURE_MQTT=ON", "WICAN_FEATURE_ELM327=ON", "WICAN_FEATURE_AUTOPID=ON"),
                   ("WICAN_FEATURE_MQTT", "WICAN_FEATURE_ELM327", "WICAN_FEATURE_AUTOPID")),
    "control_autopid": (("WICAN_FEATURE_MQTT=ON", "WICAN_FEATURE_ELM327=ON"),
                        ("WICAN_FEATURE_MQTT", "WICAN_FEATURE_ELM327")),
}
PROJECT = "the project"
MDNS = "espressif__mdns"


# ---------- judges (pure; --self-test runs them on fakes) ----------

def named(out):
    return sorted(set(_FAIL.findall(out)))


def judge(rc, out, want, extra_refusals=False, never=()):
    """(verdict, detail): want is the sorted list of (scope, symbol, kind) the check must name.
    With extra_refusals, it may also name other entries as refusals, but none in never."""
    if rc not in (0, 1):
        return "ERROR", "the check exited %d" % rc
    if "cannot read the build" in out:
        return "FAIL", "the check could not read the build: %s" % out.strip().splitlines()[-1][:200]
    got = named(out)
    if rc != (1 if want else 0):
        return "FAIL", "the check exited %d, wanted %d; it names %s" % (rc, 1 if want else 0, got)
    if ("PASS R20" in out) != (rc == 0):
        return "FAIL", "exit %d but %s PASS R20" % (rc, "prints" if rc else "does not print")
    extra = [g for g in got if g not in want]
    allowed = extra_refusals and all(k == "refusal" and sym not in never for _sc, sym, k in extra)
    if [w for w in want if w not in got] or (extra and not allowed):
        return "FAIL", "the check names %s, wanted %s" % (got, want)
    also = ("; also names " + "; ".join("%s %s %s" % g for g in extra)) if extra else ""
    return "PASS", "exit %d%s%s" % (rc, (", names " + "; ".join("%s %s %s" % w for w in want)) if want else "", also)


def scope_unlisted(out, scope):
    """The symbols the check names unlisted under scope."""
    return sorted(sym for sc, sym, k in named(out) if sc == scope and k == "unlisted")


def refusals(switches):
    return sorted((PROJECT, s, "refusal") for s in switches)


def self_test():
    ok = True
    out1 = ("R20 build switches: build\nFAIL R20: 2 failure(s)\n"
            "    the project: R20_X unlisted: main/main.c:3\n"
            "    espressif__mdns: MDNS_Y stale: listed, no longer tested\n")
    w1 = [(MDNS, "MDNS_Y", "stale"), (PROJECT, "R20_X", "unlisted")]
    out2 = ("FAIL R20: 2 failure(s)\n    the project: GI_INSTRUMENT_RXQ refusal: the image carries '\"rxq\":{'\n"
            "    the project: GI_INSTRUMENT_RXQ refusal: the ELF defines rxq_reset\n")
    rd = "    the project: WICAN_FEATURE_REALDASH refusal: project row 'no boot-time bus enable in app_main': FAIL\n"
    for label, args, want in (
            ("passes as wanted", (0, "R20 ...\nPASS R20\n", []), "PASS"),
            ("fails as wanted", (1, out1, w1), "PASS"),
            ("two lines for one refusal count once", (1, out2, refusals(["GI_INSTRUMENT_RXQ"])), "PASS"),
            ("passes when it should fail", (0, "PASS R20\n", w1), "FAIL"),
            ("fails when it should pass", (1, out1, []), "FAIL"),
            ("names one too few", (1, out1, w1 + [(PROJECT, "R20_Z", "unlisted")]), "FAIL"),
            ("names one too many", (1, out1 + "    the project: R20_Z unlisted: a:1\n", w1), "FAIL"),
            ("names the wrong scope", (1, out1.replace("espressif__mdns:", "the project:"), w1), "FAIL"),
            ("names the wrong kind", (1, out1.replace("MDNS_Y stale", "MDNS_Y unlisted"), w1), "FAIL"),
            ("exit 0 without PASS", (0, "R20 ...\n", []), "FAIL"),
            ("exit 1 with PASS", (1, out1 + "PASS R20\n", w1), "FAIL"),
            ("exit 0 naming the failures, with PASS", (0, out1 + "PASS R20\n", w1), "FAIL"),
            ("unreadable build", (1, "FAIL R20: cannot read the build or its tables: x\n", w1), "FAIL"),
            ("crashes", (2, "Traceback", []), "ERROR"),
            ("no table named", (1, "FAIL R20: 1\n    espressif/mdns: - no table: named\n",
                                [("espressif/mdns", "-", "no table")]), "PASS"),
            ("one too many refusals, exact", (1, out2 + rd, refusals(["GI_INSTRUMENT_RXQ"])), "FAIL"),
            ("an extra refusal, allowed", (1, out2 + rd, refusals(["GI_INSTRUMENT_RXQ"]), True), "PASS"),
            ("an extra refusal in never", (1, out2 + rd, refusals(["GI_INSTRUMENT_RXQ"]), True,
                                           ("WICAN_FEATURE_REALDASH",)), "FAIL"),
            ("an extra unlisted, refusals allowed", (1, out2 + "    the project: R20_Z unlisted: a:1\n",
                                                     refusals(["GI_INSTRUMENT_RXQ"]), True), "FAIL"),
            ("only the extra, refusals allowed", (1, "FAIL R20: 1\n" + rd, refusals(["GI_INSTRUMENT_RXQ"]), True),
             "FAIL"),
            ("passes, refusals allowed", (0, "PASS R20\n", refusals(["GI_INSTRUMENT_RXQ"]), True), "FAIL")):
        v = judge(*args)[0]
        print("  %s judge(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    listing = ("  espressif__mdns: 14 tested, no table, not judged\nPASS R20\n", [])
    judged = ("  espressif__mdns: 3 tested, 1 listed\n    MDNS_A  shipping variant  x.c:1\n"
              "FAIL R20: 3 failure(s)\n    espressif__mdns: MDNS_B unlisted: x.c:2\n"
              "    espressif__mdns: MDNS_C unlisted: x.c:3\n    the project: R20_X unlisted: main/main.c:3\n"
              "    espressif__mdns: MDNS_R20_PROBE stale: listed, no longer tested\n", ["MDNS_B", "MDNS_C"])
    for label, (out, want) in (("no table: no listing", listing), ("probe table: its unlisted only", judged)):
        got = scope_unlisted(out, MDNS)
        print("  %s scope_unlisted(), %s: %s" % ("ok  " if got == want else "FAIL", label, got))
        ok &= got == want
    for label, args, want in (("a -D taken", ("X=1", "X:UNINITIALIZED=1\n"), True),
                              ("a bool ON", ("F=ON", "F:BOOL=ON\n"), True),
                              ("absent", ("X=1", "Y:BOOL=ON\n"), False),
                              ("a longer name", ("X=1", "XY:UNINITIALIZED=1\n"), False),
                              ("off", ("F=ON", "F:BOOL=OFF\n"), False)):
        got = cache_has(args[1], args[0])
        print("  %s cache_has(), %s: %s" % ("ok  " if got == want else "FAIL", label, got))
        ok &= got == want
    d = RC.I.scratch("switches-cases-selftest-harness")
    rel = os.path.join("test", "build_checks", "build_output.py")
    for label, root, proj in (("the tree at the root", "old", "old"),
                              ("gen-inhibit/firmware beside gen-inhibit/test", "gi", os.path.join("gi", "firmware"))):
        h = os.path.join(d, root, rel)
        os.makedirs(os.path.dirname(h))
        open(h, "w").close()
        got = harness(os.path.join(d, proj))
        good = os.path.normcase(os.path.normpath(got)) == os.path.normcase(h)
        print("  %s harness(), %s: %s" % ("ok  " if good else "FAIL", label, got))
        ok &= good
    print("\nself-test: %s" % ("every broken input caught" if ok else "FAILED"))
    return 0 if ok else 1


def cache_has(cache_text, define):
    """CMakeCache.txt text has the -D's name at its value (ON/1 alike, any type)."""
    name, value = define.split("=", 1)
    m = re.search(r"^%s:[A-Z]+=(.*)$" % re.escape(name), cache_text, re.M)
    truthy = ("1", "ON", "TRUE", "YES", "Y")
    return bool(m) and (m.group(1).strip().upper() in truthy) == (value.upper() in truthy)


# ---------- plumbing ----------

def check(proj, log):
    return RC.run([sys.executable, "-B", "-m", "vtrux_checks", "switches", "--build", os.path.join(proj, "build")],
                  cwd=proj, env=RC.idf_env(PYTHONPATH=TOOLS), log=log)


def harness(proj):
    """The project's build_output.py: in its test/ (the WiCAN tree at the root, as at d2e0bb5), or
    beside its firmware/ folder in gen-inhibit/test/ (port step 0d on)."""
    rel = os.path.join("test", "build_checks", "build_output.py")
    old = os.path.join(proj, rel)
    return old if os.path.exists(old) else os.path.join(os.path.dirname(os.path.abspath(proj)), rel)


def project_rows(proj, log):
    """The project's harness on the build's image: writes build/vtrux_project_rows.json. Returns
    the rows, or None."""
    bd = os.path.join(proj, "build")
    pd = json.load(open(os.path.join(bd, "project_description.json")))
    RC.run([sys.executable, "-B", harness(proj), "--bin", os.path.join(bd, pd["app_bin"])],
           cwd=proj, env=RC.idf_env(), log=log)
    p = os.path.join(bd, "vtrux_project_rows.json")
    return json.load(open(p)) if os.path.exists(p) else None


def deps_files(proj):
    """Project-relative files the build's deps log names (the case's own reading, ninja -t deps)."""
    out = set()
    for d, _dirs, files in os.walk(os.path.join(proj, "build")):
        if "build.ninja" in files and "CMakeCache.txt" in files:
            _rc, text = RC.run(["ninja", "-t", "deps"], cwd=d)
            for line in text.splitlines():
                if line[:1].isspace() and line.strip():
                    p = os.path.normcase(os.path.normpath(os.path.join(d, line.strip())))
                    root = os.path.normcase(os.path.normpath(proj))
                    if p.startswith(root + os.sep):
                        out.add(os.path.relpath(p, root).replace(os.sep, "/"))
    return out


def release_built(ctx, name, log, branch="fixture"):
    RC.reset(ctx.p1, "fixture", None if branch == "fixture" else branch)
    RC.rmtree(os.path.join(ctx.p1, "build"))
    rc, _ = RC.release(ctx, ctx.p1, log, jobs=ctx.jobs)
    if rc:
        RC.report(name, "VOID", "the release build failed (exit %d)" % rc, ctx.log)
    return rc == 0


def run_check(ctx, name, log, want, **kw):
    rc, out = check(ctx.p1, log)
    RC.report(name, *judge(rc, out, sorted(want), **kw), ctx.log)


# ---------- cases ----------

def c_release_pass(ctx, log):
    if not release_built(ctx, "release_pass", log):
        return
    rows = project_rows(ctx.p1, log)
    if not rows or rows.get("rows", {}).get("no boot-time bus enable in app_main") != "PASS":
        RC.report("release_pass", "VOID", "the project's rows file lacks a passing RealDash row: %s" % (
            (rows or {}).get("rows", {}).get("no boot-time bus enable in app_main")), ctx.log)
        return
    ctx.pass_built = True
    run_check(ctx, "release_pass", log, [])


def need_pass(ctx, name):
    if not getattr(ctx, "pass_built", False):
        RC.report(name, "VOID", "needs release_pass in the same batch", ctx.log)
        return False
    return True


def c_stale_entry(ctx, log):
    if not need_pass(ctx, "stale_entry"):
        return
    p = os.path.join(ctx.p1, "vtrux_checks.json")
    orig = open(p, "rb").read()
    try:
        d = json.loads(orig)
        d["build_switches"]["R20_NOT_TESTED"] = {"class": "shipping variant", "reason": "planted by switches_cases"}
        with open(p, "w", newline="\n") as f:
            json.dump(d, f, indent=2)
        run_check(ctx, "stale_entry", log, [(PROJECT, "R20_NOT_TESTED", "stale")])
    finally:
        open(p, "wb").write(orig)


def mdns_symbols(ctx, log):
    """The symbols the build tests in managed_components/espressif__mdns/: a probe table there
    listing one symbol nothing tests makes the check judge that folder, and it names every
    tested symbol unlisted (a component without a table gets no listing). Premise: that
    folder's files are in the deps log, and the check names the probe stale (it read the table)."""
    if not any(f.startswith("managed_components/%s/" % MDNS) for f in deps_files(ctx.p1)):
        return None
    p = os.path.join(ctx.p1, "managed_components", MDNS, "vtrux_checks.json")
    try:
        with open(p, "w", newline="\n") as f:
            json.dump({"build_switches": {"MDNS_R20_PROBE": {"class": "shipping variant", "reason": "probe"}}}, f)
        _rc, out = check(ctx.p1, log)
    finally:
        os.remove(p)
    if (MDNS, "MDNS_R20_PROBE", "stale") not in named(out):
        return None
    return scope_unlisted(out, MDNS)


def c_component_table(ctx, log):
    if not need_pass(ctx, "component_table"):
        return
    syms = mdns_symbols(ctx, log)
    if not syms or len(syms) < 2:
        RC.report("component_table", "VOID", "no mdns files in the deps log, or fewer than 2 symbols: %s" % syms,
                  ctx.log)
        return
    p = os.path.join(ctx.p1, "managed_components", MDNS, "vtrux_checks.json")
    try:
        t = {s: {"class": "not a switch", "kind": "library macro", "reason": "planted"} for s in syms[1:]}
        t["MDNS_R20_NOT_TESTED"] = {"class": "shipping variant", "reason": "planted"}
        with open(p, "w", newline="\n") as f:
            json.dump({"build_switches": t}, f, indent=2)
        run_check(ctx, "component_table", log, [(MDNS, syms[0], "unlisted"), (MDNS, "MDNS_R20_NOT_TESTED", "stale")])
    finally:
        if os.path.exists(p):
            os.remove(p)


def c_shared_no_table(ctx, log):
    if not need_pass(ctx, "shared_no_table"):
        return
    p = os.path.join(ctx.p1, "vtrux_build.json")
    orig = open(p, "rb").read()
    try:
        d = json.loads(orig)
        d["shared_components"] = ["espressif/mdns"]
        with open(p, "w", newline="\n") as f:
            json.dump(d, f)
        run_check(ctx, "shared_no_table", log, [("espressif/mdns", "-", "no table")])
    finally:
        open(p, "wb").write(orig)


def c_planted(ctx, log):
    RC.reset(ctx.p1, "fixture")
    RC.git(ctx.p1, "checkout", "-q", "-B", "r20-planted", "origin/fixture")

    def add(rel, text, after=None):
        f = os.path.join(ctx.p1, rel)
        s = open(f, newline="").read()
        s = s + text if after is None else s.replace(after, after + text, 1)
        with open(f, "w", newline="") as o:
            o.write(s)
    add("main/main.c", "\n#if R20_PLANTED_C\n#endif\n")
    add("main/ftp.h", "\n#ifdef R20_UNCOMPILED\n#endif\n")
    add("main/CMakeLists.txt", "\nif(R20_PLANTED_CM)\nendif()\nset(R20_OWN_CM 1)\nif(R20_OWN_CM)\nendif()\n")
    RC.git(ctx.p1, "commit", "-q", "-am", "r20 case planted")
    with open(log, "a") as fx:
        RC.normalise(ctx, ctx.p1, "planted", fx)
    if not release_built(ctx, "planted", log, branch="r20-planted"):
        return
    project_rows(ctx.p1, log)
    files = deps_files(ctx.p1)
    if "main/main.c" not in files or "main/ftp.h" in files:
        RC.report("planted", "VOID", "main.c compiled %s, ftp.h compiled %s" % (
            "main/main.c" in files, "main/ftp.h" in files), ctx.log)
        return
    run_check(ctx, "planted", log, [(PROJECT, "R20_PLANTED_C", "unlisted"), (PROJECT, "R20_PLANTED_CM", "unlisted")])


def c_switch_on(ctx, log, name):
    defines, failing = SWITCH_ON[name]
    RC.reset(ctx.p1, "fixture")
    RC.rmtree(os.path.join(ctx.p1, "build"))
    rc, _ = RC.idf(ctx, ctx.p1, *(["-D" + d for d in defines] + ["build"]), log=log)
    if rc:
        RC.report(name, "VOID", "the build failed (exit %d)" % rc, ctx.log)
        return
    cache = open(os.path.join(ctx.p1, "build", "CMakeCache.txt"), errors="replace").read()
    missing = [d for d in defines if not cache_has(cache, d)]
    rows = project_rows(ctx.p1, log)
    if missing or not rows:
        RC.report(name, "VOID", "not in CMakeCache.txt: %s; rows file %s" % (missing, "written" if rows else "absent"),
                  ctx.log)
        return
    never = () if name != "control_autopid" else ("WICAN_FEATURE_AUTOPID",)
    run_check(ctx, name, log, refusals(failing), extra_refusals=True, never=never)


def make_case(name):
    return lambda ctx, log: c_switch_on(ctx, log, name)


CASES = [("release_pass", c_release_pass), ("stale_entry", c_stale_entry), ("component_table", c_component_table),
         ("shared_no_table", c_shared_no_table), ("planted", c_planted)] + [(n, make_case(n)) for n in SWITCH_ON]


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--out")
    ap.add_argument("--base")
    ap.add_argument("--fixture")
    ap.add_argument("--cases")
    ap.add_argument("--jobs", type=int, default=None)
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.root and a.out and a.base) or "IDF_PATH" not in os.environ:
        ap.error("--root, --out and --base, from an exported ESP-IDF shell")
    ctx = RC.Ctx(a)
    ctx.log = os.path.join(ctx.out, "switches_driver.log")
    os.makedirs(ctx.out, exist_ok=True)
    os.makedirs(os.path.join(ctx.ri, "firmware"), exist_ok=True)
    names = a.cases.split(",") if a.cases else [n for n, _ in CASES]
    unknown = set(names) - {n for n, _ in CASES}
    if unknown:
        ap.error("unknown cases %s" % sorted(unknown))
    with open(ctx.log, "a", encoding="utf-8") as f:
        f.write("=== %s  host %s  %s  python %s\nIDF %s  tooling %s (%s)\nbase %s\ncases %s\n\n" % (
            time.strftime("%Y-%m-%dT%H:%M:%S"), platform.node(), platform.platform(), platform.python_version(),
            RC.run(["git", "-C", os.environ["IDF_PATH"], "describe", "--tags", "--dirty"])[1].strip(),
            RC.git(TOOLS, "rev-parse", "HEAD"), RC.git(TOOLS, "status", "--porcelain", "--", ".") or "clean",
            a.base, names))
    if not os.path.exists(ctx.src):
        if RC.bench_clear(ctx.log):
            RC.report("fixture", "STOPPED", "bench not clear", ctx.log)
            return 1
        os.makedirs(ctx.root, exist_ok=True)
        RC.make_fixture(ctx)
    RC.clones(ctx)
    for n, fn in CASES:
        if n not in names:
            continue
        why = RC.bench_clear(ctx.log)
        if why:
            RC.report(n, "STOPPED", "; ".join(why), ctx.log)
            break
        log = os.path.join(ctx.out, "switches_%s.log" % n)
        t0 = time.time()
        try:
            fn(ctx, log)
        except Exception as e:      # a case that crashes is reported, never a pass
            RC.report(n, "ERROR", repr(e)[:300], ctx.log)
        with open(ctx.log, "a") as f:
            f.write("%s took %.0f s\n\n" % (n, time.time() - t0))
    RC.reset(ctx.p1, "fixture")
    with open(os.path.join(ctx.out, "switches_results.json"), "a") as f:
        for r in RC.RESULTS:
            f.write(json.dumps(r) + "\n")
    bad = [r for r in RC.RESULTS if r["verdict"] != "PASS"]
    print("\n%d result(s), %d not PASS: %s" % (len(RC.RESULTS), len(bad),
                                               ", ".join("%s %s" % (r["case"], r["verdict"]) for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
