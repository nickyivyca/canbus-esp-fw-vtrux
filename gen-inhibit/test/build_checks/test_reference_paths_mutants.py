#!/usr/bin/env python3
"""Mutants of build_output.py's diag references (refs_from_git and the emitted-frames
row); test_reference_paths.py --target <copy> must fail on each by a check (a FAIL line,
not a crash). Each copy is in this run's scratch/ folder. Gen-inhibit tester, 2026-10-24
(toolkit item 10).

    py -3.14 -B gen-inhibit/test/build_checks/test_reference_paths_mutants.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))   # the project folder, gen-inhibit/ since port step 0d
sys.path.insert(0, HERE)
import test_reference_paths as TRP  # noqa: E402  remove_tree (read-only git objects on Windows)
MUTANTS = [
    ("uncommitted changes accepted", "    if dirty:\n        row(name, False, \"uncommitted", "    if False:\n        row(name, False, \"uncommitted"),
    ("the working tree's copies read, not HEAD's",
     '            fh.write(gi_diag.git(repo, "show", "HEAD:%s" % rp))',
     '            fh.write(open(os.path.join(repo, rp), "rb").read())'),
    ("gi_diag's history checks skipped", "        ident = gi_diag.run_identity(repo)", "        ident = {\"dbc_commit\": \"x\" * 40, \"dbc_version\": 0, \"dbc_hash\": \"-\"}"),
    ("a repo without the references passes", "    if missing:\n        row(name, None,", "    if False:\n        row(name, None,"),
    ("the emitted-frames row decodes without the schema set",
     '    if not DIAG_REF or DIAG_REF.get("dbc") != dbc_path:', "    if not DIAG_REF:"),
    # port step 0d (2026-10-26): a folder inside the worktree (the project folder) is taken to its root
    ("the given folder read as it is", "        repo = gi_diag.toplevel(repo)\n", ""),
    ("the reverse-it default is back", "REF_PATHS = (","DEFAULT_DBC = os.path.join(os.environ.get(\"REVERSE_IT_DIR\", \"\"), \"projects\", \"vtrux\", \"x\")\nREF_PATHS = ("),
]


def main():
    base = os.path.join(os.path.dirname(REPO), "scratch", "build-checks-tests", "reference_paths_mutants_%d" % os.getpid())
    TRP.remove_tree(base)
    src = open(os.path.join(HERE, "build_output.py"), encoding="utf-8", newline="").read()
    killed = errors = 0

    def run(name, text):
        d = os.path.join(base, name)
        os.makedirs(d)
        t = os.path.join(d, "build_output.py")
        open(t, "w", encoding="utf-8", newline="").write(text)
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "test_reference_paths.py"), "--target", t],
                           capture_output=True, text=True, timeout=900)
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        crash = r.stderr.strip().splitlines()[-1:] if "Traceback" in r.stderr else []
        return r.returncode, fails, crash

    rc, fails, crashed = run("control", src)
    control_ok = rc == 0 and not fails and not crashed
    print("%s control  (exit %d, %d FAIL)" % ("PASS    " if control_ok else "FAILED  ", rc, len(fails)))
    for n, (label, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, label, src.count(old)))
            errors += 1
            continue
        rc, fails, crashed = run("m%d" % n, src.replace(old, new))
        if crashed:     # a crash is neither a kill nor a survivor (2026-10-25: it printed SURVIVED)
            print("ERROR    m%d %s: the test crashed (exit %d): %s" % (n, label, rc, crashed[0][:160]))
            errors += 1
            continue
        dead = rc != 0 and bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL)%s" % ("KILLED  " if dead else "SURVIVED", n, label, rc, len(fails),
                                                "".join("\n           " + f[:120] for f in fails[:2])))
    TRP.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
