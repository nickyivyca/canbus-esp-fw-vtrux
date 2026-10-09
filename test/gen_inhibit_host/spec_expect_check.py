#!/usr/bin/env python3
"""Judge the trip 6 scenarios against the SPEC, not against their goldens.

Gen-inhibit tester, 2026-10-08. A golden records what the core did when it was
blessed; for a rule the core may not meet, blessing would pin the failure as
correct (README: "--bless is not a way to make a failing test pass"). These
scenarios are checked here instead, and have no goldens.

THE RULE, IN ONE PLACE: spec 7, trip 6, "How the window moves" (user,
2026-10-08), as modelled by due() below.
  - 10 or more error frames within 10 s of each other, INCLUSIVE at 10.0 s,
    the window sliding (checked on every new error frame);
  - error frames are recorded whenever the device is ARMED (mode not OFF),
    not only while live; only a KEY-ON (0x592 off -> on) clears the history;
    arming, re-arming and going live do not;
  - the trip is evaluated only while live, and a qualifying burst trips only
    while its NEWEST error frame is no more than 10 s before the check
    (inclusive), so a burst that fell before going live trips at the first
    live check if recent, and never if expired.
The spec sets no time bound on "the first live check"; TICK_SLACK_US is this
test's tolerance for it and for a latch trailing its error (reviewer,
2026-10-08: judge with the tick slack and record it in the evidence).

INPUTS. Error times, arming and the key come from the .scn (the stimulus);
live intervals and latches come from the trace (what the core did). Errors
are the cumulative count on "bus" directives, a rise of k giving k errors.

TWO ANSWERS MUST AGREE BEFORE ANYTHING IS JUDGED. Each row carries the
expectation worked out by hand, one entry per live interval: a time in
seconds, "live" (at go-live), or None (no trip 6). The model must produce the
same; if it does not, the row is an ERROR (the model or the hand is wrong),
never a pass.

VERDICTS, per live interval:
  PASS          trip 6 latched within [due, due + TICK_SLACK_US], or none
                was due and none latched;
  FAIL          latched with none due, before due, later than the slack, not
                at all, or while not live;
  INCONCLUSIVE  another trip ended the interval first, the interval count
                differs from the row's, or a row precondition did not hold
                (live before the first error; the gate holding on 0x617 while
                the errors land; the key reading off then on).

    make && python3 make_scenarios.py && python3 spec_expect_check.py
    python3 spec_expect_check.py --selftest     # the model, no runner
Exit status 0 unless a row FAILs, is INCONCLUSIVE or errors.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "host_runner")
SCN = os.path.join(HERE, "scenarios")
TRIP6 = "error-frame rate exceeded"

THRESHOLD = 10              # spec 7
WINDOW_US = 10 * 1000000    # spec 7: tightness AND recency, both inclusive
TICK_SLACK_US = 100000      # test tolerance, not spec (see above)
S = 1000000

LIVE_FIRST = "live-first"   # precondition: live before the first error
HOLD_617 = "hold-617"       # precondition: gate blocked on 0x617 at every error
KEY_CYCLE = "key-cycle"     # precondition: key read off then on, 9th..10th error

ROWS = [
    # name, hand expectation per live interval, precondition, why
    ("err-rate-trip", [3.0], LIVE_FIRST, "control: 12 errors in 1.2 s"),
    ("err-rate-under", [None], LIVE_FIRST, "control: 9 errors in 0.9 s"),
    ("err-rate-spread-9", [None], LIVE_FIRST, "9 errors at 1.1 s"),
    ("err-rate-spread-10", [14.0], LIVE_FIRST, "10 at 1/s from 5 s"),
    ("err-rate-spread-10-at-1p5", [10.5], LIVE_FIRST, "10 at 1/s from 1.5 s"),
    ("err-rate-spread-10-at-2p5", [11.5], LIVE_FIRST, "10 at 1/s from 2.5 s"),
    ("err-rate-spread-10-at-9p0", [18.0], LIVE_FIRST, "10 at 1/s from 9.0 s"),
    ("err-rate-spread-10-at-11p5", [20.5], LIVE_FIRST, "10 at 1/s from 11.5 s"),
    ("err-rate-edge-9p5", [14.5], LIVE_FIRST, "10 over 9.5 s"),
    ("err-rate-edge-10p0", [15.0], LIVE_FIRST, "10 over exactly 10.0 s: inclusive"),
    ("err-rate-edge-10p5", [None], LIVE_FIRST, "10 over 10.5 s"),
    ("err-rate-two-spans", [42.5], LIVE_FIRST, "9 early, 10 at 1/s from 33.5 s"),
    ("err-rate-sustained-4", [None], LIVE_FIRST, "4 in every 10 s for 60 s"),
    ("err-rate-jump-12", [5.0], LIVE_FIRST, "+12 in one reading at 5 s"),
    ("err-rate-rearm-carries", [None, 11.5], LIVE_FIRST,
     "9, disarm, re-arm, 1: the history carries across a re-arm"),
    ("err-rate-keyon-clears", [None], KEY_CYCLE, "9, key off/on, 1: a key-on clears"),
    ("err-rate-keyon-control", [11.5], LIVE_FIRST, "control: the same, key on throughout"),
    ("err-rate-armed-not-live", ["live"], HOLD_617,
     "10 while armed, gate held on 0x617, live 1.5 s after the newest"),
    ("err-rate-stale-burst", [None], HOLD_617,
     "10 while armed, gate held on 0x617, live 11.5 s after the newest"),
    ("err-rate-rearm-after-trip-expired", [3.0, None], LIVE_FIRST,
     "trip, re-arm 10.8 s after the newest: live, no re-trip"),
    ("err-rate-rearm-after-trip-recent", [3.0, "live"], LIVE_FIRST,
     "trip, re-arm 4.8 s after the newest: re-trip at go-live"),
]

BUS_RE = re.compile(r"^bus (\d+) (\d+) (\d+) (\d+) (\d+)\s*$")
MODE_RE = re.compile(r"^mode (\d+) (\d+)\b")
KEY_RE = re.compile(r"^f (\d+) 592 \d+ ([0-9A-Fa-f]{2})")
END_RE = re.compile(r"^end (\d+)")
STATE_RE = re.compile(r"^(\d+) STATE mode=(\d+) live=(\d) block=(.*?) abort=(.*?) "
                      r"latched=(\d) .*?\bkey=(\d)")


# ------------------------------------------------------------ the stimulus

class Stimulus:
    def __init__(self, text):
        self.errs, self.modes, self.keys, self.end = [], [], [], None
        last = 0
        for ln in text.splitlines():
            ln = ln.strip()
            m = BUS_RE.match(ln)
            if m:
                t, c = int(m.group(1)), int(m.group(5))
                if c > last:
                    self.errs += [t] * (c - last)
                last = c
                continue
            m = MODE_RE.match(ln)
            if m:
                self.modes.append((int(m.group(1)), int(m.group(2))))
                continue
            m = KEY_RE.match(ln)
            if m:
                self.keys.append((int(m.group(1)), bool(int(m.group(2), 16) & 0x10)))
                continue
            m = END_RE.match(ln)
            if m:
                self.end = int(m.group(1))
        self.errs.sort()

    def mode_at(self, t):
        m = 0
        for tm, v in self.modes:            # file order is time order
            if tm <= t:
                m = v
        return m

    def key_ons(self):
        """Times the key reads on after reading off or never having been seen."""
        out, prev = [], False
        for t, on in self.keys:
            if on and not prev:
                out.append(t)
            prev = on
        return out

    def next_disarm(self, t):
        for tm, v in self.modes:
            if tm > t and v == 0:
                return tm
        return self.end


# ---------------------------------------------------------------- the model

def history(st, t):
    """Error frames recorded by time t: armed when they landed, and after the
    last key-on at or before t."""
    ons = [k for k in st.key_ons() if k <= t]
    since = ons[-1] if ons else None
    return [e for e in st.errs
            if e <= t and st.mode_at(e) != 0 and (since is None or e >= since)]


def qualifies(st, t):
    """A burst tight enough (10 within WINDOW_US) whose newest error is
    recent enough (no more than WINDOW_US before t)."""
    h = history(st, t)
    for i in range(len(h) - THRESHOLD + 1):
        j = i + THRESHOLD - 1
        if h[j] - h[i] <= WINDOW_US and t - h[j] <= WINDOW_US:
            return True
    return False


def due(st, live_from, live_to):
    """Earliest live check in [live_from, live_to] at which trip 6 is due.

    The condition can only become true at go-live or at a new error frame
    (time passing only expires bursts), so those are the candidates.
    """
    cands = sorted({live_from} | {e for e in st.errs if live_from <= e <= live_to})
    for c in cands:
        if qualifies(st, c):
            return c
    return None


# ---------------------------------------------------------------- the trace

def states(trace):
    out = []
    for ln in trace.splitlines():
        m = STATE_RE.match(ln)
        if m:
            out.append(dict(t=int(m.group(1)), mode=int(m.group(2)), live=m.group(3) == "1",
                            block=m.group(4), abort=m.group(5),
                            latched=m.group(6) == "1", key=m.group(7) == "1"))
    return out


def live_intervals(sts, end):
    """[(start, end, how, abort)]; how is trip6 / other / disarm / gate / end."""
    out, start, prev = [], None, None
    for s in sts:
        if s["live"] and start is None:
            start = s["t"]
        elif not s["live"] and start is not None:
            if s["latched"] and not (prev and prev["latched"]):
                how = "trip6" if TRIP6 in s["abort"] else "other"
            elif s["mode"] == 0:
                how = "disarm"
            else:
                how = "gate"
            out.append((start, s["t"], how, s["abort"]))
            start = None
        prev = s
    if start is not None:
        out.append((start, end, "end", ""))
    return out


def trip6_while_not_live(sts):
    bad, prev = [], None
    for s in sts:
        if (s["latched"] and TRIP6 in s["abort"] and prev is not None
                and not prev["latched"] and not prev["live"]):
            bad.append(s["t"])
        prev = s
    return bad


def precondition(kind, st, sts, ivs):
    """(ok, why)."""
    if kind == LIVE_FIRST:
        if not st.errs or not ivs or ivs[0][0] >= st.errs[0]:
            return False, "not live before the first error (live at %s)" % (
                ivs[0][0] if ivs else None)
        return True, ""
    if kind == HOLD_617:
        for e in st.errs:
            before = [s for s in sts if s["t"] <= e]
            s = before[-1] if before else None
            if s is None or s["mode"] == 0 or s["live"] or "0x617" not in s["block"]:
                return False, "at error %d us the gate was not holding on 0x617 (%s)" % (
                    e, s and (s["mode"], s["live"], s["block"]))
        return True, ""
    if kind == KEY_CYCLE:
        lo, hi = st.errs[8], st.errs[9]
        offs = [s["t"] for s in sts if lo < s["t"] < hi and not s["key"]]
        ons = [s["t"] for s in sts if offs and offs[0] < s["t"] <= hi and s["key"]]
        if not offs or not ons:
            return False, "the trace never shows key=0 then key=1 between %d and %d us" % (lo, hi)
        ok, why = precondition(LIVE_FIRST, st, sts, ivs)
        return ok, why
    raise ValueError(kind)


def judge_interval(d, iv):
    """(verdict, detail) for one live interval with model due d."""
    start, end, how, abort = iv
    if how == "trip6":
        if d is None:
            return "FAIL", "trip 6 at %d us, none due" % end
        if end < d:
            return "FAIL", "trip 6 at %d us, BEFORE due %d" % (end, d)
        if end > d + TICK_SLACK_US:
            return "FAIL", "trip 6 at %d us, later than due %d + %d" % (end, d, TICK_SLACK_US)
        return "PASS", "trip 6 at %d us (due %d)" % (end, d)
    if d is not None and d <= end:
        if how == "other":
            return "INCONCLUSIVE", "another trip first: %r at %d us (trip 6 due %d)" % (
                abort, end, d)
        return "FAIL", "no trip 6; due at %d us, live until %d (%s)" % (d, end, how)
    if d is not None:
        return "INCONCLUSIVE", "live ended (%s) at %d us before trip 6 was due (%d)" % (
            how, end, d)
    note = (" (note: %r)" % abort) if how == "other" else ""
    return "PASS", "no trip 6, none due%s" % note


def hand_us(h, start):
    if h is None:
        return None
    if h == "live":
        return start
    return int(round(h * S))


def judge(row, text, trace):
    """(verdict, [detail lines])."""
    name, hand, pre, _why = row
    st = Stimulus(text)
    sts = states(trace)
    ivs = live_intervals(sts, st.end)
    lines = []
    bad_nl = trip6_while_not_live(sts)
    if bad_nl:
        return "FAIL", ["trip 6 latched while NOT live at %s us" % bad_nl]
    if len(ivs) != len(hand):
        return "INCONCLUSIVE", ["trace has %d live interval(s), the row expects %d: %s"
                                % (len(ivs), len(hand), [(a, b, h) for a, b, h, _ in ivs])]
    ok, why = precondition(pre, st, sts, ivs)
    if not ok:
        return "INCONCLUSIVE", [why]
    worst = "PASS"
    rank = {"PASS": 0, "INCONCLUSIVE": 1, "FAIL": 2, "ERROR": 3}
    for k, (iv, h) in enumerate(zip(ivs, hand)):
        d = due(st, iv[0], st.next_disarm(iv[0]))
        want = hand_us(h, iv[0])
        if d != want:
            v, det = "ERROR", "model says due %s, the hand says %s: one of them is wrong" % (d, want)
        else:
            v, det = judge_interval(d, iv)
        lines.append("live %d..%d us: %s %s" % (iv[0], iv[1], v, det))
        if rank[v] > rank[worst]:
            worst = v
    return worst, lines


# ---------------------------------------------------------------- selftest

def _scn(errs=(), modes=((0, 3),), keys=((0, True),), end=60 * S, step=None):
    L = ["mode %d %d 500" % m for m in modes]
    L += ["f %d 592 8 %02X00000000000000" % (t, 0x10 if on else 0) for t, on in keys]
    c = 0
    for t in errs:
        c += 1
        L.append("bus %d 1 1 1 %d" % (t, c))
    L.append("end %d" % end)
    return Stimulus("\n".join(L))


def selftest():
    ten = lambda t0, span: [t0 + k * span // 9 for k in range(10)]  # noqa: E731
    cases = [
        ("10 in 9 s, live throughout", _scn(ten(5 * S, 9 * S)), (S, 60 * S), 14 * S),
        ("10 in exactly 10.0 s trips (inclusive)", _scn(ten(5 * S, 10 * S)), (S, 60 * S), 15 * S),
        ("10 in 10.0 s + 1 us does not", _scn(ten(5 * S, 10 * S + 9)), (S, 60 * S), None),
        ("9 never trips", _scn(ten(5 * S, 9 * S)[:9]), (S, 60 * S), None),
        ("+12 at once", _scn([5 * S] * 12), (S, 60 * S), 5 * S),
        ("burst before live, newest 1.5 s old: at go-live",
         _scn(ten(2 * S, 4500000)), (8 * S, 60 * S), 8 * S),
        ("burst before live, newest exactly 10.0 s old: at go-live (inclusive)",
         _scn(ten(2 * S, 4500000)), (16500000, 60 * S), 16500000),
        ("burst before live, newest 10.0 s + 1 us old: never",
         _scn(ten(2 * S, 4500000)), (16500001, 60 * S), None),
        ("errors while disarmed are not recorded",
         _scn(ten(2 * S, 4 * S), modes=((0, 0), (8 * S, 3))), (8 * S, 60 * S), None),
        ("a key-on clears the history",
         _scn(ten(2 * S, 9 * S), keys=((0, True), (10200000, False), (10800000, True))),
         (S, 60 * S), None),
        ("a re-arm does not clear it",
         _scn([2 * S + k * S for k in range(9)] + [11500000],
              modes=((0, 3), (10500000, 0), (11 * S, 3))), (11 * S, 60 * S), 11500000),
        ("expired burst, then a fresh 10: due at the fresh tenth",
         _scn(ten(2 * S, 2 * S) + ten(30 * S, 9 * S)), (20 * S, 60 * S), 39 * S),
    ]
    bad = 0
    for label, st, (a, b), want in cases:
        got = due(st, a, b)
        ok = got == want
        bad += not ok
        print("  %-4s %-66s %s" % ("ok" if ok else "FAIL", label, got))
    # The judge itself: a latch outside the slack, or with none due, must fail.
    iv = lambda end, how: (S, end, how, "")  # noqa: E731
    jcases = [
        ("latch at due passes", judge_interval(5 * S, iv(5 * S, "trip6"))[0], "PASS"),
        ("latch before due fails", judge_interval(5 * S, iv(5 * S - 1, "trip6"))[0], "FAIL"),
        ("latch past the slack fails",
         judge_interval(5 * S, iv(5 * S + TICK_SLACK_US + 1, "trip6"))[0], "FAIL"),
        ("latch with none due fails", judge_interval(None, iv(5 * S, "trip6"))[0], "FAIL"),
        ("no latch when due fails", judge_interval(5 * S, iv(9 * S, "end"))[0], "FAIL"),
        ("another trip first is inconclusive",
         judge_interval(5 * S, iv(4 * S, "other"))[0], "INCONCLUSIVE"),
    ]
    for label, got, want in jcases:
        ok = got == want
        bad += not ok
        print("  %-4s %-66s %s" % ("ok" if ok else "FAIL", label, got))
    print("\n=== %s ===" % ("PASS" if not bad else "FAIL"))
    return 1 if bad else 0


# ------------------------------------------------------------- the check

def run(name):
    path = os.path.join(SCN, name + ".scn")
    with open(path, "rb") as fh:
        text = fh.read().decode("ascii", "replace")
    with open(path, "rb") as fh:
        r = subprocess.run([RUNNER], stdin=fh, capture_output=True, timeout=300)
    return r.returncode, r.stdout.decode("ascii", "replace"), text


def main():
    if "--selftest" in sys.argv:
        return selftest()
    print("trip 6 per spec 7 'How the window moves'; tolerance TICK_SLACK_US = %d us "
          "(test, not spec)\n" % TICK_SLACK_US)
    bad = 0
    for row in ROWS:
        name, hand, pre, why = row
        rc, out, text = run(name)
        if rc != 0:
            print("[ERROR       ] %-34s host_runner rc %d" % (name, rc))
            bad += 1
            continue
        v, lines = judge(row, text, out)
        bad += v != "PASS"
        print("[%-12s] %-34s %s" % (v, name, why))
        for ln in lines:
            print("               %s" % ln)
    print("\n%d of %d rows pass, %d fail, inconclusive or error"
          % (len(ROWS) - bad, len(ROWS), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
