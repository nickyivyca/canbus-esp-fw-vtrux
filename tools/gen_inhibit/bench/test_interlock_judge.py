#!/usr/bin/env python3
"""Harness test for interlock_judge.py and interlock_bench.py's stimulus side.

    py -3.14 tools/gen_inhibit/bench/test_interlock_judge.py <scratch dir>

Synthetic wire records only: a VCM train, the stimulus signals, a device that
answers each VCM frame with a copy of the next one while it is live, and 1 Hz
diag pages encoded with vtrux-wican-diag.dbc from a stated device state. Each
judge must call the right device AS SPECIFIED, call each wrong one NOT AS
SPECIFIED, and call a stimulus that did not do its job INCONCLUSIVE.

Part 2 checks the evidence-rule model against the host core: on the host
suite's own stale-signal scenarios, predict_trip must fire on the frame where
the golden trace aborts, with the golden's reason. It needs the tester repo;
without it the part prints SKIPPED and the run fails, so it cannot pass by not
running.
"""

import math
import os
import re
import sys
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

HERE = os.path.dirname(os.path.abspath(__file__))
# Scratch: the argument, or this run's own folder in the worktree's scratch/ (it was gen/ beside
# this file once; then a fixed folder in the worktree's scratch/).
SCRATCH = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else gi_paths.scratch("interlock_judge"))
sys.path.insert(0, HERE)
sys.path.insert(0, gi_paths.reverse_it_dir())
# interlock_bench writes the stimulus files it builds to <GI_RUNS_DIR>/interlock_stimulus,
# fixed at import. Unset, that is SeaDrive's runs/, and every run of this test rewrote
# the stimuli the bench runs recorded (harness-defects row, 2026-10-24).
os.environ["GI_RUNS_DIR"] = os.path.join(os.path.abspath(SCRATCH), "runs")

import interlock_judge as J      # noqa: E402
import interlock_bench as B      # noqa: E402

NFAIL = 0
NCHK = 0


def check(name, ok, detail=""):
    global NFAIL, NCHK
    NCHK += 1
    if ok:
        print("ok   %s" % name)
    else:
        NFAIL += 1
        print("FAIL %s %s" % (name, detail))


def rev(dbc, ident, sig):
    m = J.db(dbc).get_message_by_frame_id(ident)
    return {str(v): k for k, v in (m.get_signal_by_name(sig).choices or {}).items()}


ABORT = rev(J.DIAG, 0x7F8, "diag_abort_reason")
BLOCK = rev(J.DIAG, 0x7F8, "diag_arm_block")
DISABLE = rev(J.DIAG, 0x7F1, "diag_disable_reason")


def enc(dbc, ident, **sig):
    """A frame encoded with the DBC; a diag page states HEAD's schema on its schema
    signal, as the device does (gi_diag refuses schema 0, 2026-10-24)."""
    m = J.db(dbc).get_message_by_frame_id(ident)
    vals = {s.name: 0 for s in m.signals}
    if dbc == J.DIAG:
        vals.update({n: J.schemas().head_version for n in ("diag_schema_ver", "diag_schema_ver_build") if n in vals})
    vals.update(sig)
    return m.encode(vals, scaling=False, strict=False).hex()


class Sim:
    def __init__(self):
        self.f = []
        self.posts = []

    def add(self, t, i, h):
        self.f.append((round(t, 6), i, h))

    def train(self, i, t0, t1, period, hexfn):
        t = t0
        while t < t1 - 1e-9:
            self.add(t, i, hexfn(t))
            t += period

    def vcm(self, t0, t1, period=0.02, b0=0x08, ctr0=0, dlc=None, odd_at=None):
        t, k = t0, ctr0
        self.alias = getattr(self, "alias", {})
        while t < t1 - 1e-9:
            h = "%02x0080ff7f%02x" % (b0, k % 16)
            if odd_at is not None and abs(t - odd_at) < period / 2:
                o = h
                h = h[:10] if dlc < 6 else h + "00" * (dlc - 6)
                self.alias[h] = o
            self.add(t, 0x051, h)
            self.nvcm = getattr(self, "nvcm", 0) + 1
            t += period
            k += 1

    def device(self, live, offset=0.0005):
        """The device answers each VCM frame with the command the VCM sends next
        (for an injected frame, the command it replaced)."""
        al = getattr(self, "alias", {})
        v = sorted((t, h) for t, i, h in self.f if i == 0x051)
        for (t, h), (tn, hn) in zip(v, v[1:]):
            if live(t) and len(h) >= 12:
                self.add(t + offset, 0x051, al.get(hn, hn))

    def soc(self, t0, t1, raw):
        self.train(0x411, t0, t1, 0.05, lambda t: enc(J.PTX, 0x411, bms_soc_hires=raw))

    def mainc(self, t0, t1, stat):
        self.train(0x440, t0, t1, 0.05, lambda t: enc(J.PT, 0x440, bcm_mainc_stat=stat))

    def key(self, t0, t1, on):
        self.train(0x592, t0, t1, 0.1, lambda t: enc(J.PT, 0x592, IgnitionKeyState=1 if on else 0))

    def shift(self, t0, t1, pos):
        self.train(0x639, t0, t1, 0.05, lambda t: enc(J.PTX, 0x639, shift_lever_pos=pos))

    def other(self, i, t0, t1, period):
        self.train(i, t0, t1, period, lambda t: "00" * 8)

    def pages(self, t0, t1, state):
        t = t0
        while t < t1:
            s = state(t)
            fl = (1 if s.get("disabled") else 0) | (16 if s.get("susp") else 0) | \
                 (32 if s.get("live") else 0) | (128 if s.get("abort", "none") != "none" else 0)
            self.add(t, 0x7F1, enc(J.DIAG, 0x7F1, diag_mode=3, diag_flags=fl,
                                   diag_disable_reason=int(DISABLE[s.get("dis", "none")])))
            f2 = (1 if s.get("socv", True) else 0) | (8 if s.get("refused") else 0)
            self.add(t + 0.01, 0x7F8, enc(J.DIAG, 0x7F8, diag_abort_reason=int(ABORT[s.get("abort", "none")]),
                                          diag_flags2=f2, diag_arm_block=int(BLOCK[s.get("block", "none")])))
            self.add(t + 0.02, 0x7F2, enc(J.DIAG, 0x7F2, diag_tx_fail=s.get("tx_fail", 0)))
            t += 1.0

    def arm(self, t, mode=3, sent=None):
        self.posts.append({"mode": mode, "t_sent": t if sent is None else sent, "t_ret": t})


def run(kind, sim, case=None):
    return J.judge(kind, sim.f, sim.posts, [], case or {})


def verdict_is(name, res, want, why=None):
    got = res["verdict"]
    bad = [c["name"] for c in res["checks"] + res["preconditions"] if not c["ok"]]
    ok = got == want and (why is None or any(why in b for b in bad))
    check(name, ok, "got %s, unmet %s" % (got, bad))


# --------------------------------------------------------------- SoC latches

def soc_sim(latch_at=None, keep_live=False, dis="low_soc", flag_stuck=False, block="disabled",
            lows=60, rearm=None):
    s = Sim()
    s.arm(0.0)
    s.vcm(1.0, 12.0)
    s.mainc(1.0, 12.0, 11)
    s.key(1.0, 12.0, True)
    s.other(0x617, 1.0, 12.0, 0.25)
    s.shift(1.0, 12.0, 2)
    s.soc(1.0, 3.0, 5000)
    s.soc(3.0, 3.0 + 0.05 * lows, 1900)
    s.soc(3.0 + 0.05 * lows, 12.0, 2200)
    t5 = 3.0 + 0.05 * 4 if latch_at is None else latch_at
    if rearm is not None:
        s.arm(rearm)
    s.device(lambda t: t >= 1.05 and (keep_live or t < t5))
    s.pages(0.3, 12.0, lambda t: {"live": (t < t5) or flag_stuck, "disabled": t >= t5,
                                  "dis": dis if t >= t5 else "none",
                                  "block": block if t >= t5 else "none"})
    return s


