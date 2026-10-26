#!/usr/bin/env python3
"""Mutants of gi_paths.py; test_gi_paths.py must fail on each BY A CHECK. A crash is
not a kill (a FAIL whose detail starts CRASH does not count), and an edit that does
not apply is an error. Each mutant is a copy of gi_paths.py in the worktree's
scratch/ with one edit, run through GI_PATHS_DIR. Gen-inhibit tester, 2026-10-22.

    py -3.14 -B test_gi_paths_mutants.py
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = "gi_paths.py"
MUTANTS = [
    ("unset REVERSE_IT_DIR falls back to the cwd",
     '    if not v:\n        raise PathsError("REVERSE_IT_DIR is not set',
     '    if not v:\n        v = os.getcwd()\n    if False:\n        raise PathsError("REVERSE_IT_DIR is not set'),
    ("a root without canre/ and projects/ accepted",
     '    if not (os.path.isdir(os.path.join(v, "canre")) and os.path.isdir(os.path.join(v, "projects"))):',
     '    if False:'),
    ("GI_FW_TOOLS no longer retired", '    "GI_FW_TOOLS": "the bench tools find gen-inhibit/tools/ as their parent folder",\n', ""),
    ("the retired check not run on import", "\ncheck_retired()\n", "\n"),
    ("runs() inside the repo", '    return data_dir("runs", *parts)', '    return os.path.join(repo_root(), "runs", *parts)'),
    ("verify never compares", "    if _checked[key] != want:", "    if False:"),
    ("pins looked up case-sensitively", "    want = load_pins().get(os.path.normcase(rel))",
     "    want = {k.replace(os.sep, '/'): v for k, v in load_pins().items()}.get(rel)"),
    ("the tree digest opens excluded files", "            if r in exclude:\n                continue\n",
     "            if r in exclude:\n                sha256_file(p)\n                continue\n"),
    ("the tree digest ignores its exclude list", "            if r in exclude:\n                continue\n", ""),
    ("a folder with no pin passes", '    if entry is None:\n        raise PathsError("no tree pin',
     '    if entry is None:\n        return path\n        raise PathsError("no tree pin'),
    ("the tree digest skips file contents", '            lines.append("%s %s" % (r, sha256_file(p)))',
     '            lines.append(r)'),
    ("the tree digest skips added names", '            lines.append("%s %s" % (r, sha256_file(p)))',
     '            lines.append(sha256_file(p))\n            lines.sort()'),
    # scratch folders (harness-defects row, 2026-10-24)
    ("scratch has no per-run part", '    if run:\n        d = os.path.join(d, "%s_%d"', '    if False:\n        d = os.path.join(d, "%s_%d"'),
    ("scratch in the system temp folder",
     '    d = os.path.join(worktree_of(start or HERE), "scratch", "gen-inhibit-tests", name)',
     '    d = os.path.join(os.environ.get("TEMP") or "/tmp", "gen-inhibit-tests", name)'),
    ("worktree_of counts up instead of finding .git",
     '        if os.path.exists(os.path.join(d, ".git")):\n            return d\n',
     '        return os.path.normpath(os.path.join(d, "..", "..", ".."))\n'),
    # the project layout (port step 0d, 2026-10-26)
    ("project_root three up, as tools/gen_inhibit/bench/ was",
     '    return os.path.normpath(os.path.join(HERE, "..", ".."))',
     '    return os.path.normpath(os.path.join(HERE, "..", "..", ".."))'),
    ("repo_root is the project folder", "    return os.path.dirname(project_root())", "    return project_root()"),
    ("firmware_root is the project folder", '    return os.path.join(project_root(), "firmware")',
     "    return project_root()"),
    ("old_path keeps firmware/", '(("firmware/", ""),', '(("firmware/", "firmware/"),'),
    ("old_path keeps the tools folder flat", '("tools/", "tools/gen_inhibit/")', '("tools/", "tools/")'),
    ("path_at never maps into gen-inhibit/", "    if has(moved):\n        return moved\n", ""),
    ("path_at always maps into gen-inhibit/", "    return old if old is not None and has(old) else moved",
     "    return moved"),
    ("path_at takes the old place without asking git",
     "    return old if old is not None and has(old) else moved", "    return old if old is not None else moved"),
]


def run_test(d):
    r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "test_gi_paths.py")], capture_output=True, text=True,
                       env=dict(os.environ, GI_PATHS_DIR=d), cwd=d)
    fails = [l.strip() for l in r.stdout.splitlines() if l.startswith("FAIL  ")]
    crashed = "Traceback" in r.stderr
    dead = bool(fails) and not crashed and not all(" CRASH " in f for f in fails)
    return dead, crashed, fails, r.returncode


def copy_tree(base, name, text):
    """A copy laid out as the repo is (gen-inhibit/tools/bench/), so project_root,
    repo_root and fw_tools resolve as they do in place; the folder of the copied gi_paths.py."""
    d = os.path.join(base, name, "gen-inhibit", "tools", "bench")
    shutil.rmtree(os.path.join(base, name), ignore_errors=True)
    os.makedirs(d)
    open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(text)
    shutil.copy(os.path.join(HERE, "pins.json"), d)
    open(os.path.join(os.path.dirname(d), "suite_gen_inhibit.py"), "w").close()
    return d


def main():
    base = gi_paths.scratch("mutants-gi_paths")
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    dead, crashed, fails, rc = run_test(copy_tree(base, "control", src))
    control_ok = rc == 0 and not fails
    print("%s control: the unmutated copy, laid out as the repo  (exit %d, %d FAIL)%s"
          % ("PASS    " if control_ok else "FAILED  ", rc, len(fails), "".join("\n           " + f[:130] for f in fails[:3])))
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = copy_tree(base, "m%d" % n, src.replace(old, new))
        dead, crashed, fails, rc = run_test(d)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)%s" % ("KILLED  " if dead else "SURVIVED", n, name, rc, len(fails),
                                                 ", CRASHED" if crashed else "",
                                                 "".join("\n           " + f[:130] for f in fails[:3])))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
