"""Tests cutover_verify.py: a faithful copy agrees, and a flipped byte, a
missing file and an extra file are each reported (tester, 2026-10-08).

Copies projects/vtrux/tools/interposer/ (less the exclusions) into a
throwaway folder under <machine temp>/interposer-tester/cutover_test
(tempfile.gettempdir(), outside SeaDrive: the regenerable-intermediates rule
in AGENTS.md, 2026-10-16; until then it was made under this artifacts
folder), runs the comparison against it, mutates
it three ways, and removes it afterwards. scratch_dir() refuses any folder
inside the project tree.

Run with REVERSE_IT_DIR set:
  py -3.14 charge-interposer/harness_checks/cutover_verify_test.py
  py -3.14 charge-interposer/harness_checks/cutover_verify_test.py --guard-test
The second form tests only the scratch-folder guard; it needs no source tree.
Since the move into git (port step 0, 2026-10-26) the guard also refuses a
folder inside this git tree, where this file now is.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import paths                           # noqa: E402
PROJECT_ROOT = paths.reverse_it_dir()      # the reverse-it root, on SeaDrive
TREE = os.path.dirname(paths.code_dir())   # this git tree
SCRATCH = os.path.join(tempfile.gettempdir(), "interposer-tester", "cutover_test")
sys.path.insert(0, HERE)
import cutover_verify as CV            # noqa: E402

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


def run(clone):
    r = subprocess.run([sys.executable, os.path.join(HERE, "cutover_verify.py"),
                        clone], capture_output=True, text=True)
    return r.returncode, r.stdout


def ignore(d, names):
    rel_d = os.path.relpath(d, CV.SEADRIVE).replace("\\", "/")
    out = []
    for n in names:
        rel = (("" if rel_d == "." else rel_d + "/") + n)
        if n in CV.EXCLUDE_DIRS or rel in CV.EXCLUDE_PATHS:
            out.append(n)
    return out


def inside(path, root):
    a, r = os.path.realpath(path), os.path.realpath(root)
    try:
        return os.path.commonpath([a, r]) == r
    except ValueError:
        return False


def scratch_dir(base=SCRATCH):
    """A fresh throwaway folder under base; refuses one inside the project
    tree, which is on SeaDrive, or inside this git tree."""
    if inside(base, PROJECT_ROOT):
        raise SystemExit("REFUSED: scratch folder %s is inside the project "
                         "tree (SeaDrive); use a folder outside it" % base)
    if inside(base, TREE):
        raise SystemExit("REFUSED: scratch folder %s is inside the git tree "
                         "%s; use a folder outside it" % (base, TREE))
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
    shutil.rmtree(t, ignore_errors=True)
    check(ok, "the default base outside the project is accepted: %s" % SCRATCH)
    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\ncutover_verify_test --guard-test: all checks OK")
    return 0


def main():
    if "--guard-test" in sys.argv[1:]:
        return guard_test()
    tmp = scratch_dir()
    clone = os.path.join(tmp, "charge-interposer")
    try:
        shutil.copytree(CV.SEADRIVE, clone, ignore=ignore)
        with open(os.path.join(clone, ".gitignore"), "w") as f:
            f.write("__pycache__/\n.pio/\n")
        rc, out = run(clone)
        check(rc == 0 and "AGREE" in out,
              "a faithful copy plus a .gitignore agrees -> %s"
              % out.strip().splitlines()[-1])

        victim = os.path.join(clone, "machine.py")
        b = bytearray(open(victim, "rb").read())
        b[100] ^= 0x01
        open(victim, "wb").write(bytes(b))
        rc, out = run(clone)
        check(rc != 0 and "DIFFERS: machine.py" in out,
              "one flipped byte in machine.py is reported")
        b[100] ^= 0x01
        open(victim, "wb").write(bytes(b))

        gone = os.path.join(clone, "protocol.py")
        keep = open(gone, "rb").read()
        os.remove(gone)
        rc, out = run(clone)
        check(rc != 0 and "MISSING in clone: protocol.py" in out,
              "a missing file is reported")
        open(gone, "wb").write(keep)

        open(os.path.join(clone, "stray.txt"), "w").write("x")
        rc, out = run(clone)
        check(rc != 0 and "EXTRA in clone: stray.txt" in out,
              "an extra file is reported")
        os.remove(os.path.join(clone, "stray.txt"))

        rc, out = run(clone)
        check(rc == 0, "and restored, it agrees again")

        # Line endings. machine.py is CRLF in SeaDrive (2026-10-08) and
        # protocol.py is LF; the test checks that premise first.
        mp = os.path.join(clone, "machine.py")
        pp = os.path.join(clone, "protocol.py")
        m0, p0 = open(mp, "rb").read(), open(pp, "rb").read()
        check(b"\r\n" in m0 and b"\r\n" not in p0,
              "premise: machine.py is CRLF and protocol.py LF in SeaDrive")
        open(mp, "wb").write(m0.replace(b"\r\n", b"\n"))
        rc, out = run(clone)
        check(rc == 2 and "EOL-ONLY" in out and "machine.py" in out,
              "a CRLF file stored as LF is EOL-ONLY, exit 2")
        b2 = bytearray(m0.replace(b"\r\n", b"\n"))
        b2[50] ^= 0x01
        open(mp, "wb").write(bytes(b2))
        rc, out = run(clone)
        check(rc == 1 and "DIFFERS: machine.py" in out,
              "a changed byte in a line-ending-converted file is DIFFERS")
        open(mp, "wb").write(m0)
        open(pp, "wb").write(p0.replace(b"\n", b"\r\n"))
        rc, out = run(clone)
        check(rc == 1 and "DIFFERS: protocol.py" in out,
              "an LF file given CRLF in the clone is DIFFERS, not EOL-ONLY "
              "(the normalisation runs one way only)")
        open(pp, "wb").write(p0)
        rc, out = run(clone)
        check(rc == 0, "and restored again, it agrees")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\ncutover_verify_test: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
