#!/usr/bin/env python3
"""R18 and R26-R28 options check (vtrux_checks options) on real release builds, shown passing
and failing.

    python -B options_cases.py --root <short folder in scratch/> --out <run folder> --profile <vtrux_checks.json>
                               [--cases a,b] [--jobs N] [--fixture <repo>]
    python -B options_cases.py --self-test

Run from an exported ESP-IDF shell, with its Python, like release_cases.py, whose fixture
(a clone of gen-inhibit with fixture commits, under --root) and plumbing it reuses. It makes
the fixture as release_cases.py does if --root has none, and refuses a --root whose fixture
tags older tooling than this checkout's HEAD (SPEC R23's pin). Each case
commits its sdkconfig lines (and, for one, a CMake line) on top of the fixture, commits what
the reconfigure then rewrites (as the fixture itself was made), and runs the release build.
--profile is copied into the project root as vtrux_checks.json after the build, so the
release build sees a clean tree.

The expected result is worked out here, not by the check's reader, rule by rule:
  R18  every profile option not at the profile's value in a build folder's config/sdkconfig.h
  R26  each of R26's four options defined in a build folder's sdkconfig.h
  R28  each of R28's three levels not at its value in a build folder's sdkconfig.h, and each
       of CORE_DEBUG_LEVEL and LOG_LOCAL_LEVEL found as -D in a build folder's
       compile_commands.json (a plain text search here)
  R27  CONFIG_FREERTOS_HZ missing from the profile, or not an integer
The check must name exactly those failures under each rule, print PASS for each rule with
none, and exit 1 when any rule has one. A case whose trigger does not show is VOID.

    release_pass        the fixture as it is (gen-inhibit's log levels at info, known anomaly 5
                        of its port): R18, R26 and R27 pass, R28 fails on the levels
    bootloader_missing  then build/bootloader/config/sdkconfig.json moved aside: fails, naming
                        the folder (restored after)
    repro_off           CONFIG_APP_REPRODUCIBLE_BUILD back off: R18 fails on it
    rollback_off        CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE off: R18 fails on it
    wdt_panic_on        CONFIG_ESP_TASK_WDT_PANIC on: R18 fails on it
    free_option         CONFIG_LOG_COLORS off, an option the profile does not name: R18 passes
    r26_efuse_virtual   CONFIG_EFUSE_VIRTUAL on: R26 fails on it (and R18, whose profile names it)
    r28_levels_none     ESP-IDF's default level none and the bootloader's error: R28 passes
    r28_define          r28_levels_none plus LOG_LOCAL_LEVEL defined in main/CMakeLists.txt with
                        target_compile_definitions: R28 fails on the -D alone
    r27_no_tick         release_pass's build, judged against the profile without
                        CONFIG_FREERTOS_HZ: R27 fails (needs release_pass in the same batch)

R26 is shown on CONFIG_EFUSE_VIRTUAL rather than CONFIG_SECURE_BOOT, which needs a signing key
for the build to finish. Before each case it checks the bench lock, the "No builds" line and
running simulators, as release_cases.py does.
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
_DEFINE = re.compile(r"^#define (CONFIG_[A-Z0-9_]+) (.*)$", re.M)
_NAMED = re.compile(r"^    (\S+): (CONFIG_[A-Z0-9_]+|-D[A-Z_]+) ")
RULES = ("R18", "R26", "R27", "R28")
R26_NAMES = ("CONFIG_SECURE_BOOT", "CONFIG_SECURE_FLASH_ENC_ENABLED", "CONFIG_FLASH_ENCRYPTION_ENABLED",
             "CONFIG_EFUSE_VIRTUAL")
R28_LEVELS = {"CONFIG_LOG_DEFAULT_LEVEL": 0, "CONFIG_LOG_MAXIMUM_LEVEL": 0, "CONFIG_BOOTLOADER_LOG_LEVEL": 1}
R28_DEFINES = ("CORE_DEBUG_LEVEL", "LOG_LOCAL_LEVEL")
TICK = "CONFIG_FREERTOS_HZ"
LEVELS_NONE = [("# CONFIG_LOG_DEFAULT_LEVEL_NONE is not set", "CONFIG_LOG_DEFAULT_LEVEL_NONE=y"),
               ("CONFIG_LOG_DEFAULT_LEVEL_INFO=y", "# CONFIG_LOG_DEFAULT_LEVEL_INFO is not set"),
               ("# CONFIG_BOOTLOADER_LOG_LEVEL_ERROR is not set", "CONFIG_BOOTLOADER_LOG_LEVEL_ERROR=y"),
               ("CONFIG_BOOTLOADER_LOG_LEVEL_INFO=y", "# CONFIG_BOOTLOADER_LOG_LEVEL_INFO is not set")]
DEFINE_LINE = "target_compile_definitions(${COMPONENT_LIB} PRIVATE LOG_LOCAL_LEVEL=0)"


# ---------- judges (pure; --self-test runs them on fakes) ----------

def header_meets(want, defines, name):
    """want from the profile; defines: {name: text} from one sdkconfig.h."""
    raw = defines.get(name)
    if want == "unset":
        return raw is None
    if raw is None:
        return False
    if want is True:
        return raw == "1"
    if isinstance(want, int):
        try:
            return int(raw, 0) == want
        except ValueError:
            return False
    return raw == json.dumps(want)


def command_defines(commands):
    """{folder: compile_commands.json's list}: [(folder, "-D" + name)] for each of R28's names
    passed with -D, by a plain search of each command's text."""
    out = set()
    for folder, entries in commands.items():
        for e in entries:
            text = e.get("command") or " ".join(e.get("arguments") or [])
            for name in R28_DEFINES:
                if re.search(r"-D\s*%s(?![A-Za-z0-9_])" % name, text):
                    out.add((folder, "-D" + name))
    return sorted(out)


def expected(profile, headers, commands=None):
    """profile: {name: value}; headers: {folder: defines}; commands: {folder: compile commands}.
    {rule: sorted failures}: (folder, name) pairs, and [TICK] for R27."""
    tick = profile.get(TICK)
    return {
        "R18": sorted((f, n) for f, d in headers.items() for n, v in profile.items() if not header_meets(v, d, n)),
        "R26": sorted((f, n) for f, d in headers.items() for n in R26_NAMES if not header_meets("unset", d, n)),
        "R27": [] if isinstance(tick, int) and not isinstance(tick, bool) else [TICK],
        "R28": sorted([(f, n) for f, d in headers.items() for n, v in R28_LEVELS.items() if not header_meets(v, d, n)]
                      + command_defines(commands or {})),
    }


def parse(out):
    """{rule: (passed, [named failures])} from the check's output: a rule's failures are the
    indented "folder: name" lines under its FAIL line; R27 names its option on the FAIL line."""
    res, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"^(PASS|FAIL) (R\d\d)\b", line)
        if m:
            cur = m.group(2) if m.group(1) == "FAIL" else None
            res[m.group(2)] = (m.group(1) == "PASS", [TICK] if (m.group(2) == "R27" and m.group(1) == "FAIL") else [])
            continue
        n = _NAMED.match(line)
        if n and cur in ("R18", "R26", "R28"):
            res[cur][1].append((n.group(1), n.group(2)))
        elif not line.startswith("    "):
            cur = None
    return {r: (p, sorted(set(names))) for r, (p, names) in res.items()}


