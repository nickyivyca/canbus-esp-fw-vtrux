#!/usr/bin/env python3
"""Mutants of gi_core_pin.py; test_gi_core_pin.py must fail on each BY A CHECK. A crash
is not a kill, and an edit that does not apply is an error.

    python3.13 -B gen-inhibit/tools/bench/test_gi_core_pin_mutants.py

Each mutant is a copy of gi_core_pin.py in the worktree's scratch/ with one edit, which
the test loads through GI_CORE_PIN_UNDER_TEST. The unmutated copy runs first, as the
control: if it fails a check, no kill counts. Gen-inhibit tester, 2026-11-25 (port step 4
item 1).
"""
import os
import shutil
import subprocess
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402

SRC = "gi_core_pin.py"
TEST = "test_gi_core_pin.py"
MUTANTS = [
    ("the digest is not compared", 'if digest != pin["digest"]:', "if False:"),
    ("the digest of shared/tools, not shared/bench",
     'P.git_digest(checkout, pin["commit"], COMPONENT)', 'P.git_digest(checkout, pin["commit"])'),
    ("changed tracked files pass", "        if changed:\n", "        if False:\n"),
    ("tracked files of shared/tools are compared",
     'P.tracked_differences(checkout, pin["commit"], COMPONENT)', 'P.tracked_differences(checkout, pin["commit"])'),
    ("stray and staged files pass", "        if extra:\n", "        if False:\n"),
    ("HEAD is not compared", '    if head != pin["commit"]:\n', "    if False:\n"),
    ("a missing tag passes", "    if tagged is None:\n", "    if False:\n"),
    ("the checker is read from the disk",
     '        rc, src, err = _git(checkout, "show", "%s:%s" % (commit, path))',
     '        rc, src, err = 0, open(os.path.join(checkout, *path.split("/")), "rb").read(), ""'),
    ("the pinned folder goes last on sys.path", "        sys.path.insert(0, folder)", "        sys.path.append(folder)"),
    ("a foreign vtrux_bench is kept", "        if got is None or not _same(got, folder):", "        if False:"),
    ("a commit id passes as the tag", "    if _LOOKS_LIKE_COMMIT.match(tag):", "    if False:"),
    ("the digest's case is not checked", "_DIGEST = re.compile(r\"^[0-9a-f]{64}$\")",
     "_DIGEST = re.compile(r\"^[0-9a-fA-F]{64}$\")"),
    ("the create commands leave the checkout writable",
     "            '    chmod -R a-w \"%s\"' % (top, tag, tag, os.path.join(checkout, \"shared\")))",
     "            '' % (top, tag, tag))"),
    ("module runs any module", '    if name != "vtrux_bench" and not name.startswith("vtrux_bench."):', "    if False:"),
]


def main():
    base = gi_paths.scratch("mutants-gi_core_pin")
    src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
    killed = errors = 0
    control_ok = False
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        if old is not None and src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        os.makedirs(d)
        with open(os.path.join(d, SRC), "w", encoding="utf-8", newline="") as f:
            f.write(src if old is None else src.replace(old, new))
        env = dict(os.environ, GI_CORE_PIN_UNDER_TEST=os.path.join(d, SRC))
        env.pop("PYTHONPATH", None)
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, TEST)], env=env, capture_output=True,
                           text=True, timeout=600)
        fl = [l for l in r.stdout.splitlines() if l.startswith("FAIL ")]
        crash = "Traceback" in r.stderr
        if old is None:
            control_ok = r.returncode == 0 and not fl and not crash
            print("%s m0 %s  (exit %d, %d FAIL%s)" % ("PASS    " if control_ok else "FAILED  ", name,
                                                     r.returncode, len(fl), ", CRASHED" if crash else ""))
            if not control_ok:
                print(r.stdout[-1500:] + r.stderr[-1500:])
                break
            continue
        dead = bool(fl)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode,
                                                   len(fl), ", CRASHED" if crash and not dead else "",
                                                   ("; " + fl[0][:110]) if fl else ""))
        sys.stdout.flush()
        shutil.rmtree(d)
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if control_ok and killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
