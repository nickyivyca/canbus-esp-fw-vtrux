#!/usr/bin/env python3
"""Test baseline_b_jam.sh's jam step (B5-B8) with a fake Python that logs its argv.

Gen-inhibit tester, 2026-11-16, before B5-B8 on the jammer's ESP-IDF port (row
ea762c62044cb5c6). Until then the step's records named the PlatformIO jammer a89ece8c
and "USB unplugged (no WiCAN console)" whatever the bench had, and it let
x2_jam_cases.py capture the WiCAN's console whenever GI_WICAN_DEVICE was set (the suite
sets it from settings.wican.usb_device), against the console rule (reviewer, 2026-10-20:
no WiCAN console capture until the WiCAN's factory reset). Checks:

  - jam refuses (exit 2) with nothing run when GI_JAMMER or GI_WICAN_POWER is unset or
    empty;
  - with both set, it runs the eleven B5-B8 calls of x2_jam_cases.py in order, each with
    the case arguments below, --jammer exactly as given, --power carrying
    GI_WICAN_POWER and the WiCAN's build and nothing of the old fixed text, and
    --no-wican-console; each call's output goes to the file the suite's B5_B8 check
    declares.

    python3.13 -B gen-inhibit/tools/bench/test_baseline_b_jam.py
    python3.13 -B gen-inhibit/tools/bench/test_baseline_b_jam.py --mutants

The mutants are edits of a copy of baseline_b_jam.sh; each must turn a check into a
FAIL (a crash is not a kill). Linux only (bench_host.sh's Python name, python3.13).
"""
import os
import shutil
import subprocess
import sys
import tempfile

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

SCRIPT = "baseline_b_jam.sh"
BASH = shutil.which("bash")
SEP = "\x1f"
JAMMER = "vtrux_jammer_x2_NOT_FOR_VEHICLE row ea762c62044cb5c6 (elf ae2783e2...)"
POWER = "12 V on OBD pin 16 (14.1 V read), USB at 1-3.1"
COMMIT = "5e51f80d"

# (output file, case arguments), in the order the step runs them (baseline_run_list.md;
# the suite's B5_B8 outputs)
EXPECTED = (
    [("B5_livetrain_p5000.txt", ["livetrain", "--arms", "3", "--p", "5000", "--ms", "5000"])]
    + [("B5_livetrain_p2000_run%d.txt" % r, ["livetrain", "--arms", "3", "--p", "2000", "--ms", "5000"])
       for r in (1, 2, 3)]
    + [("B6_threshold_n10_lead%s.txt" % lead, ["threshold", "--arms", "2", "--n", "10", "--interval-ms", "1000",
                                               "--lead-s", lead, "--reset-wican"]) for lead in ("1.5", "5", "8.5")]
    + [("B6_threshold_n9_lead%s.txt" % lead, ["threshold", "--arms", "2", "--n", "9", "--interval-ms", "1100",
                                              "--lead-s", lead, "--reset-wican"]) for lead in ("1.5", "5")]
    + [("B7_busoff_rearm.txt", ["busoff-rearm", "--arms", "3", "--expect", "reinstalls"]),
       ("B8_busoff.txt", ["busoff", "--arms", "3", "--control"])])

FAKE_PY = r'''#!/usr/bin/env bash
# fake python3.13 for test_baseline_b_jam.py: logs its argv, one call per line
for x in "$@"; do printf '%s\037' "$x" >> "$LOG"; done
printf '\n' >> "$LOG"
echo "fake run"
'''

FAILS = []


def check(label, ok, detail=""):
    print("  %-4s %-74s %s" % ("ok" if ok else "FAIL", label[:74], "" if ok else str(detail)[:160]))
    if not ok:
        FAILS.append(label)


def run(src, **env_over):
    """Run `bash <copy of src> jam` in a temporary tree. Returns (rc, output, calls, files)."""
    tmp = tempfile.mkdtemp(prefix="gi-b-jam-", dir=gi_paths.scratch("tmp", run=False))
    try:
        a = os.path.join(tmp, "bench")
        root = os.path.join(tmp, "root")
        out = os.path.join(tmp, "out")
        b = os.path.join(tmp, "bin")
        for d in (a, root, b):
            os.makedirs(d)
        with open(os.path.join(a, SCRIPT), "w", newline="\n") as fh:
            fh.write(src)
        shutil.copy(os.path.join(HERE, "bench_host.sh"), a)
        p = os.path.join(b, "python3.13")
        with open(p, "w", newline="\n") as fh:
            fh.write(FAKE_PY)
        os.chmod(p, 0o755)
        log = os.path.join(tmp, "log.txt")
        e = {k: v for k, v in os.environ.items()
             if k not in ("GI_JAMMER", "GI_WICAN_POWER", "GI_OUT", "GI_RUNS_DIR", "GI_WICAN_DEVICE")}
        e.update({"PATH": b + os.pathsep + e.get("PATH", ""), "LOG": log, "REVERSE_IT_DIR": root,
                  "GI_COMMIT": COMMIT, "GI_OUT": out, "GI_JAMMER": JAMMER, "GI_WICAN_POWER": POWER})
        for k, v in env_over.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        r = subprocess.run([BASH, os.path.join(a, SCRIPT), "jam"], cwd=tmp, capture_output=True, text=True,
                           env=e, timeout=60)
        calls = []
        if os.path.exists(log):
            calls = [ln.split(SEP)[:-1] for ln in open(log).read().splitlines()]
        files = sorted(f for f in os.listdir(out)) if os.path.isdir(out) else []
        return r.returncode, r.stdout + r.stderr, calls, files
    finally:
        gi_paths.remove_tree(tmp)