def judge(rc, out, want):
    """(verdict, detail): want is expected()'s {rule: failures}."""
    if rc not in (0, 1):
        return "ERROR", "the check exited %d" % rc
    any_fail = any(want[r] for r in RULES)
    if rc != (1 if any_fail else 0):
        return "FAIL", "the check exited %d, wanted %d" % (rc, 1 if any_fail else 0)
    got = parse(out)
    for r in RULES:
        if r not in got:
            return "FAIL", "the check printed no PASS or FAIL line for %s" % r
        passed, named = got[r]
        if passed != (not want[r]):
            return "FAIL", "%s %s, wanted %s" % ("PASS" if passed else "FAIL", r, "PASS" if not want[r] else "FAIL")
        if named != want[r]:
            return "FAIL", "%s names %s, wanted %s" % (r, named, want[r])
    return "PASS", "exit %d; %s" % (rc, "; ".join("%s %s" % (r, "PASS" if not want[r] else "FAIL on " + ", ".join(
        " ".join(x) if isinstance(x, tuple) else x for x in want[r])) for r in RULES))


def judge_unreadable(rc, out, folder):
    if rc != 1 or "FAIL R18: cannot read" not in out:
        return "FAIL", "exit %d; wanted 1 with 'cannot read'" % rc
    if folder not in out:
        return "FAIL", "exit 1, but the output does not name %s" % folder
    return "PASS", "exit 1, cannot read, names %s" % folder


