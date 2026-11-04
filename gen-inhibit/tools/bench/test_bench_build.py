#!/usr/bin/env python3
"""Tests for bench_build.py, with real child processes: a fake launcher and a fake ninja,
Python scripts written in scratch/, each appending what it was run with (its arguments,
and the R32 variables and a planted one as it sees them) to one log, in the order they ran.
No ESP-IDF, no build.

    py -3.14 -B gen-inhibit/tools/bench/test_bench_build.py

The can-fail is the point of the tool: a launcher that refuses (exit 1), crashes (a
traceback), does not know check-env (exit 2) or is not there stops the build, and ninja
is never run. When check-env passes, ninja runs after it, once, on the build folder, with
-j N only when --jobs N is given, and its exit code is bench_build's. Both children see
the caller's environment as it is: a planted variable, and an unset IDF_TOOLS_PATH left
unset (R24: no default). The defaults are gen-inhibit/vtrux_tools.py, run by this
Python with check-env, and gen-inhibit/firmware/build.

Gen-inhibit tester, 2026-11-03 (port step 1d).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402
import bench_build as B  # noqa: E402

fails = []
oks = 0
WATCH = ("IDF_PATH", "IDF_TOOLS_PATH", "IDF_PYTHON_ENV_PATH", "ESP_ROM_ELF_DIR", "BB_PLANT")

FAKE = r'''
import json, os, sys
WHO, MODE = %r, %r
WATCH = %r
with open(os.environ["BB_FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"who": WHO, "argv": sys.argv[1:], "env": {k: os.environ.get(k) for k in WATCH}}) + "\n")
if MODE == "crash":
    raise KeyError("IDF_TOOLS_PATH")
if MODE == "refuse":
    print("refused: IDF_TOOLS_PATH")
    sys.exit(1)
if WHO == "launcher" and sys.argv[1:] != ["check-env"]:
    print("unknown command %%r" %% sys.argv[1:])
    sys.exit(2)
sys.exit(int(os.environ.get("BB_NINJA_RC", "0")) if WHO == "ninja" else 0)
'''


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def main():
    d = gi_paths.scratch("bench-build-test")
    log = os.path.join(d, "ran.jsonl")
    build = os.path.join(d, "build")
    launchers = {m: write(os.path.join(d, "launcher_%s.py" % m), FAKE % ("launcher", m, WATCH))
                 for m in ("ok", "refuse", "crash")}
    # a launcher without check-env: every command is unknown to it (exit 2)
    launchers["unknown"] = write(os.path.join(d, "launcher_unknown.py"),
                                 FAKE.replace('sys.argv[1:] != ["check-env"]', "True") % ("launcher", "ok", WATCH))
    ninja = (sys.executable, write(os.path.join(d, "ninja.py"), FAKE % ("ninja", "ok", WATCH)))
    saved = dict(os.environ)
    os.environ.update(BB_FAKE_LOG=log, BB_PLANT="planted", IDF_PATH=os.path.join(d, "idf"))
    os.environ.pop("IDF_TOOLS_PATH", None)
    os.environ.pop("BB_NINJA_RC", None)
    caller = {k: os.environ.get(k) for k in WATCH}

    def run(argv, launcher, ninja_rc=None, nin=ninja):
        if os.path.exists(log):
            os.remove(log)
        if ninja_rc is None:
            os.environ.pop("BB_NINJA_RC", None)
        else:
            os.environ["BB_NINJA_RC"] = str(ninja_rc)
        try:
            rc = B.main(argv, launcher=launcher, build=build, ninja=nin)
        except (OSError, subprocess.SubprocessError) as e:   # judged by the checks, never a crash
            rc = "raised %s: %s" % (type(e).__name__, e)
        ran = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as f:
                ran = [json.loads(l) for l in f if l.strip()]
        return rc, ran

    try:
        rc, ran = run(["--jobs", "1"], launchers["ok"])
        check("check-env passes, --jobs 1: exit 0", rc == 0, rc)
        check("check-env passes: the launcher, then ninja, once each",
              [r["who"] for r in ran] == ["launcher", "ninja"], [r["who"] for r in ran])
        check("the launcher is given check-env and nothing else", ran[:1] and ran[0]["argv"] == ["check-env"], ran[:1])
        check("ninja: -C the build folder, -j 1", ran[1:] and ran[1]["argv"] == ["-C", build, "-j", "1"], ran[1:])
        check("both see the caller's environment as it is (planted var; IDF_TOOLS_PATH still unset)",
              [r["env"] for r in ran] == [caller, caller], ran)

        rc, ran = run([], launchers["ok"])
        check("no --jobs: ninja gets no -j", rc == 0 and ran[1:] and ran[1]["argv"] == ["-C", build], (rc, ran))

        rc, ran = run(["--jobs", "6"], launchers["ok"])
        check("--jobs 6: -j 6", ran[1:] and ran[1]["argv"] == ["-C", build, "-j", "6"], ran)

        rc, ran = run(["--jobs", "1"], launchers["ok"], ninja_rc=7)
        check("ninja's exit code (7) is bench_build's", rc == 7, rc)

        for mode, what in (("refuse", "refuses (exit 1)"), ("crash", "crashes (a traceback)"),
                           ("unknown", "has no check-env (exit 2)")):
            rc, ran = run(["--jobs", "1"], launchers[mode])
            check("the launcher %s: exit %d, nothing built" % (what, B.REFUSED), rc == B.REFUSED, rc)
            check("the launcher %s: it ran, and ninja never did" % what,
                  [r["who"] for r in ran] == ["launcher"], [r["who"] for r in ran])

        rc, ran = run(["--jobs", "1"], os.path.join(d, "no-such-launcher.py"))
        check("no launcher file: exit %d, ninja never run" % B.REFUSED, rc == B.REFUSED and ran == [], (rc, ran))

        rc, ran = run(["--jobs", "1"], launchers["ok"], nin=(os.path.join(d, "no-such-ninja.exe"),))
        check("check-env passes, ninja cannot be started: exit %d" % B.NO_NINJA,
              rc == B.NO_NINJA and [r["who"] for r in ran] == ["launcher"], (rc, ran))

        for bad in (["--jobs", "0"], ["--jobs", "x"], ["--nosuch"]):
            try:
                rc, ran = run(bad, launchers["ok"])
                refused = False
            except SystemExit as e:
                refused, rc = e.code != 0, e.code
                ran = []
                if os.path.exists(log):
                    with open(log, encoding="utf-8") as f:
                        ran = [l for l in f if l.strip()]
            check("%s refused before anything runs" % " ".join(bad), refused and ran == [], (rc, ran))
    finally:
        os.environ.clear()
        os.environ.update(saved)

    # the defaults: this project's launcher, run by this Python with check-env; its build folder
    check("bench_build is in gen-inhibit/tools/bench/",
          os.path.basename(B.HERE) == "bench" and os.path.basename(os.path.dirname(B.HERE)) == "tools", B.HERE)
    check("the launcher is gen-inhibit/vtrux_tools.py, and is there",
          B.LAUNCHER == os.path.join(B.PROJECT, "vtrux_tools.py") and os.path.isfile(B.LAUNCHER)
          and B.PROJECT == os.path.dirname(os.path.dirname(B.HERE)), B.LAUNCHER)
    check("the build folder is gen-inhibit/firmware/build",
          os.path.relpath(B.BUILD, B.PROJECT).replace("\\", "/") == "firmware/build", B.BUILD)
    check("check-env is run as <this Python> -B <launcher> check-env",
          B.check_env_cmd() == [sys.executable, "-B", B.LAUNCHER, "check-env"], B.check_env_cmd())
    check("ninja is the one on PATH", tuple(B.NINJA) == ("ninja",) and B.ninja_cmd()[:3] == ["ninja", "-C", B.BUILD],
          B.ninja_cmd())

    gi_paths.remove_tree(d)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
