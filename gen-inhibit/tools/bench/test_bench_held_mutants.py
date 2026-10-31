#!/usr/bin/env python3
"""Mutants of bench_held.py and of the two lock gates that call it; test_bench_held.py
must fail on each BY A CHECK. A crash is not a kill, and an edit that does not apply
is an error. Needs the pinned core's shared/bench on PYTHONPATH, as the test does.

    py -3.14 -B gen-inhibit/tools/bench/test_bench_held_mutants.py

Each mutant is a copy of the files in the worktree's scratch/, one edit applied to
one of them. Gen-inhibit tester, 2026-10-25.

The unmutated copy runs first, as the control (2026-10-30): if it fails, no mutant is
judged and the run exits 1 (without the core on PYTHONPATH a run once read 0 of 12, every
mutant crashed). Until then a mutant that crashed after a FAIL line was also counted as
killed.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = "test_bench_held.py"
FILES = ("bench_held.py", "gi_paths.py", "interlock_bench.py", "x2_pins_noreset_check.py")
MUTANTS = [
    ("any holder will do", "bench_held.py", '    return h["who"] == ROLE, h["who"]', '    return True, h["who"]'),
    ("any tester will do", "bench_held.py", 'h["who"] == ROLE', 'h["who"].startswith("tester")'),
    ("anyone of gen-inhibit will do", "bench_held.py", 'h["who"] == ROLE', '"gen-inhibit" in h["who"]'),
    ("a free lock is ours", "bench_held.py", '        return False, "free"', '        return True, "free"'),
    ("a log that cannot be read is ours", "bench_held.py",
     '        return False, "the bench log could not be read: %s" % e',
     '        return True, "the bench log could not be read: %s" % e'),
    ("no vtrux_bench is ours", "bench_held.py",
     '        return False, "vtrux_bench is not importable', '        return True, "vtrux_bench is not importable'),
    ("the default reads bench-holder.md", "bench_held.py", "gi_paths.bench_log()", "gi_paths.bench_holder()"),
    ("bench_log() is bench-holder.md", "gi_paths.py",
     '    return project("firmware", "bench-log.jsonl")', '    return project("firmware", "bench-holder.md")'),
    ("interlock_bench does not gate", "interlock_bench.py",
     "    ok, who = bench_held.held_by_us()\n    if not ok:", "    ok, who = True, ''\n    if not ok:"),
    ("interlock_bench gates after it imports can", "interlock_bench.py",
     "    ok, who = bench_held.held_by_us()\n", "    import can\n    ok, who = bench_held.held_by_us()\n"),
    ("x2_pins does not gate", "x2_pins_noreset_check.py",
     "    ok, who = bench_held.held_by_us()\n    if not ok:", "    ok, who = True, ''\n    if not ok:"),
    ("x2_pins reads the Holder line too", "x2_pins_noreset_check.py",
     "    ok, who = bench_held.held_by_us()\n",
     "    ok, who = bench_held.held_by_us()\n    ok = ok or '**Holder:**' in open(gi_paths.bench_holder()).read()\n"),
]


def main():
    base = gi_paths.scratch("mutants-bench_held")
    srcs = {f: open(os.path.join(HERE, f), encoding="utf-8", newline="").read() for f in FILES}
    killed = errors = 0
    # Each copy runs from its own folder, so a relative PYTHONPATH (vtrux_bench) is made absolute.
    pp = [os.path.abspath(p) for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(pp))
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed; and a crash is not a kill
    for n, (name, target, old, new) in enumerate([("the control: the unmutated copy", None, None, None)] + MUTANTS):
        if old is not None and srcs[target].count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times in %s" % (n, name, srcs[target].count(old), target))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        for f, s in srcs.items():
            open(os.path.join(d, f), "w", encoding="utf-8", newline="").write(s.replace(old, new) if f == target else s)
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, "-B", os.path.join(d, TEST)], cwd=d, env=env, capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % (
                "PASS    " if ok else "FAILED  ", name, r.returncode, len(fails),
                ", CRASHED: " + r.stderr.strip().splitlines()[-1][:120] if crashed else "",
                ("; " + fails[0][:100]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED: " + r.stderr.strip().splitlines()[-1][:120] if crashed else "",
            ("; " + fails[0][:100]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