def test_soc():
    verdict_is("soc: right device", run("soc_latch", soc_sim(), {"recover_to": 2200}), "AS SPECIFIED")
    verdict_is("soc: transmits after the latch", run("soc_latch", soc_sim(keep_live=True)),
               "NOT AS SPECIFIED", "no device 0x051 after the 5th")
    verdict_is("soc: released on the 4th", run("soc_latch", soc_sim(latch_at=3.0 + 0.05 * 3 + 0.001)),
               "NOT AS SPECIFIED", "not released before the 5th")
    verdict_is("soc: wrong reason", run("soc_latch", soc_sim(dis="m_mode")),
               "NOT AS SPECIFIED", "disable_reason reads low_soc")
    verdict_is("soc: live flag stuck", run("soc_latch", soc_sim(flag_stuck=True)),
               "NOT AS SPECIFIED", "live flag clear")
    verdict_is("soc: stale block", run("soc_latch", soc_sim(block="no_fresh_cmd")),
               "NOT AS SPECIFIED", "arm_block reports the disable")
    verdict_is("soc: only four lows", run("soc_latch", soc_sim(lows=4)), "INCONCLUSIVE",
               "valid sub-threshold readings")
    verdict_is("soc: recovery missing", run("soc_latch", soc_sim(lows=200), {"recover_to": 2200}),
               "INCONCLUSIVE", "readings back at 2200")
    # The marker: readings before any closed 0x440 do not count.
    s = soc_sim()
    s.f = [x for x in s.f if not (x[1] == 0x440 and x[0] < 6.0)]
    verdict_is("soc: no closed 0x440, no valid readings", run("soc_latch", s), "INCONCLUSIVE")
    # Contactors reading OPEN (7, precharge) until 6 s: the lows before then are
    # not valid, so the device latching at 3.2 s was live before no valid low.
    s = soc_sim()
    s.f = [x for x in s.f if not (x[1] == 0x440 and x[0] < 6.0)]
    s.mainc(1.0, 6.0, 7)
    verdict_is("soc: an open 0x440 does not set the marker", run("soc_latch", s), "INCONCLUSIVE")
    # A raw 0 never counts (spec 6.2), and a reading at or above 21.00 % restarts
    # the count: lows 1900,0,1900,0,... latch on the 5th non-zero one; four lows,
    # a healthy reading, then five more latch on the 5th of the second run.
    s = Sim()
    s.arm(0.0)
    s.vcm(1.0, 12.0)
    s.mainc(1.0, 12.0, 11)
    s.key(1.0, 12.0, True)
    s.other(0x617, 1.0, 12.0, 0.25)
    s.shift(1.0, 12.0, 2)
    s.soc(1.0, 3.0, 5000)
    for k in range(40):
        s.add(3.0 + 0.05 * k, 0x411, enc(J.PTX, 0x411, bms_soc_hires=(1900 if k % 2 == 0 else 0)))
    t5 = 3.0 + 0.05 * 8
    s.device(lambda t: 1.05 <= t < t5)
    s.pages(0.3, 12.0, lambda t: {"live": t < t5, "disabled": t >= t5, "dis": "low_soc" if t >= t5 else "none",
                                  "block": "disabled" if t >= t5 else "none"})
    verdict_is("soc: zeros between the lows do not count", run("soc_latch", s), "AS SPECIFIED")
    s = Sim()
    s.arm(0.0)
    s.vcm(1.0, 12.0)
    s.mainc(1.0, 12.0, 11)
    s.key(1.0, 12.0, True)
    s.other(0x617, 1.0, 12.0, 0.25)
    s.shift(1.0, 12.0, 2)
    s.soc(1.0, 3.0, 5000)
    s.soc(3.0, 3.2, 1900)
    s.soc(3.2, 3.25, 5000)
    s.soc(3.25, 12.0, 1900)
    t5 = 3.25 + 0.05 * 4
    s.device(lambda t: 1.05 <= t < t5)
    s.pages(0.3, 12.0, lambda t: {"live": t < t5, "disabled": t >= t5, "dis": "low_soc" if t >= t5 else "none",
                                  "block": "disabled" if t >= t5 else "none"})
    verdict_is("soc: a healthy reading restarts the count", run("soc_latch", s), "AS SPECIFIED")
    # Re-arm while latched must not light the flag.
    s = soc_sim(rearm=7.1)
    s.f = [x for x in s.f if not (x[1] == 0x7F1 and x[0] > 7.1)]
    s.pages(7.3, 12.0, lambda t: {"live": True, "disabled": True, "dis": "low_soc", "block": "disabled"})
    res = run("soc_latch", s)
    lit = (res["facts"].get("after_rearm") or {}).get("live_flag_set_at_s")
    check("soc: a re-arm that lights the flag is recorded, not judged (reviewer, 2026-10-15)",
          res["verdict"] == "AS SPECIFIED" and bool(lit), "%s %s" % (res["verdict"], lit))


def boundary_sim(hold_dis=False):
    s = Sim()
    s.arm(0.0)
    s.vcm(1.0, 10.0)
    s.mainc(1.0, 10.0, 11)
    s.key(1.0, 10.0, True)
    s.other(0x617, 1.0, 10.0, 0.25)
    s.shift(1.0, 10.0, 2)
    s.soc(1.0, 5.0, 2100)
    s.soc(5.0, 10.0, 2099)
    t5 = 5.0 + 0.05 * 4
    s.device(lambda t: t >= 1.05 and t < t5)
    s.pages(0.3, 10.0, lambda t: {"live": t < t5, "disabled": t >= t5 or (hold_dis and t > 3.0),
                                  "dis": "low_soc" if (t >= t5 or (hold_dis and t > 3.0)) else "none",
                                  "block": "disabled" if t >= t5 else "none"})
    return s


def test_boundary():
    verdict_is("boundary: right device", run("soc_latch", boundary_sim(), {"hold_above": 2100}), "AS SPECIFIED")
    verdict_is("boundary: 2100 released", run("soc_latch", boundary_sim(hold_dis=True), {"hold_above": 2100}),
               "NOT AS SPECIFIED", "2100 keeps inhibiting")


# ------------------------------------------------------------------ M mode

def mm_sim(late=False, rearm_live=False, flag_stuck=False, block="disabled", page0=0.3, catch=3.0, live_until=None):
    # page0: the first page's time (pages every 1.0 s). catch: when the pages
    # catch up with the 3.0 s latch. live_until: the live flag clears then
    # (default: at catch).
    live_end = catch if live_until is None else live_until
    s = Sim()
    s.arm(0.0)
    s.vcm(1.0, 9.0)
    s.mainc(1.0, 9.0, 11)
    s.soc(1.0, 9.0, 5000)
    s.key(1.0, 9.0, True)
    s.other(0x617, 1.0, 9.0, 0.25)
    s.shift(1.0, 3.0, 2)
    s.shift(3.0, 9.0, 4)
    s.arm(5.0, 0)
    s.arm(6.0)
    s.device(lambda t: t >= 1.05 and (t < 3.0 or (late and t < 3.1) or (rearm_live and t > 6.2)))
    # While disarmed (5-6 s) the block reads not_armed, which is right and must
    # not be judged as a stale gate reason.
    s.pages(page0, 9.0, lambda t: {"live": t < live_end or (flag_stuck and t < 5.0), "disabled": t >= catch,
                                   "dis": "m_mode" if t >= catch else "none",
                                   "block": "none" if t < catch else ("not_armed" if 5.0 <= t < 6.0 else block)})
    return s


def test_mmode():
    verdict_is("mmode: right device", run("mmode_latch", mm_sim()), "AS SPECIFIED")
    verdict_is("mmode: answers after position 4", run("mmode_latch", mm_sim(late=True)),
               "NOT AS SPECIFIED", "after the first position 4")
    verdict_is("mmode: transmits after the re-arm", run("mmode_latch", mm_sim(rearm_live=True)),
               "NOT AS SPECIFIED", "after the first position 4")
    verdict_is("mmode: live flag set at the latch", run("mmode_latch", mm_sim(flag_stuck=True)),
               "NOT AS SPECIFIED", "live flag clear after the latch")
    verdict_is("mmode: stale gate reason at the latch", run("mmode_latch", mm_sim(block="no_fresh_shift")),
               "NOT AS SPECIFIED", "arm_block reports the disable")
    # Page phase (reviewer, 2026-10-20; e3d9437 B20 first run): latch 3.0, POST 5.0,
    # pages at 3.08 and 4.08, the next after the POST. Nothing in [latch + 1.1, POST).
    verdict_is("mmode: phase: the only page before the POST is in the lag and has caught up",
               run("mmode_latch", mm_sim(page0=0.08)), "AS SPECIFIED")
    verdict_is("mmode: phase: no page in the lag has caught up, none after: precondition",
               run("mmode_latch", mm_sim(page0=0.08, catch=4.5)), "INCONCLUSIVE",
               "0x7F1 pages between the latch and any mode change")
    verdict_is("mmode: phase: a page in the lag not yet caught up is not judged",
               run("mmode_latch", mm_sim(page0=0.08, catch=3.5)), "AS SPECIFIED")
    verdict_is("mmode: phase: the caught-up page in the lag is judged (live flag)",
               run("mmode_latch", mm_sim(page0=0.08, live_until=4.5)), "NOT AS SPECIFIED",
               "live flag clear after the latch")
    verdict_is("mmode: phase: a page after the lag that has not caught up is a check FAIL",
               run("mmode_latch", mm_sim(catch=4.5)), "NOT AS SPECIFIED",
               "disable_reason reads m_mode after the latch")
    verdict_is("mmode: phase: a stale block in the lag, none after: precondition, not a pass",
               run("mmode_latch", mm_sim(page0=0.08, block="no_fresh_shift")), "INCONCLUSIVE",
               "0x7F8 pages between the latch and any mode change")


