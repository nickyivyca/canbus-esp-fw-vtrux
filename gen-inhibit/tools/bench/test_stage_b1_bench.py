#!/usr/bin/env python3
"""Tests stage_b1_bench.py without a device: the gate's watch on planted pages and a fake
clock, and main()'s refusals (a lock that is not ours, an unknown step) with stand-in
modules. Gen-inhibit tester, 2026-11-23.

  watch_gate  rising uptime on the image past 75 s: PASS; another image (a rollback): FAIL;
              uptime falling (a restart): FAIL; never past 75 s in the limit: FAIL; a read
              that fails is retried, not judged.
  main        the lock held by anyone else: exit 2, nothing run; an unknown step: exit 2.
--mutants: copies with each gate condition dropped; each must fail a case.

    python3.13 -B test_stage_b1_bench.py [--mutants]
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.environ.get("SB_TARGET") or os.path.join(HERE, "stage_b1_bench.py")
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location("sb", TARGET)
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
FAILS = []


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:160]))
    if not ok:
        FAILS.append(label)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def pages(seq):
    it = iter(seq)

    def fn():
        x = next(it)
        if isinstance(x, Exception):
            raise x
        return x
    return fn


def cases():
    P = lambda up, sha="aa": {"elf_sha256": sha, "uptime_ms": up, "mode": 0}
    c = Clock()
    ok, why, reads = S.watch_gate(pages([P(10000), P(40000), P(70000), P(76000)]), "aa", clock=c.now, sleep=c.sleep)
    check("gate: rising past 75 s on the image: PASS", ok and len(reads) == 4, (ok, why))
    c = Clock()
    ok, why, _ = S.watch_gate(pages([P(10000), P(40000, "bb")]), "aa", clock=c.now, sleep=c.sleep)
    check("gate: another image: FAIL", not ok and "left the image" in why, why)
    c = Clock()
    ok, why, _ = S.watch_gate(pages([P(30000), P(2000)]), "aa", clock=c.now, sleep=c.sleep)
    check("gate: uptime falls: FAIL", not ok and "fell" in why, why)
    c = Clock()
    ok, why, _ = S.watch_gate(pages([P(1000 + i) for i in range(100)]), "aa", clock=c.now, sleep=c.sleep)
    check("gate: never past 75 s: FAIL", not ok and "never" in why, why)
    c = Clock()
    ok, why, reads = S.watch_gate(pages([P(10000), OSError("no route"), P(80000)]), "aa", clock=c.now, sleep=c.sleep)
    check("gate: a failed read is retried: PASS", ok and any("error" in r for r in reads), (ok, why))

    # main's refusals, with stand-ins for the lock check
    fake = types.ModuleType("bench_held")
    fake.held_by_us = lambda: (False, "android")
    sys.modules["bench_held"] = fake
    try:
        rc = S.main(["--settings", "x", "--image", "y", "--run-dir", "z"])
    except Exception as e:      # noqa: BLE001  going on past the lock shows as an error here
        rc = "went on past the lock: %r" % e
    check("main: a lock not ours: exit 2", rc == 2, rc)
    check("main: an unknown step: exit 2",
          S.main(["--settings", "x", "--image", "y", "--run-dir", "z", "--steps", "load,bogus"]) == 2)
    check("main: nothing written on a refusal", not os.path.exists("z"))


MUTANTS = [
    ("image not checked", "        if pg.get(\"elf_sha256\") != sha:", "        if False:"),
    ("uptime fall not checked", "        if len(ups) > 1 and ups[-1] < ups[-2]:", "        if False:"),
    ("passes on the first read", "        if isinstance(pg.get(\"uptime_ms\"), int) and pg[\"uptime_ms\"] >= until_ms:",
     "        if True:"),
    ("timeout passes", "    return False, \"uptime never reached %d ms in %.0f s\" % (until_ms, limit), reads",
     "    return True, \"\", reads"),
    ("lock not checked", "    if not ours:", "    if False:"),
]


def mutants():
    text = open(TARGET, encoding="utf-8").read()
    work = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "scratch", "stage-b1-test",
                        "m%d" % os.getpid())
    os.makedirs(work, exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    killed = 0
    try:
        ctl = subprocess.run([sys.executable, "-B", __file__], capture_output=True, text=True, env=env, cwd=work)
        check("control: the unmutated script passes", ctl.returncode == 0, ctl.stdout[-200:])
        for i, (name, old, new) in enumerate(MUTANTS):
            if text.count(old) != 1:
                check("mutant %s: text found once" % name, False, text.count(old))
                continue
            p = os.path.join(work, "sb_%d.py" % i)
            open(p, "w", encoding="utf-8", newline="\n").write(text.replace(old, new))
            r = subprocess.run([sys.executable, "-B", __file__], capture_output=True, text=True,
                               env=dict(env, SB_TARGET=p), cwd=work)
            dead = r.returncode == 1 and "\nFAIL  " in "\n" + r.stdout and "Traceback" not in r.stderr
            killed += dead
            check("mutant %s: killed by a check" % name, dead, (r.stdout[-120:], r.stderr[-200:]))
    finally:
        shutil.rmtree(work)
    print("%d of %d mutants killed" % (killed, len(MUTANTS)))


if __name__ == "__main__":
    if "--mutants" in sys.argv[1:]:
        mutants()
    else:
        cases()
    print("%s: %d failed" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    sys.exit(1 if FAILS else 0)
