#!/usr/bin/env python3
"""Tests b25_keystate_judge on synthetic runs built with test_interlock_judge's Sim;
no device. --mutants runs its mutants; each must make a check fail.

    py -3.14 tools/gen_inhibit/bench/test_b25_keystate_judge.py [--mutants]
Run from the reverse-it root.
"""
import os
import shutil
import subprocess
import tempfile
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import b25_keystate_judge as K     # noqa: E402
import test_interlock_judge as T   # noqa: E402

FAILS = []


def check(label, ok, detail=""):
    print("%s  %s  %s" % ("ok  " if ok else "FAIL", label, str(detail)[:150]))
    if not ok:
        FAILS.append(label)


def rekey(tx_off=False, keep_latch=False, early=False, mode0_in_off=False, no_key_until=None,
          stale_key=False):
    """Armed at 0.2 s; key on 0.5-5 s, off 5-9 s, on 9-14 s; contactors closed
    0.5-5.2 s and again from 10 s with 0x411 throughout; the device answers while the
    key is on and the gate allows; inverter lost latched at 6 s, cleared at the key-on."""
    s = T.Sim()
    s.arm(0.2)
    s.vcm(0.3, 14.0, period=0.01)
    k0 = no_key_until if no_key_until is not None else 0.5
    s.key(k0, 5.0, True)
    s.key(5.0, 9.0, False)
    if not stale_key:
        s.key(9.0, 14.0, True)
    else:
        s.key(9.0, 9.3, True)        # then no 0x592 at all
    s.mainc(0.5, 5.2, 11)
    s.mainc(10.0, 14.0, 11)
    s.soc(0.5, 14.0, 7700)
    gate2 = 9.0 if early else 10.06
    s.device(lambda t: (0.6 <= t < 5.0) or (tx_off and 6.0 <= t < 6.2) or (gate2 <= t < 14.0))
    s.pages(0.3, 14.0, lambda t: {"live": t < 5.0 or t > 10.1,
                                  "abort": "inverter_lost" if (6.0 < t < 9.5 or (keep_latch and t >= 6.0))
                                  else "none"})
    if mode0_in_off:
        s.f = [x for x in s.f if not (x[1] == 0x7F1 and 6.0 < x[0] < 8.0)]
        s.f.append((7.0, 0x7F1, T.enc(T.J.DIAG, 0x7F1, diag_mode=0, diag_flags=0, diag_disable_reason=0)))
    return s


def run(s):
    return K.judge(s.f, s.posts)


def failed(r, part):
    return [c["name"] for c in r["checks"] if not c["ok"] and part in c["name"]]


def main():
    r = run(rekey())
    check("the right device: AS SPECIFIED, one latched key-on, cleared, re-entered at the gate",
          r["verdict"] == "AS SPECIFIED" and r["facts"]["latched_key_ons"] == 1, r["checks"])
    r = run(rekey(tx_off=True))
    check("a 0x051 while the key reads off fails rule 1", failed(r, "rule 1"), r["checks"])
    r = run(rekey(keep_latch=True))
    check("a latch still set after the key-on fails rule 2", failed(r, "rule 2"), r["checks"])
    r = run(rekey(early=True))
    check("live again before the contactors close fails re-entry", failed(r, "re-entry"), r["checks"])
    r = run(rekey(mode0_in_off=True))
    check("a page reading mode 0 during the key-off fails 'armed'", failed(r, "armed"), r["checks"])
    r = run(rekey(no_key_until=1.0))
    check("a 0x051 before the first 0x592 fails decision 3 (never-seen reads off)",
          failed(r, "decision 3") and failed(r, "rule 1"), r["checks"])
    keys = [(1.0, 1), (2.0, 0)]
    check("key_on_at: fresh 1 is on, stale 1 (0.6 s) is off, a 0 is off",
          K.key_on_at(keys, 1.4) and not K.key_on_at(keys, 1.6) and not K.key_on_at(keys, 2.1))
    check("key_on_at: within EDGE_S after a 1 -> 0 reading is still on (the device may not have it yet)",
          K.key_on_at([(1.0, 1), (1.4, 1), (1.5, 0)], 1.5 + K.EDGE_S / 2)
          and not K.key_on_at([(1.0, 1), (1.4, 1), (1.5, 0)], 1.5 + 3 * K.EDGE_S))
    r = run(rekey(stale_key=True))
    check("a key gone stale after the key-on: device frames then fail rule 1", failed(r, "rule 1"), r["checks"])
    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


MUTANTS = [
    ("no freshness", "    if v == 1 and t - tk <= KEY_FRESH_S:\n", "    if v == 1:\n"),
    ("rule 2 not checked", '                ab2 == "none" and (dis2 == 0 or not dis),', "                True,"),
    ("re-entry not checked", "                not pre, \"%d device 0x051 before it\" % len(pre))",
     "                True, \"%d device 0x051 before it\" % len(pre))"),
    ("mode 0 in a key-off accepted", "        in_off and not zero,", "        in_off,"),
    ("never-seen reads on", "        return False\n    tk, v = keys[i]", "        return True\n    tk, v = keys[i]"),
    ("no edge allowance", "    if v == 0 and i > 0 and keys[i - 1][1] == 1 and t - tk <= EDGE_S:\n",
     "    if False:\n"),
]


def mutants():
    src = open(os.path.join(HERE, "b25_keystate_judge.py"), encoding="utf-8").read()
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "b25_mutants")   # outside SeaDrive (AGENTS.md)
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, "b25_keystate_judge.py"), "w", encoding="utf-8").write(src.replace(old, new))
        shutil.copy(os.path.abspath(__file__), d)
        r = subprocess.run([sys.executable, os.path.join(d, os.path.basename(__file__))],
                           capture_output=True, text=True, cwd=os.getcwd(),
                           env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + os.getcwd()))
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        killed += bool(fails)
        print("%s m%d %s%s" % ("KILLED  " if fails else "SURVIVED", n, name,
                               ("; " + fails[0][:80]) if fails else (", CRASHED" if "Traceback" in r.stderr else "")))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
