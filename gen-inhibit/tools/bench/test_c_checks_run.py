#!/usr/bin/env python3
"""Harness test for c_checks_run.py, the Python port of c_checks_run.ps1 (suite check
C2scan; gen-inhibit tester, 2026-11-16, the gen-inhibit reviewer's item 1), with real child
processes: the script runs against a fake ESP-IDF (IDF_PATH/export.sh, which prints noise and
puts a fake `python` first on PATH) and a fake clone (a git repo with
gen-inhibit/test/build_checks/scan_scope_check.py), all in this run's scratch folder. A decoy
`python` is first on the caller's PATH, and the clone's scan_scope_check.py, run by any
python, logs itself, so a check run without the export, or by another interpreter, shows.
Each fake appends what it saw to one log. No ESP-IDF, no build.

Checks:
  - the check runs after the export, as `python -B scan_scope_check.py --commit <c>
    --components <absolute> --out <outdir>/scan_scope_check_<c>.json`, the exported
    environment's python, in the clone's test/build_checks/, with PYTHONDONTWRITEBYTECODE=1;
    the export sees IDF_TOOLS_PATH; the caller's python never runs;
  - <outdir>/scan_scope_check_<c>.txt: the start line (time, check, commit, the clone's short
    HEAD), the check's stdout and stderr, the end line (time, exit); the export's output is
    not in it but in scan_scope_check_<c>_export.txt; stdout `scan_scope_check exit <n>`
    then `c checks done`;
  - the suite's own C2scan judge (suite_judges) reads that .txt: PASS with 3 steps for a
    passing check, FAIL for a failing one; the script exits 0 for both (its verdict is the
    .txt's, as with the .ps1);
  - relative --clone, --components and --outdir are made absolute, and a missing outdir is
    made;
  - a failing export: exit 3, the check never runs, the .txt says so and does not judge PASS;
  - IDF_PATH unset, IDF_TOOLS_PATH unset, no export.sh in IDF_PATH, a --clone with no
    test/build_checks/scan_scope_check.py, or a missing argument: exit 2, nothing run (not
    even the export), nothing written.

    python3.13 -B gen-inhibit/tools/bench/test_c_checks_run.py [--mutants]

--mutants: each mutant of c_checks_run.py is a copy in this run's scratch folder with one
edit, run through GI_CCR_DIR; a check must fail on each, and the unmutated copy, the control,
must pass. A crash is not a kill.
"""
import json
import os
import re
import stat
import subprocess
import sys

# no bytecode anywhere below this process (charge-interposer tester, 2026-11-16)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("GI_CCR_DIR") or HERE
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

sys.path.insert(0, os.path.dirname(HERE))
import suite_judges as SJ  # noqa: E402

SCRIPT = os.path.join(SRC, "c_checks_run.py")
COMMIT = "743cf7cd"
RESULTS = []

EXPORT = r'''# fake ESP-IDF export.sh (sourced)
printf '%%s\n' "{\"who\": \"export\", \"tools\": \"$IDF_TOOLS_PATH\"}" >> "$CCR_LOG"
echo "Detecting the Python interpreter"
echo "  ok noise from the export"
echo "PASS noise from the export"
export PATH="%s:$PATH"
export CCR_EXPORTED=yes
if [ "${CCR_EXPORT_RC:-0}" != 0 ]; then echo "ERROR: fake export failed"; return "$CCR_EXPORT_RC"; fi
'''

FAKE = r'''#!%s -B
import json, os, sys
WHO = %r
with open(os.environ["CCR_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"who": WHO, "argv": sys.argv[1:], "cwd": os.getcwd(),
                        "exported": os.environ.get("CCR_EXPORTED"),
                        "nobytecode": os.environ.get("PYTHONDONTWRITEBYTECODE")}) + "\n")
if WHO != "python":
    sys.exit(5)
a = sys.argv[1:]
if a[:2] != ["-B", "scan_scope_check.py"]:
    sys.exit(6)
mode = os.environ.get("CCR_SCAN", "pass")
with open(a[a.index("--out") + 1], "w", encoding="utf-8") as f:
    json.dump({"verdict": mode.upper()}, f)
print("  ok   baseline: the scan's result")
if mode == "pass":
    print("  ok   planted: the scan failed on it")
    print("  ok   set-first: the scan passed")
    print("PASS (3 of 3 steps)")
else:
    print("  FAIL planted: the scan did not report it")
    print("  ok   set-first: the scan passed")
    print("FAIL (2 of 3 steps)")
sys.stderr.write("scan stderr line\n")
sys.exit(0 if mode == "pass" else 1)
'''

