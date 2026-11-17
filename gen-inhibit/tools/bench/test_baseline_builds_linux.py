#!/usr/bin/env python3
"""Harness test for baseline_builds.py, the Linux port of baseline_builds.ps1 (gen-inhibit
tester, 2026-11-16, the gen-inhibit reviewer's item 2), with real child processes: the
script runs against a fake idf.py on PATH and a fake launcher at gen-inhibit/vtrux_tools.py
beside a fake tree (a git repo), all in this run's scratch folder. Each fake appends what it
was run with (its arguments, folder and a planted variable) to one log, in order. No ESP-IDF,
no build.

Checks:
  - the names and defines are the .ps1's table, read from it, name for name;
  - --bench is exactly the bench suite's WiCAN images (suite_gen_inhibit.WICAN_BUILDS'
    folders, read from the suite's source), built in that order;
  - each build runs check-env first, through the tree's own launcher, asked for check-env
    alone, from the worktree's root, with the shell's environment; then idf.py
    -B <tree>/build-tester-<name> <its defines> build, in the tree;
  - each build's log: git status and HEAD before, check-env's command, output and exit
    before the idf.py command, start, end and seconds, idf.py's output and exit, and git
    status after;
  - a launcher that refuses, crashes, does not know check-env or is missing stops that build
    (exit 3, idf.py never run for it, no build folder, the log says refused); a refusal then
    a failed build is still exit 3;
  - an existing build folder is refused (exit 2) and left as it was, the other builds go
    on; a failed build is exit 1, and an existing folder's 2 is not hidden by it;
  - an unknown name, IDF_PATH unset, or a tree that is not gen-inhibit/firmware: exit 2,
    nothing run at all.

    python3.13 -B gen-inhibit/tools/bench/test_baseline_builds_linux.py [--mutants]

--mutants: each mutant of baseline_builds.py is a copy in this run's scratch folder with one
edit, run through GI_BLB_DIR; a check must fail on each, and the unmutated copy, the control,
must pass. A crash is not a kill.
"""
import ast
import json
import os
import re
import stat
import subprocess
import sys

# no bytecode anywhere below this process: a child importing the pinned core must not write __pycache__
# into its read-only checkout (charge-interposer tester, 2026-11-16)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("GI_BLB_DIR") or HERE
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

SCRIPT = os.path.join(SRC, "baseline_builds.py")
RESULTS = []