# ------------------------------------------------------------------ 6.2 wake

def wake_sim(live_early=False, release=False, no_low=False, slow_post=False):
    s = Sim()
    if slow_post:
        s.arm(3.02, sent=2.9)       # in flight across the close at 3.0 (see rekey_sim)
    else:
        s.arm(0.0)
    s.vcm(1.0, 8.0)
    s.key(1.0, 8.0, True)
    s.other(0x617, 1.0, 8.0, 0.25)
    s.shift(1.0, 8.0, 2)
    s.mainc(1.0, 3.0, 0)
    s.soc(1.0, 2.0, 2441 if no_low else 1877)
    s.soc(2.0, 8.0, 2441)
    s.mainc(3.0, 8.0, 11)
    s.device(lambda t: t >= (1.5 if live_early else 3.06))
    s.pages(0.3, 8.0, lambda t: {"live": t > 3.06, "disabled": release and t > 2.5,
                                 "dis": "low_soc" if (release and t > 2.5) else "none",
                                 "socv": t > 3.0})
    return s


def test_wake():
    verdict_is("wake: right device", run("wake", wake_sim()), "AS SPECIFIED")
    verdict_is("wake: a close while the arm POST is in flight still counts",
               run("wake", wake_sim(slow_post=True)), "AS SPECIFIED")
    verdict_is("wake: live before the close", run("wake", wake_sim(live_early=True)),
               "NOT AS SPECIFIED", "never live before the close")
    verdict_is("wake: released", run("wake", wake_sim(release=True)), "NOT AS SPECIFIED", "no low_soc release")
    verdict_is("wake: no wake reading", run("wake", wake_sim(no_low=True)), "INCONCLUSIVE")


def nc_sim(live=False, block="no_soc_valid"):
    s = Sim()
    s.arm(0.0)
    s.vcm(1.0, 6.0)
    s.key(1.0, 6.0, True)
    s.other(0x617, 1.0, 6.0, 0.25)
    s.shift(1.0, 6.0, 2)
    s.mainc(1.0, 6.0, 7)
    s.soc(1.0, 6.0, 5000)
    if live:
        s.device(lambda t: t > 4.0)
    s.pages(0.3, 6.0, lambda t: {"block": block, "socv": False})
    return s


def test_never_closed():
    verdict_is("never closed: right device", run("never_closed", nc_sim()), "AS SPECIFIED")
    verdict_is("never closed: went live", run("never_closed", nc_sim(live=True)), "NOT AS SPECIFIED", "never live")
    verdict_is("never closed: wrong block", run("never_closed", nc_sim(block="contactors_open")),
               "NOT AS SPECIFIED", "arm_block names the SoC marker")


# --------------------------------------------------------------- gate, 0x440

def gate_sim(control=False, live_after=None, block="contactors_open", socv=True):
    if live_after is None:
        live_after = control
    s = Sim()
    s.arm(0.0)
    s.vcm(1.0, 9.0)
    s.key(1.0, 9.0, True)
    s.other(0x617, 1.0, 9.0, 0.25)
    s.shift(1.0, 9.0, 2)
    s.soc(1.0, 9.0, 5000)
    s.mainc(1.0, 3.0, 11)
    s.mainc(3.0, 9.0, 11 if control else 14)
    s.arm(5.0, 0)
    s.arm(5.1)
    s.device(lambda t: (1.05 <= t < 5.0) or (live_after and t > 5.15))
    s.pages(0.3, 9.0, lambda t: {"live": t < 5.0 or live_after, "socv": socv or t < 5.0,
                                 "block": "none" if (t < 5.0 or live_after) else block})
    return s


def test_gate():
    verdict_is("gate: right device", run("gate_open", gate_sim()), "AS SPECIFIED")
    verdict_is("gate: live after the re-arm", run("gate_open", gate_sim(live_after=True)),
               "NOT AS SPECIFIED", "not live after the re-arm")
    verdict_is("gate: wrong block", run("gate_open", gate_sim(block="no_soc_valid")),
               "NOT AS SPECIFIED", "names condition 2")
    verdict_is("gate: marker clear", run("gate_open", gate_sim(socv=False)), "INCONCLUSIVE", "marker set")
    verdict_is("gate control: right device", run("gate_open", gate_sim(control=True), {"control": True}),
               "AS SPECIFIED")
    verdict_is("gate control: stays off", run("gate_open", gate_sim(control=True, live_after=False),
                                              {"control": True}), "NOT AS SPECIFIED", "control")
    verdict_is("gate control on an open stimulus", run("gate_open", gate_sim(), {"control": True}),
               "INCONCLUSIVE", "reads closed")


# ------------------------------------------------------------ 0x10, twice

def sup_sim(leak=None, no_resume=False, latch=False, flag_off=False):
    s = Sim()
    s.arm(0.0)
    s.vcm(1.0, 2.0)
    s.vcm(2.0, 4.0, b0=0x10, ctr0=5)
    s.vcm(4.0, 6.0, ctr0=3)
    s.vcm(6.0, 8.0, b0=0x10, ctr0=9)
    s.vcm(8.0, 10.0, ctr0=7)
    s.mainc(1.0, 10.0, 11)
    s.soc(1.0, 10.0, 5000)
    s.key(1.0, 10.0, True)
    s.other(0x617, 1.0, 10.0, 0.25)
    s.shift(1.0, 10.0, 2)
    eps = ((2.0, 4.0), (6.0, 8.0))
    s.device(lambda t: t >= 1.05 and not any(a - 0.001 <= t < b for a, b in eps)
             and not (no_resume and t > 6.0))
    if leak:
        s.add(leak, 0x051, "080080ff7f0f")
    s.pages(0.3, 10.0, lambda t: {"live": True, "susp": (not flag_off) and any(a <= t < b for a, b in eps),
                                  "disabled": latch and t > 4.5, "dis": "low_soc" if latch and t > 4.5 else "none"})
    return s


def test_suppress():
    verdict_is("0x10: right device", run("suppress_twice", sup_sim()), "AS SPECIFIED")
    verdict_is("0x10: frame inside episode 2", run("suppress_twice", sup_sim(leak=7.0105)),
               "NOT AS SPECIFIED", "episode 2: no device")
    verdict_is("0x10: no resume after episode 2", run("suppress_twice", sup_sim(no_resume=True)),
               "NOT AS SPECIFIED", "episode 2: transmission resumes")
    verdict_is("0x10: latched", run("suppress_twice", sup_sim(latch=True)), "NOT AS SPECIFIED", "nothing latched")
    verdict_is("0x10: flag not set", run("suppress_twice", sup_sim(flag_off=True)),
               "NOT AS SPECIFIED", "shutdown_suppressed set")


# ------------------------------------------------------------------- trips

def trip_sim(cut, expect_name, stop_at=None, reason=None, leak_cut=False, others_stop=False):
    """Live from 1 s; `cut` ids stop at 6.0 s; the device stops at stop_at."""
    s = Sim()
    s.arm(1.0)
    s.vcm(0.505, 12.0, period=0.01)    # off the other trains' grid: no expiry on a frame
    s.key(0.5, 12.0, True)
    sigs = {0x411: 0.05, 0x440: 0.05, 0x617: 0.25, 0x639: 0.01, 0x054: 0.01, 0x471: 0.01}
    for i, p in sigs.items():
        end = 6.0 if (i in cut and not leak_cut) else (6.5 if (others_stop and i not in cut) else 12.0)
        if i == 0x440:
            s.mainc(0.5, end, 11)
        elif i == 0x411:
            s.soc(0.5, end, 7700)
        elif i == 0x639:
            s.shift(0.5, end, 2)
        else:
            s.other(i, 0.5, end, p)
    last = max(t for t, i, _ in s.f if i in cut and t < 6.0)
    w = max(J.WINDOW_S[i] for i in cut)
    exp = last + w
    t_stop = exp if stop_at is None else stop_at
    s.device(lambda t: 1.05 <= t < t_stop)
    nm = reason or expect_name
    s.pages(0.3, 12.0, lambda t: {"live": t < t_stop, "abort": nm if t > t_stop + 0.005 else "none"})
    case = {"cut": cut, "expect": expect_name, "cut_at_epoch": 6.0}
    return s, case, exp


