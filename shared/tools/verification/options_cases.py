#!/usr/bin/env python3
"""R18 options check (vtrux_checks options) on real release builds, shown passing and failing.

    python -B options_cases.py --root <short folder in scratch/> --out <run folder> --profile <vtrux_checks.json>
                               [--cases a,b] [--jobs N] [--fixture <repo>]
    python -B options_cases.py --self-test

Run from an exported ESP-IDF shell, with its Python, like release_cases.py, whose fixture
(a clone of gen-inhibit with fixture commits, under --root) and plumbing it reuses. Each case
commits one sdkconfig change on top of the fixture, commits what the reconfigure then rewrites
(as the fixture itself was made), and runs the release build. --profile is copied into the
project root as vtrux_checks.json after the build, so the release build sees a clean tree.

The expected result comes from the build's config/sdkconfig.h in each CMake build folder (the
app's and the bootloader's), read here, not by the check's reader: every profile option not
at the profile's value there is a failure the check must name, with its folder, and no other.
A case whose trigger does not show in sdkconfig.h is VOID.

    release_pass        the fixture as it is: every profile option at its value; passes
    bootloader_missing  then build/bootloader/config/sdkconfig.json moved aside: fails, naming
                        the folder (restored after)
    repro_off           CONFIG_APP_REPRODUCIBLE_BUILD back off, as gen-inhibit has it until
                        port step 0b: fails on it
    rollback_off        CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE off: fails on it
    wdt_panic_on        CONFIG_ESP_TASK_WDT_PANIC on: fails on it
    free_option         CONFIG_LOG_COLORS off, an option the profile does not name: passes

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
_DEFINE = re.compile(r"^#define (CONFIG_[A-Z0-9_]+) (.*)$", re.M)
_FAIL = re.compile(r"^    (\S+): (CONFIG_[A-Z0-9_]+) is ", re.M)


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


def expected(profile, headers):
    """profile: {name: value}; headers: {folder label: defines}. The (folder, name) pairs not at
    the profile's value."""
    return sorted((f, n) for f, d in headers.items() for n, v in profile.items() if not header_meets(v, d, n))


def judge(rc, out, want):
    """(verdict, detail): want is the sorted list of (folder, name) the check must name."""
    if rc not in (0, 1):
        return "ERROR", "the check exited %d" % rc
    named = sorted(set(_FAIL.findall(out)))
    if rc != (1 if want else 0):
        return "FAIL", "the check exited %d, wanted %d" % (rc, 1 if want else 0)
    if ("PASS R18" in out) != (rc == 0):
        return "FAIL", "exit %d but %s PASS R18" % (rc, "prints" if rc else "does not print")
    if named != want:
        return "FAIL", "the check names %s, wanted %s" % (named, want)
    return "PASS", "exit %d%s" % (rc, (", names " + ", ".join("%s %s" % p for p in want)) if want else "")


def judge_unreadable(rc, out, folder):
    if rc != 1 or "FAIL R18: cannot read" not in out:
        return "FAIL", "exit %d; wanted 1 with 'cannot read'" % rc
    if folder not in out:
        return "FAIL", "exit 1, but the output does not name %s" % folder
    return "PASS", "exit 1, cannot read, names %s" % folder


def self_test():
    ok = True
    prof = {"CONFIG_A": True, "CONFIG_B": "unset", "CONFIG_N": 3, "CONFIG_S": "x"}
    good = {"CONFIG_A": "1", "CONFIG_N": "0x3", "CONFIG_S": '"x"', "CONFIG_FREE": "1"}
    hdr_cases = (("all at value", {"build": good}, []),
                 ("a set option missing", {"build": dict(good, CONFIG_A=None)}, [("build", "CONFIG_A")]),
                 ("an unset option defined", {"build": dict(good, CONFIG_B="1")}, [("build", "CONFIG_B")]),
                 ("a number differs", {"build": dict(good, CONFIG_N="4")}, [("build", "CONFIG_N")]),
                 ("a string differs", {"build": dict(good, CONFIG_S='"y"')}, [("build", "CONFIG_S")]),
                 ("the bootloader alone", {"build": good, "build/bootloader": dict(good, CONFIG_A=None)},
                  [("build/bootloader", "CONFIG_A")]))
    for label, headers, want in hdr_cases:
        headers = {f: {k: v for k, v in d.items() if v is not None} for f, d in headers.items()}
        got = expected(prof, headers)
        print("  %s expected(), %s: %s" % ("ok  " if got == want else "FAIL", label, got))
        ok &= got == want
    out1 = ("R18 options ...\nFAIL R18: 1 option(s) not at the profile's value\n"
            "    build: CONFIG_A is false, the profile says true (s)\n")
    w = [("build", "CONFIG_A")]
    for label, args, want in (("passes as wanted", (0, "R18 ...\nPASS R18\n", []), "PASS"),
                              ("fails as wanted", (1, out1, w), "PASS"),
                              ("passes when it should fail", (0, "PASS R18\n", w), "FAIL"),
                              ("fails when it should pass", (1, out1, []), "FAIL"),
                              ("names another folder", (1, out1.replace("build:", "build/bootloader:"), w), "FAIL"),
                              ("names one too many", (1, out1 + "    build: CONFIG_B is true, x\n", w), "FAIL"),
                              ("exit 0 without PASS", (0, "R18 ...\n", []), "FAIL"),
                              ("exit 1 with PASS", (1, out1 + "PASS R18\n", w), "FAIL"),
                              ("crashes", (2, "Traceback", []), "ERROR")):
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