FAKE = r'''#!%s -B
import json, os, sys
WHO = %r
log = os.environ["BLB_LOG"]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"who": WHO, "file": os.path.abspath(__file__), "argv": sys.argv[1:],
                        "cwd": os.getcwd(), "plant": os.environ.get("BLB_PLANT")}) + "\n")
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


def layout(d, launcher=True):
    """d/repo, a git repo with gen-inhibit/firmware (the tree) and, unless not launcher,
    the fake gen-inhibit/vtrux_tools.py; d/bin/idf.py, the fake idf.py."""
    root = os.path.join(d, "repo")
    tree = os.path.join(root, "gen-inhibit", "firmware")
    write(os.path.join(tree, "CMakeLists.txt"), "# fake\n")
    write(os.path.join(root, ".gitignore"), "build-*/\n")
    if launcher:
        write(os.path.join(root, "gen-inhibit", "vtrux_tools.py"), FAKE % (sys.executable, "launcher"))
    write(os.path.join(d, "bin", "idf.py"), FAKE % (sys.executable, "idf"), exe=True)
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "fake"]):
        subprocess.run(["git", "-C", root] + args, check=True)
    return root, tree


def run(d, args, check_mode="ok", idf_rc=0, launcher=True, idf_path=True, pre=(), tree_arg=None):
    """baseline_builds.py with args after --tree/--out; pre: build folders made first."""
    root, tree = layout(d, launcher)
    for n in pre:
        write(os.path.join(tree, "build-tester-" + n, "keep.txt"), "made before\n")
    out = os.path.join(d, "out")
    log = os.path.join(d, "ran.jsonl")
    open(log, "w").close()
    env = dict(os.environ, BLB_LOG=log, BLB_PLANT="planted", BLB_CHECK=check_mode, BLB_IDF_RC=str(idf_rc),
               PATH=os.path.join(d, "bin") + os.pathsep + os.environ.get("PATH", ""))
    if idf_path:
        env["IDF_PATH"] = os.path.join(d, "idf")
    else:
        env.pop("IDF_PATH", None)
    p = subprocess.run([sys.executable, "-B", SCRIPT, "--tree", tree_arg or tree, "--out", out] + list(args),
                       env=env, capture_output=True, text=True, timeout=120)
    with open(log, encoding="utf-8") as f:
        ran = [json.loads(line) for line in f if line.strip()]
    logs = {}
    if os.path.isdir(out):
        for n in os.listdir(out):
            with open(os.path.join(out, n), encoding="utf-8", errors="replace") as f:
                logs[n[len("build_"):-len(".txt")]] = f.read()
    return {"rc": p.returncode, "out": p.stdout + p.stderr, "ran": ran, "logs": logs, "root": root, "tree": tree}


def who(r):
    return [x["who"] for x in r["ran"]]


def built(r, name):
    return os.path.isdir(os.path.join(r["tree"], "build-tester-" + name))


def ps1_defs():
    """The .ps1's $defs table: {name: [defines]}."""
    src = open(os.path.join(HERE, "baseline_builds.ps1"), encoding="utf-8").read()
    body = src[src.index("$defs = @{"):]
    body = body[:body.index("\n}\n")]
    return {m.group(1): re.findall(r'"([^"]+)"', m.group(2))
            for m in re.finditer(r'^\s*"(\w+)"\s*=\s*@\(([^)]*)\);', body, re.M)}


def suite_builds():
    """suite_gen_inhibit.WICAN_BUILDS' folders, in order, read from the suite's source."""
    tree = ast.parse(open(os.path.join(os.path.dirname(HERE), "suite_gen_inhibit.py"), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "WICAN_BUILDS" for t in node.targets):
            return [v.value for v in node.value.values]
    return None


def refused(label, r, names, launcher_calls):
    check("%s: exit 3" % label, r["rc"] == 3, "exit %s; %s" % (r["rc"], r["out"]))
    check("%s: idf.py never ran" % label, "idf" not in who(r), who(r))
    check("%s: no build folder" % label, not any(built(r, n) for n in names))
    check("%s: the launcher was asked %d time(s)" % (label, launcher_calls),
          who(r).count("launcher") == launcher_calls, who(r))
    for n in names:
        lg = r["logs"].get(n, "")
        check("%s: build_%s.txt says refused and names no idf.py command" % (label, n),
              "refused" in lg and "command: idf.py" not in lg, lg)


def cases(d):
    sys.path.insert(0, SRC)
    import baseline_builds as B
    want = ps1_defs()
    check("names: the .ps1's table was read (14 names)", len(want) == 14, sorted(want))
    check("names: the same names and defines as the .ps1, name for name", B.DEFS == want, (B.DEFS, want))
    sb = suite_builds()
    check("--bench: exactly the bench suite's WiCAN images, in order", sb is not None and list(B.BENCH) == sb,
          (B.BENCH, sb))

    r = run(os.path.join(d, "ok"), ["--builds", "man,rt"])
    check("ok: exit 0", r["rc"] == 0, "exit %s; %s" % (r["rc"], r["out"]))
    check("ok: check-env before each build", who(r) == ["launcher", "idf", "launcher", "idf"], who(r))
    for i, x in enumerate(x for x in r["ran"] if x["who"] == "launcher"):
        check("ok: launcher %d is the tree's own" % i,
              x["file"] == os.path.join(r["root"], "gen-inhibit", "vtrux_tools.py"), x["file"])
        check("ok: launcher %d asked for check-env alone" % i, x["argv"] == ["check-env"], x["argv"])
        check("ok: launcher %d ran from the worktree's root" % i, x["cwd"] == r["root"], x["cwd"])
        check("ok: launcher %d saw the shell's environment" % i, x["plant"] == "planted", x)
    idfs = [x for x in r["ran"] if x["who"] == "idf"]
    check("ok: idf.py built man then rt, each into its own folder with its defines",
          [x["argv"] for x in idfs] == [["-B", os.path.join(r["tree"], "build-tester-man"), "build"],
                                       ["-B", os.path.join(r["tree"], "build-tester-rt"),
                                        "-DRESTART_GUARD_TEST=1", "build"]], [x["argv"] for x in idfs])
    check("ok: idf.py ran in the tree", all(x["cwd"] == r["tree"] for x in idfs), [x["cwd"] for x in idfs])
    head = subprocess.run(["git", "-C", r["root"], "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    for n in ("man", "rt"):
        lg = r["logs"].get(n, "")
        check("ok: build_%s.txt has git status and HEAD before" % n, "git status before: [] HEAD %s" % head in lg, lg)
        check("ok: build_%s.txt has check-env's command, output and exit 0 before the idf.py command" % n,
              "check-env: %s -B " % sys.executable in lg and "check-env: ok" in lg and "check-env exit: 0" in lg
              and lg.find("check-env exit: 0") < lg.find("command: idf.py -B"), lg)
        check("ok: build_%s.txt has idf.py's output, times and exit 0, then git status after" % n,
              "fake idf.py: built" in lg and "start: " in lg and "seconds " in lg and "idf exit: 0" in lg
              and "git status after: []" in lg, lg)

    r = run(os.path.join(d, "bench"), ["--bench"])
    check("--bench: builds the bench set in order",
          [os.path.basename(x["argv"][1]) for x in r["ran"] if x["who"] == "idf"]
          == ["build-tester-" + n for n in (sb or [])] and r["rc"] == 0, (r["rc"], who(r)))
    r = run(os.path.join(d, "fall"), ["--builds", "fall"])
    check("defines: fall's four switches, in order",
          [x["argv"][2:-1] for x in r["ran"] if x["who"] == "idf"] == [want.get("fall")], r["ran"])

    for mode, label in (("refuse", "check-env refuses"), ("crash", "check-env crashes"),
                        ("unknown", "the launcher has no check-env")):
        refused(label, run(os.path.join(d, mode), ["--builds", "man,rt"], check_mode=mode), ("man", "rt"), 2)
    r = run(os.path.join(d, "nolauncher"), ["--builds", "man"], launcher=False)
    refused("no launcher", r, ("man",), 0)
    check("no launcher: build_man.txt names the missing launcher", "no launcher at" in r["logs"].get("man", ""))

    r = run(os.path.join(d, "mixed"), ["--builds", "man,rt"], check_mode="refuse_first", idf_rc=1)
    check("refused then failed: exit 3, not hidden by the later failure", r["rc"] == 3, r["rc"])
    check("refused then failed: man not built, rt built after its own check-env",
          who(r) == ["launcher", "launcher", "idf"] and not built(r, "man") and built(r, "rt"), who(r))
    r = run(os.path.join(d, "idffail"), ["--builds", "man"], idf_rc=1)
    check("idf.py fails: exit 1", r["rc"] == 1, r["rc"])

    r = run(os.path.join(d, "exists"), ["--builds", "man,rt"], pre=("man",))
    check("an existing build folder: exit 2", r["rc"] == 2, (r["rc"], r["out"]))
    check("an existing build folder: left as it was, nothing run for it, rt built",
          os.listdir(os.path.join(r["tree"], "build-tester-man")) == ["keep.txt"]
          and who(r) == ["launcher", "idf"] and built(r, "rt") and "man" not in r["logs"], (who(r), r["logs"].keys()))
    r = run(os.path.join(d, "exists_fail"), ["--builds", "man,rt"], pre=("man",), idf_rc=1)
    check("an existing folder, then a failed build: exit 2, not hidden by the 1", r["rc"] == 2, r["rc"])

    for label, kw in (("an unknown name", dict(args=["--builds", "man,nosuch"])),
                      ("no name", dict(args=["--builds", ","])),
                      ("IDF_PATH unset", dict(args=["--builds", "man"], idf_path=False)),
                      ("a tree that is not gen-inhibit/firmware", dict(args=["--builds", "man"], tree_arg="REPO"))):
        dd = os.path.join(d, "pre_" + re.sub(r"\W+", "_", label))
        if kw.get("tree_arg") == "REPO":
            kw["tree_arg"] = os.path.join(dd, "repo")
        r = run(dd, **kw)
        check("%s: exit 2, nothing run at all" % label, r["rc"] == 2 and r["ran"] == [] and not r["logs"],
              (r["rc"], who(r), r["out"]))


MUTANTS = [
    ("check-env not run", "        envrc = run_logged(env_cmd, root, log)\n", "        envrc = 0\n"),
    ("a refusal is ignored", "        if envrc != 0:\n", "        if False:\n"),
    ("only exit 1 is a refusal", "        if envrc != 0:\n", "        if envrc == 1:\n"),
    ("a refusal still builds", "            say(\"%s : check-env refused (exit %s), nothing built\" % (name, envrc))\n"
     "            return REFUSED_ENV\n", "            say(\"%s : check-env refused (exit %s), nothing built\" % (name, envrc))\n"),
    ("a missing launcher is not looked for", "        if not os.path.isfile(launcher):\n", "        if False:\n"),
    ("check-env from the tree, not the root", "        envrc = run_logged(env_cmd, root, log)\n",
     "        envrc = run_logged(env_cmd, tree, log)\n"),
    ("check-env asked with another command", '        env_cmd = [sys.executable, "-B", launcher, "check-env"]\n',
     '        env_cmd = [sys.executable, "-B", launcher, "check-env", "--quiet"]\n'),
    ("the launcher not the tree's own", '    launcher = os.path.join(os.path.dirname(tree), "vtrux_tools.py")\n',
     '    launcher = os.path.join(tree, "vtrux_tools.py")\n'),
    ("an existing folder is reused", "    if os.path.exists(d):\n", "    if False:\n"),
    ("defines dropped", '        cmd = [IDF, "-B", d] + DEFS[name] + ["build"]\n', '        cmd = [IDF, "-B", d, "build"]\n'),
    ("rt's define wrong", '    "rt": ["-DRESTART_GUARD_TEST=1"],\n', '    "rt": ["-DRESTART_GUARD=1"],\n'),
    ("fslcan dropped", '    "fslcan": ["-DWICAN_SLCAN_COMMANDS=ON"],\n', ""),
    ("--bench misses one", 'BENCH = ("man", "rt", "txabort", "abortwin", "rxq")', 'BENCH = ("man", "rt", "txabort", "rxq")'),
    ("idf.py not in the tree", "        code = run_logged(cmd, tree, log)\n", "        code = run_logged(cmd, root, log)\n"),
    ("a failed build exits 0", "    return 0 if code == 0 else FAILED\n", "    return 0\n"),
    ("a failure hides a refusal", "    for rc in (REFUSED_ENV, REFUSED, FAILED):\n", "    for rc in (FAILED, REFUSED_ENV, REFUSED):\n"),
    ("a failure hides an existing folder", "    for rc in (REFUSED_ENV, REFUSED, FAILED):\n",
     "    for rc in (REFUSED_ENV, FAILED, REFUSED):\n"),
    ("an unknown name goes on", "    if not names or bad:\n", "    if not names:\n"),
    ("IDF_PATH not looked at", '    if not os.environ.get("IDF_PATH"):\n', "    if False:\n"),
    ("any tree accepted", "        say(\"refused: %s is not a worktree's gen-inhibit/firmware; nothing built\" % tree)\n        return REFUSED\n",
     "        say(\"refused: %s is not a worktree's gen-inhibit/firmware; nothing built\" % tree)\n"),
    ("no git status before", '        log.write("git status before: [%s] HEAD %s\\n" % (st, git(tree, "rev-parse", "HEAD")))\n', ""),
    ("no git status after", '        log.write("git status after: [%s]\\n" % st)\n', ""),
    ("check-env's exit not logged", '        log.write("check-env exit: %s\\n" % envrc)\n', ""),
]


def mutants():
    base = gi_paths.scratch("mutants-baseline_builds_linux", start=__file__)
    src = open(os.path.join(HERE, "baseline_builds.py"), encoding="utf-8").read()
    killed = errors = 0
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        os.makedirs(d)
        with open(os.path.join(d, "baseline_builds.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(src if old is None else src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, GI_BLB_DIR=d), timeout=600)
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
    d = gi_paths.scratch("test-baseline_builds_linux", start=__file__)
    try:
        cases(d)
    except Exception as e:      # noqa: BLE001 -- a crash is a failed check, never a pass
        check("the cases ran to their end", False, repr(e))
    fails = [n for n, ok in RESULTS if not ok]
    print("PASS, %d ok" % len(RESULTS) if not fails else "FAIL: %d of %d" % (len(fails), len(RESULTS)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv[1:] else main())