def test_trips():
    for cut, name in (([0x411], "stale_soc"), ([0x440], "stale_contactor"), ([0x617], "stale_fault"),
                      ([0x639], "stale_shift"), ([0x054], "stale_rpm"), ([0x054, 0x471], "inverter_lost")):
        s, case, exp = trip_sim(cut, name)
        verdict_is("trip %s: right device" % name, run("trip", s, case), "AS SPECIFIED")
    s, case, exp = trip_sim([0x411], "stale_soc", stop_at=None)
    s2, case2, exp2 = trip_sim([0x411], "stale_soc", stop_at=exp + 0.035)
    verdict_is("trip: late by three frames", run("trip", s2, case2), "NOT AS SPECIFIED", "not late")
    # Spec 7, user 2026-10-16: the evidence 0x051 (the first after the expiry)
    # may be answered; the next must not be. VCM frames sit on 0.505 + k * 0.01.
    ev = 0.505 + 0.01 * math.ceil((exp + J.EDGE_S - 0.505) / 0.01 + 1e-6)
    s9, case9, _ = trip_sim([0x411], "stale_soc", stop_at=ev + 0.005)
    r9 = run("trip", s9, case9)
    verdict_is("trip: the evidence 0x051 answered, nothing after (ruled allowed)", r9, "AS SPECIFIED")
    check("trip: that answer is recorded as evidence_answered 1",
          r9["facts"].get("evidence_answered") == 1, r9["facts"].get("evidence_answered"))
    s10, case10, _ = trip_sim([0x411], "stale_soc", stop_at=ev + 0.015)
    verdict_is("trip: the 0x051 after the evidence answered too", run("trip", s10, case10),
               "NOT AS SPECIFIED", "not late")
    s3, case3, exp3 = trip_sim([0x411], "stale_soc", stop_at=exp - 0.2)
    verdict_is("trip: early", run("trip", s3, case3), "NOT AS SPECIFIED", "not early")
    s4, case4, _ = trip_sim([0x411], "stale_soc", reason="bus_lost")
    verdict_is("trip: wrong reason", run("trip", s4, case4), "NOT AS SPECIFIED", "names stale_soc")
    s5, case5, _ = trip_sim([0x411], "stale_soc", leak_cut=True)
    s5.f = [x for x in s5.f if not (x[1] == 0x411 and 6.0 <= x[0] < 6.3)]
    verdict_is("trip: cut not proved", run("trip", s5, case5), "INCONCLUSIVE", "cut 0x411 proved")
    s6, case6, _ = trip_sim([0x054], "stale_rpm")
    s6.f = [x for x in s6.f if not (x[1] == 0x471 and x[0] >= 6.0)]
    verdict_is("trip: GENE pair read as 0x054 alone", run("trip", s6, case6), "INCONCLUSIVE", "model predicts")
    # 0x054 heard only before this arm cycle is not tracked (spec 7 trip 5, once heard).
    s8, case8, _ = trip_sim([0x411], "stale_soc")
    s8.f = [x for x in s8.f if not (x[1] == 0x054 and x[0] >= 0.9)]
    s8.posts = [{"mode": 3, "t_ret": 1.0}]
    verdict_is("trip: 0x054 heard only before the arm is not tracked", run("trip", s8, case8), "AS SPECIFIED")
    s7, case7, _ = trip_sim([0x411], "stale_soc", others_stop=True)
    verdict_is("trip: other signals stopped too", run("trip", s7, case7), "INCONCLUSIVE", "every other")


class GapCtx:
    """The three things diag_gaps reads: by_id, posts, times()."""
    def __init__(self, diag, posts, end=19.5):
        self.by_id = {0x051: [(end, "00")]}
        for i, ts in diag.items():
            self.by_id[i] = [(t, "00") for t in ts]
        self.posts = [{"mode": m, "t_ret": t} for t, m in posts]

    def times(self, i):
        return [t for t, _ in self.by_id.get(i, [])]


def test_diag_cadence():
    """Spec 10's cadence while armed (reviewer, 2026-10-16): bound DIAG_GAP_MAX_S."""
    every = [0.5 + 1.2 * k for k in range(16)]
    g, _ = J.diag_gaps(GapCtx({0x7F1: every}, [(0.4, 3)]))
    check("cadence: pages every 1.2 s while armed, largest gap 1.2 s", abs(g["0x7F1"] - 1.2) < 0.01, g)
    g, _ = J.diag_gaps(GapCtx({0x7F1: [t for t in every if not 5.0 < t < 6.0]}, [(0.4, 3)]))
    check("cadence: one page missing gives 2.4 s, under the bound",
          abs(g["0x7F1"] - 2.4) < 0.01 and g["0x7F1"] <= J.DIAG_GAP_MAX_S, g)
    g, _ = J.diag_gaps(GapCtx({0x7F1: [t for t in every if not 5.0 < t < 8.0]}, [(0.4, 3)]))
    check("cadence: three pages missing gives 4.8 s, over the bound", abs(g["0x7F1"] - 4.8) < 0.01, g)
    g, _ = J.diag_gaps(GapCtx({0x7F1: [t for t in every if t < 5.0]}, [(0.4, 3), (9.0, 0)]))
    check("cadence: pages that stop and never resume count to the mode 0 POST (4.9 s)",
          abs(g["0x7F1"] - 4.9) < 0.01, g)
    g, _ = J.diag_gaps(GapCtx({0x7F1: [t for t in every if not 5.0 < t < 9.0]},
                              [(0.4, 3), (5.0, 0), (9.0, 3)]))
    check("cadence: silence while in mode 0 is not judged", g["0x7F1"] <= 1.25, g)
    g, _ = J.diag_gaps(GapCtx({0x7F1: [t for t in every if not 5.0 < t < 9.0]},
                              [(0.4, 3), (7.0, 3), (12.0, 4)]))
    check("cadence: a silence across a 3->3 re-arm is one gap (6.0 s), not two (2.9 s and 3.1 s)", abs(g["0x7F1"] - 6.0) < 0.01, g)
    g, _ = J.diag_gaps(GapCtx({0x7F1: every, 0x7F8: [t for t in every if not 5.0 < t < 8.0]}, [(0.4, 3)]))
    check("cadence: judged per id", g["0x7F1"] < 1.3 and g["0x7F8"] > J.DIAG_GAP_MAX_S, g)
    s, case, _ = trip_sim([0x411], "stale_soc")
    r = run("trip", s, case)
    check("cadence: the trip sim's 1 s pages pass and are recorded",
          not [c for c in r["checks"] if "cadence" in c["name"] and not c["ok"]]
          and r["facts"].get("diag_gap_max_s", {}).get("0x7F1", 9) <= 1.05, r["facts"].get("diag_gap_max_s"))
    s.f = [x for x in s.f if not (x[1] in J.DIAG_IDS and 6.5 <= x[0] < 9.5)]
    verdict_is("cadence: 3 s with no page while armed fails", run("trip", s, case),
               "NOT AS SPECIFIED", "cadence")
    s2, case2, _ = trip_sim([0x411], "stale_soc")
    s2.f = [x for x in s2.f if x[1] not in J.DIAG_IDS]
    verdict_is("cadence: no diag page at all while armed fails", run("trip", s2, case2),
               "NOT AS SPECIFIED", "cadence")


def base_live(t_end=12.0, rpm=None, rpm_from=6.0, fault_from=None, gap=None, torque=None, no051=False):
    """The slice-like bus every trip case starts from: 100 Hz 0x051 off the other
    trains' grid, armed at 1.0 s."""
    s = Sim()
    s.arm(1.0)
    if not no051:
        s.vcm(0.505, t_end, period=0.01)
    if torque is not None:
        s.f = [(t, i, enc(J.PTX, 0x051, gen_torque_cmd=32768 + torque, gen_rpm_ref=32767)[:10] + h[10:]
                if i == 0x051 else h) for t, i, h in s.f]
    s.key(0.5, t_end, True)
    s.mainc(0.5, t_end, 11)
    s.soc(0.5, t_end, 7700)
    s.shift(0.5, t_end, 2)
    s.train(0x617, 0.5, t_end, 0.25, lambda t: "00" * 7 + ("ca" if fault_from is not None and t >= fault_from else "c8"))
    s.train(0x054, 0.5, t_end, 0.01, lambda t: enc(J.PT, 0x054, GENE_RotSpd=32767 + (
        rpm if rpm is not None and t >= rpm_from else 0)))
    s.other(0x471, 0.5, t_end, 0.01)
    if gap:
        s.f = [x for x in s.f if not (gap[0] <= x[0] < gap[1])]
    return s


def event_case(kind):
    if kind == "vcm_fault":
        return {"event": "vcm_fault", "expect": "vcm_fault", "cut_at_epoch": 6.0}
    return {"event": "engine", "expect": "engine_turning", "cut_at_epoch": 6.0}


