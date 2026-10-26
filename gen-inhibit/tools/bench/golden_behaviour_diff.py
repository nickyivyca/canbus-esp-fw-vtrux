#!/usr/bin/env python3
"""Separate a REAL behaviour change from the noise of a trace-format change.

Written 2026-09-20 for the spec 7.1 implementation, and kept because the
problem recurs every time the host harness learns to print a new field.

THE PROBLEM. `test/gen_inhibit_host/run_tests.py` is a regression harness: it
diffs the runner's trace against a blessed golden. That works beautifully
while only behaviour moves. It fails the moment the TRACE FORMAT moves -- add
one field to the STATE line and every STATE line in all thirty goldens
differs, so the diff is 100 % noise and the only way through it is to bless
blind. Blessing blind is exactly what run_tests.py's own header warns against:
"a wrong rule blessed into a golden stays green forever".

WHAT THIS DOES. It normalises both sides -- the pre-change goldens and the
current output -- by erasing the fields that are new or that the new scenario
inputs necessarily moved, then diffs what is left. Anything that still differs
is a behaviour change and has to be justified one by one.

It takes the baseline directory as a second argument, because the problem
recurs: `golden_pre71` for the spec 7.1 change, `golden_pre_a1` for review A1's
contactor gate, and whatever the next one is called.

For spec 7.1 the erasures are:

  ` latched=N`   new STATE/FINAL field (section 7 abort latch)
  ` key=N`       new STATE/FINAL field (the spec 7.1 transmit gate)
  `other=N`      frame counts moved because every scenario now carries an
                 injected 0x592 key train; the count is an input change, not
                 a decision change
  `EV KEY...`    new event kinds

and, because a mode-OFF abort became a latched-in-place abort, `mode=N` is
erased ONLY on lines that also carry an abort -- so a mode change anywhere
else still shows up.

Review A1 (2026-09-24) adds:

  ` socv=N`, ` socsince=N`, ` mainc=N`   new STATE/FINAL fields (the spec 6.2
                                         SoC-valid marker)
  `EV SOC_VALID`                         new event kind
  `soc=N`                                every scenario now carries an injected
                                         healthy 0x411 train, so soc_raw is a
                                         reported input, not a decision (it is
                                         already outside FINAL_KEEP)

A1's own four scenarios are new and have no baseline, so they show as `gone`
here and must be read as traces in their own right.

Usage (on madhouse-debian, where the harness runs):

    python3 golden_behaviour_diff.py ~/gi_host/test/gen_inhibit_host golden_pre_a1

expects that directory beside `golden/`, holding the pre-change goldens. Make
it with `cp -r golden golden_pre_a1` BEFORE blessing anything.
"""

import os
import re
import subprocess
import sys
import difflib

SUBS = [
    (re.compile(r" latched=\d+"), ""),
    (re.compile(r" key=\d+"), ""),
    (re.compile(r" socv=\d+"), ""),
    (re.compile(r" socsince=\d+"), ""),
    (re.compile(r" mainc=\d+"), ""),
]

# FINAL carries both decision state and reception volume. Only the first kind
# is comparable across this change: once a section 7 abort stops dropping the
# mode to OFF the device keeps receiving, so other/ctr_ok/rx_gap_n all rise by
# however many frames the old code slept through. That is the change working,
# not a regression, and leaving those fields in drowns everything else.
FINAL_KEEP = ("mode=", "live=", "tx_ok=", "tx_fail=", "disabled=", "dcode=",
              "block=", "abort=")


def normalise(text):
    """Reduce a trace to its DECISION sequence.

    Dropped: TX lines (payloads and the diag round-robin, whose cadence moves
    with the frame count because a tick runs once per loop iteration and the
    injected key train adds iterations); the new KEY events; and the FINAL
    fields that only count frames.
    """
    out = []
    for ln in text.splitlines():
        if (" EV KEY" in ln or " EV SOC_VALID" in ln or " TX " in ln
                or ln.startswith("#")):
            continue
        for rx, rep in SUBS:
            ln = rx.sub(rep, ln)
        # A section 7 abort used to set mode = GI_OFF and now latches in
        # place. Erase mode only where an abort is being reported, so an
        # ordinary mode change is still visible.
        if "abort=" in ln and not ln.rstrip().endswith("abort="):
            ln = re.sub(r"mode=\d+", "mode=X", ln)
        if " FINAL " in ln:
            # The leading timestamp goes with them. The runner's OFF branch
            # steps 50 ms at a time and its armed branch up to 200 ms, so a
            # scenario that no longer drops to OFF on an abort overshoots
            # `end` by a different amount. That is the loop, not a decision.
            rest = ln.partition(" FINAL ")[2]
            # Fields are space-separated key=value, but `dcode=`, `block=` and
            # `abort=` hold text with spaces, so split on the keys instead.
            keep = []
            for k in FINAL_KEEP:
                m = re.search(re.escape(k) + r"(.*?)(?= \w+=|$)", rest)
                if m:
                    keep.append(k + m.group(1))
            ln = "FINAL " + " ".join(keep)
        # A STATE line that repeats the previous STATE line's contents at a
        # later time collapses into it. STATE is emitted on change, so a
        # repeat means only a field this script erases moved -- the key going
        # on, or going stale -- while the decision state stood still. The
        # timestamp is therefore not information here, and keeping it would
        # make every key transition read as a behaviour change.
        #
        # Deliberately restricted to STATE and to genuine adjacency: an EV
        # line between two identical STATEs means something happened, and two
        # identical EVs at different times are two real events.
        if " STATE " in ln:
            body = ln.partition(" STATE ")[2]
            if out and " STATE " in out[-1] \
                    and out[-1].partition(" STATE ")[2] == body:
                continue
        if out and out[-1] == ln:
            continue
        out.append(ln)
    return out


def main():
    here = sys.argv[1] if len(sys.argv) > 1 else "."
    old = os.path.join(here, sys.argv[2] if len(sys.argv) > 2
                       else "golden_pre71")
    runner = os.path.join(here, "host_runner")
    scn = os.path.join(here, "scenarios")

    names = sorted(n[:-6] for n in os.listdir(old) if n.endswith(".trace"))
    same = changed = missing = 0

    for name in names:
        spath = os.path.join(scn, name + ".scn")
        if not os.path.exists(spath):
            print("[gone  ] %-30s no scenario (regenerate it)" % name)
            missing += 1
            continue
        with open(spath) as fh:
            r = subprocess.run([runner], stdin=fh, capture_output=True,
                               text=True, cwd=here)
        out = r.stdout
        if name.startswith("replay-"):
            out = "".join(ln + "\n" for ln in out.splitlines()
                          if " TX " not in ln)
        with open(os.path.join(old, name + ".trace")) as fh:
            want = fh.read()

        a, b = normalise(want), normalise(out)
        if a == b:
            print("[same  ] %-30s behaviour unchanged" % name)
            same += 1
            continue

        changed += 1
        print("[CHANGE] %-30s" % name)
        for ln in difflib.unified_diff(a, b, "pre-7.1", "now", n=2,
                                       lineterm=""):
            print("    " + ln)

    print()
    print("%d unchanged, %d changed, %d without a scenario" %
          (same, changed, missing))


if __name__ == "__main__":
    main()
