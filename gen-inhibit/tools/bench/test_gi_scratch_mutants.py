#!/usr/bin/env python3
"""Mutants of gi_paths.py's scratch removal; test_gi_scratch.py must fail on each BY A
CHECK. A crash is not a kill (a FAIL whose detail starts CRASH does not count), and an
edit that does not apply is an error. Each mutant is a copy of gi_paths.py in this run's
scratch/ folder with one edit, run through GI_PATHS_DIR. Gen-inhibit tester, 2026-10-31.

    python3.13 -B test_gi_scratch_mutants.py      (py -3.14 on Windows)

A mutant of one platform's branch (a 4th field, "nt" or "posix") is printed N/A on the
other and not counted, since that branch never runs there; its edit must still apply
(2026-11-12, the reviewer's Linux leftovers).

Since port step 4 item 3 the removal, the owner files and process_start are shared/bench's
vtrux_bench/scratch.py, and its mechanics' mutants are in its own can-fail (tests/
test_scratch_helper.py --can-fail). The mutants here are of what gen-inhibit keeps: where
the helper is loaded from (the pinned checkout in a worktree, never the staged file; the
staged file only with a matching record), stage_core, the owner-state names, its layout
and keep/finish. Each copy has gi_core_pin.py beside it, as gi_paths loads it from there.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  this run's scratch/ folder, and its removal

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = "gi_paths.py"
MUTANTS = [
    # where the helper is loaded from (the gen-inhibit reviewer's ruling, 2026-11-26)
    ("in a worktree, the staged file is used", "    if in_worktree():\n        try:\n            core_pin().use_bench()",
     "    if False:\n        try:\n            core_pin().use_bench()"),
    ("in a worktree, the staged file when the pin check refuses",
     "            raise PathsError(\"the shared scratch helper (%s's %s) cannot",
     "            S = _staged_core(); 0 and PathsError(\"the shared scratch helper (%s's %s) cannot"),
    ("no copy is ever taken to be in a worktree", "    return True\n\n\ndef core_pin", "    return False\n\n\ndef core_pin"),
    ("no git, a missing record: the staged file trusted as it is",
     '        raise PathsError("%s is in no git worktree, so the shared scratch helper is taken only from the "',
     '        want = hashlib.sha256(open(src_path, "rb").read()).hexdigest(); '
     '0 and PathsError("%s is in no git worktree, so the shared scratch helper is taken only from the "'),
    ("the staged file's sha256 not compared", "    if got != want:", "    if False:"),
    ("the staged file's sha256 taken from its record", "    got = hashlib.sha256(data).hexdigest()\n", "    got = want\n"),
    ("stage_core: the record's sha256 of other bytes", '"sha256": hashlib.sha256(data).hexdigest()}',
     '"sha256": hashlib.sha256(data + b" ").hexdigest()}'),
    ("stage_core at a commit reads HEAD's pin", '_git(repo, "show", "%s:%s" % (commit, PIN_REL))',
     '_git(repo, "show", "%s:%s" % ("HEAD", PIN_REL))'),
    # gen-inhibit's layout and its names for the helper's states
    ("scratch() makes its folder without the helper (no owner file, no prune)",
     '    d = scratch_core().make(d, time.strftime("%Y%m%dT%H%M%S"), out=_say(None))\n',
     '    d = os.path.join(d, time.strftime("%Y%m%dT%H%M%S"))\n    os.makedirs(d)\n'),
    ("scratch() registers no removal at exit", "    if at_exit:\n        atexit.register", "    if False:\n        atexit.register"),
    ("the owner file inside the folder", "    return scratch_core().owner_path(d)", '    return os.path.join(d, "owner.json")'),
    ("OWNER_SUFFIX not the helper's", 'OWNER_SUFFIX = ".owner.json"        #', 'OWNER_SUFFIX = ".owner"        #'),
    ("owner_state: a live one is finished", '"live": "running"', '"live": "finished"'),
    ("owner_state: another host's is finished", '"kept": "kept"}', '"kept": "kept", "other-host": "finished"}'),
    ("owner_state: no owner file is finished", '"kept": "kept"}', '"kept": "kept", "no-owner": "finished"}'),
    ("siblings: nothing removed", "    return scratch_core().prune(parent, out=_say(out))", "    return []"),
    ("process_start: one number for every process", "    return scratch_core().process_start(pid)", "    return 1"),
    # remove_tree's report
    ("a failure not printed", '    print("could not remove %s: %d entr%s failed"', '    0 and print("could not remove %s: %d entr%s failed"'),
    ("the failing entries and reasons not printed", "    for p, why in left[:10]:", "    for p, why in left[:0]:"),
    ("a failure returns True", "        print(\"  ... and %d more\" % (len(left) - 10), file=out)\n    return False",
     "        print(\"  ... and %d more\" % (len(left) - 10), file=out)\n    return True"),
    # finish and run_dir
    ("finish ignores GI_KEEP_SCRATCH", '        keep = bool(os.environ.get(KEEP_ENV, "").strip())', "        keep = False"),
    ("finish keeps without marking the owner file", "                S.keep(d, ", "                0 and S.keep(d, "),
    ("finish keeps silently", '            print("kept %s" % d, file=out)\n', ""),
    ("finish ignores the pid (a forked child removes its parent's)", "    if pid is not None and pid != os.getpid():", "    if False:"),
    ("run_dir removes only on a clean end", "        try:\n            yield d\n        finally:\n            finish(d, keep)",
     "        yield d\n        finish(d, keep)"),
    ("run_dir never removes", "        finally:\n            finish(d, keep)", "        finally:\n            pass"),
]
BESIDE = ("gi_core_pin.py",)


def run_test(d):
    r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "test_gi_scratch.py")], capture_output=True, text=True,
                       env=dict(os.environ, GI_PATHS_DIR=d), cwd=d)
    fails = [l.strip() for l in r.stdout.splitlines() if l.startswith("FAIL  ")]
    crashed = "Traceback" in r.stderr
    dead = bool(fails) and not crashed and not all(" CRASH " in f for f in fails)
    return dead, crashed, fails, r.returncode


def copy_tree(base, name, text):
    """The copied gi_paths.py's folder, laid out as the repo is (gen-inhibit/tools/bench/)."""
    d = os.path.join(base, name, "gen-inhibit", "tools", "bench")
    os.makedirs(d)
    open(os.path.join(d, TARGET), "w", encoding="utf-8", newline="").write(text)
    for f in BESIDE:
        shutil.copyfile(os.path.join(HERE, f), os.path.join(d, f))
    return d


def main():
    with gi_paths.run_dir("mutants-gi_scratch") as base:
        return run(base)


def run(base):
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = na = 0
    dead, crashed, fails, rc = run_test(copy_tree(base, "control", src))
    control_ok = rc == 0 and not fails
    print("%s control: the unmutated copy  (exit %d, %d FAIL)%s"
          % ("PASS    " if control_ok else "FAILED  ", rc, len(fails), "".join("\n           " + f[:130] for f in fails[:3])))
    for n, (name, old, new, *where) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        if where and where[0] != os.name:
            print("N/A      m%d %s  (%s only; this run is on %s)"
                  % (n, name, "Windows" if where[0] == "nt" else "POSIX", sys.platform))
            na += 1
            continue
        dead, crashed, fails, rc = run_test(copy_tree(base, "m%d" % n, src.replace(old, new)))
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)%s" % ("KILLED  " if dead else "SURVIVED", n, name, rc, len(fails),
                                                 ", CRASHED" if crashed else "",
                                                 "".join("\n           " + f[:130] for f in fails[:3])))
    print("\n%d of %d killed by a check, %d errors, %d not applicable on %s; the control %s"
          % (killed, len(MUTANTS) - na, errors, na, sys.platform, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) - na and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
