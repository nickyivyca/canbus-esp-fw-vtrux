"""Tested cutover_verify.py: a faithful copy agrees, and a flipped byte, a
missing file and an extra file are each reported (tester, 2026-10-08).

That full mode is RETIRED (tester, 2026-10-31; the reviewer's
diagnosis request of that day). It copied projects/vtrux/tools/
interposer/ in SeaDrive as the faithful clone, and that folder was
emptied after the move cutover_verify.py checked (7a27421, 2026-10-08;
the folder's README.md says what was checked first). It holds only
README.md and firmware/ (a README and the data sheets) now, so the
full mode has failed since (no machine.py), and cutover_verify.py, kept
as the record of that check, has no source left to compare. The code is in git history
before this commit. Run without --guard-test, it refuses (exit 2).

Run with REVERSE_IT_DIR set:
  py -3.14 charge-interposer/harness_checks/cutover_verify_test.py --guard-test
It tests only the scratch-folder guard; it needs no source tree.
Since the move into git (port step 0, 2026-10-26) the guard also refuses a
folder inside this git tree, where this file now is, except under its
scratch/ (gitignored; 2026-10-31).
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import paths                           # noqa: E402
import scratch_dir as SD               # noqa: E402
PROJECT_ROOT = paths.reverse_it_dir()      # the reverse-it root, on SeaDrive
TREE = os.path.dirname(paths.code_dir())   # this git tree

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


def inside(path, root):
    a, r = os.path.realpath(path), os.path.realpath(root)
    try:
        return os.path.commonpath([a, r]) == r
    except ValueError:
        return False


def scratch_dir(base=None):
    """A fresh throwaway folder: a scratch_dir run folder, or one under
    base; refuses one inside the project tree, which is on SeaDrive, or
    inside this git tree outside its scratch/."""
    given = base
    base = SD.root() if base is None else base
    if inside(base, PROJECT_ROOT):
        raise SystemExit("REFUSED: scratch folder %s is inside the project "
                         "tree (SeaDrive); use a folder outside it" % base)
    if inside(base, TREE) and not inside(base, os.path.join(TREE, "scratch")):
        raise SystemExit("REFUSED: scratch folder %s is inside the git tree "
                         "%s; use a folder outside it" % (base, TREE))
    if given is None:
        return SD.make("cutover-verify-test")
    os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(dir=base, prefix="cutover_test_")


def guard_test():
    premise = os.path.isfile(os.path.join(PROJECT_ROOT, "AGENTS.md"))
    check(premise, "premise: PROJECT_ROOT %s holds AGENTS.md" % PROJECT_ROOT)
    for bad in (HERE, os.path.join(HERE, "sub"), PROJECT_ROOT,
                os.path.join(PROJECT_ROOT, "projects", "vtrux"), TREE):
        try:
            scratch_dir(bad)
            refused = False
        except SystemExit as e:
            refused = "REFUSED" in str(e)
        check(refused, "a scratch base inside the project is refused: %s" % bad)
    t = scratch_dir()
    ok = os.path.isdir(t) and not inside(t, PROJECT_ROOT)
    left = SD.remove(t)
    check(ok and not left, "the default, a scratch_dir run folder outside the "
          "project, is accepted and removed: %s" % t)
    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\ncutover_verify_test --guard-test: all checks OK")
    return 0


def main():
    if "--guard-test" in sys.argv[1:]:
        return guard_test()
    print("REFUSED: the full mode is retired (tester, 2026-10-31). It "
          "copied projects/vtrux/tools/interposer/ in SeaDrive, the "
          "source of the move cutover_verify.py checked (7a27421, "
          "2026-10-08), and that folder was emptied after the check "
          "(its README.md): there is nothing left to copy. Only "
          "--guard-test runs.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
