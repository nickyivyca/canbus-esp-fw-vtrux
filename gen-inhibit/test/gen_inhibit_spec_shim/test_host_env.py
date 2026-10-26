#!/usr/bin/env python3
"""Tests host_env.py and how run_spec_shim.py and mutate.py record the build's host
and compiler (2026-10-24: every host C run records `uname -n` and `gcc --version`).

    python3 -B test_host_env.py --dbc-dir <reverse-it>/projects/vtrux
    python3 -B test_host_env.py --dbc-dir ... --mutants   # each mutant must fail a check,
                                                           # the unmutated copy must pass

The pure checks run anywhere. The real-shell checks run only on Linux, where
--local is meant to run (on Windows a bare `bash` is WSL's): the identity command
in a real bash with a fake gcc on PATH and with none; run_spec_shim.py --local
with each (identity.json, the summary line, a FAIL naming the missing compiler);
mutate.py --local with no gcc (an error, exit 1, before any mutant). --dbc-dir
(or $REVERSE_IT_DIR) is for run_spec_shim.py's payload check. Gen-inhibit tester,
2026-10-24.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
FAKE_GCC = "gcc (Fake 9.9.9-1) 9.9.9"
TOOLS = ["bash", "sh", "uname", "head", "grep", "rm", "timeout", "git", "cat", "printf", "env"]
bad = []


def check(name, ok, detail=""):
    print("  %-4s %-66s %s" % ("ok" if ok else "FAIL", name, detail))
    if not ok:
        bad.append(name)


def toolbin(root, with_gcc):
    """A PATH holding only the tools the runs need, and a fake gcc if with_gcc."""
    d = os.path.join(root, "bin_gcc" if with_gcc else "bin_nogcc")
    os.makedirs(d)
    for t in TOOLS:
        w = shutil.which(t)
        if w:
            os.symlink(w, os.path.join(d, t))
    if with_gcc:
        p = os.path.join(d, "gcc")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "%s"; echo "Copyright line two"; exit 0; fi\n'
                     'exit 1\n' % FAKE_GCC)
        os.chmod(p, 0o755)
    return d


def pure(he):
    fake = lambda out: (lambda cmd, local: (0, out, ""))   # noqa: E731
    seen = []
    he.host_identity(True, lambda cmd, local: seen.append(cmd) or (0, "", ""))
    check("the identity command asks uname -n", "uname -n" in seen[0])
    check("the identity command asks gcc --version, its first line", "gcc --version" in seen[0] and "head -1" in seen[0])
    i = he.host_identity(True, fake("host=madhouse-debian\ncompiler=gcc (Debian 14.2.0-19) 14.2.0\n"))
    check("host and compiler read from the shell's answer",
          i["host"] == "madhouse-debian" and i["compiler"] == "gcc (Debian 14.2.0-19) 14.2.0", i)
    check("--local is recorded as the local shell", i["build_env"] == "local")
    check("without --local, WSL", he.host_identity(False, fake("host=x\ncompiler=y\n"))["build_env"] == "wsl")
    check("nothing missing when both are given", he.missing(i) == [])
    check("no gcc: the compiler is missing", he.missing(he.host_identity(True, fake("host=h\ncompiler=\n"))) == ["compiler"])
    check("no answer: both are missing", he.missing(he.host_identity(True, fake(""))) == ["host", "compiler"])
    check("the summary line of a WSL build says self-check",
          "self-check only" in he.describe({"host": "h", "compiler": "c", "build_env": "wsl"}))
    check("the summary line of a local build does not",
          "self-check" not in he.describe({"host": "h", "compiler": "c", "build_env": "local"}))
    d = he.describe({"host": "", "compiler": "", "build_env": "local"})
    check("a missing value reads NOT RECORDED", d.count("NOT RECORDED") == 2, d)
    d = he.describe(i)
    check("the summary line names host and compiler", "madhouse-debian" in d and "14.2.0" in d, d)


def real(he, folder, dbc, tmp):
    env_gcc = dict(os.environ, PATH=toolbin(tmp, True))
    env_none = dict(os.environ, PATH=toolbin(tmp, False))
    node = os.uname().nodename

    def ident(env):
        old = os.environ["PATH"]
        os.environ["PATH"] = env["PATH"]
        try:
            return he.host_identity(True)
        finally:
            os.environ["PATH"] = old
    i = ident(env_gcc)
    check("real bash, fake gcc: host is this machine's", i["host"] == node, i)
    check("real bash, fake gcc: the compiler is its first line only", i["compiler"] == FAKE_GCC, i)
    i = ident(env_none)
    check("real bash, no gcc: the compiler is missing", he.missing(i) == ["compiler"], i)

    for label, env, want in (("fake gcc", env_gcc, FAKE_GCC), ("no gcc", env_none, "")):
        out = os.path.join(tmp, "run_" + label.replace(" ", "_"))
        r = subprocess.run([sys.executable, "-B", os.path.join(folder, "run_spec_shim.py"), "--local", "--out", out,
                            "--dbc-dir", dbc], env=env, capture_output=True, text=True, timeout=600)
        try:
            idj = json.load(open(os.path.join(out, "identity.json")))
            summ = open(os.path.join(out, "summary.txt"), encoding="utf-8").read()
        except (OSError, ValueError) as e:
            check("run_spec_shim, %s: wrote identity.json and summary.txt" % label, False, repr(e)[:80])
            continue
        check("run_spec_shim, %s: identity.json has this host" % label, idj.get("host") == node, idj.get("host"))
        check("run_spec_shim, %s: identity.json has the compiler" % label, idj.get("compiler") == want,
              idj.get("compiler"))
        check("run_spec_shim, %s: identity.json says local" % label, idj.get("build_env") == "local")
        line = he.describe({"host": node, "compiler": want, "build_env": "local"})
        check("run_spec_shim, %s: the summary carries the host line" % label, line in summ.splitlines())
        named = any("not recorded: compiler" in f for f in idj.get("fails", []))
        check("run_spec_shim, %s: %s" % (label, "a FAIL names the missing compiler" if not want
                                         else "no FAIL about the record"), named == (not want), idj.get("fails"))
        check("run_spec_shim, %s: not exit 0 (the fake builds nothing)" % label, r.returncode != 0, r.returncode)

    r = subprocess.run([sys.executable, "-B", os.path.join(folder, "mutate.py"), "--local", "--only", "1"],
                       env=env_none, capture_output=True, text=True, timeout=300)
    lines = r.stdout.splitlines()
    check("mutate, no gcc: the second line is the host line",
          len(lines) > 1 and lines[1] == he.describe({"host": node, "compiler": "", "build_env": "local"}), lines[1:2])
    check("mutate, no gcc: an error naming the compiler, exit 1, no mutant run",
          r.returncode == 1 and "ERROR    not recorded: compiler" in r.stdout and " m1 " not in r.stdout,
          (r.returncode, r.stdout[-120:]))


def checks(folder, dbc, tmp):
    del bad[:]
    sys.path.insert(0, folder)
    sys.modules.pop("host_env", None)
    import host_env as he
    try:
        pure(he)
        if os.name == "nt":
            print("  ---- the real-shell checks run on Linux only (on Windows a bare bash is WSL's)")
        else:
            real(he, folder, dbc, tmp)
    finally:
        sys.path.remove(folder)
        sys.modules.pop("host_env", None)
    return list(bad)


def copy_tree(d):
    """The repo's layout under d (main/, the fakes, this folder), so run_spec_shim.py
    finds ../../main as it does here; returns the copy of this folder."""
    shutil.copytree(os.path.join(REPO, "main"), os.path.join(d, "main"))
    shutil.copytree(os.path.join(REPO, "test", "gen_inhibit_shim"), os.path.join(d, "test", "gen_inhibit_shim"))
    shutil.copytree(HERE, os.path.join(d, "test", "gen_inhibit_spec_shim"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    os.makedirs(os.path.join(d, "tmp"))
    return os.path.join(d, "test", "gen_inhibit_spec_shim")


MUTANTS = [
    ("host_env.py", "WSL recorded as local", '"build_env": "local" if local else "wsl"}', '"build_env": "local"}'),
    ("host_env.py", "the compiler is never missing", 'for k in ("host", "compiler")', 'for k in ("host",)'),
    ("host_env.py", "no self-check note", '"built in WSL: a self-check only, not for VERIFICATION.md"',
     '"built in the local shell"'),
    ("host_env.py", "cc asked, not gcc", '"$(gcc --version', '"$(cc --version'),
    ("run_spec_shim.py", "run_spec_shim records no host", "    ident.update(host_env.host_identity(a.local))\n", ""),
    ("run_spec_shim.py", "run_spec_shim takes a missing compiler",
     '        fails.append("not recorded: %s" % ", ".join(host_env.missing(ident)))\n', "        pass\n"),
    ("run_spec_shim.py", "the summary has no host line", "               host_env.describe(ident),\n", ""),
    ("mutate.py", "mutate runs on with no compiler", "        shutil.rmtree(base, ignore_errors=True)\n        return 1\n",
     "        pass\n"),
]
# Not a mutant: gcc --version without head -1. Its later lines hold no "=", and only
# key=value lines are read, so the record comes out the same (equivalent).
# The run_spec_shim and mutate mutants are caught only by the real-shell checks, so
# --mutants counts on Linux (madhouse-debian) only.


def main():
    dbc = None
    if "--dbc-dir" in sys.argv:
        dbc = sys.argv[sys.argv.index("--dbc-dir") + 1]
    elif os.environ.get("REVERSE_IT_DIR"):
        dbc = os.path.join(os.environ["REVERSE_IT_DIR"], "projects", "vtrux")
    if os.name != "nt" and not (dbc and os.path.exists(os.path.join(dbc, "epri-pt-bus.dbc"))):
        print("REFUSED: give --dbc-dir <reverse-it>/projects/vtrux (run_spec_shim.py's payload check needs the DBCs)")
        return 2
    sys.path.insert(0, HERE)
    import host_env
    base = host_env.scratch_dir("test_host_env")
    try:
        if "--mutants" not in sys.argv[1:]:
            os.makedirs(os.path.join(base, "t"))
            got = checks(HERE, dbc, os.path.join(base, "t"))
            print("\n%s" % ("all ok" if not got else "%d FAIL: %s" % (len(got), "; ".join(got))))
            return 1 if got else 0
        print("--- control: the unmutated copy")
        got = checks(copy_tree(os.path.join(base, "control")), dbc, os.path.join(base, "control", "tmp"))
        control_ok = not got
        print("%s control  (%d failing check(s)%s)" % ("PASS    " if control_ok else "FAILED  ", len(got),
                                                    ("; " + got[0]) if got else ""))
        killed = errors = 0
        for n, (f, name, old, new) in enumerate(MUTANTS, 1):
            src = open(os.path.join(HERE, f), encoding="utf-8", newline="").read()
            if src.count(old) != 1:
                print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
                errors += 1
                continue
            d = os.path.join(base, "m%d" % n)
            folder = copy_tree(d)
            open(os.path.join(folder, f), "w", encoding="utf-8", newline="").write(src.replace(old, new))
            print("--- m%d %s" % (n, name))
            got = checks(folder, dbc, os.path.join(d, "tmp"))
            killed += bool(got)
            print("%s m%d %s  (%d failing check(s)%s)" % ("KILLED  " if got else "SURVIVED", n, name, len(got),
                                                        ("; " + got[0]) if got else ""))
        print("\n%d of %d killed by a check, %d errors; the control %s"
              % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
        return 0 if killed == len(MUTANTS) and not errors and control_ok else 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
