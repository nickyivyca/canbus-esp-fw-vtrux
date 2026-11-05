#!/usr/bin/env python3
"""Tests of idf_build_shell.py, the acceptance runner's "idf" shell for the
interposer's builds. No ESP-IDF and no build: a stand-in IDF folder whose
export.ps1 sets what ESP-IDF's would, a stand-in command, and a throwaway
git checkout. Windows only, as the shell is.

    py -3.14 -B charge-interposer/fw_harness/idf_build_shell_test.py [--can-fail]

Each case names what it checks:
  1. the command sees the export: IDF_PATH and IDF_TOOLS_PATH as given,
     the Python folder first on PATH, what export.ps1 set (which writes to
     stderr from a native command, as ESP-IDF's does), no MSYSTEM though
     the shell had one, and runs in the current folder
  2. the command's exit code is passed on
  3. a tracked file the command changed (dependencies.lock) is a FAIL line
     naming it, exit 3; with the command's own failure, its code is kept
  4. an untracked file is listed, not failed
  5. a failing export, or one that leaves no IDF_PYTHON_ENV_PATH, is
     refused, exit 2, and the command never runs
  6. a checkout not clean before the command is refused, exit 2, and the
     command never runs; so is a folder that is not a git checkout
--can-fail runs the cases first on the unedited copy (the control, which
must pass), then on copies of idf_build_shell.py with one edit each; every
edit must make a case fail.

Charge-interposer tester, 2026-11-04.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
if CODE not in sys.path:
    sys.path.insert(0, CODE)

import scratch_dir                                      # noqa: E402

SHELL = os.path.join(HERE, "idf_build_shell.py")

# as ESP-IDF's export does, a native command writes to stderr on the way
EXPORT_PS1 = r"""if ($env:STANDIN_EXPORT_FAIL) { throw "the stand-in export failed" }
$x = cmd /c "echo Activating the stand-in 1>&2"
if (-not $env:STANDIN_NO_PYENV) {
  $env:IDF_PYTHON_ENV_PATH = Join-Path $env:IDF_TOOLS_PATH "python_env\standin"
}
$env:STANDIN_EXPORTED = "yes"
Write-Host "a line the real export prints"
"""

# the command: writes what it sees, then does what its arguments say
COMMAND = r"""import json, os, sys
out = {k: os.environ.get(k, "") for k in ("IDF_PATH", "IDF_TOOLS_PATH",
       "IDF_PYTHON_ENV_PATH", "STANDIN_EXPORTED")}
out["MSYSTEM"] = os.environ.get("MSYSTEM")
out["PATH0"] = os.environ.get("PATH", "").split(os.pathsep)[0]
out["cwd"] = os.getcwd()
with open(os.environ["STANDIN_SEEN"], "w") as f:
    json.dump(out, f)
for a in sys.argv[1:]:
    if a == "touch":
        with open(os.path.join("charge-interposer", "firmware", "dependencies.lock"), "a") as f:
            f.write("idf: 5.4.1\n")
    if a == "untracked":
        open("stray.txt", "w").close()
