#!/usr/bin/env python3
"""Tests for project_dir.py and its use in from_capture.py, test_signals.py and
replay_stimulus_delta.py (the project-folder variable, methodology 2026-10-20).

Needs no project folder: it makes a fake one (a canre/ with a parser that
returns no frames, and projects/vtrux/logs/) in a temporary folder, and runs the
tools as subprocesses with $REVERSE_IT_DIR set, unset or wrong.

    python3 test_project_dir.py            # the checks
    python3 test_project_dir.py --mutants  # each mutant must fail a check
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ("project_dir.py", "from_capture.py", "test_signals.py", "replay_stimulus_delta.py")

FAILS = []
# A machine's own path: a home folder, a sync client's folder (home-path ruling B, user 2026-10-23).
MACHINE_PATH = re.compile(r"Seafile/|seadrive_root|NotGit|nickivyc|/home/[a-z]|[A-Za-z]:[\\/]+Users")


def check(name, ok, detail=""):
    print("%s  %s%s" % ("ok  " if ok else "FAIL", name, "" if ok else "  -- %r" % (detail,)))
    if not ok:
        FAILS.append(name)


def fake_project(root):
    """A reverse-it root with just enough for the tools: canre.parsers.parse_file."""
    os.makedirs(os.path.join(root, "canre", "parsers"))
    os.makedirs(os.path.join(root, "projects", "vtrux", "logs"))
    open(os.path.join(root, "canre", "__init__.py"), "w").close()
    with open(os.path.join(root, "canre", "parsers", "__init__.py"), "w") as fh:
        fh.write("def parse_file(path, *a, **k):\n    return iter(())\n")
    with open(os.path.join(root, "x.log"), "w") as fh:
        fh.write("")
    return root


def run(tree, script, args, env_dir):
    env = {k: v for k, v in os.environ.items() if k not in ("REVERSE_IT_DIR", "GEN_INHIBIT_REPO")}
    if env_dir is not None:
        env["REVERSE_IT_DIR"] = env_dir
    r = subprocess.run([sys.executable, "-B", os.path.join(tree, script)] + args, cwd=tree,
                       capture_output=True, text=True, env=env, timeout=120)
    return r.returncode, r.stdout + r.stderr


def checks(tree, tmp):
    sys.path.insert(0, tree)
    try:
        for m in ("project_dir",):
            sys.modules.pop(m, None)
        import project_dir as pd
    finally:
        sys.path.pop(0)
    good = fake_project(os.path.join(tmp, "rit"))
    nocanre = os.path.join(tmp, "nocanre")
    os.makedirs(nocanre)
    gone = os.path.join(tmp, "gone")

    # ---- the resolver
    check("resolve: --project-dir is used", _resolves(pd, good, {}) == good)
    check("resolve: $REVERSE_IT_DIR is used when --project-dir is not given",
          _resolves(pd, None, {"REVERSE_IT_DIR": good}) == good)
    check("resolve: --project-dir wins over $REVERSE_IT_DIR",
          _resolves(pd, good, {"REVERSE_IT_DIR": nocanre}) == good)
    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
    os.environ["HOME"] = os.environ["USERPROFILE"] = tmp
    try:
        got = (_resolves(pd, "~/rit", {}), _resolves(pd, None, {"REVERSE_IT_DIR": "~/rit"}))
        check("resolve: ~ is expanded", all(os.path.normpath(g) == os.path.normpath(good) for g in got), got)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def err(given, environ):
        try:
            pd.resolve(given, environ)
        except pd.ProjectDirError as e:
            return str(e)
        return None

    e = err(None, {"GEN_INHIBIT_REPO": good})
    check("resolve: unset is an error, not a default (the old variable does not count)", e is not None, e)
    check("resolve: the unset error names REVERSE_IT_DIR and --project-dir, and gives an example path",
          e is not None and "REVERSE_IT_DIR" in e and "--project-dir" in e and pd.EXAMPLE in e, e)
    check("resolve: the unset error gives the WSL setting", e is not None and "WSLENV=REVERSE_IT_DIR/p" in e, e)
    e = err(None, {"REVERSE_IT_DIR": ""})
    check("resolve: an empty $REVERSE_IT_DIR is unset", e is not None and "REVERSE_IT_DIR" in e, e)
    e = err(None, {"REVERSE_IT_DIR": gone})
    check("resolve: a missing folder is refused, naming the variable and the path",
          e is not None and "$REVERSE_IT_DIR" in e and gone in e and "does not exist" in e, e)
    e = err(gone, {})
    check("resolve: a missing --project-dir is refused, naming the flag",
          e is not None and "--project-dir" in e and gone in e, e)
    e = err(None, {"REVERSE_IT_DIR": nocanre})
    check("resolve: a folder with no canre/ is refused", e is not None and "canre" in e and nocanre in e, e)

    # ---- no machine's own path (home-path ruling B, user 2026-10-23)
    check("machine paths: the scan finds a planted home path in each form",
          all(MACHINE_PATH.search(t) for t in ("~/Seafile/NotGit/reverse-it", "/home/someone/rit",
                                               "C:\\Users\\someone\\rit", "seadrive_root/lib/rit")))
    hits = ["%s: %s" % (f, m.group(0)) for f in TOOLS
            for m in MACHINE_PATH.finditer(open(os.path.join(tree, f), encoding="utf-8").read())]
    check("machine paths: none in the tools, their docstrings or the example", not hits, hits)
    hits = MACHINE_PATH.findall(open(os.path.join(HERE, "README.md"), encoding="utf-8").read())
    check("machine paths: none in this folder's README", not hits, hits)
    check("the example is a placeholder, not a home folder", not pd.EXAMPLE.startswith("~"), pd.EXAMPLE)

    # ---- from_capture.py
    rc, out = run(tree, "from_capture.py", [os.path.join(good, "x.log"), "--scan"], None)
    check("from_capture: unset refuses by name, not 'No module named canre'",
          rc != 0 and "REVERSE_IT_DIR" in out and "No module named" not in out, (rc, out[-300:]))
    rc, out = run(tree, "from_capture.py", [os.path.join(good, "x.log"), "--scan"], good)
    check("from_capture: $REVERSE_IT_DIR reaches the parser", rc == 0 and "REVERSE_IT_DIR" not in out, (rc, out[-300:]))
    rc, out = run(tree, "from_capture.py", [os.path.join(good, "x.log"), "--scan", "--project-dir", good], None)
    check("from_capture: --project-dir reaches the parser", rc == 0, (rc, out[-300:]))
    rc, out = run(tree, "from_capture.py", [os.path.join(good, "x.log"), "--scan", "--project-dir", nocanre], good)
    check("from_capture: a wrong --project-dir is refused even with $REVERSE_IT_DIR right",
          rc != 0 and "canre" in out, (rc, out[-300:]))
    rc, out = run(tree, "from_capture.py", [os.path.join(good, "x.log"), "--scan", "--repo", good], good)
    check("from_capture: --repo is gone", rc != 0 and "--repo" in out, (rc, out[-300:]))

    # ---- test_signals.py (the folder is resolved first, before cantools and the probe;
    # this copy has no extract_probe, so a resolved folder stops at "build extract_probe")
    rc, out = run(tree, "test_signals.py", [], None)
    check("test_signals: unset refuses by name", rc == 2 and "REVERSE_IT_DIR" in out, (rc, out[-300:]))
    rc, out = run(tree, "test_signals.py", ["--project-dir", nocanre], None)
    check("test_signals: a wrong --project-dir is refused", rc == 2 and "canre" in out, (rc, out[-300:]))
    rc, out = run(tree, "test_signals.py", ["--project-dir", good], None)
    check("test_signals: --project-dir is taken (it goes on to the next precondition)",
          "REVERSE_IT_DIR" not in out and "canre" not in out, (rc, out[-300:]))
    rc, out = run(tree, "test_signals.py", [], good)
    check("test_signals: $REVERSE_IT_DIR is taken", "REVERSE_IT_DIR" not in out and "canre" not in out, (rc, out[-300:]))

    # ---- replay_stimulus_delta.py --regen
    rc, out = run(tree, "replay_stimulus_delta.py", ["--regen"], None)
    check("replay_stimulus_delta: --regen with nothing set is NO REFERENCE, naming the variable",
          rc == 2 and "NO REFERENCE" in out and "REVERSE_IT_DIR" in out, (rc, out[-300:]))
    rc, out = run(tree, "replay_stimulus_delta.py", ["--regen"], nocanre)
    check("replay_stimulus_delta: --regen with a wrong folder is NO REFERENCE",
          rc == 2 and "NO REFERENCE" in out and "canre" in out, (rc, out[-300:]))
    nologs = fake_project(os.path.join(tmp, "nologs"))
    os.rmdir(os.path.join(nologs, "projects", "vtrux", "logs"))
    rc, out = run(tree, "replay_stimulus_delta.py", ["--regen"], nologs)
    check("replay_stimulus_delta: --regen with no projects/vtrux/logs is NO REFERENCE",
          rc == 2 and "NO REFERENCE" in out and "projects/vtrux/logs" in out, (rc, out[-300:]))
    rc, out = run(tree, "replay_stimulus_delta.py", ["--regen", "--project-dir", good], None)
    check("replay_stimulus_delta: --regen passes the folder to from_capture (it reads the folder's logs)",
          "NO REFERENCE" not in out and all(("regen %-30s" % n) in out for n in REPLAY_NAMES(tree)) and
          "REVERSE_IT_DIR" not in out, out[-600:])


def _resolves(pd, given, environ):
    try:
        return pd.resolve(given, environ)
    except pd.ProjectDirError as e:
        return "refused: %s" % e


def REPLAY_NAMES(tree):
    sys.path.insert(0, tree)
    try:
        sys.modules.pop("replay_stimulus_delta", None)
        sys.modules.pop("project_dir", None)
        import replay_stimulus_delta as rsd
        return [r[0] for r in rsd.REPLAYS]
    finally:
        sys.path.pop(0)


def copy_tree(dst):
    os.makedirs(dst)
    for f in TOOLS:
        shutil.copy2(os.path.join(HERE, f), os.path.join(dst, f))
    return dst


MUTANTS = [
    ("project_dir.py", "$REVERSE_IT_DIR is not read", 'p = given or environ.get(VAR, "")', 'p = given or ""'),
    ("project_dir.py", "the old variable is still read",
     'p = given or environ.get(VAR, "")', 'p = given or environ.get(VAR, "") or environ.get("GEN_INHIBIT_REPO", "")'),
    ("project_dir.py", "unset falls back to a default path", 'p = given or environ.get(VAR, "")',
     'p = given or environ.get(VAR, "") or EXAMPLE'),
    ("project_dir.py", "the environment wins over --project-dir", 'p = given or environ.get(VAR, "")',
     'p = environ.get(VAR, "") or given'),
    ("project_dir.py", "the unset error has no example", 'e.g. export %s=%s (under WSL also WSLENV=%s/p)" % (VAR, VAR, EXAMPLE, VAR))',
     'under WSL also WSLENV=%s/p)" % (VAR, VAR))'),
    ("project_dir.py", "a folder without canre/ is accepted", '    if not os.path.isdir(os.path.join(p, "canre")):\n',
     '    if False:\n'),
    ("project_dir.py", "a missing folder is accepted", '    if not os.path.isdir(p):\n', '    if False:\n'),
    ("project_dir.py", "~ is not expanded", '    p = os.path.expanduser(p)\n', '\n'),
    ("project_dir.py", "the example is a home folder again", 'EXAMPLE = "/path/to/reverse-it"',
     'EXAMPLE = "~/Seafile/NotGit/reverse-it"'),
    ("from_capture.py", "from_capture ignores --project-dir", "parse_file = load_parser(args.project_dir)",
     "parse_file = load_parser(None)"),
    ("from_capture.py", "from_capture lets the import fail instead",
     "        repo = project_dir.resolve(given)\n    except project_dir.ProjectDirError as e:\n        sys.exit(str(e))\n",
     "        repo = given or os.environ.get('REVERSE_IT_DIR', '')\n    except project_dir.ProjectDirError as e:\n        sys.exit(str(e))\n"),
    ("test_signals.py", "test_signals ignores --project-dir", "repo = project_dir.resolve(args.project_dir)",
     "repo = project_dir.resolve(None)"),
    ("replay_stimulus_delta.py", "regen does not pass the folder to from_capture",
     '"--project-dir", pdir, "--channel"', '"--channel"'),
    ("replay_stimulus_delta.py", "regen ignores --project-dir", "pdir = project_dir.resolve(args.project_dir)",
     "pdir = project_dir.resolve(None)"),
    ("replay_stimulus_delta.py", "regen does not check for the logs folder", "    return cand if os.path.isdir(cand) else None\n",
     "    return cand\n"),
]


def main():
    if "--mutants" not in sys.argv[1:]:
        with tempfile.TemporaryDirectory(prefix="gi-project-dir-") as tmp:
            checks(copy_tree(os.path.join(tmp, "tree")), tmp)
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        return 1 if FAILS else 0
    killed = 0
    for i, (f, name, old, new) in enumerate(MUTANTS, 1):
        with tempfile.TemporaryDirectory(prefix="gi-project-dir-m%d-" % i) as tmp:
            tree = copy_tree(os.path.join(tmp, "tree"))
            p = os.path.join(tree, f)
            s = open(p, encoding="utf-8").read()
            if s.count(old) != 1:
                print("ERROR    m%d %s: the substitution matches %d times" % (i, name, s.count(old)))
                continue
            open(p, "w", encoding="utf-8").write(s.replace(old, new))
            r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--in-tree", tree],
                               capture_output=True, text=True, timeout=600)
            fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
            crashed = r.returncode not in (0, 1) or "Traceback" in r.stderr
            if fails and not crashed:
                killed += 1
                print("KILLED   m%d %s  (%d FAIL; %s)" % (i, name, len(fails), fails[0][6:90]))
            else:
                print("%s m%d %s  (rc %d)%s" % ("CRASHED " if crashed else "SURVIVED", i, name, r.returncode,
                                                ("  " + r.stderr.strip().splitlines()[-1]) if crashed and r.stderr.strip() else ""))
    print("%d of %d killed by a check" % (killed, len(MUTANTS)))
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--in-tree"]:
        with tempfile.TemporaryDirectory(prefix="gi-project-dir-t-") as tmp:
            checks(sys.argv[2], tmp)
        print("%d FAIL" % len(FAILS) if FAILS else "all ok")
        sys.exit(1 if FAILS else 0)
    sys.exit(main())
