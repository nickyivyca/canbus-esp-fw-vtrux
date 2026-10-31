#!/usr/bin/env python3
"""Mutants of gi_paths.py's scratch removal; test_gi_scratch.py must fail on each BY A
CHECK. A crash is not a kill (a FAIL whose detail starts CRASH does not count), and an
edit that does not apply is an error. Each mutant is a copy of gi_paths.py in this run's
scratch/ folder with one edit, run through GI_PATHS_DIR. Gen-inhibit tester, 2026-10-31.

    py -3.14 -B test_gi_scratch_mutants.py
"""
import os
import subprocess
import sys
import gi_paths  # noqa: E402  this run's scratch/ folder, and its removal

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = "gi_paths.py"
MUTANTS = [
    ("remove_tree with ignore_errors", "        shutil.rmtree(lp, onexc=again)", "        shutil.rmtree(lp, ignore_errors=True)"),
    ("the read-only flag not cleared", "            os.chmod(p, stat.S_IWRITE)", "            pass"),
    ("no long-path prefix on Windows", '    return "\\\\\\\\?\\\\" + p\n', "    return p\n"),
    ("a failure not printed", '    print("could not remove %s: %d entr%s failed"', '    0 and print("could not remove %s: %d entr%s failed"'),
    ("the failing entries and reasons not printed", "    for p, e in failed[:10]:", "    for p, e in failed[:0]:"),
    ("a failure raises", "        failed.append((p, exc))", "        raise exc"),
    ("a failure returns True", "        print(\"  ... and %d more\" % (len(failed) - 10), file=out)\n    return False",
     "        print(\"  ... and %d more\" % (len(failed) - 10), file=out)\n    return True"),
    ("owner_state ignores the start time (a reused pid is running)",
     '    return "running" if now == start else "finished"', '    return "running"'),
    ("owner_state ignores keep", '    if o.get("keep"):\n        return "kept"\n', ""),
    ("an unreadable owner file is finished", "    except (OSError, ValueError, KeyError, TypeError):\n        return None",
     "    except (OSError, ValueError, KeyError, TypeError):\n        return \"finished\""),
    ("the owner file inside the folder", "    return d.rstrip(\"/\\\\\") + OWNER_SUFFIX",
     "    return os.path.join(d, \"owner.json\")"),
    ("the owner file records no start time", '"start": process_start(os.getpid())', '"start": None'),
    ("siblings: anything not running is removed", '        if owner_state(d) == "finished" and remove_tree(d, out):',
     '        if owner_state(d) != "running" and remove_tree(d, out):'),
    ("siblings: the owner file removed when the folder wasn't",
     '        if owner_state(d) == "finished" and remove_tree(d, out):\n            _remove_owner(d)',
     '        if owner_state(d) == "finished":\n            remove_tree(d, out)\n            _remove_owner(d)'),
    ("scratch() doesn't remove finished siblings", "        remove_finished_siblings(d)\n", ""),
    ("scratch() writes no owner file", "        write_owner(d)                  # before the folder: a folder never lacks one\n", ""),
    ("scratch() registers no removal at exit", "        if at_exit:\n            atexit.register", "        if False:\n            atexit.register"),
    ("finish ignores GI_KEEP_SCRATCH", '        keep = bool(os.environ.get(KEEP_ENV, "").strip())', "        keep = False"),
    ("finish keeps without marking the owner file", "            write_owner(d, keep=True)\n", ""),
    ("finish keeps silently", '            print("kept %s" % d, file=out)\n', ""),
    ("finish leaves the owner file", "    if remove_tree(d, out):\n        _remove_owner(d)\n        return True\n    return False",
     "    if remove_tree(d, out):\n        return True\n    return False"),
    ("finish ignores the pid (a forked child removes its parent's)", "    if pid is not None and pid != os.getpid():", "    if False:"),
    ("run_dir removes only on a clean end", "        try:\n            yield d\n        finally:\n            finish(d, keep)",
     "        yield d\n        finish(d, keep)"),
    ("run_dir never removes", "        finally:\n            finish(d, keep)", "        finally:\n            pass"),
    ("process_start: one number for every process", "            return (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime",
     "            return 1"),
    ("process_start: a process that isn't there is running", '            return "?" if ctypes.get_last_error() == 5 else None',
     '            return "?"'),
]


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
    return d


def main():
    with gi_paths.run_dir("mutants-gi_scratch") as base:
        return run(base)


def run(base):
    src = open(os.path.join(HERE, TARGET), encoding="utf-8", newline="").read()
    killed = errors = 0
    dead, crashed, fails, rc = run_test(copy_tree(base, "control", src))
    control_ok = rc == 0 and not fails
    print("%s control: the unmutated copy  (exit %d, %d FAIL)%s"
          % ("PASS    " if control_ok else "FAILED  ", rc, len(fails), "".join("\n           " + f[:130] for f in fails[:3])))
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        dead, crashed, fails, rc = run_test(copy_tree(base, "m%d" % n, src.replace(old, new)))
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s)%s" % ("KILLED  " if dead else "SURVIVED", n, name, rc, len(fails),
                                                 ", CRASHED" if crashed else "",
                                                 "".join("\n           " + f[:130] for f in fails[:3])))
    print("\n%d of %d killed by a check, %d errors; the control %s"
          % (killed, len(MUTANTS), errors, "passes" if control_ok else "DOES NOT PASS"))
    return 0 if killed == len(MUTANTS) and not errors and control_ok else 1


if __name__ == "__main__":
    sys.exit(main())
