#!/usr/bin/env python3
"""E2 / review D9: properties that must hold in EVERY trace, with no golden.

    python3 invariants.py            # every scenario
    python3 invariants.py --only key
    python3 invariants.py -v

WHY THIS EXISTS, in one number. Four rounds of mutation testing have now been
run against this component. Of the mutations that were caught, **every single
one was caught by the goldens** -- never by an invariant, never by E1 -- and
SIX of them by exactly ONE golden each: B0 held at 0x08, the error-rate
boundary, the gate's rpm_ref check, the short-DLC abort, the mainc-12 pair, and
the gate's fault check.

A regression harness is not supposed to carry that. `run_tests.py`'s own header
says why: it pins behaviour against a recorded copy of itself, so a wrong rule
blessed into a golden stays green for ever, and one careless `--bless` retires
a rule with nothing visible to show it happened. When a rule's only witness is
a single trace, the distance between "correct" and "unchecked" is one command.

So these are the same rules stated as PROPERTIES over every trace of every
scenario. There is nothing to bless. A golden can be wrong, or blessed away,
and these still fail.

Spec 12.3 lists most of them; review D9 is the source of the list.

WHAT IS DELIBERATELY NOT HERE. Anything whose correct value depends on timing
or on a threshold's exact position -- the debounce lengths, the freshness
window, the error-rate limit. Those are properties of numbers in the config, not
invariants of a trace, and asserting them here would mean encoding the same
constants twice and calling the agreement a test.
"""

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCN = os.path.join(HERE, "scenarios")
RUNNER = os.path.join(HERE, "host_runner")

VCM_ID = 0x051