def self_test():
    ok = True
    prof = {"CONFIG_A": True, "CONFIG_B": "unset", "CONFIG_N": 3, "CONFIG_S": "x", TICK: 1000}
    good = {"CONFIG_A": "1", "CONFIG_N": "0x3", "CONFIG_S": '"x"', "CONFIG_FREE": "1", TICK: "1000",
            "CONFIG_LOG_DEFAULT_LEVEL": "0", "CONFIG_LOG_MAXIMUM_LEVEL": "0", "CONFIG_BOOTLOADER_LOG_LEVEL": "1"}
    none = {"R18": [], "R26": [], "R27": [], "R28": []}
    hdr_cases = (("all at value", {"build": good}, None, prof, none),
                 ("a set option missing", {"build": dict(good, CONFIG_A=None)}, None, prof,
                  dict(none, R18=[("build", "CONFIG_A")])),
                 ("an unset option defined", {"build": dict(good, CONFIG_B="1")}, None, prof,
                  dict(none, R18=[("build", "CONFIG_B")])),
                 ("a number differs", {"build": dict(good, CONFIG_N="4")}, None, prof, dict(none, R18=[("build", "CONFIG_N")])),
                 ("the bootloader alone", {"build": good, "build/bootloader": dict(good, CONFIG_A=None)}, None, prof,
                  dict(none, R18=[("build/bootloader", "CONFIG_A")])),
                 ("an R26 option on", {"build": dict(good, CONFIG_EFUSE_VIRTUAL="1")}, None, prof,
                  dict(none, R26=[("build", "CONFIG_EFUSE_VIRTUAL")])),
                 ("a level at info", {"build": dict(good, CONFIG_LOG_DEFAULT_LEVEL="3")}, None, prof,
                  dict(none, R28=[("build", "CONFIG_LOG_DEFAULT_LEVEL")])),
                 ("a level missing", {"build": dict(good, CONFIG_BOOTLOADER_LOG_LEVEL=None)}, None, prof,
                  dict(none, R28=[("build", "CONFIG_BOOTLOADER_LOG_LEVEL")])),
                 ("a -D in a command", {"build": good}, {"build": [{"command": "gcc -DLOG_LOCAL_LEVEL=0 -c a.c"},
                                                                    {"arguments": ["gcc", "-D", "CORE_DEBUG_LEVEL"]}]}, prof,
                  dict(none, R28=[("build", "-DCORE_DEBUG_LEVEL"), ("build", "-DLOG_LOCAL_LEVEL")])),
                 ("a longer name is not the define", {"build": good}, {"build": [{"command": "gcc -DLOG_LOCAL_LEVEL_X"}]},
                  prof, none),
                 ("no tick in the profile", {"build": good}, None, {k: v for k, v in prof.items() if k != TICK},
                  dict(none, R27=[TICK])))
    for label, headers, cmds, pr, want in hdr_cases:
        headers = {f: {k: v for k, v in d.items() if v is not None} for f, d in headers.items()}
        got = expected(pr, headers, cmds)
        print("  %s expected(), %s: %s" % ("ok  " if got == want else "FAIL", label, got))
        ok &= got == want
    allpass = "R18 ...\nPASS R18\nR26 options ...\nPASS R26\nR28 options ...\nPASS R28\nPASS R27: CONFIG_FREERTOS_HZ 1000 (s)\n"
    out1 = allpass.replace("PASS R18\n", "FAIL R18: 1 option(s) not at the profile's value\n"
                           "    build: CONFIG_A is false, the profile says true (s)\n")
    w1 = dict(none, R18=[("build", "CONFIG_A")])
    out28 = allpass.replace("PASS R28\n", "FAIL R28\n    build: CONFIG_LOG_DEFAULT_LEVEL is 3, R28 says 0\n"
                            "    build/bootloader: -DLOG_LOCAL_LEVEL in 2 compile command(s), e.g. a.c\n")
    w28 = dict(none, R28=[("build", "CONFIG_LOG_DEFAULT_LEVEL"), ("build/bootloader", "-DLOG_LOCAL_LEVEL")])
    out27 = allpass.replace("PASS R27: CONFIG_FREERTOS_HZ 1000 (s)\n", "FAIL R27: the profile does not name CONFIG_FREERTOS_HZ\n")
    for label, args, want in (("passes as wanted", (0, allpass, none), "PASS"),
                              ("R18 fails as wanted", (1, out1, w1), "PASS"),
                              ("R28 fails as wanted, a level and a -D", (1, out28, w28), "PASS"),
                              ("R27 fails as wanted", (1, out27, dict(none, R27=[TICK])), "PASS"),
                              ("passes when it should fail", (0, allpass, w1), "FAIL"),
                              ("fails when it should pass", (1, out1, none), "FAIL"),
                              ("names another folder", (1, out1.replace("build:", "build/bootloader:"), w1), "FAIL"),
                              ("names one too many", (1, out1.replace("(s)\n", "(s)\n    build: CONFIG_B is true, x\n", 1), w1),
                               "FAIL"),
                              ("a failure under the wrong rule", (1, out28, dict(none, R26=w28["R28"])), "FAIL"),
                              ("a rule with no line", (0, allpass.replace("PASS R26\n", ""), none), "FAIL"),
                              ("exit 1 with every rule PASS", (1, allpass, none), "FAIL"),
                              ("R27 fails when it should pass", (1, out27, none), "FAIL"),
                              ("crashes", (2, "Traceback", none), "ERROR")):
        v = judge(*args)[0]
        print("  %s judge(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    for label, args, want in (("unreadable, named", (1, "FAIL R18: cannot read ...: build/bootloader: x", "build/bootloader"), "PASS"),
                              ("unreadable, not named", (1, "FAIL R18: cannot read ...: x", "build/bootloader"), "FAIL"),
                              ("passes instead", (0, "PASS R18", "build/bootloader"), "FAIL")):
        v = judge_unreadable(*args)[0]
        print("  %s judge_unreadable(), %s: %s" % ("ok  " if v == want else "FAIL", label, v))
        ok &= v == want
    print("\nself-test: %s" % ("every broken input caught" if ok else "FAILED"))
    return 0 if ok else 1