def test_event_trips():
    for kind, mk, t_trip in (("vcm_fault", lambda: base_live(fault_from=6.0), None),
                             ("engine", lambda: base_live(rpm=800), None)):
        def sim(stop_delta=0.0, reason=None):
            s = mk()
            if kind == "vcm_fault":
                t0 = min(t for t, i, h in s.f if i == 0x617 and h.endswith("ca"))
            else:
                t0 = min(t for t, i, h in s.f if i == 0x054 and J.decode(J.PT, 0x054, h)["GENE_RotSpd"] >= 33067) + 0.3
            ts = t0 + stop_delta
            s.device(lambda t: 1.05 <= t < ts)
            nm = reason or event_case(kind)["expect"]
            s.pages(0.3, 12.0, lambda t: {"live": t < ts, "abort": nm if t > ts + 0.005 else "none"})
            return s
        verdict_is("%s: right device" % kind, run("event_trip", sim(), event_case(kind)), "AS SPECIFIED")
        verdict_is("%s: late" % kind, run("event_trip", sim(0.05), event_case(kind)), "NOT AS SPECIFIED", "not late")
        verdict_is("%s: early" % kind, run("event_trip", sim(-0.3), event_case(kind)), "NOT AS SPECIFIED", "not early")
        verdict_is("%s: wrong reason" % kind, run("event_trip", sim(reason="bus_lost"), event_case(kind)),
                   "NOT AS SPECIFIED", "names")
    s = base_live()
    s.device(lambda t: t >= 1.05)
    s.pages(0.3, 12.0, lambda t: {"live": True})
    verdict_is("vcm_fault: no 0xCA on the wire", run("event_trip", s, event_case("vcm_fault")), "INCONCLUSIVE")


def test_bus_stop():
    def sim(resume_tx=False, reason="bus_lost"):
        s = base_live(gap=(6.0, 8.0))
        s.device(lambda t: 1.05 <= t < 6.0 or (resume_tx and t >= 8.0))
        s.pages(0.3, 12.0, lambda t: {"live": t < 6.0, "abort": reason if t > 6.6 else "none"})
        return s
    case = {"cut_at_epoch": 6.0, "resume_at_epoch": 8.0}
    verdict_is("bus stop: right device", run("bus_stop", sim(), case), "AS SPECIFIED")
    verdict_is("bus stop: resumes when the bus returns", run("bus_stop", sim(resume_tx=True), case),
               "NOT AS SPECIFIED", "no device 0x051 after the stop")
    verdict_is("bus stop: wrong reason", run("bus_stop", sim(reason="stale_soc"), case), "NOT AS SPECIFIED", "names bus_lost")
    s = base_live(gap=(6.0, 6.3))
    s.device(lambda t: 1.05 <= t < 6.0)
    s.pages(0.3, 12.0, lambda t: {"abort": "bus_lost" if t > 6.6 else "none"})
    verdict_is("bus stop: the bus never stopped as declared", run("bus_stop", s, case), "INCONCLUSIVE")


def test_gate_refuse():
    for blk, mk in (("gen_running", lambda: base_live(rpm=800, rpm_from=0.0)),
                    ("vcm_torque", lambda: base_live(torque=-50)),
                    ("no_fresh_cmd", lambda: base_live(no051=True))):
        def sim(live=False, shown=None):
            s = mk()
            if live:
                s.device(lambda t: t > 2.0)
            s.pages(0.3, 12.0, lambda t: {"live": live, "block": shown or blk})
            return s
        verdict_is("gate %s: right device" % blk, run("gate_refuse", sim(), {"block": blk}), "AS SPECIFIED")
        if blk != "no_fresh_cmd":
            verdict_is("gate %s: went live" % blk, run("gate_refuse", sim(live=True), {"block": blk}),
                       "NOT AS SPECIFIED", "never live")
        verdict_is("gate %s: wrong block" % blk, run("gate_refuse", sim(shown="contactors_open"), {"block": blk}),
                   "NOT AS SPECIFIED", "arm_block names")
    s = base_live()
    s.pages(0.3, 12.0, lambda t: {"block": "gen_running"})
    verdict_is("gate: the stimulus does not fail the condition", run("gate_refuse", s, {"block": "gen_running"}),
               "INCONCLUSIVE")


def short_case(dlc, sim):
    return {"dlc": dlc, "alias": dict(sim.alias)}


def short_sim(dlc, answer=False, tx_fail_after=0, abort="short_vcm_frame", stop=None):
    s = Sim()
    s.arm(1.0)
    s.vcm(0.5, 12.0, period=0.01, dlc=dlc, odd_at=6.0)
    s.key(0.5, 12.0, True)
    s.mainc(0.5, 12.0, 11)
    s.soc(0.5, 12.0, 7700)
    s.other(0x617, 0.5, 12.0, 0.25)
    s.shift(0.5, 12.0, 2)
    t_stop = 6.0 if dlc < 6 else 99
    if stop is not None:
        t_stop = stop
    s.device(lambda t: 1.05 <= t < t_stop)
    if answer:
        nxt = min((t, h) for t, i, h in s.f if i == 0x051 and t > 6.001)
        s.add(6.0005, 0x051, nxt[1])
    s.pages(0.3, 12.0, lambda t: {"live": t < t_stop, "abort": abort if t > t_stop + 0.01 else "none",
                                  "tx_fail": tx_fail_after if t > 6.0 else 0})
    return s


def test_short():
    def go(name, sim, dlc, want, why=None):
        verdict_is(name, run("short_frame", sim, short_case(dlc, sim)), want, why)
    go("trip 8: right device", short_sim(5), 5, "AS SPECIFIED")
    go("trip 8: answered", short_sim(5, answer=True), 5, "NOT AS SPECIFIED", "not answered")
    go("trip 8: counted as tx_fail", short_sim(5, tx_fail_after=1), 5, "NOT AS SPECIFIED", "tx_fail not incremented")
    go("trip 8: wrong reason", short_sim(5, abort="tx_failed"), 5, "NOT AS SPECIFIED", "names short_vcm_frame")
    go("trip 8 control: right device", short_sim(8), 8, "AS SPECIFIED")
    go("trip 8 control: tripped", short_sim(8, stop=6.0, abort="short_vcm_frame"), 8, "NOT AS SPECIFIED", "control")
    s = short_sim(8)
    s.f = [x for x in s.f if not (x[1] == 0x051 and 6.0 < x[0] < 6.002)]
    go("trip 8 control: the DLC 8 frame not answered", s, 8, "NOT AS SPECIFIED", "is answered")
    verdict_is("trip 8: wrong DLC on the wire", run("short_frame", short_sim(8), {"dlc": 5}), "INCONCLUSIVE")
    # Attribution around an injected frame: with the alias every VCM frame is
    # booked as the VCM's; without it the device's answer before it is not.
    for dlc in (5, 8):
        s = short_sim(dlc)
        nv = s.nvcm
        c = J.Ctx(s.f, alias=s.alias)
        check("attribution: DLC %d with the alias books %d VCM frames" % (dlc, nv), len(c.vcm) == nv,
              "%d" % len(c.vcm))
        check("attribution: DLC %d without the alias misbooks one" % dlc, len(J.Ctx(s.f).vcm) == nv + 1,
              "%d" % len(J.Ctx(s.f).vcm))


# --------------------------------------------------------- rekey (model + key)

def rekey_sim(tx_after_off=False, abort_before_off=False, reason="inverter_lost", soc_low=False,
              slow_post=False):
    s = Sim()
    if slow_post:
        # The build trial's run 1: the arm POST is sent before the first closed
        # 0x440 (1.0) and returns after it; the device goes live after the 0x411
        # that follows that close, which is correct.
        s.arm(1.03, sent=0.95)
    else:
        s.arm(0.0)
    s.vcm(1.0, 30.0, period=0.01)
    s.key(1.0, 20.0, True)
    s.key(20.0, 30.0, False)
    s.mainc(1.0, 20.3, 11)
    s.soc(1.0, 20.3, 1500 if soc_low else 5000)
    s.other(0x617, 1.0, 30.0, 0.25)
    s.shift(1.0, 30.0, 2)
    s.other(0x054, 1.0, 20.2, 0.01)
    s.other(0x471, 1.0, 20.2, 0.01)
    s.device(lambda t: (1.06 if slow_post else 1.1) <= t < (21.0 if tx_after_off else 20.0))
    ta = 19.0 if abort_before_off else 20.71
    s.pages(0.3, 30.0, lambda t: {"live": t < 20.0, "abort": reason if t > ta else "none",
                                  "disabled": soc_low and t > 2.0, "dis": "low_soc" if soc_low and t > 2.0 else "none"})
    return s