sys.exit(int(next((a for a in sys.argv[1:] if a.isdigit()), "0")))
"""


def git(d, *args):
    subprocess.run(["git", "-C", d] + list(args), check=True, capture_output=True)


class World:
    def __init__(self, base):
        self.base = base
        self.idf = os.path.join(base, "idf")
        self.tools = os.path.join(base, "tools")
        self.py = os.path.join(base, "py311")
        for d in (self.idf, self.tools, self.py):
            os.makedirs(d)
        with open(os.path.join(self.idf, "export.ps1"), "w", encoding="utf-8") as f:
            f.write(EXPORT_PS1)
        self.cmd = os.path.join(base, "command.py")
        with open(self.cmd, "w", encoding="utf-8", newline="\n") as f:
            f.write(COMMAND)
        self.repo = os.path.join(base, "repo")
        lock = os.path.join(self.repo, "charge-interposer", "firmware", "dependencies.lock")
        os.makedirs(os.path.dirname(lock))
        with open(lock, "w", encoding="utf-8", newline="\n") as f:
            f.write("idf: 5.4.2\n")
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "core.autocrlf", "false")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "tree")
        self.seen = os.path.join(base, "seen.json")

    def reset(self):
        git(self.repo, "checkout", "-q", "--", ".")
        git(self.repo, "clean", "-q", "-fdx")
        if os.path.exists(self.seen):
            os.remove(self.seen)

    def run(self, shell, args=(), env=None, cwd=None):
        e = dict(os.environ, STANDIN_SEEN=self.seen)
        e.pop("STANDIN_EXPORT_FAIL", None)
        e.update(env or {})
        r = subprocess.run([sys.executable, "-B", shell, "--idf-path", self.idf,
                            "--idf-tools-path", self.tools, "--python-dir", self.py, "--",
                            self.cmd] + list(args),
                           capture_output=True, text=True, env=e, cwd=cwd or self.repo,
                           timeout=300)
        return r.returncode, r.stdout + r.stderr

    def got(self):
        if not os.path.exists(self.seen):
            return None
        import json
        with open(self.seen) as f:
            return json.load(f)


def same(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def case_env(w, shell):
    w.reset()
    rc, out = w.run(shell, env={"MSYSTEM": "MINGW64"})      # as from Git Bash
    g = w.got() or {}
    assert rc == 0, ("rc", rc, out[-400:])
    assert same(g.get("IDF_PATH", ""), w.idf) and same(g.get("IDF_TOOLS_PATH", ""), w.tools), g
    assert same(g.get("PATH0", ""), w.py), ("PATH first", g.get("PATH0"))
    assert g.get("STANDIN_EXPORTED") == "yes", g
    assert same(g.get("IDF_PYTHON_ENV_PATH", ""), os.path.join(w.tools, "python_env", "standin")), g
    assert same(g.get("cwd", ""), w.repo), g
    assert g.get("MSYSTEM") is None, ("MSYSTEM reached the command", g.get("MSYSTEM"))


def case_rc(w, shell):
    w.reset()
    rc, out = w.run(shell, ["5"])
    assert rc == 5, ("rc", rc, out[-300:])


def case_tracked(w, shell):
    w.reset()
    rc, out = w.run(shell, ["touch"])
    fails = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
    assert rc == 3 and fails and "dependencies.lock" in fails[0], (rc, out[-400:])
    w.reset()
    rc, out = w.run(shell, ["touch", "4"])
    assert rc == 4 and any(ln.startswith("FAIL") for ln in out.splitlines()), (rc, out[-400:])


def case_untracked(w, shell):
    w.reset()
    rc, out = w.run(shell, ["untracked"])
    assert rc == 0 and "stray.txt" in out and not any(
        ln.startswith("FAIL") for ln in out.splitlines()), (rc, out[-400:])


def case_export_fails(w, shell):
    w.reset()
    rc, out = w.run(shell, env={"STANDIN_EXPORT_FAIL": "1"})
    assert rc == 2 and w.got() is None and "REFUSED" in out, (rc, out[-400:])
    w.reset()
    rc, out = w.run(shell, env={"STANDIN_NO_PYENV": "1"})
    assert rc == 2 and w.got() is None and "IDF_PYTHON_ENV_PATH" in out, (rc, out[-400:])


def case_not_clean(w, shell):
    w.reset()
    with open(os.path.join(w.repo, "charge-interposer", "firmware", "dependencies.lock"),
              "a") as f:
        f.write("changed before\n")
    rc, out = w.run(shell)
    assert rc == 2 and w.got() is None and "REFUSED" in out, (rc, out[-400:])
    w.reset()
    plain = os.path.join(w.base, "plain")
    os.makedirs(plain, exist_ok=True)
    # the test's folder is inside a worktree's scratch/: git must not look
    # above it
    rc, out = w.run(shell, cwd=plain, env={"GIT_CEILING_DIRECTORIES": w.base})
    assert rc == 2 and w.got() is None and "REFUSED" in out, (rc, out[-400:])


CASES = [case_env, case_rc, case_tracked, case_untracked, case_export_fails, case_not_clean]

S = "idf_build_shell.py"
MUTANTS = [
    ("MSYSTEM passed down", '    env.pop("MSYSTEM", None)\n', ""),
    ("the export under ErrorActionPreference Stop", "EXPORT = (\n",
     "EXPORT = (\n    \"$ErrorActionPreference = 'Stop'; \"\n"),
    ("tracked changes not failed", "    if rc == 0 and changed:\n        rc = 3\n", ""),
    ("the command's exit code dropped",
     "    rc = subprocess.run([sys.executable, \"-B\"] + cmd, env=env, cwd=here).returncode\n",
     "    subprocess.run([sys.executable, \"-B\"] + cmd, env=env, cwd=here)\n    rc = 0\n"),
    ("IDF_TOOLS_PATH not set", "\"$env:IDF_TOOLS_PATH = $env:_IBS_TOOLS; \"", "\"\""),
    ("the Python folder last on PATH",
     "\"$env:PATH = $env:_IBS_PY + ';' + $env:PATH; \"",
     "\"$env:PATH = $env:PATH + ';' + $env:_IBS_PY; \""),
    ("a failed export taken", "    if r.returncode != 0 or line is None:\n",
     "    if False:\n"),
    ("an export with no Python env taken", '    if not got.get("IDF_PYTHON_ENV_PATH"):\n',
     "    if False:\n"),
    ("untracked files failed", "        (untracked if ln.startswith(\"??\") else changed).append(ln[3:])\n",
     "        changed.append(ln[3:])\n"),
    ("not clean before, taken", "    if before:\n", "    if False:\n"),
    ("the command run elsewhere", "env=env, cwd=here).returncode",
     "env=env, cwd=os.path.dirname(here)).returncode"),
]


def run_cases(shell, base):
    w = World(base)
    bad = []
    for c in CASES:
        try:
            c(w, shell)
            print("ok    %s" % c.__name__, flush=True)
        except Exception as e:                      # noqa: BLE001
            print("FAIL  %s  %s: %s" % (c.__name__, type(e).__name__, str(e)[:300]), flush=True)
            bad.append(c.__name__)
    return bad


def main(argv):
    if os.name != "nt":
        print("REFUSED: idf_build_shell is Windows only (the ESP-IDF export is PowerShell's)")
        return 2
    # depth: its deepest git repo, control\repo (13 below the run folder),
    # + 12 for .git\config (measured 2026-11-05; scratch_dir)
    with scratch_dir.run("idf-build-shell-test", depth=25) as base:
        if "--can-fail" not in argv:
            bad = run_cases(SHELL, os.path.join(base, "t"))
            print("\nidf_build_shell_test: %s" % ("all checks OK" if not bad
                                                 else "%d FAILED" % len(bad)))
            return 1 if bad else 0
        print("-- control: the unedited shell", flush=True)
        if run_cases(SHELL, os.path.join(base, "control")):
            print("\nREFUSED: the control fails")
            return 1
        src = open(SHELL, encoding="utf-8").read()
        caught = 0
        for n, (name, old, new) in enumerate(MUTANTS, 1):
            if src.count(old) != 1:
                print("FAIL  m%d %s: NOT APPLIED (the edit matched %d times)"
                      % (n, name, src.count(old)), flush=True)
                continue
            d = os.path.join(base, "m%d" % n)
            os.makedirs(d)
            p = os.path.join(d, S)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(src.replace(old, new))
            print("-- m%d %s" % (n, name), flush=True)
            bad = run_cases(p, os.path.join(d, "t"))
            caught += bool(bad)
            print("%s  m%d %s: %s" % ("ok  " if bad else "FAIL", n, name,
                                      ("fails " + ", ".join(bad)) if bad
                                      else "STILL PASSES -- nothing checks it"), flush=True)
        print("\n%d of %d mutants caught" % (caught, len(MUTANTS)))
        return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
