#!/usr/bin/env python3
"""Mutants of b11_post_latency.post_rows (reviewer, 2026-10-17);
test_b11_post_latency.py must fail on each BY A CHECK. A crash is not a kill, and
an edit that does not apply is an error.

    py -3.14 gen-inhibit/tools/bench/test_b11_post_latency_mutants.py

Run from the reverse-it root, which goes on PYTHONPATH with this folder.

The unmutated copy runs first, as the control (2026-10-30): if it fails, no mutant is
judged and the run exits 1. Until then a crashed mutant was also counted as killed.
"""
import os
import shutil
import subprocess
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

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
    base = gi_paths.scratch("mutants-b11_post_latency_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed; and a crash is not a kill
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        d = os.path.join(base, "m%d" % n)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        src = open(os.path.join(HERE, SRC), encoding="utf-8", newline="").read()
        nl = "\r\n" if "\r\n" in src else "\n"
        o, w = (old or "").replace("\n", nl), (new or "").replace("\n", nl)
        if old is not None and src.count(o) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(o)))
            errors += 1
            continue
        open(os.path.join(d, SRC), "w", encoding="utf-8", newline="").write(src if old is None else src.replace(o, w))
        shutil.copy(os.path.join(HERE, TEST), d)
        r = subprocess.run([sys.executable, os.path.join(d, TEST)], cwd=root,
                           env=dict(os.environ, PYTHONPATH=HERE + os.pathsep + root),
                           capture_output=True, text=True)
        fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL ")]
        crashed = "Traceback" in r.stderr
        if old is None:
            ok = r.returncode == 0 and not fails and not crashed
            print("%s m0 %s  (exit %d, %d FAIL%s%s)" % ("PASS    " if ok else "FAILED  ", name, r.returncode,
                                                       len(fails), ", CRASHED" if crashed else "",
                                                       ("; " + fails[0][:90]) if fails else ""))
            if not ok:
                shutil.rmtree(base, ignore_errors=True)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if "Traceback" in r.stderr else "", ("; " + fails[0][:80]) if fails else ""))
    shutil.rmtree(base, ignore_errors=True)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
