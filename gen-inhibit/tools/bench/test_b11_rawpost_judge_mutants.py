#!/usr/bin/env python3
"""Mutants of b11_rawpost_judge (the request-to-reply window since topic (E), 2026-11-19);
test_b11_rawpost_judge.py must fail on each BY A CHECK. A crash is not a kill, and
an edit that does not apply is an error.

    py -3.14 gen-inhibit/tools/bench/test_b11_rawpost_judge_mutants.py

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
SRC = "b11_rawpost_judge.py"
TEST = "test_b11_rawpost_judge.py"

MUTANTS = [
    # the request-to-reply window (spec 3, topic (E), 2026-11-19; the reviewer's readings (m)-(r))
    ("mapping: the margin from the largest lateness, not the smallest", "    return off, off - min(late)\n",
     "    return off, max(late) - off\n"),
    ("mapping: the mean, not the median", "    off = statistics.median(late)\n", "    off = sum(late) / len(late)\n"),
    ("mapping: every frame of the replay, not those near the POST", "    lo, hi = max(0, idx - n), min(len(src_t), idx + n)\n",
     "    lo, hi = 0, len(src_t)\n"),
    ("OBSERVE: a device 0x051 after the reply passes", "    if late:\n        return FAIL,", "    if False:\n        return FAIL,"),
    ("OBSERVE: no margin, inside it FAIL", "    late = [t for t in device if tc + margin < t < t_end]\n",
     "    late = [t for t in device if tc < t < t_end]\n"),
    ("OBSERVE: inside the margin passes", "    if near:\n        return ERROR, \"a device 0x051 %s ms after the reply, within",
     "    if False:\n        return ERROR, \"a device 0x051 %s ms after the reply, within"),
    ("OBSERVE: the next POST's frames counted", "    late = [t for t in device if tc + margin < t < t_end]\n",
     "    late = [t for t in device if tc + margin < t]\n"),
    ("OBSERVE: nothing to see read as PASS",
     "    if not any(ts - LIVE_BEFORE_S <= t <= ts for t in device):\n        return NA,",
     "    if False:\n        return NA,"),
    ("OBSERVE: any earlier frame makes it applicable", "    if not any(ts - LIVE_BEFORE_S <= t <= ts for t in device):\n",
     "    if not any(t <= ts for t in device):\n"),
    ("back: a stale answer passes", "    if stale:\n        t, a = stale[0]\n        return FAIL,",
     "    if False:\n        t, a = stale[0]\n        return FAIL,"),
    ("back: an answer to any command after the reply is stale",
     "    stale = [(t, a) for t, a in after if t > tc + margin and a is not None and a < ts - margin]\n",
     "    stale = [(t, a) for t, a in after if t > tc + margin and a is not None]\n"),
    ("back: commands in the window counted as from before the request",
     "    stale = [(t, a) for t, a in after if t > tc + margin and a is not None and a < ts - margin]\n",
     "    stale = [(t, a) for t, a in after if t > tc + margin and a is not None and a < tc]\n"),
    ("back: within the margins passes", "    if near:\n        t, a = near[0]\n        return ERROR,",
     "    if False:\n        t, a = near[0]\n        return ERROR,"),
    ("back: the in-window count not kept", "               sum(1 for t, a in after if ts < t <= tc and a is not None and a < ts)}",
     "               0}"),
    ("page: a missing read ERROR, not VOID (before reading (t))",
     '        return VOID, VOID, ["no page read after the reply in the record"]',
     '        return ERROR, ERROR, ["no page read after the reply in the record"]'),
    ("page: an unanswered read VOID, not ERROR", '        return ERROR, ERROR, ["the page read after the reply did not answer',
     '        return VOID, VOID, ["the page read after the reply did not answer'),
    ("page: the mode not compared", "    mv = PASS if page.get(\"mode\") == mode else FAIL\n", "    mv = PASS\n"),
    ("page: the queue over the bound passes", "    elif q > QUEUE_BOUND:\n", "    elif False:\n"),
    ("page: no bound set passes", "    elif QUEUE_BOUND is None:\n        qv = ERROR\n", "    elif QUEUE_BOUND is None:\n        qv = PASS\n"),
    ("POST: a refused POST judged anyway", '    if raw.get("t_complete") is None or raw.get("t_sent") is None or e.get("http") != 200:\n',
     '    if raw.get("t_complete") is None or raw.get("t_sent") is None:\n'),
    ("POST: the stale rule for OBSERVE, the silence rule for the rest", '    if e.get("mode") == 1:\n        wv, wwhy = observe_half',
     '    if e.get("mode") != 1:\n        wv, wwhy = observe_half'),
    ("POST: the page halves left out of the verdict", "verdict=worst([wv, mv, qv])", "verdict=worst([wv])"),
    ("worst: a VOID outranks an ERROR", "RANK = [FAIL, ERROR, VOID]", "RANK = [FAIL, VOID, ERROR]"),
    ("worst: NOT APPLICABLE never passes", "    return next((v for v in RANK if v in vs), PASS)",
     "    return next((v for v in RANK + [NA] if v in vs), PASS)"),
    ("judge_run: the window placed without the offset", "        w = (lambda t: None if t is None or off is None else t + off)",
     "        w = (lambda t: None if t is None or off is None else t)"),
    ("judge_run: the first POST from the arm's mode lost", '    prev = MODE_NUMBER.get(d.get("mode"))\n', "    prev = None\n"),
    # the arm void (spec 12.1)
    ("an arm void for a 12.1 reason is judged", '    if r.get("arm_void") or r.get("arm_unlisted"):\n', "    if False:\n"),
    ("a void arm's posts are dropped", '"verdict": why} for e in sched])', '"verdict": why} for e in sched[:1]])'),
]


def main():
    root = os.getcwd()
    base = gi_paths.scratch("mutants-b11_rawpost_judge_mutants")   # the worktree's scratch/ (AGENTS.md)
    killed = errors = 0
    # the unmutated copy runs first as the control (2026-10-30): where the test cannot pass,
    # every mutant would read killed; and a crash is not a kill
    for n, (name, old, new) in enumerate([("the control: the unmutated copy", None, None)] + MUTANTS):
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
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
                gi_paths.remove_tree(base)
                print("\nthe control FAILS: no mutant is judged, so none is killed")
                return 1
            continue
        dead = bool(fails) and not crashed
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL%s%s)" % (
            "KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fails),
            ", CRASHED" if "Traceback" in r.stderr else "", ("; " + fails[0][:80]) if fails else ""))
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors; the control passes" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