# ---------- plumbing ----------

def check(proj, log):
    e = RC.idf_env(PYTHONPATH=TOOLS)
    return RC.run([sys.executable, "-B", "-m", "vtrux_checks", "options", "--build", os.path.join(proj, "build")],
                  cwd=proj, env=e, log=log)


def build_folders(proj):
    b = os.path.join(proj, "build")
    for d, _, files in os.walk(b):
        if d == b or "CMakeCache.txt" in files:
            yield d, os.path.relpath(d, proj).replace(os.sep, "/")


def headers(proj):
    """{folder label: {name: text}} from config/sdkconfig.h in build/ and in each CMake build
    folder below it (one with its own CMakeCache.txt)."""
    out = {}
    for d, label in build_folders(proj):
        p = os.path.join(d, "config", "sdkconfig.h")
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                out[label] = dict(_DEFINE.findall(f.read()))
    return out


def commands(proj):
    """{folder label: compile_commands.json's list} for the same folders."""
    out = {}
    for d, label in build_folders(proj):
        p = os.path.join(d, "compile_commands.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                out[label] = json.load(f)
    return out


def set_line(proj, old, new, rel="sdkconfig"):
    p = os.path.join(proj, rel)
    lines = open(p, newline="").read().replace("\r\n", "\n").split("\n")
    if lines.count(old) != 1:
        raise RuntimeError("%s: %d '%s' lines" % (rel, lines.count(old), old))
    with open(p, "w", newline="") as f:
        f.write("\n".join(new if x == old else x for x in lines))


def append_line(proj, rel, line):
    p = os.path.join(proj, rel)
    text = open(p, newline="").read()
    with open(p, "w", newline="") as f:
        f.write(text + ("" if text.endswith("\n") else "\n") + line + "\n")


def built(ctx, name, log, changes=None, cmake=None):
    """A release build of the fixture, with changes = [(old line, new line)] in sdkconfig and
    cmake = (file, line to append) committed on top. Returns (headers, commands), or None after
    reporting VOID."""
    RC.reset(ctx.p1, "fixture")
    if changes or cmake:
        RC.git(ctx.p1, "checkout", "-q", "-B", "opt-" + name, "origin/fixture")
        for old, new in changes or []:
            set_line(ctx.p1, old, new)
        if cmake:
            append_line(ctx.p1, *cmake)
        RC.git(ctx.p1, "commit", "-q", "-am", "options case %s" % name)
        with open(log, "a") as fx:
            RC.normalise(ctx, ctx.p1, name, fx)
            fx.write("%s: %s\n" % (name, RC.git(ctx.p1, "rev-parse", "HEAD")))
    RC.rmtree(os.path.join(ctx.p1, "build"))
    rc, _ = RC.release(ctx, ctx.p1, log, jobs=ctx.jobs)
    if rc:
        RC.report(name, "VOID", "the release build failed (exit %d)" % rc, ctx.log)
        return None
    shutil.copyfile(ctx.profile, os.path.join(ctx.p1, "vtrux_checks.json"))
    return headers(ctx.p1), commands(ctx.p1)


def run_case(ctx, name, log, changes, premise, cmake=None, prof=None):
    """premise(want, headers) -> True when the case's trigger shows in the build."""
    b = built(ctx, name, log, changes, cmake)
    if b is None:
        return
    h, cc = b
    want = expected(prof or ctx.prof, h, cc)
    with open(log, "a") as f:
        f.write("sdkconfig.h folders %s; compile_commands folders %s; expected %s\n" % (sorted(h), sorted(cc), want))
    if not premise(want, h, cc):
        RC.report(name, "VOID", "the build does not show the case's trigger; expected %s" % want, ctx.log)
        return
    rc, out = check(ctx.p1, log)
    v, d = judge(rc, out, want)
    RC.report(name, v, d, ctx.log)
    return True


# ---------- cases ----------

def c_release_pass(ctx, log):
    ctx.pass_built = run_case(ctx, "release_pass", log, None,
                              lambda w, h, cc: "build/bootloader" in h and set(cc) == set(h) and not w["R18"]
                              and not w["R26"] and not w["R27"])


def c_bootloader_missing(ctx, log):
    if not getattr(ctx, "pass_built", False):
        RC.report("bootloader_missing", "VOID", "needs release_pass in the same batch", ctx.log)
        return
    p = os.path.join(ctx.p1, "build", "bootloader", "config", "sdkconfig.json")
    os.replace(p, p + ".aside")
    try:
        rc, out = check(ctx.p1, log)
        RC.report("bootloader_missing", *judge_unreadable(rc, out, "build/bootloader"), ctx.log)
    finally:
        os.replace(p + ".aside", p)


def r18(trigger):
    return lambda w, h, cc: trigger in w["R18"]


def c_repro_off(ctx, log):
    run_case(ctx, "repro_off", log, [(RC.REPRO_ON, RC.REPRO_OFF)], r18(("build", "CONFIG_APP_REPRODUCIBLE_BUILD")))


def c_rollback_off(ctx, log):
    run_case(ctx, "rollback_off", log, [("CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y",
                                         "# CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is not set")],
             r18(("build/bootloader", "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE")))


def c_wdt_panic_on(ctx, log):
    run_case(ctx, "wdt_panic_on", log, [("# CONFIG_ESP_TASK_WDT_PANIC is not set", "CONFIG_ESP_TASK_WDT_PANIC=y")],
             r18(("build", "CONFIG_ESP_TASK_WDT_PANIC")))


def c_free_option(ctx, log):
    run_case(ctx, "free_option", log, [("CONFIG_LOG_COLORS=y", "# CONFIG_LOG_COLORS is not set")],
             lambda w, h, cc: not w["R18"] and "CONFIG_LOG_COLORS" not in h.get("build", {}))


def c_r26_efuse_virtual(ctx, log):
    run_case(ctx, "r26_efuse_virtual", log, [("# CONFIG_EFUSE_VIRTUAL is not set", "CONFIG_EFUSE_VIRTUAL=y")],
             lambda w, h, cc: ("build", "CONFIG_EFUSE_VIRTUAL") in w["R26"])


def c_r28_levels_none(ctx, log):
    run_case(ctx, "r28_levels_none", log, LEVELS_NONE,
             lambda w, h, cc: not w["R28"] and "build/bootloader" in h and set(cc) == set(h))


def c_r28_define(ctx, log):
    run_case(ctx, "r28_define", log, LEVELS_NONE,
             lambda w, h, cc: w["R28"] and all(n.startswith("-D") for _, n in w["R28"]),
             cmake=("main/CMakeLists.txt", DEFINE_LINE))


def c_r27_no_tick(ctx, log):
    if not getattr(ctx, "pass_built", False):
        RC.report("r27_no_tick", "VOID", "needs release_pass in the same batch", ctx.log)
        return
    data = json.load(open(ctx.profile, encoding="utf-8"))
    if TICK not in data["release_profile"]:
        RC.report("r27_no_tick", "VOID", "the profile does not name %s to take out" % TICK, ctx.log)
        return
    del data["release_profile"][TICK]
    p = os.path.join(ctx.p1, "vtrux_checks.json")
    try:
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=1)
        prof = {n: e["value"] for n, e in data["release_profile"].items()}
        want = expected(prof, headers(ctx.p1), commands(ctx.p1))
        with open(log, "a") as f:
            f.write("profile without %s; expected %s\n" % (TICK, want))
        rc, out = check(ctx.p1, log)
        RC.report("r27_no_tick", *judge(rc, out, want), ctx.log)
    finally:
        shutil.copyfile(ctx.profile, p)