START = re.compile(r"^start \d{4}-\d\d-\d\d \d\d:\d\d:\d\d scan_scope_check --commit %s \(clone HEAD (\w+)\)$" % COMMIT)
END = re.compile(r"^end \d{4}-\d\d-\d\d \d\d:\d\d:\d\d exit (\d+)$")


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", name, "" if ok else "  [%s]" % (str(detail)[-400:],)))


def write(path, text, exe=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if exe:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def layout(d, export=True):
    """d/repo (git, gen-inhibit/test/build_checks/scan_scope_check.py logging itself),
    d/idf/export.sh, d/idfbin/python (the exported one), d/callerbin/python (the decoy)."""
    root = os.path.join(d, "repo")
    write(os.path.join(root, "gen-inhibit", "test", "build_checks", "scan_scope_check.py"),
          FAKE % (sys.executable, "the clone's file, run by another python"))
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "fake"]):
        subprocess.run(["git", "-C", root] + args, check=True)
    os.makedirs(os.path.join(d, "idf"), exist_ok=True)
    if export:
        write(os.path.join(d, "idf", "export.sh"), EXPORT % os.path.join(d, "idfbin"))
    write(os.path.join(d, "idfbin", "python"), FAKE % (sys.executable, "python"), exe=True)
    write(os.path.join(d, "callerbin", "python"), FAKE % (sys.executable, "the caller's python"), exe=True)
    return root


def run(d, scan="pass", export_rc=0, unset=(), export=True, clone=None, args=None, cwd=None,
        outdir=None, components=None):
    root = layout(d, export)
    log = os.path.join(d, "ran.jsonl")
    open(log, "w").close()
    env = dict(os.environ, CCR_LOG=log, CCR_SCAN=scan, CCR_EXPORT_RC=str(export_rc),
               IDF_PATH=os.path.join(d, "idf"), IDF_TOOLS_PATH="planted-tools",
               PATH=os.path.join(d, "callerbin") + os.pathsep + os.environ.get("PATH", ""))
    env.pop("CCR_EXPORTED", None)
    # not inherited from this process (which sets it): the script must set it for the check itself
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    for k in unset:
        env.pop(k, None)
    out = outdir or os.path.join(d, "runs", "deep", "out")
    comps = components or os.path.join(d, "comps")
    if args is None:
        args = ["--clone", clone or os.path.join(root, "gen-inhibit"), "--commit", COMMIT,
                "--components", comps, "--outdir", out]
    p = subprocess.run([sys.executable, "-B", SCRIPT] + args, env=env, capture_output=True, text=True,
                       timeout=120, cwd=cwd or d)
    with open(log, encoding="utf-8") as f:
        ran = [json.loads(line) for line in f if line.strip()]
    outabs = os.path.join(cwd or d, out)
    files = {}
    if os.path.isdir(outabs):
        for n in os.listdir(outabs):
            with open(os.path.join(outabs, n), encoding="utf-8", errors="replace") as f:
                files[n] = f.read()
    return {"rc": p.returncode, "stdout": p.stdout, "out": p.stdout + p.stderr, "ran": ran, "files": files,
            "root": root, "outdir": outabs}


def who(r):
    return [x["who"] for x in r["ran"]]


def txt(r):
    return r["files"].get("scan_scope_check_%s.txt" % COMMIT, "")


def judge(r):
    return SJ.judge_one("C2scan", r["outdir"])[0] if os.path.isdir(r["outdir"]) else {"verdict": None}