def test_rekey():
    verdict_is("rekey: right device", run("rekey", rekey_sim()), "AS SPECIFIED")
    verdict_is("rekey: a close while the arm POST is in flight still counts",
               run("rekey", rekey_sim(slow_post=True)), "AS SPECIFIED")
    verdict_is("rekey: transmits after key-off", run("rekey", rekey_sim(tx_after_off=True)),
               "NOT AS SPECIFIED", "after the key-off")
    verdict_is("rekey: abort before the key-off", run("rekey", rekey_sim(abort_before_off=True)),
               "NOT AS SPECIFIED", "key reads 0 before the trip")
    verdict_is("rekey: reason the model forbids", run("rekey", rekey_sim(reason="stale_soc")),
               "NOT AS SPECIFIED", "model allows")
    verdict_is("rekey: low_soc", run("rekey", rekey_sim(soc_low=True)), "NOT AS SPECIFIED", "no low_soc")


# --------------------------------------------------------------- the common

def test_common():
    s = soc_sim()
    s.f = [x for x in s.f if x[1] != 0x7F8]
    # The refused 0x7F8 pages replace the sim's own; the 0x7F1/0x7F2 that pages() adds
    # beside them are dropped, and the sim's own kept, so each 0x7F8 follows a schema
    # page (until 2026-10-24 every 0x7F1 was dropped, which gi_diag rightly cannot decode).
    n = len(s.f)
    s.pages(0.31, 12.0, lambda t: {"refused": True})
    s.f = s.f[:n] + [x for x in s.f[n:] if x[1] not in (0x7F1, 0x7F2)]
    res = run("soc_latch", s)
    check("emit_refused fails any case", any(c["name"] == "emit_refused never set" and not c["ok"]
                                            for c in res["checks"]) and res["verdict"] == "NOT AS SPECIFIED",
          res["verdict"])
    # Content split: an adjacent identical pair in a stimulus is caught before a run.
    p = B.parse_scn("mode 0 3 500\nf 1000 051 6 080080FF7F01\nf 2000 051 6 080080FF7F01\nend 3000\n")
    check("premise: adjacent identical 0x051 counted", B.stimulus_premise(p) == 1, B.stimulus_premise(p))
    p = B.parse_scn("mode 0 3 500\ntxfail 10 20\nf 1000 051 6 080080FF7F01\nend 3000\n")
    check("parse: a host-only directive is refused", p["refused"] == ["txfail 10 20"], p["refused"])
    p = B.parse_scn("f 1000 051 6 080080FF7F\n")
    check("parse: a DLC that disagrees with the bytes is refused", len(p["refused"]) == 1, p)
    info = {"refused": [], "adjacent_identical_0x051": 0, "pin_match": False, "modes": [(0, 3, 500)]}
    check("stimulus: a pin mismatch refuses the run", bool(B.stimulus_ok(info)), "")
    info["pin_match"] = True
    info["modes"] = [(0, 0, 500)]
    check("stimulus: never armed refuses the run", bool(B.stimulus_ok(info)), "")
    for case, dlc in (("trip8-dlc5", 5), ("trip8-control-dlc8", 8)):
        info, parsed = B.build(case, os.path.join(SCRATCH, "gen"))
        inj, orig = info.get("injected_hex", ""), info.get("original_hex", "")
        check("build: %s records the injected frame and the payload it replaced" % case,
              len(inj) == 2 * dlc and len(orig) == 12 and inj[:10] == orig[:10] and inj != orig,
              "%r %r" % (inj, orig))
        check("build: %s records its stimulus file relative to the run folder that holds it" % case,
              info.get("file") == "interlock_stimulus/%s.scn" % case
              and os.path.isfile(os.path.join(B.RUNS, info["file"])), info.get("file"))
    h0 = "080080ff7f0b"
    h1 = B.encode_sig(J.PTX, 0x051, h0, "gen_torque_cmd", -50)
    d = J.decode(J.PTX, 0x051, h1)
    check("encode_sig: torque set, rpm_ref and counter kept, length kept",
          len(h1) == 12 and d["gen_torque_cmd"] == 32718 and d["gen_rpm_ref"] == 32767 and h1[10:] == "0b", h1)
    h2 = B.encode_sig(J.PT, 0x054, "ff7f8ac480", "GENE_RotSpd", 800)
    check("encode_sig: 0x054 at 800 rpm keeps its five bytes and the other signal",
          len(h2) == 10 and J.decode(J.PT, 0x054, h2)["GENE_RotSpd"] == 33567 and h2[6:] == "c480", h2)
    h = "2cfea5ee6247d000"          # a real 0x440 from the slice (state 11)
    h2 = B.encode_mainc(h, 14)
    d0, d = J.decode(J.PT, 0x440, h), J.decode(J.PT, 0x440, h2)
    check("derived: close_all sets the state and keeps every other bit",
          d["bcm_mainc_stat"] == 14 and all(d[k] == d0[k] for k in d0 if k != "bcm_mainc_stat")
          and (int(h, 16) ^ int(h2, 16)) & ~(0xF << 58) == 0, (h, h2))
    h3 = B.encode_sig(J.PTX, 0x051, h0[:10], "gen_torque_cmd", -50)
    check("encode_sig: a frame shorter than the DBC keeps its own length",
          len(h3) == 10 and h3 == h1[:10], h3)
    plan = {"frames": [(k * 10000, 0x411 if k % 2 else 0x617, "00") for k in range(40)]}
    on_time = [(100.0 + t / 1e6 + 0.002, i, h, 1) for t, i, h in plan["frames"]]
    worst, st = B.sender_stalls(plan, on_time)
    check("sender_stalls: an on-time sender is not a stall", not st and set(worst) == {"0x411", "0x617"},
          "%s %s" % (worst, st))
    late = [(t + (0.15 if j >= 25 else 0.0), i, h, ok) for j, (t, i, h, ok) in enumerate(on_time)]
    worst, st = B.sender_stalls(plan, late)
    check("sender_stalls: a 150 ms stall voids the run, on every ID it spans",
          st == ["0x411", "0x617"] and 0.14 < worst["0x411"] < 0.16, "%s %s" % (worst, st))
    late = [(t + (0.05 if j >= 25 else 0.0), i, h, ok) for j, (t, i, h, ok) in enumerate(on_time)]
    check("sender_stalls: a 50 ms stall is under the limit", not B.sender_stalls(plan, late)[1], "")
    check("sender_stalls: a sent list that does not match the stimulus voids the run",
          B.sender_stalls(plan, on_time[:-1])[1] != [], "")
    plan2 = {"frames": plan["frames"] + [(k * 10000 + 5000, 0x051, "00") for k in range(40)]}
    wit = [(50.0 + t / 1e6 + 0.0003, i, h) for t, i, h in plan2["frames"]]
    wit += [(50.0 + k * 0.01 + 0.006, 0x051, "11") for k in range(40)]
    worst, st = B.witness_stalls(plan2, wit)
    check("witness_stalls: an on-time wire is not a stall, 0x051 left out",
          not st and set(worst) == {"0x411", "0x617"} and abs(worst["0x411"]) < 1e-6, "%s %s" % (worst, st))
    late = [(t + (0.2 if t > 50.2 and i != 0x051 else 0.0), i, h) for t, i, h in wit]
    worst, st = B.witness_stalls(plan2, late)
    check("witness_stalls: a 200 ms gap on the wire voids the run",
          st == ["0x411", "0x617"] and 0.19 < worst["0x617"] < 0.21, "%s %s" % (worst, st))
    worst, st = B.witness_stalls(plan2, [f for f in wit if not (f[1] == 0x617 and f[0] > 50.3)])
    check("witness_stalls: an ID seen fewer times than planned cannot be paired",
          any(x.startswith("0x617 planned") for x in st), "%s" % st)
    if sys.platform == "win32":
        check("raise_priority: the sender runs above normal", B.raise_priority() == "above_normal", "")
    # The lock check is bench_held.py's, tested in test_bench_held.py (2026-10-25).
    gen = os.path.join(SCRATCH, "gen")
    st = {}
    for case in ("gate-open", "gate-open-control"):
        _, parsed = B.build(case, gen)
        st[case] = sorted({J.decode(J.PT, 0x440, h)["bcm_mainc_stat"]
                           for _, i, h in parsed["frames"] if i == 0x440})
    check("derived: gate-open-control has every 0x440 closed, gate-open has some open",
          st["gate-open-control"] and all(x in J.CLOSED for x in st["gate-open-control"])
          and any(x not in J.CLOSED for x in st["gate-open"]), "%s" % st)


# ------------------------------------------------- part 2: model vs host core

