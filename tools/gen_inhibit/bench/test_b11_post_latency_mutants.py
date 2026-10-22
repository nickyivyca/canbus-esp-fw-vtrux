#!/usr/bin/env python3
"""Mutants of b11_post_latency.post_rows (reviewer, 2026-10-17);
test_b11_post_latency.py must fail on each BY A CHECK. A crash is not a kill, and
an edit that does not apply is an error.

    py -3.14 tools/gen_inhibit/bench/test_b11_post_latency_mutants.py

Run from the reverse-it root, which goes on PYTHONPATH with this folder.
"""
import os
import shutil
import subprocess
import tempfile
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "b11_post_latency.py"
TEST = "test_b11_post_latency.py"

MUTANTS = [
    ("frames before the send count", "c = [t for t in dev_cmd if t0 <= t < t_next]", "c = [t for t in dev_cmd if t < t_next]"),
    ("the span runs past the next POST", "t_next = posts[k + 1][0] if k + 1 < len(posts) else end", "t_next = end"),
    ("a raw POST's reply is post_s's end", 'if raw and raw["send_to_first_byte_ms"] is not None:', "if False:"),
    ("reply -> last has the wrong sign", "ms(a[-1] - t0 - reply_after)", "ms(a[-1] - t0 + reply_after)"),
    ("the last frame is the last 0x051 only", "a = [t for t in dev_any if t0 <= t < t_next]",
     "a = [t for t in dev_cmd if t0 <= t < t_next]"),
    ("the period is the mean of all gaps before the send",
     "before = dev_cmd[max(0, bisect.bisect_left(dev_cmd, t0) - 21):bisect.bisect_left(dev_cmd, t0)]",
     "before = dev_cmd[:bisect.bisect_left(dev_cmd, t0) + 2]"),
    ("the first 0x051 back may be before the send", "i = bisect.bisect_left(dev_cmd, t0)\n            f = dev_cmd[i]",
     "i = max(0, bisect.bisect_left(dev_cmd, t0) - 1)\n            f = dev_cmd[i]"),
]


def main():
    root = os.getcwd()
    base = os.path.join(tempfile.gettempdir(), "gen-inhibit-mutants", "b11_post_latency_mutants")   # outside SeaDrive (AGENTS.md)
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