def cases(d):
    r = run(os.path.join(d, "pass"))
    root = r["root"]
    checks_dir = os.path.join(root, "gen-inhibit", "test", "build_checks")
    out = os.path.join(d, "pass", "runs", "deep", "out")
    jsn = os.path.join(out, "scan_scope_check_%s.json" % COMMIT)
    check("pass: exit 0", r["rc"] == 0, "exit %s; %s" % (r["rc"], r["out"]))
    check("pass: stdout is `scan_scope_check exit 0` then `c checks done`",
          r["stdout"].splitlines() == ["scan_scope_check exit 0", "c checks done"], r["stdout"])
    check("pass: the export ran, then the exported python, and nothing else", who(r) == ["export", "python"], who(r))
    check("pass: the export saw IDF_TOOLS_PATH", r["ran"] and r["ran"][0].get("tools") == "planted-tools", r["ran"])
    py = [x for x in r["ran"] if x["who"] == "python"]
    py = py[0] if py else {}
    check("pass: the check ran as python -B scan_scope_check.py --commit --components <absolute> --out <outdir json>",
          py.get("argv") == ["-B", "scan_scope_check.py", "--commit", COMMIT, "--components",
                             os.path.join(d, "pass", "comps"), "--out", jsn], py.get("argv"))
    check("pass: in the clone's test/build_checks/", py.get("cwd") == checks_dir, py.get("cwd"))
    check("pass: in the exported environment", py.get("exported") == "yes", py)
    check("pass: with PYTHONDONTWRITEBYTECODE=1", py.get("nobytecode") == "1", py)
    check("pass: the check's json is where --out said", os.path.isfile(jsn), sorted(r["files"]))
    lines = txt(r).splitlines()
    head = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    m = START.match(lines[0]) if lines else None
    check("pass: the .txt starts with the start line naming the commit and the clone's short HEAD",
          m is not None and m.group(1) == head, lines[:1])
    e = END.match(lines[-1]) if lines else None
    check("pass: the .txt ends with the end line, exit 0", e is not None and e.group(1) == "0", lines[-1:])
    check("pass: the .txt has the check's stdout and stderr",
          "PASS (3 of 3 steps)" in lines and "scan stderr line" in lines, lines)
    check("pass: the export's output is not in the .txt", "noise from the export" not in txt(r)
          and "Detecting" not in txt(r), lines)
    check("pass: the export's output is in scan_scope_check_<commit>_export.txt",
          "PASS noise from the export" in r["files"].get("scan_scope_check_%s_export.txt" % COMMIT, ""),
          sorted(r["files"]))
    j = judge(r)
    check("pass: the suite's C2scan judge reads PASS, 3 steps",
          j.get("verdict") == "PASS" and (j.get("figures") or {}).get("steps", {}).get("value") == 3, j)

    r = run(os.path.join(d, "fail"), scan="fail")
    check("fail: exit 0 (the verdict is the .txt's)", r["rc"] == 0, (r["rc"], r["out"]))
    check("fail: stdout says `scan_scope_check exit 1`", "scan_scope_check exit 1" in r["stdout"].splitlines(),
          r["stdout"])
    lines = txt(r).splitlines()
    e = END.match(lines[-1]) if lines else None
    check("fail: the .txt ends with exit 1", e is not None and e.group(1) == "1", lines[-1:])
    check("fail: the suite's C2scan judge reads FAIL", judge(r).get("verdict") == "FAIL", judge(r))

    dd = os.path.join(d, "relative")
    r = run(dd, args=["--clone", os.path.join("repo", "gen-inhibit"), "--commit", COMMIT,
                      "--components", "comps", "--outdir", os.path.join("new", "out")],
            outdir=os.path.join("new", "out"))
    py = [x for x in r["ran"] if x["who"] == "python"]
    check("relative: exit 0, the outdir made, the .txt in it", r["rc"] == 0 and txt(r) != "",
          (r["rc"], r["out"], sorted(r["files"])))
    check("relative: --components and --out passed on as absolute paths",
          py and py[0]["argv"][5] == os.path.join(dd, "comps")
          and py[0]["argv"][7] == os.path.join(dd, "new", "out", "scan_scope_check_%s.json" % COMMIT),
          py and py[0]["argv"])

    r = run(os.path.join(d, "exportfail"), export_rc=4)
    check("export fails: exit 3", r["rc"] == 3, (r["rc"], r["out"]))
    check("export fails: the check never ran", who(r) == ["export"], who(r))
    check("export fails: the .txt says the export failed, with its exit",
          "export.sh failed (exit 4)" in txt(r), txt(r))
    check("export fails: the .txt does not judge PASS", judge(r).get("verdict") != "PASS", judge(r))

    for label, kw in (("IDF_PATH unset", dict(unset=("IDF_PATH",))),
                      ("IDF_TOOLS_PATH unset", dict(unset=("IDF_TOOLS_PATH",))),
                      ("no export.sh in IDF_PATH", dict(export=False)),
                      ("a --clone without test/build_checks/scan_scope_check.py", dict(clone="REPO")),
                      ("no --components", dict(args=["--clone", "x", "--commit", COMMIT, "--outdir", "out"]))):
        dd = os.path.join(d, "pre_" + re.sub(r"\W+", "_", label))
        if kw.get("clone") == "REPO":
            kw["clone"] = os.path.join(dd, "repo")
        r = run(dd, **kw)
        check("%s: exit 2, nothing run, nothing written" % label,
              r["rc"] == 2 and r["ran"] == [] and not os.path.isdir(r["outdir"]),
              (r["rc"], who(r), r["out"][-300:]))