def opt(argv, name):
    """The value after the last `name` in argv, or None."""
    vals = [argv[i + 1] for i, x in enumerate(argv[:-1]) if x == name]
    return vals[-1] if vals else None


def checks(src):
    for var in ("GI_JAMMER", "GI_WICAN_POWER"):
        for how, val in (("unset", None), ("empty", "")):
            rc, out, calls, _f = run(src, **{var: val})
            check("jam refuses with %s %s: exit 2, nothing run" % (var, how), rc == 2 and not calls,
                  (rc, len(calls), out[-120:]))
            check("  ... and says it needs %s" % var, var in out, out[-120:])
    rc, out, calls, files = run(src)
    check("jam with both set runs to the end (exit 0)", rc == 0, (rc, out[-200:]))
    check("eleven x2_jam_cases.py calls", len(calls) == len(EXPECTED) and
          all(len(c) > 1 and c[0] == "-B" and c[1].endswith("/x2_jam_cases.py") for c in calls),
          [c[:3] for c in calls])
    for i, (name, case) in enumerate(EXPECTED):
        c = calls[i] if i < len(calls) else []
        check("%s: the case arguments" % name, c[2:2 + len(case)] == case, c[2:2 + len(case) + 2])
        check("%s: --jammer is GI_JAMMER exactly" % name, opt(c, "--jammer") == JAMMER, opt(c, "--jammer"))
        pw = opt(c, "--power") or ""
        check("%s: --power carries GI_WICAN_POWER and the WiCAN's build" % name,
              POWER in pw and ("WiCAN %s _man" % COMMIT) in pw, pw)
        check("%s: --power has none of the old fixed text" % name,
              "USB unplugged" not in pw and "a89ece8c" not in pw, pw)
        check("%s: no WiCAN console capture (--no-wican-console)" % name, "--no-wican-console" in c, c[-4:])
    check("each call's output is the file the suite declares", files == sorted(n for n, _c in EXPECTED) + ["x2"],
          files)


MUTANTS = [
    ("GI_JAMMER not required",
     '    [ -n "${GI_JAMMER:-}" ] || { echo "jam needs GI_JAMMER', '    true || { echo "jam needs GI_JAMMER'),
    ("GI_WICAN_POWER not required",
     '    [ -n "${GI_WICAN_POWER:-}" ] || {', '    true || {'),
    ("GI_JAMMER may be empty",
     '[ -n "${GI_JAMMER:-}" ]', '[ -n "${GI_JAMMER+set}" ]'),
    ("the console is captured again",
     ' --jammer "$GI_JAMMER" --no-wican-console >', ' --jammer "$GI_JAMMER" >'),
    ("the old fixed jammer",
     '--jammer "$GI_JAMMER"', '--jammer "x2_jammer bb20e4a37b64 / md5 a89ece8c..."'),
    ("no --jammer passed",
     ' --jammer "$GI_JAMMER"', ''),
    ("the old fixed power text",
     '    PW="$GI_WICAN_POWER; no WiCAN console capture; WiCAN $C _man"\n',
     '    PW="12 V on OBD pin 16, USB unplugged (no WiCAN console); WiCAN $C _man; X2 jammer a89ece8c (custody)"\n'),
    ("the power without the WiCAN's build",
     'PW="$GI_WICAN_POWER; no WiCAN console capture; WiCAN $C _man"', 'PW="$GI_WICAN_POWER"'),
    ("one B6 control lead dropped",
     'for l in 1.5 5; do j B6_threshold_n9_lead$l', 'for l in 1.5; do j B6_threshold_n9_lead$l'),
]


def mutants():
    src = open(os.path.join(HERE, SCRIPT), encoding="utf-8", newline="").read()
    r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True)
    ok = r.returncode == 0 and "Traceback" not in r.stderr
    print("%s m0 the control: the unmutated script  (exit %d)" % ("PASS    " if ok else "FAILED  ", r.returncode))
    if not ok:
        print("the control FAILS: no mutant is judged")
        return 1
    killed = errors = 0
    for i, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: the edit matches %d times" % (i, name, src.count(old)))
            errors += 1
            continue
        d = tempfile.mkdtemp(prefix="gi-b-jam-m%d-" % i, dir=gi_paths.scratch("tmp", run=False))
        try:
            mp = os.path.join(d, SCRIPT)
            with open(mp, "w", encoding="utf-8", newline="") as fh:
                fh.write(src.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--script", mp],
                               capture_output=True, text=True, timeout=600)
        finally:
            gi_paths.remove_tree(d)
        fails = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL")]
        crashed = "Traceback" in r.stderr
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d%s%s)" % ("KILLED  " if dead else "SURVIVED", i, name, r.returncode,
                                           ", CRASHED" if crashed else "", ("; " + fails[0][:90]) if fails else ""))
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


def main():
    if os.name == "nt" or not BASH:
        print("REFUSED: Linux only (bench_host.sh names python3.13 there)")
        return 2
    if "--mutants" in sys.argv[1:]:
        return mutants()
    path = sys.argv[sys.argv.index("--script") + 1] if "--script" in sys.argv[1:] else os.path.join(HERE, SCRIPT)
    checks(open(path, encoding="utf-8", newline="").read())
    print("\n%s" % ("%d FAIL" % len(FAILS) if FAILS else "all ok"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