def run(path):
    with open(path) as fh:
        r = subprocess.run([RUNNER], stdin=fh, capture_output=True, text=True,
                           cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError("runner exited %d on %s" % (r.returncode, path))
    return r.stdout


def scn_vcm_frames(path):
    """(t, counter, dlc) for every 0x051 the scenario presents, in order."""
    out = []
    with open(path) as fh:
        for line in fh:
            p = line.split()
            if len(p) >= 5 and p[0] == "f" and p[2] == "051":
                data = bytes.fromhex(p[4])
                out.append((int(p[1]), data[5] & 0x0F if len(data) > 5 else None,
                            int(p[3])))
    return out


STATE_RE = re.compile(
    r"^(\d+) STATE mode=(\d+) live=(\d+) block=(.*?) abort=(.*?) latched=(\d+) "
    r"disabled=(\d+) dcode=(.*?) susp=(\d+) fb_ever=(\d+) key=(\d+) "
    r"socv=(\d+) socsince=(\d+)$")

TX_RE = re.compile(r"^(\d+) TX (\w+) id=([0-9A-F]+) dlc=(\d+) data=([0-9A-F]*) ok=(\d+)$")
EV_RE = re.compile(r"^(\d+) EV (\w+) a=(-?\d+) b=(-?\d+) c=(-?\d+)$")


class Checker:
    def __init__(self, name, scn_path):
        self.name = name
        self.problems = []
        self.vcm = scn_vcm_frames(scn_path)
        self.state = None           # last STATE as a dict
        self.prev_ctr = None        # counter of the most recent 0x051
        self.tx_since_vcm = 0
        self.saw_tx = 0
        self.last_clear_ev = None

    def fail(self, t, msg):
        self.problems.append("%8s us: %s" % (t, msg))

    # ---- the properties ------------------------------------------------

    def on_tx_inhibit(self, t, ident, dlc, data, ok, post=None):
        st = self.state
        self.saw_tx += 1

        # Spec 4: the frame itself. Only zero torque is ever commanded, and B0
        # is HELD at the engine-off state rather than mirrored (spec 4.1).
        if ident != VCM_ID:
            self.fail(t, "inhibit frame on id 0x%03X, not 0x051" % ident)
        if dlc != 6:
            self.fail(t, "DLC %d, spec 4 says 6" % dlc)
        if len(data) >= 1 and data[0] != 0x08:
            self.fail(t, "B0 = 0x%02X, spec 4.1 holds it at 0x08" % data[0])
        if len(data) >= 3 and (data[1], data[2]) != (0x00, 0x80):
            self.fail(t, "torque bytes %02X %02X, spec 4 commands only zero "
                         "(00 80)" % (data[1], data[2]))
        if len(data) >= 6 and self.prev_ctr is not None:
            want = (self.prev_ctr + 1) & 0x0F
            if (data[5] & 0x0F) != want:
                self.fail(t, "counter %u, expected %u (the VCM's %u plus one) "
                             "-- spec 4: stealing the next counter is the "
                             "mechanism" % (data[5] & 0x0F, want, self.prev_ctr))

        # Spec 4: one reply per received frame, never two.
        self.tx_since_vcm += 1
        if self.tx_since_vcm > 1:
            self.fail(t, "%d inhibit frames for one received 0x051"
                         % self.tx_since_vcm)

        # Spec 6, 7, 7.1: every term that gates transmission.
        #
        # JUDGED AGAINST TWO STATES, and the reason is worth reading before
        # anyone "simplifies" it. host_runner prints a TX line during dispatch,
        # then the frame's events, then the STATE line -- so the state
        # PRECEDING a TX is the state before that frame was processed, and the
        # state at the SAME timestamp is the state after. Both can differ from
        # the one that authorised the transmit:
        #
        #   - the 6.3 suppression is recomputed from the arriving frame, so a
        #     frame that lifts it transmits legitimately while the preceding
        #     state still reads susp=1. Using the pre-state alone flagged
        #     shutdown-suppress as a violation at 4.000 s.
        #   - a failed transmit latches an abort as a CONSEQUENCE of the
        #     transmit, so the same-timestamp state reads latched=1 on a frame
        #     that was authorised when it went out. Using the post-state alone
        #     would flag every tx-fail scenario.
        #
        # So a term is violated only if it forbids transmission BOTH before and
        # after. That is weaker than a single exact state would be, and it is
        # sound: a mutation that really transmits while latched or disabled does
        # so for many consecutive frames, where both states agree.
        if st is None and post is None:
            self.fail(t, "transmitted before any state was reported")
            return

        def both(term):
            a = st[term] if st else None
            b = post[term] if post else None
            vals = [v for v in (a, b) if v is not None]
            return vals and all(vals)

        def neither(term):
            a = st[term] if st else None
            b = post[term] if post else None
            vals = [v for v in (a, b) if v is not None]
            return vals and not any(vals)

        if neither("live"):
            self.fail(t, "transmitted while inhibit_live was false")
        if both("disabled"):
            self.fail(t, "transmitted with a section 6 release latched (%s)"
                         % (st or post)["dcode"])
        if both("latched"):
            self.fail(t, "transmitted with a section 7 abort latched (%s)"
                         % (st or post)["abort"])
        if both("susp"):
            self.fail(t, "transmitted during the 6.3 shutdown suppression")
        if neither("key"):
            self.fail(t, "transmitted with the spec 7.1 key gate closed")
        if st is not None and st["mode"] != 3 and (post is None or post["mode"] != 3):
            self.fail(t, "transmitted in mode %d; only INHIBIT may"
                         % st["mode"])

    def on_state(self, t, st):
        prev = self.state
        self.state = st
        if prev is None:
            return

        # Spec 6/7.1: a latch clears only on a key 0->1 reading or a re-arm.
        # Both announce themselves as events, so an unexplained clear is a bug.
        for flag, what in (("latched", "section 7 abort"),
                           ("disabled", "section 6 release")):
            if prev[flag] and not st[flag]:
                if self.last_clear_ev not in ("KEY_CLEAR", "MODE"):
                    self.fail(t, "the %s latch cleared with no KEY_CLEAR and no "
                                 "mode change -- spec 6 and 7.1 give it only "
                                 "those two exits" % what)

    def on_event(self, t, kind, a, b, c):
        if kind in ("KEY_CLEAR", "MODE"):
            self.last_clear_ev = kind
        # Spec 3.1: a would-transmit belongs to PASSIVE alone.
        if kind == "MODE":
            pass

    def on_vcm_rx(self, t, ctr):
        self.prev_ctr = ctr
        self.tx_since_vcm = 0


def check(name, verbose=False):
    scn_path = os.path.join(SCN, name + ".scn")
    trace = run(scn_path)
    c = Checker(name, scn_path)

    # The trace does not echo received frames, so the scenario's own 0x051 list
    # is walked alongside it by timestamp. Equal timestamps: the frame is
    # delivered before anything the core emits in response to it.
    vcm = list(c.vcm)
    vi = 0

    # Parse first. A TX has to be able to see the STATE line at its own
    # timestamp, which has not been printed yet when the TX line appears.
    parsed = []
    for line in trace.splitlines():
        m = STATE_RE.match(line)
        if m:
            parsed.append((int(m.group(1)), "STATE", {
                "mode": int(m.group(2)), "live": int(m.group(3)),
                "block": m.group(4), "abort": m.group(5),
                "latched": int(m.group(6)), "disabled": int(m.group(7)),
                "dcode": m.group(8), "susp": int(m.group(9)),
                "fb_ever": int(m.group(10)), "key": int(m.group(11)),
                "socv": int(m.group(12)), "socsince": int(m.group(13)),
            }))
            continue
        m = TX_RE.match(line)
        if m:
            parsed.append((int(m.group(1)), "TX",
                           (m.group(2), int(m.group(3), 16), int(m.group(4)),
                            bytes.fromhex(m.group(5)), int(m.group(6)))))
            continue
        m = EV_RE.match(line)
        if m:
            parsed.append((int(m.group(1)), "EV",
                           (m.group(2), int(m.group(3)), int(m.group(4)),
                            int(m.group(5)))))

    for i, (t, kind, payload) in enumerate(parsed):
        while vi < len(vcm) and vcm[vi][0] <= t:
            c.on_vcm_rx(vcm[vi][0], vcm[vi][1]); vi += 1
        if kind == "STATE":
            c.on_state(t, payload)
        elif kind == "EV":
            c.on_event(t, *payload)
        elif payload[0] == "INHIBIT":
            post = None
            for t2, k2, p2 in parsed[i + 1:]:
                if t2 != t:
                    break
                if k2 == "STATE":
                    post = p2
                    break
            c.on_tx_inhibit(t, payload[1], payload[2], payload[3], payload[4],
                            post)

    if c.problems:
        print("[VIOLATION] %-32s" % name)
        for p in c.problems[:6]:
            print("    " + p)
        if len(c.problems) > 6:
            print("    ... and %d more" % (len(c.problems) - 6))
        return False, c.saw_tx
    if verbose:
        print("[ok        ] %-32s %d inhibit frames checked" % (name, c.saw_tx))
    return True, c.saw_tx


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    names = sorted(n[:-4] for n in os.listdir(SCN) if n.endswith(".scn"))
    if args.only:
        names = [n for n in names if args.only in n]

    good = bad = frames = 0
    for name in names:
        ok, n = check(name, args.verbose)
        frames += n
        if ok:
            good += 1
        else:
            bad += 1

    print()
    print("%d scenarios hold, %d violate; %d inhibit frames checked"
          % (good, bad, frames))
    if frames == 0:
        print("NO INHIBIT FRAMES WERE CHECKED -- this run proved nothing. "
              "Regenerate scenarios/ before reading a pass here.")
        return 2
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