CASES = [("release_pass", c_release_pass), ("bootloader_missing", c_bootloader_missing),
         ("r27_no_tick", c_r27_no_tick),
         ("repro_off", c_repro_off), ("rollback_off", c_rollback_off), ("wdt_panic_on", c_wdt_panic_on),
         ("free_option", c_free_option), ("r26_efuse_virtual", c_r26_efuse_virtual),
         ("r28_levels_none", c_r28_levels_none), ("r28_define", c_r28_define)]


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--out")
    ap.add_argument("--profile", help="the vtrux_checks.json to judge against")
    ap.add_argument("--base", default="gen-inhibit/main")
    ap.add_argument("--fixture")
    ap.add_argument("--cases")
    ap.add_argument("--jobs", type=int, default=None)
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.root and a.out and a.profile) or "IDF_PATH" not in os.environ:
        ap.error("--root, --out and --profile, from an exported ESP-IDF shell")
    ctx = RC.Ctx(a)
    ctx.profile = os.path.abspath(a.profile)
    ctx.prof = {n: e["value"] for n, e in json.load(open(ctx.profile))["release_profile"].items()}
    ctx.log = os.path.join(ctx.out, "options_driver.log")
    os.makedirs(ctx.out, exist_ok=True)
    os.makedirs(os.path.join(ctx.ri, "firmware"), exist_ok=True)
    names = a.cases.split(",") if a.cases else [n for n, _ in CASES]
    unknown = set(names) - {n for n, _ in CASES}
    if unknown:
        ap.error("unknown cases %s" % sorted(unknown))
    with open(ctx.log, "a", encoding="utf-8") as f:
        f.write("=== %s  host %s  %s  python %s\nIDF %s  tooling %s (%s)\nprofile %s sha256 %s\ncases %s\n\n" % (
            time.strftime("%Y-%m-%dT%H:%M:%S"), platform.node(), platform.platform(), platform.python_version(),
            RC.run(["git", "-C", os.environ["IDF_PATH"], "describe", "--tags", "--dirty"])[1].strip(),
            RC.git(TOOLS, "rev-parse", "HEAD"), RC.git(TOOLS, "status", "--porcelain") or "clean",
            ctx.profile, RC.sha256_file(ctx.profile), names))
    if not os.path.exists(ctx.src):
        if not ctx.made_fixture:
            ap.error("--fixture %s does not exist" % ctx.src)
        RC.make_fixture(ctx)
    else:
        RC.make_pin(ctx)    # the fixture's tag must mark the tooling under test (refuses an older root)
    RC.clones(ctx)
    for n, fn in CASES:
        if n not in names:
            continue
        why = RC.bench_clear(ctx.log)
        if why:
            RC.report(n, "STOPPED", "; ".join(why), ctx.log)
            break
        log = os.path.join(ctx.out, "options_%s.log" % n)
        t0 = time.time()
        try:
            fn(ctx, log)
        except Exception as e:      # a case that crashes is reported, never a pass
            RC.report(n, "ERROR", repr(e)[:300], ctx.log)
        with open(ctx.log, "a") as f:
            f.write("%s took %.0f s\n\n" % (n, time.time() - t0))
    RC.reset(ctx.p1, "fixture")
    with open(os.path.join(ctx.out, "options_results.json"), "a") as f:
        for r in RC.RESULTS:
            f.write(json.dumps(r) + "\n")
    bad = [r for r in RC.RESULTS if r["verdict"] != "PASS"]
    print("\n%d result(s), %d not PASS: %s" % (len(RC.RESULTS), len(bad),
                                               ", ".join("%s %s" % (r["case"], r["verdict"]) for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