MUTANTS = [
    ("no export", "EXPORT_THEN_RUN = ('. \"$IDF_PATH/export.sh\" > \"$GI_EXPORT_LOG\" 2>&1 || '\n",
     "EXPORT_THEN_RUN = ('true || '\n"),
    ("the export's output in the .txt", "'. \"$IDF_PATH/export.sh\" > \"$GI_EXPORT_LOG\" 2>&1 || '",
     "'. \"$IDF_PATH/export.sh\" || '"),
    ("a failed export goes on", "exit 199; }; exec python", "}; exec python"),
    ("another python", "exec python -B", "exec python3 -B"),
    ("no -B", "exec python -B \"$@\"", "exec python \"$@\""),
    ("bytecode allowed", "    cenv = dict(env, PYTHONDONTWRITEBYTECODE=\"1\",\n", "    cenv = dict(env,\n"),
    ("the check outside test/build_checks", "cwd=checks, env=cenv", "cwd=outdir, env=cenv"),
    ("components left relative", "    a.components = os.path.abspath(a.components)", "    pass"),
    ("the json elsewhere", "    jsn = os.path.join(outdir, \"%s_%s.json\" % (c, a.commit))",
     "    jsn = os.path.join(a.clone, \"%s_%s.json\" % (c, a.commit))"),
    ("the .txt misnamed", "    txt = os.path.join(outdir, \"%s_%s.txt\" % (c, a.commit))",
     "    txt = os.path.join(outdir, \"%s-%s.txt\" % (c, a.commit))"),
    ("no clone HEAD", "capture_output=True, text=True).stdout.strip()", "capture_output=True, text=True).stdout.strip()[:0]"),
    ("stderr dropped", "stdout=f, stderr=subprocess.STDOUT,", "stdout=f, stderr=subprocess.DEVNULL,"),
    ("no end line", "        f.write(\"end %s exit %s\\n\" % (stamp(), code))\n", ""),
    ("the check's exit is the script's", "    return EXPORT_FAIL if EXPORT_FAILED in codes else 0\n",
     "    return EXPORT_FAIL if EXPORT_FAILED in codes else max(codes)\n"),
    ("a failed export exits 0", "    return EXPORT_FAIL if EXPORT_FAILED in codes else 0\n", "    return 0\n"),
    ("no `c checks done`", "    say(\"c checks done\")\n", ""),
    ("IDF_PATH not looked at", "    if not idf:\n", "    if False:\n"),
    ("IDF_TOOLS_PATH not looked at", "    if not env.get(\"IDF_TOOLS_PATH\"):\n", "    if False:\n"),
    ("export.sh not looked for", "    if not os.path.isfile(os.path.join(idf, \"export.sh\")):\n", "    if False:\n"),
    ("any clone accepted", "        if not os.path.isfile(os.path.join(a.clone, \"test\", \"build_checks\", c + \".py\")):\n",
     "        if False:\n"),
    ("a refusal still runs", "        say(\"refused: %s; nothing run\" % why)\n        return REFUSED\n",
     "        say(\"refused: %s; nothing run\" % why)\n"),
]


def mutants():
    base = gi_paths.scratch("mutants-c_checks_run", start=__file__)
    src = open(os.path.join(HERE, "c_checks_run.py"), encoding="utf-8").read()
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        os.makedirs(d)
        with open(os.path.join(d, "c_checks_run.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, GI_CCR_DIR=d), timeout=600)
        fails = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d)" % ("PASS    " if ok else "FAILED  ", name, r.returncode))
            if not ok:
                print(r.stdout[-1500:], r.stderr[-1500:])
                print("\nthe control FAILS: no mutant is judged")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (%d FAIL%s)%s" % ("KILLED  " if dead else "SURVIVED", n, name, len(fails),
                                           ", CRASHED" if crashed else "", ("; " + fails[0][:90]) if fails else ""))
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


def main():
    d = gi_paths.scratch("test-c_checks_run", start=__file__)
    try:
        cases(d)
    except Exception as e:      # noqa: BLE001 -- a crash is a failed check, never a pass
        check("the cases ran to their end", False, repr(e))
    fails = [n for n, ok in RESULTS if not ok]
    print("PASS, %d ok" % len(RESULTS) if not fails else "FAIL: %d of %d" % (len(fails), len(RESULTS)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv[1:] else main())
