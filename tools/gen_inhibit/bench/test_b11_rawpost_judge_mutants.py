#!/usr/bin/env python3
"""Mutants of b11_rawpost_judge's pure parts (reviewer, 2026-10-17);
test_b11_rawpost_judge.py must fail on each BY A CHECK. A crash is not a kill, and
an edit that does not apply is an error.

    py -3.14 tools/gen_inhibit/bench/test_b11_rawpost_judge_mutants.py

Run from the reverse-it root, which goes on PYTHONPATH with this folder.
"""
import os
import shutil
import subprocess
import tempfile
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "b11_rawpost_judge.py"
TEST = "test_b11_rawpost_judge.py"

MUTANTS = [
    ("a device that sent no 0x051 is judged",
     "    if not any(arrival - LIVE_BEFORE_S <= t <= arrival for t in answers):\n        return NOT_APPLICABLE, []\n", ""),
    ("any earlier answer makes it applicable", "if not any(arrival - LIVE_BEFORE_S <= t <= arrival for t in answers):", "if not any(t <= arrival for t in answers):"),
    ('the wake frame is not allowed', 'strict_ok = all(n <= 1 for n in k)', 'strict_ok = all(n <= 0 for n in k)'),
    ('two commands after arrival allowed', 'strict_ok = all(n <= 1 for n in k)', 'strict_ok = all(n <= 2 for n in k)'),
    ('a pass on the ACK is folded into PASS', '        return ACK_ONLY, k', '        return STRICT_PASS, k'),
    ('a pass on the ACK is folded into FAIL', '        return ACK_ONLY, k', '        return STRICT_FAIL, k'),
    ('no arrival passes', '        return NO_DATA, []', '        return STRICT_PASS, []'),
    ('a command at the arrival counts as after it', 'bisect.bisect_right(commands, arrival)', 'bisect.bisect_left(commands, arrival)'),
    ("answers past the span's end count", 'if arrival < t < t_end:', 'if arrival < t:'),
    ('the request time is the retransmission', 't_req = min(p["t"] for p in req if p["seq"] + p["len"] == end)', 't_req = last["t"]'),
    ('the request time is the first segment, not the last', 'last = max(req, key=lambda p: (p["seq"] + p["len"], p["t"]))', 'last = min(req, key=lambda p: (p["seq"], p["t"]))'),
    ('any device packet is the ACK', 'p["src"] == DEVICE_IP and p["ack"] >= end and p["t"] >= t_req', 'p["src"] == DEVICE_IP and p["t"] >= t_req'),
    ('a finished page read is still in progress', 'if sent and (not done or max(done) > t):', 'if sent:'),
]


def main():
    root = os.getcwd()
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "b11_rawpost_judge_mutants")   # outside SeaDrive (AGENTS.md)
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        nl = "\r\n" if "\r\n" in src else "\n"
        o, w = old.replace("\n", nl), new.replace("\n", nl)
        if src.count(o) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(o)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src.replace(o, w))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, os.path.join(d, TEST)], cwd=root,
                           env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + root),
                           capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        dead = bool(fails)
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if "Traceback" in r.stderr else "", ("; " + fails[0][:80]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