def headers(proj):
    """{folder label: {name: text}} from config/sdkconfig.h in build/ and in each CMake build
    folder below it (one with its own CMakeCache.txt)."""
    b = os.path.join(proj, "build")
    out = {}
    for d, _, files in os.walk(b):
        if d == b or "CMakeCache.txt" in files:
            p = os.path.join(d, "config", "sdkconfig.h")
            if os.path.exists(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    out[os.path.relpath(d, proj).replace(os.sep, "/")] = dict(_DEFINE.findall(f.read()))
    return out


def set_line(proj, old, new):
    p = os.path.join(proj, "sdkconfig")
    lines = open(p, newline="").read().replace("\r\n", "\n").split("\n")
    if lines.count(old) != 1:
        raise RuntimeError("sdkconfig: %d '%s' lines" % (lines.count(old), old))
    with open(p, "w", newline="") as f:
        f.write("\n".join(new if x == old else x for x in lines))


def built(ctx, name, log, change=None):
    """A release build of the fixture, with change = (old line, new line) in sdkconfig committed on
    top. Returns the headers, or None after reporting VOID."""
    RC.reset(ctx.p1, "fixture")
    if change:
        RC.git(ctx.p1, "checkout", "-q", "-B", "r18-" + name, "origin/fixture")
        set_line(ctx.p1, *change)
        RC.git(ctx.p1, "commit", "-q", "-am", "options case %s: %s" % (name, change[1]))
        with open(log, "a") as fx:
            RC.normalise(ctx, ctx.p1, name, fx)
            fx.write("%s: %s\n" % (name, RC.git(ctx.p1, "rev-parse", "HEAD")))
    RC.rmtree(os.path.join(ctx.p1, "build"))
    rc, _ = RC.release(ctx, ctx.p1, log, jobs=ctx.jobs)
    if rc:
        RC.report(name, "VOID", "the release build failed (exit %d)" % rc, ctx.log)
        return None
    shutil.copyfile(ctx.profile, os.path.join(ctx.p1, "vtrux_checks.json"))
    return headers(ctx.p1)


def run_case(ctx, name, log, change, trigger):
    """trigger: (folder, name) that must be among the expected failures; ("free", name) for an
    option that must be absent from build/'s sdkconfig.h while nothing fails; None for nothing
    failing, with a bootloader folder read."""
    h = built(ctx, name, log, change)
    if h is None:
        return
    want = expected(ctx.prof, h)
    with open(log, "a") as f:
        f.write("sdkconfig.h folders %s; expected failures %s\n" % (sorted(h), want))
    if trigger is None:
        premise = not want and "build/bootloader" in h
    elif trigger[0] == "free":
        premise = not want and trigger[1] not in h.get("build", {})
    else:
        premise = trigger in want
    if not premise:
        RC.report(name, "VOID", "sdkconfig.h does not show the trigger %s; expected %s" % (trigger, want), ctx.log)
        return
    rc, out = check(ctx.p1, log)
    v, d = judge(rc, out, want)
    RC.report(name, v, d, ctx.log)
    return True


# ---------- cases ----------

def c_release_pass(ctx, log):
    ctx.pass_built = run_case(ctx, "release_pass", log, None, None)


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


def c_repro_off(ctx, log):
    run_case(ctx, "repro_off", log, (RC.REPRO_ON, RC.REPRO_OFF), ("build", "CONFIG_APP_REPRODUCIBLE_BUILD"))


def c_rollback_off(ctx, log):
    run_case(ctx, "rollback_off", log, ("CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y",
                                        "# CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is not set"),
             ("build/bootloader", "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE"))


def c_wdt_panic_on(ctx, log):
    run_case(ctx, "wdt_panic_on", log, ("# CONFIG_ESP_TASK_WDT_PANIC is not set", "CONFIG_ESP_TASK_WDT_PANIC=y"),
             ("build", "CONFIG_ESP_TASK_WDT_PANIC"))


def c_free_option(ctx, log):
    run_case(ctx, "free_option", log, ("CONFIG_LOG_COLORS=y", "# CONFIG_LOG_COLORS is not set"),
             ("free", "CONFIG_LOG_COLORS"))


CASES = [("release_pass", c_release_pass), ("bootloader_missing", c_bootloader_missing),
         ("repro_off", c_repro_off), ("rollback_off", c_rollback_off), ("wdt_panic_on", c_wdt_panic_on),
         ("free_option", c_free_option)]


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
        ap.error("no fixture at %s: make it with release_cases.py first" % ctx.src)
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