GOLD_ABORT = re.compile(r"^(\d+) EV ABORT a=(\d+)")


def test_model_vs_host(scratch):
    gold = os.path.join(B.HOSTDIR, "golden")
    if not os.path.isdir(gold):
        check("model vs host: tester repo present", False, "SKIPPED: no %s" % gold)
        return
    gen = os.path.join(scratch, "scn")
    os.makedirs(gen, exist_ok=True)
    names = ["stale-soc-aborts", "stale-contactor-aborts", "stale-fault-aborts", "stale-shift-aborts",
             "gene-rpm-stale-while-fb-fresh", "gene-family-quiet-together", "inverter-lost"]
    enum = {v: k for k, v in ABORT.items()}
    for n in names:
        p = B.host_scn(n, gen)
        parsed = B.parse_scn(open(p, encoding="utf-8").read())
        frames = [(t / 1e6, i, h) for t, i, h in parsed["frames"]]
        gp = os.path.join(gold, n + ".trace")
        if not os.path.exists(gp):
            check("model vs host: %s golden" % n, False, "no golden")
            continue
        ab = [GOLD_ABORT.match(ln) for ln in open(gp, encoding="utf-8")]
        ab = [(int(m.group(1)) / 1e6, int(m.group(2))) for m in ab if m]
        c = J.Ctx(frames)
        t_arm = parsed["modes"][0][0] / 1e6
        t_live = min(t for t, i, _ in frames if i == 0x051)
        reasons, t_fire, det = J.predict_trip(c, t_live, t_arm)
        want = enum.get(str(ab[0][1])) if ab else None
        want = None if want is None else str(want)
        name_of = {int(k): v for v, k in ABORT.items()}
        want = name_of.get(ab[0][1]) if ab else None
        # The golden's frame must lie in the window the model allows for a
        # signal carrying the golden's reason (an expiry on a frame is ambiguous).
        span = [(x["fires_lo"], x["fires_hi"]) for k, x in det.items()
                if x["fires_lo"] is not None and x["fires_hi"] is not None]
        ok = bool(ab) and want in reasons and t_fire is not None and             any(lo - 1e-6 <= ab[0][0] <= hi + 1e-6 for lo, hi in span)
        check("model vs host: %s fires %s on %s" % (n, sorted(reasons), t_fire), ok,
              "golden %s" % (ab[:1],))


def test_model_rekey(scratch):
    """The real capture: each key-off's trip as the golden has it, from the live
    moment and arm-cycle start the golden reports."""
    gp = os.path.join(B.HOSTDIR, "golden", "replay-rekey-long.trace")
    if not os.path.exists(gp):
        check("model vs host: replay-rekey-long golden", False, "SKIPPED: no golden")
        return
    gen = os.path.join(scratch, "scn")
    os.makedirs(gen, exist_ok=True)
    parsed = B.parse_scn(open(B.host_scn("replay-rekey-long", gen), encoding="utf-8").read())
    c = J.Ctx([(t / 1e6, i, h) for t, i, h in parsed["frames"]])
    lines = open(gp, encoding="utf-8").read().splitlines()
    lives = [int(ln.split()[0]) / 1e6 for ln in lines if " EV INHIBIT_LIVE" in ln]
    starts = [0.0] + [int(ln.split()[0]) / 1e6 for ln in lines if " EV KEY_CLEAR" in ln]
    aborts = [(int(m.group(1)) / 1e6, int(m.group(2))) for m in map(GOLD_ABORT.match, lines) if m]
    name_of = {int(k): v for v, k in ABORT.items()}
    check("model vs host: rekey-long has two live segments", len(lives) == 2 == len(aborts), (lives, aborts))
    for live, start, (ta, code) in zip(lives, starts, aborts):
        reasons, t_fire, det = J.predict_trip(c, live, start)
        span = [(x["fires_lo"], x["fires_hi"]) for x in det.values() if x["fires_lo"] and x["fires_hi"]]
        ok = name_of[code] in reasons and any(lo - 1e-6 <= ta <= hi + 1e-6 for lo, hi in span)
        check("model vs host: rekey-long segment from %.3f fires %s, golden %s at %.4f"
              % (live, sorted(reasons), name_of[code], ta), ok, det)


def test_attribution():
    """attribute_by_sender (reviewer, 2026-10-16): the sender's log decides which
    0x051 are the sender's; an identical pair is split by send time, or refused."""
    A = J.attribute_by_sender
    N, N1, N2 = "080080ff7f01", "080080ff7f02", "080080ff7f03"
    other = [(0.0, 0x411, "aa"), ]

    def sends(*xs):
        return [(t, i, h, True) for t, i, h in xs]
    # Calibration: unambiguous sends (no device yet) set the offset at 0.1 ms.
    cal_s = [(t, 0x051, "0800%010x" % k) for k, t in enumerate([-1.0, -0.98, -0.96, -0.94])]
    cal_w = [(t + 0.0001, i, h) for t, i, h in cal_s]
    # 1. VCM N, the device's answer (N+1's payload) 0.26 ms after it, VCM N+1 2 ms on.
    s1 = sends(*cal_s, (0.0, 0x051, N), (0.002, 0x051, N1))
    w1 = cal_w + [(0.0001, 0x051, N), (0.00036, 0x051, N1), (0.0021, 0x051, N1)]
    vcm, dev, info = A(w1, s1)
    check("attribution: N, answer, N+1 -> the answer is the device's, the later twin the VCM's",
          dev == [(0.00036, N1)] and (0.0021, N1) in vcm and not info["ambiguous"], (dev, info))
    # 2. trip8-control-dlc8: sends 0.08 ms apart, the second an 8-byte copy.
    N1_8 = N1 + "0000"
    s2 = sends(*cal_s, (0.0, 0x051, N), (0.002, 0x051, N1), (0.00208, 0x051, N1_8))
    w2 = cal_w + [(0.0001, 0x051, N), (0.00036, 0x051, N1), (0.0021, 0x051, N1), (0.0022, 0x051, N1_8)]
    vcm, dev, info = A(w2, s2)
    check("attribution: back-to-back sends 0.08 ms apart -> both the sender's, one device answer",
          len(vcm) == 4 + 3 and dev == [(0.00036, N1)] and not info["ambiguous"], (dev, info))
    # 3. an answer with no next send (the stimulus end).
    s3 = sends(*cal_s, (0.0, 0x051, N))
    w3 = cal_w + [(0.0001, 0x051, N), (0.00036, 0x051, N1)]
    vcm, dev, info = A(w3, s3)
    check("attribution: an answer at the stimulus end is the device's",
          dev == [(0.00036, N1)] and len(vcm) == 5, (dev, vcm))
    # 4. two identical candidates the residual cannot separate: refused, not guessed.
    cal_w4 = [(t + 0.0001 + (0.0004 if k % 2 else 0.0), i, h) for k, (t, i, h) in enumerate(cal_s)]
    s4 = sends(*cal_s, (0.0, 0x051, N), (0.0006, 0x051, N1))
    w4 = cal_w4 + [(0.0001, 0x051, N), (0.00036, 0x051, N1), (0.0007, 0x051, N1)]
    vcm, dev, info = A(w4, s4)
    check("attribution: a pair within the residual is AMBIGUOUS, recorded",
          len(info["ambiguous"]) == 1, info)
    # 5. a genuine VCM frame 1.2 ms behind the last, no device: both the sender's.
    s5 = sends(*cal_s, (0.0, 0x051, N), (0.0012, 0x051, N1), (0.0024, 0x051, N2))
    w5 = cal_w + [(0.0001, 0x051, N), (0.0013, 0x051, N1), (0.0025, 0x051, N2)]
    vcm, dev, info = A(w5, s5)
    check("attribution: genuine VCM frames 1.2 ms apart are never the device's",
          dev == [] and len(vcm) == 7, dev)
    # 7. a LATE answer: VCM N+1 first, then the device's copy of it. The first of
    #    the pair is the sender's here (kills "the later is always the sender's").
    w7 = cal_w + [(0.0001, 0x051, N), (0.0021, 0x051, N1), (0.0024, 0x051, N1)]
    vcm, dev, info = A(w7, s1)
    check("attribution: a late answer after VCM N+1 is the device's, the earlier twin the VCM's",
          dev == [(0.0024, N1)] and (0.0021, N1) in vcm, (dev, info))
    # 8. a 1.5 ms sender-to-wire offset: without it the answer sits nearer the send
    #    time than the real frame does (kills "offset ignored").
    cal_w8 = [(t + 0.0015, i, h) for t, i, h in cal_s]
    w8 = cal_w8 + [(0.0015, 0x051, N), (0.00176, 0x051, N1), (0.0035, 0x051, N1)]
    vcm, dev, info = A(w8, s1)
    check("attribution: the offset is applied (1.5 ms send-to-wire)",
          dev == [(0.00176, N1)] and (0.0035, N1) in vcm and abs(info["offset_s"] - 0.0015) < 1e-9,
          (dev, info))
    # 6. the content rule (no log) on sequence 3 books the answer as a VCM frame:
    #    the defect this replaces.
    c = J.Ctx(w3, [], [])
    check("attribution: the content rule alone gets sequence 3 wrong (control)",
          (0.00036, N1) in c.vcm, c.dev)
    c = J.Ctx(w3, [], [], sent=s3)
    check("attribution: Ctx with the sender log gets it right",
          c.dev == [(0.00036, N1)] and c.attribution is not None, c.dev)


