#!/usr/bin/env python3
"""Tests for baseline_builds.ps1's check-env step (port step 1d), with real child processes:
Windows PowerShell runs the script against a fake ESP-IDF (an export.ps1 that marks the
shell exported, and a tools/idf.py that makes the build folder) and a fake launcher at
gen-inhibit/vtrux_tools.py beside the tree, all written in scratch/. Each fake appends what
it was run with (its arguments, folder and the variables it watches) to one log, in the
order they ran. No ESP-IDF, no build. Windows only.

    py -3.14 -B gen-inhibit/tools/bench/test_baseline_builds.py

The can-fail is the point of the step: a launcher that refuses (exit 1), crashes (a
traceback), does not know check-env (exit 2) or is not there stops that build: no build
folder, idf.py never run, the script exits 3, and a later build's failure does not hide it.
When check-env passes, each build runs it first, after the export, through the tree's own
launcher, from its worktree's root, with the shell's environment as it is (a planted
variable, and IDF_TOOLS_PATH as set); then idf.py builds into build-tester-<name>.

Gen-inhibit tester, 2026-11-04 (port step 1d).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

SCRIPT = os.path.join(HERE, "baseline_builds.ps1")
WATCH = ("BLB_EXPORTED", "IDF_TOOLS_PATH", "BLB_PLANT")
fails = []
oks = 0

FAKE = r'''
import json, os, sys
WHO = %r
WATCH = %r
log = os.environ["BLB_LOG"]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"who": WHO, "file": os.path.abspath(__file__), "argv": sys.argv[1:],
                        "cwd": os.getcwd(), "env": {k: os.environ.get(k) for k in WATCH}}) + "\n")
if WHO == "idf":
    os.makedirs(sys.argv[sys.argv.index("-B") + 1])
    print("fake idf.py: built")
    sys.exit(int(os.environ.get("BLB_IDF_RC", "0")))
mode = os.environ.get("BLB_CHECK", "ok")
if mode == "refuse_first":
    n = sum(1 for line in open(log, encoding="utf-8") if '"who": "launcher"' in line)
    mode = "refuse" if n == 1 else "ok"
if sys.argv[1:] != ["check-env"] or mode == "unknown":
    print("unknown command %%r" %% sys.argv[1:])
    sys.exit(2)
if mode == "crash":
    raise KeyError("IDF_TOOLS_PATH")
if mode == "refuse":
    print("refused: IDF_TOOLS_PATH")
    sys.exit(1)
print("check-env: ok")
'''

EXPORT = ('$env:BLB_EXPORTED = "1"\n'
          "Add-Content -Encoding ascii -Path $env:BLB_LOG -Value '{\"who\": \"export\"}'\n")


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def layout(d, launcher=True):
    """A worktree root d/repo with gen-inhibit/firmware (the tree) and, unless not
    launcher, the fake gen-inhibit/vtrux_tools.py; and a fake ESP-IDF in d/idf."""
    root = os.path.join(d, "repo")
    tree = os.path.join(root, "gen-inhibit", "firmware")
    write(os.path.join(tree, "CMakeLists.txt"), "# fake\n")
    if launcher:
        write(os.path.join(root, "gen-inhibit", "vtrux_tools.py"), FAKE % ("launcher", WATCH))
    write(os.path.join(d, "idf", "export.ps1"), EXPORT)
    write(os.path.join(d, "idf", "tools", "idf.py"), FAKE % ("idf", WATCH))
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "fake"]):
        subprocess.run(["git", "-C", root] + args, check=True)
    return root, tree


def run(d, builds, check_mode="ok", idf_rc=0, launcher=True):
    root, tree = layout(d, launcher)
    out = os.path.join(d, "out")
    log = os.path.join(d, "ran.jsonl")
    open(log, "w").close()
    env = dict(os.environ, BLB_LOG=log, BLB_PLANT="planted", BLB_CHECK=check_mode,
               BLB_IDF_RC=str(idf_rc), IDF_PATH=os.path.join(d, "idf"),
               IDF_TOOLS_PATH=os.path.join(d, "tools-5.4.2"),
               GI_IDF_PYTHON=os.path.dirname(sys.executable))
    env.pop("BLB_EXPORTED", None)
    cmd = "& '%s' -Tree '%s' -Out '%s' -Builds %s; exit $LASTEXITCODE" % (SCRIPT, tree, out, builds)
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                       env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    with open(log, encoding="utf-8") as f:
        ran = [json.loads(line) for line in f if line.strip()]
    logs = {}
    for name in builds.split(","):
        p_ = os.path.join(out, "build_%s.txt" % name)
        if os.path.exists(p_):
            with open(p_, encoding="utf-8-sig", errors="replace") as f:
                logs[name] = f.read()
    return {"rc": p.returncode, "out": p.stdout + p.stderr, "ran": ran, "logs": logs,
            "root": root, "tree": tree}


def who(r):
    return [x["who"] for x in r["ran"]]


def built(r, name):
    return os.path.isdir(os.path.join(r["tree"], "build-tester-" + name))


def refused(label, r, names, launcher_calls):
    check("%s: exit 3" % label, r["rc"] == 3, "exit %s; %s" % (r["rc"], r["out"][-600:]))
    check("%s: idf.py never ran" % label, "idf" not in who(r), who(r))
    check("%s: no build folder" % label, not any(built(r, n) for n in names))
    check("%s: the launcher was asked %d time(s)" % (label, launcher_calls),
          who(r).count("launcher") == launcher_calls, who(r))
    for n in names:
        lg = r["logs"].get(n, "")
        check("%s: build_%s.txt says refused and names no idf.py command" % (label, n),
              "refused" in lg and "command: idf.py" not in lg, lg[-400:])


def main():
    if os.name != "nt":
        print("SKIP: Windows PowerShell only")
        return 0
    d = gi_paths.scratch("baseline-builds-test")

    r = run(os.path.join(d, "ok"), "man,rt")
    check("ok: exit 0", r["rc"] == 0, "exit %s; %s" % (r["rc"], r["out"][-600:]))
    check("ok: export, then check-env before each build",
          who(r) == ["export", "launcher", "idf", "launcher", "idf"], who(r))
    launchers = [x for x in r["ran"] if x["who"] == "launcher"]
    want_file = os.path.normcase(os.path.join(r["root"], "gen-inhibit", "vtrux_tools.py"))
    for i, x in enumerate(launchers):
        check("ok: launcher %d is the tree's own" % i, os.path.normcase(x["file"]) == want_file, x["file"])
        check("ok: launcher %d asked for check-env alone" % i, x["argv"] == ["check-env"], x["argv"])
        check("ok: launcher %d ran from the worktree's root" % i,
              os.path.normcase(x["cwd"]) == os.path.normcase(r["root"]), x["cwd"])
        check("ok: launcher %d ran in the exported shell" % i, x["env"]["BLB_EXPORTED"] == "1", x["env"])
        check("ok: launcher %d saw IDF_TOOLS_PATH as set" % i,
              x["env"]["IDF_TOOLS_PATH"] == os.path.join(d, "ok", "tools-5.4.2"), x["env"])
        check("ok: launcher %d saw the planted variable" % i, x["env"]["BLB_PLANT"] == "planted", x["env"])
    idfs = [x for x in r["ran"] if x["who"] == "idf"]
    check("ok: idf.py built man then rt, each into its own folder",
          [x["argv"] for x in idfs] == [["-B", os.path.join(r["tree"], "build-tester-man"), "build"],
                                       ["-B", os.path.join(r["tree"], "build-tester-rt"),
                                        "-DRESTART_GUARD_TEST=1", "build"]],
          [x["argv"] for x in idfs])
    for n in ("man", "rt"):
        lg = r["logs"].get(n, "")
        check("ok: build_%s.txt has check-env's output and exit 0 before the idf.py command" % n,
              "check-env: ok" in lg and "check-env exit: 0" in lg
              and lg.find("check-env exit: 0") < lg.find("command: idf.py") and "idf exit: 0" in lg, lg[-500:])

    for mode, label in (("refuse", "check-env refuses"), ("crash", "check-env crashes"),
                        ("unknown", "the launcher has no check-env")):
        refused(label, run(os.path.join(d, mode), "man,rt", check_mode=mode), ("man", "rt"), 2)

    r = run(os.path.join(d, "nolauncher"), "man", launcher=False)
    refused("no launcher", r, ("man",), 0)
    check("no launcher: build_man.txt names the missing launcher",
          "no launcher at" in r["logs"].get("man", ""), r["logs"].get("man", "")[-300:])

    r = run(os.path.join(d, "mixed"), "man,rt", check_mode="refuse_first", idf_rc=1)
    check("refused then failed: exit 3, the refusal not hidden by the later build's exit 1",
          r["rc"] == 3, "exit %s" % r["rc"])
    check("refused then failed: man not built, rt built after its own check-env",
          who(r) == ["export", "launcher", "launcher", "idf"] and not built(r, "man") and built(r, "rt"), who(r))

    r = run(os.path.join(d, "idffail"), "man", idf_rc=1)
    check("check-env passes, idf.py fails: exit 1", r["rc"] == 1, "exit %s" % r["rc"])

    print("%d ok, %d failed%s" % (oks, len(fails), (": " + ", ".join(fails)) if fails else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