def test_modes_at(scratch):
    """interlock_bench's "modes_at" derivation (2026-10-16): the host scenario's frames
    verbatim, the first mode line kept, the later ones replaced, never past the frames."""
    gen = os.path.join(scratch, "gen")
    _, held = B.build("liveflag-mmode-hold", gen)
    _, plain = B.build("liveflag-mmode", gen)
    check("modes_at: the frames are the host scenario's, verbatim", held["frames"] == plain["frames"],
          "%d vs %d" % (len(held["frames"]), len(plain["frames"])))
    check("modes_at: the arm kept, the later mode lines replaced, the arm's offset carried",
          held["modes"] == [plain["modes"][0], (8500000, 0, 500), (8800000, 3, 500)], held["modes"])
    _, held2 = B.build("liveflag-lowsoc-hold", gen)
    check("modes_at: low SoC held to 8.0 s, re-armed at 8.1 s",
          [m[:2] for m in held2["modes"]] == [(0, 3), (8000000, 0), (8100000, 3)], held2["modes"])
    row = B.BY_NAME["liveflag-mmode-hold"]
    B.BY_NAME["_modes_at_past_end"] = ("_modes_at_past_end",) + row[1:3] + (
        ("derived", "disable-clears-live-flag", "modes_at", [(8500000, 0), (9500000, 3)]), {})
    try:
        B.build("_modes_at_past_end", gen)
        refused = False
    except RuntimeError:
        refused = True
    finally:
        del B.BY_NAME["_modes_at_past_end"]
    check("modes_at: a mode line after the last frame is refused", refused)


def seadrive_stimuli():
    """{path: (mtime_ns, size)} of the stimulus files in SeaDrive's runs/interlock_stimulus/."""
    d = os.path.join(gi_paths.runs(), "interlock_stimulus")
    out = {}
    for n in (os.listdir(d) if os.path.isdir(d) else []):
        st = os.stat(os.path.join(d, n))
        out[n] = (st.st_mtime_ns, st.st_size)
    return out


def test_decoding():
    """Diag pages through gi_diag (toolkit item 10; readings Q1, Q2, Q6, 2026-10-24)."""
    import gi_diag
    base = soc_sim()
    res = run("soc_latch", base)
    check("decoding: the sim's pages all decode, on HEAD's schema",
          res["facts"]["diag_decode"]["undecodable"] == 0 and
          res["facts"]["diag_decode"]["schemas"] == [J.schemas().head_version], res["facts"]["diag_decode"])
    first7f1 = min(t for t, i, h in base.f if i == 0x7F1)
    s = soc_sim()
    s.f.append((round(first7f1 - 0.05, 6), 0x7F8, enc(J.DIAG, 0x7F8)))
    res = run("soc_latch", s)
    check("decoding: a 0x7F8 before the first schema page makes the verdict ERROR, not absent",
          res["verdict"] == "ERROR" and res["errors"] and "before the first schema page" in res["errors"][0]["detail"],
          (res["verdict"], res.get("errors")))
    s = soc_sim()
    k = next(n for n, x in enumerate(s.f) if x[1] == 0x7F1)
    bad = bytearray(bytes.fromhex(s.f[k][2]))
    bad[0] = 0xEE
    s.f[k] = (s.f[k][0], 0x7F1, bytes(bad).hex())
    try:
        run("soc_latch", s)
        raised = None
    except gi_diag.UnknownSchema as e:
        raised = str(e)
    check("decoding: an unknown schema byte raises (refused, not judged)", raised and "schema byte 238" in raised, raised)
    s = soc_sim()
    s.f = [x if x[1] != 0x7F2 else (x[0], x[1], x[2][:4]) for x in s.f]
    res = run("soc_latch", s)
    check("decoding: a page that fails otherwise (2 bytes of 0x7F2, truncated) is not an ERROR",
          res["verdict"] != "ERROR", res["verdict"])
    other = {"dbc_hash": "0" * 16, "dbc_commit": "0" * 40}
    try:
        J.judge("soc_latch", base.f, base.posts, [], {}, identity=other)
        raised = None
    except gi_diag.SchemaMismatch as e:
        raised = str(e)
    check("decoding: a record whose identity names another DBC is refused", raised and "run record" in raised, raised)
    mine = gi_diag.run_identity(schemas=J.schemas()) if J.schemas().source.startswith("git") else None
    check("decoding: the record's own identity judges as before",
          mine is None or J.judge("soc_latch", base.f, base.posts, [], {}, identity=mine)["verdict"] ==
          run("soc_latch", base)["verdict"])
    try:
        J.decode(J.DIAG, 0x7F8, "00" * 8)
        raised = None
    except ValueError as e:
        raised = str(e)
    check("decoding: J.decode refuses a diag page on its own", raised and "in order" in raised, raised)
    # interlock_bench.rejudge_file passes the record's own identity, when it has one.
    import gzip
    import json
    d = os.path.join(SCRATCH, "rejudge")
    os.makedirs(d, exist_ok=True)
    name = next(iter(B.BY_NAME))
    with gzip.open(os.path.join(d, "w.json.gz"), "wt", encoding="ascii") as fh:
        json.dump({"frames": base.f, "sent": [], "clock": None, "errs": []}, fh)

    def rec_with(decoder):
        p = os.path.join(d, "run_%s.json" % ("none" if decoder is None else decoder["dbc_hash"][:4]))
        r = {"stimulus": {"case": name, "judge": "soc_latch"}, "t0_epoch": 0.0, "witness_file": "w.json.gz",
             "posts": base.posts, "samples": []}
        if decoder is not None:
            r["decoder"] = decoder
        json.dump(r, open(p, "w", encoding="ascii"))
        return p
    try:
        B.rejudge_file(rec_with(other))
        raised = None
    except gi_diag.SchemaMismatch as e:
        raised = str(e)
    check("rejudge_file: a record whose decoder identity names another DBC is refused", raised, raised)
    try:
        res2, _out = B.rejudge_file(rec_with(None))
        got = res2["judged"]["facts"]["diag_decode"]["schemas"]
    except Exception as e:      # noqa: BLE001
        got = "CRASH %s" % e
    check("rejudge_file: a record with no identity (before 2026-10-24) decodes by schema byte",
          got == [J.schemas().head_version], got)
    c = J.Ctx(base.f, base.posts)
    pg = c.p7f8[0][1]
    check("decoding: each page carries the schema it was decoded on",
          getattr(pg, "schema", None) == J.schemas().head_version, getattr(pg, "schema", None))


def main():
    seadrive_before = seadrive_stimuli()
    check("stimulus files are built under the scratch folder, not SeaDrive's runs/",
          os.path.commonpath([os.path.abspath(B.STIM_DIR), os.path.abspath(SCRATCH)]) == os.path.abspath(SCRATCH)
          and os.path.abspath(gi_paths.runs()) not in os.path.abspath(B.STIM_DIR), B.STIM_DIR)
    # Absolute (2026-10-16): make_scenarios runs with the firmware clone's host folder
    # as its cwd, so a relative scratch path wrote generated scenarios into that clone.
    scratch = SCRATCH
    os.makedirs(scratch, exist_ok=True)
    test_soc()
    test_boundary()
    test_mmode()
    test_wake()
    test_never_closed()
    test_gate()
    test_suppress()
    test_trips()
    test_diag_cadence()
    test_short()
    test_event_trips()
    test_bus_stop()
    test_gate_refuse()
    test_rekey()
    test_common()
    test_decoding()
    test_attribution()
    test_model_vs_host(scratch)
    test_model_rekey(scratch)
    test_modes_at(scratch)
    after = seadrive_stimuli()
    check("no stimulus file in SeaDrive's runs/interlock_stimulus/ was written or added",
          after == seadrive_before, sorted(n for n in set(after) | set(seadrive_before)
                                           if after.get(n) != seadrive_before.get(n))[:5])
    print("\n%d checks, %d failed" % (NCHK, NFAIL))
    return 1 if NFAIL else 0


if __name__ == "__main__":
    sys.exit(main())
