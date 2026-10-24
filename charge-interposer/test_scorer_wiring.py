"""Tests the HARNESS, not the core: the scenario scorer's wiring in
run_scenario.py, as changed by the tester on 2026-10-07.

Each change there exists because the old form could not fail on a wrong
core, so each gets a case here that would fail if the change were undone:

  * spec 9 "every scenario decodes the diagnostic CAN frames": every
    scenario asks for diag_frames_ok(), and every termination scenario for
    no_rewrite_after_release();
  * the spec literals -- 20 frames, 50 ms, firmware 6, schema 4 (fw 6
    since E5, 2026-10-09; 5 / 4 at E4, 4 / 3 before) -- are the harness's own, not read from
    machine.py, and 0x7F4 B2 6 decodes as HOLD;
  * 0x7F4 B4 bit 5 override_active is 1 in OVERRIDE and HOLD and 0
    otherwise, and B5 is a setpoint there and 255 otherwise (schema 4);
  * the firmware and schema versions are checked in EVERY decoded 0x7F7,
    not only the last (tracker F reading 15);
  * check_invariants() fails on a missing trace and passes on a known-good
    one, and actually judged override page-01 frames when it passed;
  * the 16 A scenario no longer leans on current_at_most(), which reads the
    charger sim's clamped output and could not fail on the core.

Run:  py -3.14 projects/vtrux/tools/interposer/test_scorer_wiring.py
"""

import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_scenario as R

import paths                                                # noqa: E402

TRACES = Path(paths.fixtures())

failures = 0


def check(cond, name):
    global failures
    print(("  ok  " if cond else "FAIL  ") + name)
    if not cond:
        failures += 1


def _diag(fw7f4=9, sch7f4=4, fw7f7=(9, 9, 9), sch7f7=4, state=0,
          flags=None, b5=None):
    """A minimal decoded diag stream in parse_diag_frames()'s shape.
    B4 bit 5 and B5 follow schema 4 for `state` unless given: bit 5 set and
    B5 a setpoint (29 = 2.9 A) in OVERRIDE (2) and HOLD (6), else bit 5
    clear and B5 255."""
    rewriting = state in (2, 6)
    if flags is None:
        flags = 0x20 if rewriting else 0x00
    if b5 is None:
        b5 = 29 if rewriting else 255
    out = []
    t = 0.0
    for fw in fw7f7:
        out.append((t, 0x7F4, bytes([sch7f4, fw7f4, state, 0, flags, b5,
                                     0, 0])))
        out.append((t, 0x7F5, bytes(8)))
        out.append((t, 0x7F6, bytes(8)))
        out.append((t, 0x7F7, bytes([0xfd, 0x7b, 0x19, 0x7f,
                                     fw & 0xFF, fw >> 8, sch7f7, 0])))
        t += 1.0
    return out


C0 = 100000                      # chg_max reaches 0 A here (ms)
# The wire anchor (reviewer, 2026-10-13): bcm_soc reads 100 at sim t=650 s
# and on the trace at SOC100_T, so sim t maps to trace ms by -600 s, and the
# step below lands at C0 + 200 s by both anchors. The pack current steps up
# 0.2 A there (_ib), the drop's bcm_ibat step the cross-check looks for.
SOC100_T = 50000
SOC100_LINE = "BMS: bcm_soc reads 100 % on the bus at t=650.0s"
STEP_T = C0 + 200000
DROP_LINE = (SOC100_LINE + "\n12 V load step: 266.0 -> 136.0 W at t=900.0s "
             "(+200.0s after chg_max reached 0 A)")


def _ib(h0=50000, h1=500000, at=STEP_T, before=-0.3, after=-0.1):
    """Pack current every second: `before` until `at`, `after` from it."""
    return [(t, before if t < at else after) for t in range(h0, h1 + 1, 1000)]


def _sp_path(hold0, hold1, start_a, legs, every=300):
    """Page-01 setpoints every `every` ms over the hold: `start_a` until the
    first leg, then each (t_from, target) leg walks 0.1 A per frame toward
    its target and stays there."""
    out, a, legs = [], start_a, list(legs)
    target = None
    t = hold0
    while t <= hold1:
        while legs and t >= legs[0][0]:
            target = legs.pop(0)[1]
        if target is not None and abs(target - a) > 1e-6:
            a = round(a + (0.1 if target > a else -0.1), 2)
        out.append((t, a))
        t += every
    return out


def _hs(hold=(50000, 500000), chg0=C0, sp=None, ibat=None, chg_after=None,
        soc100=SOC100_T, walk_at=None):
    """A series in hold_series_from_trace()'s shape: chg_max 2.0 A, then
    0 A from chg0 (None: never); pack current -0.3 A every second, -0.1 A
    from STEP_T (_ib); the setpoint 2.0 A walking up to 2.9 A from chg0,
    and from walk_at (if given) back down to 2.0 A, 0.1 A per 300 ms frame:
    a load drop's walk-down (spec 826925f4); bcm_soc reading 100 from
    soc100."""
    h0, h1 = hold
    chg = [(0, 2.0)] + ([(chg0, 0.0)] if chg0 is not None else [])
    if chg_after:
        # a 0x420 every 100 ms, as on a trace, with extra (t, A) changes
        pts = sorted(chg + list(chg_after))
        chg = [(t, [a for tp, a in pts if tp <= t][-1])
               for t in range(0, h1 + 1, 100)]
    return dict(hold=[hold], chgmax=chg, soc100=soc100,
                ibat=ibat if ibat is not None else _ib(h0, h1),
                sp=sp if sp is not None else
                _sp_path(h0, h1, 2.0, [(chg0 or h1 + 1, 2.9)] +
                         ([(walk_at, 2.0)] if walk_at else [])),
                t_last=h1)


def hold_follows_loads_cases():
    def run(series, veh=""):
        return R._hold_follows_loads(series, veh)

    ok, why = run(_hs())
    check(ok, "a hold that settles at 2.9 A 2.7 s after chg_max 0 passes "
          "-> %s" % why)
    base_ib = _ib()
    ok, why = run(_hs(ibat=base_ib + [(C0 + 201000, 0.5)], walk_at=STEP_T),
                  DROP_LINE)
    check(ok, "pack current above chg_max 1 s after an injected load DROP, "
          "during its walk-down, passes -> %s" % why)
    ok, why = run(_hs(ibat=base_ib + [(C0 + 203500, 0.5)], walk_at=STEP_T),
                  DROP_LINE)
    check(not ok and why.startswith("1:"),
          "...3.5 s after the drop, past the walk-down's hold frame at "
          "302.9 s, fails on clause 1 -> %s" % why)
    ok, why = run(_hs(ibat=base_ib + [(C0 + 50000, 0.3)]))
    check(not ok and "1:" in why,
          "pack current above chg_max with no drop fails on clause 1 -> %s"
          % why)
    # Clause 1's fall in bcm_chg_max (spec 75b154da): 0 -> 0.5 A (a rise)
    # at R1, then 0.5 -> 0.25 A (a fall) at F1, 0x420 every 100 ms.
    r1, f1 = C0 + 300000, C0 + 310000
    legs = [(r1, 0.5), (f1, 0.25)]
    ok, why = run(_hs(ibat=base_ib + [(C0 + 1000, 0.5)]))
    check(ok, "pack current above chg_max 1 s after chg_max FALLS to 0 A "
          "passes (the amendment) -> %s" % why)
    ok, why = run(_hs(ibat=base_ib + [(f1 + 1000, 0.4)], chg_after=legs))
    check(ok, "...and 1 s after a 0.5 -> 0.25 A fall, 0x420 every 100 ms "
          "-> %s" % why)
    ok, why = run(_hs(ibat=base_ib + [(f1 + 2500, 0.4)], chg_after=legs))
    check(not ok and why.startswith("1:"),
          "...2.5 s after that fall fails on clause 1 -> %s" % why)
    held = [(t, 0.4) for t in range(f1, f1 + 5001, 100)]
    ok, why = run(_hs(ibat=base_ib + held, chg_after=legs))
    check(not ok and why.startswith("1:")
          and "first at t=%.1f s" % ((f1 + 2100) / 1000.0) in why,
          "the pack held above a fallen chg_max for 5 s fails, from the "
          "first frame past 2 s: the later 0x420s at 0.25 A are not falls "
          "-> %s" % why)
    ok, why = run(_hs(ibat=base_ib + [(f1 - 500, 0.6)], chg_after=legs))
    check(not ok and why.startswith("1:"),
          "above chg_max 0.5 s BEFORE a fall fails (the 2 s run after it) "
          "-> %s" % why)
    ok, why = run(_hs(ibat=base_ib + [(r1 + 1000, 0.75)], chg_after=legs))
    check(not ok and why.startswith("1:"),
          "above chg_max 1 s after a RISE in chg_max fails (a rise is not "
          "a fall) -> %s" % why)
    up_line = DROP_LINE.replace("266.0 -> 136.0", "136.0 -> 266.0")
    up_ib = _ib(before=-0.1, after=-0.3)
    ok, why = run(_hs(ibat=up_ib), up_line)
    check(ok, "a load step UP with its bcm_ibat fall passes -> %s" % why)
    ok, why = run(_hs(ibat=up_ib + [(C0 + 201000, 0.5)]), up_line)
    check(not ok and "1: pack current above" in why
          and "no bcm_ibat" not in why,
          "...and a load step UP is not a drop: no exception -> %s" % why)
    sp = _hs()["sp"]
    k = len(sp) // 2
    jump = sp[:k] + [(t, round(a + 0.2, 2)) for t, a in sp[k:k + 1]] + \
        [(t, round(a + 0.2, 2)) for t, a in sp[k + 1:]]
    ok, why = run(_hs(sp=jump))
    check(not ok and "2:" in why and "0.20 A step" in why,
          "a 0.2 A step fails on clause 2 -> %s" % why)
    fast = _sp_path(50000, 500000, 2.0, [(C0, 2.9)], every=200)
    ok, why = run(_hs(sp=fast))
    check(not ok and "ms apart" in why,
          "steps 200 ms apart fail on clause 2 -> %s" % why)
    # the cadence, 0.28 s (spec dfc11cbb, user 2026-10-13, option A)
    s270 = _sp_path(50000, 500000, 2.0, [(C0, 2.9)], every=270)
    ok, why = run(_hs(sp=s270))
    check(not ok and "2:" in why and "steps 270 ms apart" in why,
          "steps 270 ms apart fail on clause 2 -> %s" % why)
    s280 = _sp_path(50000, 500000, 2.0, [(C0, 2.9)], every=280)
    ok, why = run(_hs(sp=s280))
    check(ok and ">= 0.28 s apart" in why,
          "steps 280 ms apart pass clause 2 -> %s" % why)
    ok, why = R._hold_follows_loads(_hs(sp=s280), "", _step_gap_ms=300)
    check(not ok and "steps 280 ms apart" in why,
          "...and fail under the rule before dfc11cbb (0.3 s) -> %s" % why)
    zero = _sp_path(50000, 500000, 0.3, [(60000, 0.0), (C0, 2.9)])
    ok, why = run(_hs(sp=zero))
    check(not ok and "0 A on" in why, "a 0 A setpoint fails on clause 2 "
          "-> %s" % why)
    ok, why = run(_hs(chg0=None))
    check(not ok and "never reached 0 A" in why,
          "a run where chg_max never reaches 0 A fails -> %s" % why)
    late = _sp_path(50000, 500000, 2.0, [(C0 + 125000, 2.9)])
    ok, why = run(_hs(sp=late))
    check(not ok and "3:" in why and "later than 120" in why,
          "settling 125+ s after chg_max 0 fails on clause 3 -> %s" % why)
    leaves = _sp_path(50000, 500000, 2.0, [(C0, 2.9), (C0 + 150000, 2.0)])
    ok, why = run(_hs(sp=leaves), DROP_LINE)
    check(not ok and "3:" in why,
          "leaving the band BEFORE the first load step fails on clause 3 "
          "-> %s" % why)
    after = _sp_path(50000, 500000, 2.0, [(C0, 2.9), (C0 + 205000, 2.0)])
    ok, why = run(_hs(sp=after), DROP_LINE)
    check(ok, "leaving the band AFTER the first load step passes -> %s"
          % why)
    ok, why = run(_hs(hold=(50000, C0 + 60000)))
    check(not ok and "shorter than" in why,
          "a hold that ends 60 s after chg_max 0 fails (no 2 min to "
          "settle) -> %s" % why)
    ok, why = run(_hs(hold=(50000, C0 - 1000)))
    check(not ok and "not in HOLD when" in why,
          "chg_max reaching 0 A after the hold ended fails -> %s" % why)
    ok, why = run(None)
    check(not ok and "no flight trace" in why, "no trace fails -> %s" % why)
    # The trace reader on a real trace: HOLD found, chg_max never 0 there.
    s = R.hold_series_from_trace(TRACES / "syn_override_release.trace")
    check(s is not None and s["hold"] and len(s["sp"]) > 10
          and s["ibat"] and s["chgmax"],
          "hold_series_from_trace reads HOLD spans, setpoints, ibat and "
          "chg_max off syn_override_release (%d span(s), %d page-01 frames "
          "in HOLD)" % (len(s["hold"]) if s else 0,
                        len(s["sp"]) if s else 0))
    ok, why = R._hold_follows_loads(s, "")
    check(not ok and "never reached 0 A" in why,
          "...where chg_max stays at 2.75 A, so the assertion refuses it "
          "-> %s" % why)
    check(R.hold_series_from_trace(TRACES / "no_such.l1.trace") is None,
          "a missing trace gives None, not an empty pass")


def _with_cap(series, caps):
    """`series` plus a spcap list aligned with its sp: `caps` is
    [(t_from, A)], the cap in force from each t."""
    def cap_at(t):
        return [a for tf, a in caps if tf <= t][-1]
    out = dict(series)
    out["spcap"] = [(t, cap_at(t)) for t, _a in series["sp"]]
    return out


def p3_anchor_cases():
    """Spec 9 at 96dd37033d4bb763 / 05999682eddd3a50 (user, 2026-10-12),
    clauses 1, 2 and 3, each shown against the rule before it
    (`_entry_grace` / `_clamp_move` / `_anchor_later` False)."""
    def run(series, veh="", **kw):
        return R._hold_follows_loads(series, veh, **kw)

    h0 = 50000
    # the hold entered at the 6.10 A floor, the cap at 16 A until it falls
    # to 6.00 A 100 ms in: the clamp, then 0.1 A steps every 300 ms from
    # 200 ms after the clamp (not restarted by it) down to 2.0 A, and up to
    # 2.9 A from chg_max 0
    walk = _sp_path(h0 + 300, 500000, 6.0, [(h0 + 300, 2.0), (C0, 2.9)])
    entry = _with_cap(_hs(sp=[(h0, 6.1), (h0 + 100, 6.0)] + walk),
                      [(0, 16.0), (h0 + 100, 6.0)])
    ok, why = run(entry)
    check(ok and "1 clamp(s)" in why,
          "clause 2: a clamp 6.10 -> 6.00 A at the cap, then a step 200 ms "
          "later, passes (the clamp is not a step and does not restart the "
          "0.3 s) -> %s" % why)
    ok, why = run(entry, _clamp_move=False)
    check(not ok and "2:" in why and "ms apart" in why,
          "...and fails with no clamp allowed: the clamp counts as a step "
          "200 ms before the next -> %s" % why)
    # a mid-hold clamp 2.9 -> 2.0 A, back up once the cap returns
    t1 = C0 + 30000
    mid_sp = [(t, a) for t, a in _hs()["sp"] if t < t1] + [(t1, 2.0)] + \
        _sp_path(t1 + 3000, 500000, 2.0, [(t1 + 3000, 2.9)])
    mid = _with_cap(_hs(sp=mid_sp), [(0, 16.0), (t1, 2.0), (t1 + 3000, 6.0)])
    ok, why = run(mid)
    check(ok and "1 clamp(s)" in why,
          "clause 2: a 0.9 A clamp mid-hold to a 2.0 A cap, then 0.1 A steps "
          "back once the cap returns, passes -> %s" % why)
    ok, why = run(mid, _clamp_move=False)
    check(not ok and "2:" in why and "-0.90 A step" in why,
          "...and with no clamp allowed it is a -0.90 A step -> %s" % why)
    # not clamps: below the cap but not at it; up to the cap from below
    short = _with_cap(_hs(sp=[(h0, 6.1), (h0 + 100, 5.5)] +
                           _sp_path(h0 + 400, 500000, 5.5,
                                    [(h0 + 400, 2.0), (C0, 2.9)])),
                      [(0, 16.0), (h0 + 100, 6.0)])
    ok, why = run(short)
    check(not ok and "2:" in why and "-0.60 A step" in why,
          "clause 2: 6.10 -> 5.50 A with the cap at 6.00 A is not a clamp "
          "(not AT the cap) -> %s" % why)
    rise = _with_cap(_hs(sp=[(h0, 2.0), (h0 + 300, 6.0)] +
                          _sp_path(h0 + 600, 500000, 6.0,
                                   [(h0 + 600, 2.0), (C0, 2.9)])),
                     [(0, 6.0)])
    ok, why = run(rise)
    check(not ok and "2:" in why and "4.00 A step" in why,
          "clause 2: 2.00 -> 6.00 A up to the cap from below is not a "
          "clamp -> %s" % why)

    # clause 3: chg_max 0 A at C0, the hold entered 60 s later
    late0 = C0 + 60000
    in2 = _hs(hold=(late0, 500000),
              sp=_sp_path(late0, 500000, 2.0, [(late0 + 300, 2.9)]))
    ok, why = run(in2)
    check(ok and "the hold's entry after chg_max 0 A" in why,
          "clause 3: chg_max 0 A 60 s BEFORE the hold, in band 3 s after "
          "the entry, passes (anchored on the later) -> %s" % why)
    ok, why = run(in2, _anchor_later=False)
    check(not ok and "not in HOLD when" in why,
          "...and fails when anchored on chg_max 0 alone -> %s" % why)
    slow = _hs(hold=(late0, 500000),
               sp=_sp_path(late0, 500000, 2.0, [(late0 + 125000, 2.9)]))
    ok, why = run(slow)
    check(not ok and "3:" in why and "later than 120" in why
          and "the hold's entry" in why,
          "clause 3: in band 125+ s after that entry fails, timed from the "
          "entry -> %s" % why)
    ok, why = run(_hs())
    check(ok and "after chg_max 0 A (t=" in why,
          "clause 3: HOLD first, chg_max 0 A later: still anchored on chg_max "
          "0 -> %s" % why)
    # clause 1's entry grace (spec 05999682, reading A): the hold enters at
    # 50 s; the pack 0.8 A above chg_max on one frame
    base_ib = [(t, -0.1) for t in range(h0, 500001, 1000)]
    for dt, passes in ((29000, True), (31000, False)):
        ok, why = run(_hs(ibat=base_ib + [(h0 + dt, 2.7)]))
        check(ok == passes and (passes or why.startswith("1:")),
              "clause 1: above chg_max %.0f s after the hold's entry %s -> %s"
              % (dt / 1000.0, "is exempt (the 30 s grace)" if passes
                 else "fails", why))
    ok, why = run(_hs(ibat=base_ib + [(h0 + 29000, 2.7)]), _entry_grace=False)
    check(not ok and why.startswith("1:"),
          "...and 29 s after the entry fails under the rule before 05999682 "
          "(no entry grace) -> %s" % why)
    # the trace reader's cap, on the trace built to clamp twice
    s = R.hold_series_from_trace(TRACES / "syn_hold_cap_clamp.trace")
    pairs = list(zip(s["sp"], s["sp"][1:], s["spcap"][1:])) if s else []
    clamps = [(tb, a, b, cap) for (_ta, a), (tb, b), (_tc, cap) in pairs
              if cap is not None and a > cap + 0.025 and abs(b - cap) <= 0.025]
    check(s is not None and len(s["spcap"]) == len(s["sp"]) and
          [round(cap, 2) for _t, _a, _b, cap in clamps] == [6.0, 2.0],
          "hold_series_from_trace gives the spec 4 cap at every hold page 01 "
          "of syn_hold_cap_clamp, where the core clamps to 6.00 then 2.00 A: "
          "%s" % ["t=%.1f %.2f->%.2f cap %.2f" % (t / 1000.0, a, b, c)
                  for t, a, b, c in clamps])


def p3_clamp_step_cases():
    """Spec 5.2 at 1d1fae8d2aad1a38 (user, 2026-10-13) in P3 clause 2: a
    frame from above the cap to the cap less 0.1 A is one clamp plus one
    0.1 A down step taken from the cap, held to the 0.3 s like any step and
    timing the next; shown against the rule before it (`_clamp_step`
    False)."""
    def run(series, veh="", **kw):
        return R._hold_follows_loads(series, veh, **kw)

    h0 = 50000
    caps = [(0, 16.0), (h0 + 400, 6.0)]

    def walk(t, a):
        return _sp_path(t, 500000, a, [(t, 2.0), (C0, 2.9)])
    # entered at 6.10 A, the cap falls to 6.00 A 400 ms in with a step due:
    # 5.90 A, then 0.1 A steps from 300 ms after it
    comb = _with_cap(_hs(sp=[(h0, 6.1), (h0 + 400, 5.9)] +
                         walk(h0 + 700, 5.9)), caps)
    ok, why = run(comb)
    check(ok and "1 of them with a down step taken from the cap" in why,
          "clause 2: 6.10 -> 5.90 A as the cap falls to 6.00 A, then steps "
          "300 ms apart, passes as a clamp plus a step -> %s" % why)
    ok, why = run(comb, _clamp_step=False)
    check(not ok and "2:" in why and "-0.20 A step" in why,
          "...and under the rule before 1d1fae8d it is a -0.20 A step -> %s"
          % why)
    deep = _with_cap(_hs(sp=[(h0, 6.1), (h0 + 400, 5.8)] +
                         walk(h0 + 700, 5.8)), caps)
    ok, why = run(deep)
    check(not ok and "2:" in why and "-0.30 A step" in why,
          "clause 2: 6.10 -> 5.80 A (the cap less 0.2 A) is neither a clamp "
          "nor a clamp plus a step -> %s" % why)
    soon = _with_cap(_hs(sp=[(h0, 6.1), (h0 + 400, 5.9)] +
                         walk(h0 + 600, 5.9)), caps)
    ok, why = run(soon)
    check(not ok and "2:" in why and "steps 200 ms apart" in why,
          "clause 2: a step 200 ms after the combined frame fails, the "
          "cadence timed from it -> %s" % why)
    early = _with_cap(_hs(sp=[(h0, 6.3), (h0 + 300, 6.2), (h0 + 400, 5.9)] +
                          walk(h0 + 700, 5.9)), caps)
    ok, why = run(early)
    check(not ok and "2:" in why and "steps 100 ms apart" in why,
          "clause 2: a combined frame 100 ms after a step fails: its step "
          "is held to the 0.3 s too -> %s" % why)
    # the trace reader on the trace built for it: one combined frame
    s = R.hold_series_from_trace(TRACES / "syn_hold_cap_clamp_step.trace")
    pairs = list(zip(s["sp"], s["sp"][1:], s["spcap"][1:])) if s else []
    comb_t = [(tb, a, b, cap) for (_ta, a), (tb, b), (_tc, cap) in pairs
              if cap is not None and a > cap + 0.025
              and abs(b - (cap - 0.1)) <= 0.025]
    clamp_t = [(tb, a, b, cap) for (_ta, a), (tb, b), (_tc, cap) in pairs
               if cap is not None and a > cap + 0.025
               and abs(b - cap) <= 0.025]
    check([(t, round(a, 2), round(b, 2)) for t, a, b, _c in comb_t]
          == [(121400, 6.1, 5.9)] and
          [round(c, 2) for _t, _a, _b, c in clamp_t] == [3.0],
          "hold_series_from_trace on syn_hold_cap_clamp_step: one clamp plus "
          "a step (6.10 -> 5.90 A at 121.4 s) and one clamp to 3.00 A: %s / "
          "%s" % (comb_t, clamp_t))


def p3_walk_down_cases():
    """Spec 826925f461768701 clause 1 (user, 2026-10-13), read per the
    reviewer's rulings the same day: after an injected load drop every
    frame is exempt from the drop until the first page-01 frame on which
    the setpoint holds or steps up (that frame is judged), each down step
    within 0.3 s plus one page-01 frame (inclusive) of the drop or the
    step before it; and one count of bcm_ibat (0.025 A) over chg_max
    passes. Each shown against the rule before it (`_walk_down` /
    `_one_count` False)."""
    def run(series, veh="", **kw):
        return R._hold_follows_loads(series, veh, **kw)

    def walk_sp(missing=(), up_at=None):
        """Page 01 every 300 ms from 50 s: up to 2.9 A from C0, then from
        STEP_T down 0.1 A on every PRESENT frame to 2.0 A (frames in
        `missing` are not sent), or one 0.1 A step UP at up_at after which
        it holds."""
        out, a, t, up = [], 2.0, 50000, False
        while t <= 500000:
            if t not in missing:
                if C0 <= t < STEP_T and a < 2.9 - 1e-6:
                    a = round(a + 0.1, 2)
                elif t >= STEP_T and not up:
                    if t == up_at:
                        a, up = round(a + 0.1, 2), True
                    elif a > 2.0 + 1e-6:
                        a = round(a - 0.1, 2)
                out.append((t, a))
            t += 300
        return out

    base_ib = _ib()
    # frames at 50 s + k * 300 ms: the first after the drop at 300.0 s is
    # 300.2 s; nine steps to 2.0 A end on the hold frame at 302.9 s
    ok, why = run(_hs(sp=walk_sp(), ibat=base_ib + [(302800, 0.5)]),
                  DROP_LINE)
    check(ok and "ended at t=302.9 s (a hold frame)" in why,
          "above chg_max on the last frame of an on-time walk-down passes "
          "-> %s" % why)
    ok, why = run(_hs(sp=walk_sp(), ibat=base_ib + [(302900, 0.5)]),
                  DROP_LINE)
    check(not ok and "1: pack current above" in why
          and "first at t=302.9 s" in why,
          "...ON the hold frame fails: it is judged, not exempt -> %s" % why)
    # the walk-down opens WALK_LEAD_MS (10 ms) before the placed drop (user,
    # 2026-10-23, c40b): the drop is placed at 300.000 s here
    ok, why = run(_hs(sp=walk_sp(), ibat=base_ib + [(299990, 0.5)]),
                  DROP_LINE)
    check(ok, "above chg_max 10 ms before the placed drop passes (the "
          "witness's stamp jitter) -> %s" % why)
    ok, why = run(_hs(sp=walk_sp(), ibat=base_ib + [(299990, 0.5)]),
                  DROP_LINE, _walk_lead=False)
    check(not ok and "1: pack current above" in why,
          "...and fails under the rule before (the walk-down opening at the "
          "placed drop) -> %s" % why)
    ok, why = run(_hs(sp=walk_sp(), ibat=base_ib + [(299989, 0.5)]),
                  DROP_LINE)
    check(not ok and "1: pack current above" in why,
          "can fail: above chg_max 11 ms before the placed drop fails -> %s"
          % why)
    ok, why = run(_hs(sp=walk_sp(), ibat=base_ib + [(299900, 0.5)]),
                  DROP_LINE)
    check(not ok and "1: pack current above" in why,
          "can fail: above chg_max one 0x410 period (100 ms) before the "
          "placed drop fails -> %s" % why)
    ok, why = run(_hs(ibat=base_ib + [(301000, 0.5)]), DROP_LINE)
    check(not ok and "1: pack current above" in why
          and "(a hold frame)" in why,
          "a setpoint that holds on the first frame after the drop ends the "
          "walk at once: 1 s after the drop fails -> %s" % why)
    ok, why = run(_hs(ibat=base_ib + [(301000, 0.5)]), DROP_LINE,
                  _walk_down=False)
    check(ok, "...where the rule before (a fixed 2 s) exempted it -> %s"
          % why)
    # one late step: frames 301.1 and 301.4 s not sent, so the step after
    # 300.8 s comes 0.9 s later, past 0.3 s + one frame
    late = walk_sp(missing=(301100, 301400))
    ok, why = run(_hs(sp=late, ibat=base_ib + [(301500, 0.5)]), DROP_LINE)
    check(not ok and "1: pack current above" in why
          and "no down step within 0.6 s of the last step" in why,
          "a walk-down with one step 0.9 s late ends 0.6 s after the step "
          "before it: above chg_max at 301.5 s fails -> %s" % why)
    ok, why = run(_hs(sp=late, ibat=base_ib + [(301400, 0.5)]), DROP_LINE)
    check(ok, "...and at 301.4 s, exactly 0.6 s after that step, passes "
          "(inclusive) -> %s" % why)
    one = walk_sp(missing=(301100,))
    ok, why = run(_hs(sp=one, ibat=base_ib + [(302900, 0.5)]), DROP_LINE)
    check(ok and "ended at t=303.2 s (a hold frame)" in why,
          "a step exactly 0.3 s + one frame after the last (one frame not "
          "sent) keeps the walk going -> %s" % why)
    up = walk_sp(up_at=301400)
    ok, why = run(_hs(sp=up, ibat=base_ib + [(301300, 0.5)]), DROP_LINE)
    check(ok, "above chg_max just before an UP step mid-walk passes -> %s"
          % why)
    ok, why = run(_hs(sp=up, ibat=base_ib + [(301400, 0.5)]), DROP_LINE)
    check(not ok and "(an up step)" in why and "first at t=301.4 s" in why,
          "...and on the up-step frame fails: an up step ends the walk "
          "-> %s" % why)
    print("  clause 1's tolerance, one bcm_ibat count")
    for over, passes in ((0.025, True), (0.050, False)):
        ok, why = run(_hs(ibat=base_ib + [(C0 + 100000, over)]))
        check(ok == passes and (passes or "by more than one count" in why),
              "pack current +%.3f A over chg_max 0 %s -> %s"
              % (over, "passes" if passes else "fails", why))
    ok, why = run(_hs(ibat=base_ib + [(C0 + 100000, 0.025)]),
                  _one_count=False)
    check(not ok and why.startswith("1:"),
          "...and +0.025 A fails under the rule before (no tolerance) -> %s"
          % why)
    ok, why = run(_hs(ibat=base_ib + [(C0 + 100000, 0.026)]))
    check(not ok, "+0.026 A (a reading off the count grid) fails -> %s"
          % why)


def p3_wire_anchor_cases():
    """The injected load steps placed by the wire anchor (reviewer,
    2026-10-13): each step's sim time mapped onto the trace by the 0x410
    on which bcm_soc first reads 100, and cross-checked against a bcm_ibat
    step of its sign within 0.6 s. Shown against the rule before it
    (`_wire_anchor` False: the trace's first chg_max 0 plus D), on P3 at
    E6a's shape, where the two anchors are 13.7 s apart."""
    def run(series, veh, **kw):
        return R._hold_follows_loads(series, veh, **kw)

    # vehicle_sim's internal chg_max reaches 0 13.7 s after the encoded
    # one, so D = 200 s lands at sim 913.7 s -> trace C0 + 213.7 s
    t_drop = C0 + 213700
    line = (SOC100_LINE + "\n12 V load step: 266.0 -> 136.0 W at t=913.7s "
            "(+200.0s after chg_max reached 0 A)")

    def drop_ib(at, peak=0.5, walk_ms=1500):
        """-0.1 A until `at`, then `peak` A falling linearly to 0 over
        walk_ms (100 ms frames), then -0.1 A."""
        out = [(t, -0.1) for t in range(50000, at, 1000)]
        n = walk_ms // 100
        out += [(at + k * 100, round(peak * (1 - k / n), 3))
                for k in range(n + 1)]
        out += [(t, -0.1) for t in range(at + walk_ms + 1000, 500001, 1000)]
        return out

    ok, why = run(_hs(ibat=drop_ib(t_drop), walk_at=t_drop), line)
    check(ok and "offset -600.000 s" in why,
          "a drop placed by bcm_soc 100, walked to 0 A in 1.5 s, passes "
          "-> %s" % why)
    ok, why = run(_hs(ibat=drop_ib(t_drop), walk_at=t_drop), line,
                  _wire_anchor=False)
    check(not ok and why.startswith("1:")
          and "first at t=%.1f s" % (t_drop / 1000.0) in why,
          "...and fails under the rule before (chg_max 0 plus D puts the "
          "exemption 13.7 s early) -> %s" % why)
    s = _hs(ibat=drop_ib(t_drop, walk_ms=3200), walk_at=t_drop)
    hold_t = next(tb for (_ta, a), (tb, b) in zip(s["sp"], s["sp"][1:])
                  if tb > t_drop and abs(b - a) < 0.025)
    ok, why = run(s, line)
    check(not ok and "1: pack current above" in why
          and "first at t=%.1f s" % (hold_t / 1000.0) in why,
          "pack current still over when the setpoint's walk-down ends fails, "
          "from its hold frame (t=%.1f s) -> %s" % (hold_t / 1000.0, why))
    ok, why = run(_hs(ibat=drop_ib(t_drop + 500), walk_at=t_drop), line)
    check(ok, "the bcm_ibat step 0.5 s after the mapped time is inside the "
          "0.6 s cross-check -> %s" % why)
    ok, why = run(_hs(ibat=drop_ib(t_drop + 1000), walk_at=t_drop), line)
    check(not ok and "no bcm_ibat rise" in why and "1: pack current above"
          in why, "...1.0 s after it fails the cross-check, and the drop "
          "exempts nothing -> %s" % why)
    small = [(t, 0.05 if t >= t_drop else -0.02)
             for t in range(50000, 500001, 100)]
    ok, why = run(_hs(ibat=small), line)
    check(not ok and "no bcm_ibat rise" in why,
          "a bcm_ibat change of 0.07 A is not the drop's step -> %s" % why)
    falling = _ib(at=t_drop, before=-0.1, after=-0.6)
    ok, why = run(_hs(ibat=falling), line)
    check(not ok and "no bcm_ibat rise" in why,
          "a drop whose bcm_ibat FALLS fails the cross-check (wrong sign) "
          "-> %s" % why)
    no_soc = line.replace(SOC100_LINE + "\n", "")
    ok, why = run(_hs(ibat=drop_ib(t_drop)), no_soc)
    check(not ok and "no wire anchor" in why and "logged no bcm_soc 100"
          in why, "a step with no bcm_soc 100 in the vehicle log cannot be "
          "placed, and fails -> %s" % why)
    ok, why = run(_hs(ibat=drop_ib(t_drop), soc100=None), line)
    check(not ok and "no wire anchor" in why and "no 0x410 on the trace"
          in why, "...nor with no bcm_soc 100 on the trace -> %s" % why)
    ok, why = run(_hs(soc100=None), "")
    check(ok, "a run with no injected step needs no anchor -> %s" % why)
    # the trace reader's anchor: the FIRST 0x410 reading 100, not the first
    # 0x410 (unit_random_calm_seed2 sends 37 below 100 first)
    s = R.hold_series_from_trace(TRACES / "unit_random_calm_seed2.trace")
    check(s is not None and s["soc100"] == 3862,
          "hold_series_from_trace finds bcm_soc 100 at t=3862 ms on "
          "unit_random_calm_seed2 -> %s" % (s and s["soc100"]))
    s = R.hold_series_from_trace(TRACES / "syn_override_release.trace")
    check(s is not None and s["soc100"] is None,
          "...and none on syn_override_release, below 100 throughout -> %s"
          % (s and s["soc100"]))
    check(len(R.AUX_STEP_AT.findall(line)) == len(R.AUX_STEP.findall(line))
          == 1, "AUX_STEP_AT reads the same vehicle_sim line as AUX_STEP")


def keep_trace_cases():
    """--keep-traces (2026-10-12): after a clean L1 replay the trace is
    kept with keep=True and deleted without it, on a copy of a small
    differential trace."""
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(dir=str(HERE))
    try:
        src = TRACES / "unit_no_trip_control.trace"
        for keep in (True, False):
            tr = Path(tmp) / ("keep_%s.l1.trace" % keep)
            shutil.copyfile(str(src), str(tr))
            ok, why = R.check_flight_core(tr, keep=keep)
            check(ok and tr.exists() == keep and
                  ("trace kept" if keep else "trace deleted") in why,
                  "a clean L1 replay with keep=%s %s the trace -> %s"
                  % (keep, "keeps" if keep else "deletes", why))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def p3a_cases():
    """Spec 9, P3a (user, 2026-10-10): each of the three checks run against
    a wrong simulation must fail on its own number, and the sims as built
    must pass. The wrong ones replace charger_sim / vehicle_sim's three
    functions only."""
    from types import SimpleNamespace as NS
    import charger_sim as CS
    import vehicle_sim as VS
    ok, why = R._p3a_check()
    check(ok and "8 holds inside" in why and "delivers 0" in why,
          "P3a passes the sims as built -> %s" % why[:160])
    names = [s.name for s in R.SCENARIOS
             if "p3a" in [k for k, _v in s.expect]]
    check(names == ["evap-override-imbalanced-hold"]
          and "hold_follows_loads" in [k for k, _v in
                                       R.BY_NAME[names[0]].expect],
          "exactly the P3 scenario asks for P3a and the hold checks (%s)"
          % names)

    def run(cs=CS, vs=VS):
        return R._p3a_check(cs, vs)
    today_cs = NS(mode3_report_a=CS.mode3_report_a,
                  delivered_a=lambda r: r)
    today_vs = NS(hv_draw_a=lambda p12, v: 2.4, P12_PARTS_W=VS.P12_PARTS_W)
    ok, why = run(today_cs, today_vs)
    check(not ok and "1:" in why and "3:" in why,
          "today's model (delivered = reported, a flat 2.4 A draw) fails "
          "checks 1 and 3 -> %s" % why[:200])
    ok, why = run(NS(mode3_report_a=lambda sp: CS.delivered_a(
        CS.mode3_report_a(sp)), delivered_a=CS.delivered_a))
    check(not ok and "2:" in why,
          "a charger that reports what it delivers fails check 2 -> %s"
          % why[:160])
    ok, why = run(vs=NS(hv_draw_a=lambda p12, v: VS.hv_draw_a(p12, v) + 0.2,
                        P12_PARTS_W=VS.P12_PARTS_W))
    check(not ok and "1:" in why and "2:" not in why,
          "a fixed HV draw 0.2 A too high fails check 1 alone -> %s"
          % why[:160])
    ok, why = run(NS(mode3_report_a=CS.mode3_report_a,
                     delivered_a=lambda r: max(CS.delivered_a(r), 0.3)))
    check(not ok and "3: at a 0 A setpoint in mode 3 the charger delivers"
          in why, "a charger delivering 0.3 A at a 0 A setpoint fails check "
          "3 -> %s" % why[:200])
    ok, why = run(vs=NS(hv_draw_a=lambda p12, v: 0.0,
                        P12_PARTS_W=VS.P12_PARTS_W))
    check(not ok and "must drain" in why,
          "a truck with no HV draw (the pack would not drain) fails check 3 "
          "-> %s" % why[-160:])


def bypass_diag_cases():
    """Spec 9 at c383868a834b6158 (user, 2026-10-10): the diag check is
    waived only for a scenario DECLARING --bypass; one that declares nothing
    and shows no diagnostic frames fails."""
    bypassed = [sc.name for sc in R.SCENARIOS if "--bypass" in sc.interposer]
    check(bypassed == ["evap-bypass"],
          "exactly one scenario declares --bypass: %s" % bypassed)

    def fake(name, interposer):
        return R.Scenario(name, "", vehicle=[], interposer=interposer)
    ok, why = R._diag_check(fake("x", ["--bypass"]), [], "PASSTHROUGH")
    check(ok and "not applicable" in why,
          "a scenario declaring --bypass with no diag frames: not "
          "applicable -> %s" % why)
    ok, why = R._diag_check(fake("x", []), [], "PASSTHROUGH")
    check(not ok and "none seen" in why,
          "a scenario declaring nothing with no diag frames FAILS -> %s"
          % why)
    ok, why = R._diag_check(fake("evap-bypass", []), [], "PASSTHROUGH")
    check(not ok and "none seen" in why,
          "...even one NAMED evap-bypass: the exemption is the declaration, "
          "not the name -> %s" % why)
    ok, why = R._diag_check(fake("x", ["--other"]), _diag(), "PASSTHROUGH")
    check(ok, "a scenario declaring nothing, with clean frames, passes -> %s"
          % why)


def listen_only_diag_cases():
    """Spec 2.3 (d9f49ea6, user 2026-10-13) in the diag check, as the
    reviewer accepted it: the last 0x7F4 is judged against the core's state
    when it was sent -- for a run that ends in listen-only, the state at the
    last fall-back -- and a status frame the witness saw outside normal
    mode fails."""
    safe = _diag(state=5)
    to_listen = ("10:00:00 intp VPORT normal: charger frame 0x18FFD4C0 at sim "
                 "t=1.000s (core state PASSTHROUGH)\n"
                 "10:30:00 intp VPORT listen-only: no charger frame for 2010 "
                 "ms at sim t=1800.000s (core state SAFE)\n"
                 "10:30:05 intp vport: 1 switch(es) to normal, 1 back to "
                 "listen-only, 0 frame(s) toward the vehicle dropped in "
                 "listen-only, ending listen-only (spec 2.3)\n")
    wit0 = ("10:30:05 veh vport witness: 7200 status frame(s) in normal mode,"
            " 4 in the power-up round, 0 outside (spec 2.3)\n")
    ok, why = R._diag_frames_ok(safe, "PASSTHROUGH", intp=to_listen,
                                veh=wit0)
    check(ok and "when the port went listen-only" in why
          and "0 outside" in why and "0 frame(s) toward the vehicle" in why,
          "last 0x7F4 SAFE, the core SAFE at the fall-back, PASSTHROUGH at "
          "the end: passes -> %s" % why)
    ok, why = R._diag_frames_ok(safe, "PASSTHROUGH")
    check(not ok and "at the end of the run" in why,
          "...and fails judged against the final state (the rule before "
          "d9f49ea6, no VPORT lines) -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(state=1), "PASSTHROUGH",
                                intp=to_listen, veh=wit0)
    check(not ok and "the core was SAFE when the port went listen-only"
          in why,
          "last 0x7F4 MONITOR with the core SAFE at the fall-back fails -> %s"
          % why)
    to_normal = to_listen + ("10:31:00 intp VPORT normal: charger frame "
                             "0x18FFD4C0 at sim t=1860.000s (core state "
                             "PASSTHROUGH)\n")
    ok, why = R._diag_frames_ok(safe, "PASSTHROUGH", intp=to_normal,
                                veh=wit0)
    check(not ok and "at the end of the run" in why,
          "a run that ends in normal mode is judged against its final state "
          "-> %s" % why)
    wit1 = ("10:30:05 veh vport witness: 7200 status frame(s) in normal mode,"
            " 4 in the power-up round, 1 outside (spec 2.3); first outside "
            "0x7F4 at t=1805.0\n")
    ok, why = R._diag_frames_ok(safe, "PASSTHROUGH", intp=to_listen,
                                veh=wit1)
    check(not ok and "1 status frame(s) on the vehicle segment while the "
          "port should be listen-only" in why and "1805.0" in why,
          "ONE status frame outside normal mode fails -> %s" % why)
    fake = R.Scenario("x", "", vehicle=[], interposer=[])
    ok, why = R._diag_check(fake, safe, "PASSTHROUGH", intp=to_listen,
                            veh=wit1)
    check(not ok and "listen-only" in why,
          "_diag_check passes both logs through -> %s" % why)


def leader_release_cases():
    """Spec 5.1 / 9 (47c31278, user 2026-10-10): the release is on the
    leader (vmax >= 3585 mV); the high-cell scenarios also release before
    the bus SoC reads 100 %, bounded by the first HOLD frame."""
    want = {sc.name: v for sc in R.SCENARIOS for k, v in sc.expect
            if k == "leader_release"}
    check(sorted(n for n, v in want.items() if v)
          == ["evap-override-high-cell", "evap-override-high-cell-far"],
          "exactly the two high-cell scenarios ask for the release before "
          "SoC 100: %s" % sorted(n for n, v in want.items() if v))
    check(want.get("evap-override-imbalanced") is False
          and want.get("evap-override-imbalanced-hold") is False,
          "the low-cell scenarios ask for the release on the leader")
    check(not any("--bms-taper-shift-mv" in sc.vehicle for sc in R.SCENARIOS),
          "no scenario passes the removed --bms-taper-shift-mv")

    def toc(vmax):
        # spec 8.3 fields: a=vmax b=chg_max (cA) c=vmin
        return "top of charge reached a=%d b=7125 c=3440\n" % vmax

    # the TOC key, each way it could be wrong (2026-10-14: one form now)
    check(R.release_vmax_vmin(toc(3585)) == (3585, 3440),
          "TOC: vmax from a=, vmin from c= (b= is chg_max)")
    check(R.release_vmax_vmin("[  99.000] HOLD top of charge reached a=3590 "
                              "b=7125 c=3441\n") == (3590, 3441),
          "TOC: the board's line form is read by the same key")
    check(R.release_vmax_vmin("top of charge reached: vmax=3585 mV vmin=3440 "
                              "mV\n") == (None, None),
          "TOC: the OLD prose form is no longer read")

    def hold_at(t):
        return [(t - 5.0, 0x7F4, bytes([4, 7, 2, 0, 0x20, 61, 0, 0])),
                (t, 0x7F4, bytes([4, 7, 6, 0, 0x20, 60, 0, 0]))]

    def soc100(t):
        return "BMS: bcm_soc reads 100 %% on the bus at t=%.1fs\n" % t

    for label, intp, veh, diag, flag, good, frag in (
            ("no release", "", "", [], False, False, "no release event"),
            ("released at 3584 mV", toc(3584), "", [], False, False,
             "below the leader"),
            ("released at 3585 mV", toc(3585), "", [], False, True,
             "3585 mV"),
            ("before: HOLD at 1000 s, SoC 100 at 1005 s", toc(3586),
             soc100(1005.0), hold_at(1000.0), True, True, "5.0 s before"),
            ("before: HOLD at 1005 s, SoC 100 at 1000 s", toc(3586),
             soc100(1000.0), hold_at(1005.0), True, False, "is not before"),
            ("before: HOLD and SoC 100 at the same moment", toc(3586),
             soc100(1000.0), hold_at(1000.0), True, False, "is not before"),
            ("before: SoC never read 100", toc(3586), "", hold_at(1000.0),
             True, True, "never read 100"),
            ("before: no HOLD frame", toc(3586), soc100(1000.0), [], True,
             False, "no 0x7F4 ever reported HOLD"),
            ("before: but released at 3584 mV", toc(3584), "",
             hold_at(1000.0), True, False, "below the leader")):
        ok, why = R._leader_release_check(intp, veh, diag, flag)
        check(ok == good and frag in why, "%s -> %s: %s"
              % (label, "pass" if ok else "FAIL", why))
    check(R.SOC100.search(soc100(12.5)) is not None,
          "SOC100 reads vehicle_sim's line")


def main():
    print("spec 9: every scenario decodes the diag frames")
    for sc in R.SCENARIOS:
        kinds = [k for k, _v in sc.expect]
        check("diag_frames_ok" in kinds, "%s asks for diag_frames_ok"
              % sc.name)
        if "chg_released_into_hold" in kinds:
            check("diag_no_rewrite_after_release" in kinds,
                  "%s (a termination scenario) asks for "
                  "no_rewrite_after_release" % sc.name)

    print("\nspec 9 (c383868a): a declared --bypass is exempt, nothing else")
    bypass_diag_cases()
    listen_only_diag_cases()

    print("\nspec 5.1 / 9 (47c31278): the release on the leader, before "
          "SoC 100 on the high-cell scenarios")
    leader_release_cases()

    print("\nspec 9, P3a: the simulation against the truck's holds")
    p3a_cases()

    print("\nthe spec's literals, not machine.py's")
    check(R.BURST_FRAMES == 20 and R.BURST_MS == 1000,
          "a burst is 20 frames x 50 ms (got %d, %d ms)"
          % (R.BURST_FRAMES, R.BURST_MS))
    check(R.SPEC_FW_VER == 9 and R.SPEC_SCHEMA_VER == 4,
          "firmware 9, schema 4 -- the schema doc bae9948312c38cb1 (got "
          "%d, %d)" % (R.SPEC_FW_VER, R.SPEC_SCHEMA_VER))
    check(R.DIAG_STATES.get(6) == "HOLD",
          "0x7F4 B2 6 decodes as HOLD (schema 4)")
    src = (HERE / "run_scenario.py").read_text(encoding="utf-8")
    for needle in ("M.DIAG_FW_VER", "M.DIAG_SCHEMA_VER", "burst_frames"):
        check(needle not in src.replace("# ", "#"),
              "run_scenario.py no longer reads %s" % needle)

    print("\ndiag versions in every frame (reading 15)")
    ok, why = R._diag_frames_ok(_diag(), "PASSTHROUGH")
    check(ok, "a clean fw 9 / schema 4 stream passes -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(fw7f7=(9, 8, 9)), "PASSTHROUGH")
    check(not ok and "0x7F7" in why,
          "a MIDDLE 0x7F7 reporting fw 8 (E7's) fails (rev 1 read only the "
          "last) -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(sch7f4=3), "PASSTHROUGH")
    check(not ok and "schema" in why, "0x7F4 schema 3 fails -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(fw7f4=8), "PASSTHROUGH")
    check(not ok and "firmware" in why,
          "0x7F4 firmware 8 (E7's) fails -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(state=2), "PASSTHROUGH")
    check(not ok and "OVERRIDE" in why,
          "a last 0x7F4 disagreeing with the final state fails -> %s" % why)

    print("\n0x7F4 override_active and B5, schema 4 (2026-10-09)")
    ok, why = R._diag_frames_ok(_diag(state=6), "HOLD")
    check(ok, "a HOLD stream with bit 5 set and a setpoint in B5 passes "
          "-> %s" % why)
    ok, why = R._diag_frames_ok(_diag(state=2), "OVERRIDE")
    check(ok, "an OVERRIDE stream likewise -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(state=6, flags=0x00), "HOLD")
    check(not ok and "bit 5" in why,
          "bit 5 clear in HOLD fails (it read 0 before the 2026-10-09 "
          "ruling) -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(state=1, flags=0x20), "MONITOR")
    check(not ok and "bit 5" in why, "bit 5 set in MONITOR fails -> %s"
          % why)
    ok, why = R._diag_frames_ok(_diag(state=4, flags=0x20), "TERMINATED")
    check(not ok and "bit 5" in why, "bit 5 set in TERMINATED fails -> %s"
          % why)
    ok, why = R._diag_frames_ok(_diag(state=6, b5=255), "HOLD")
    check(not ok and "B5 = 255" in why,
          "B5 255 (not commanding) in HOLD fails -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(state=1, b5=29), "MONITOR")
    check(not ok and "B5 = 29" in why,
          "a setpoint in B5 outside OVERRIDE / HOLD fails -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(state=1, flags=0x40), "MONITOR")
    check(ok, "other B4 bits (bridge_ok) do not trip the bit-5 rule -> %s"
          % why)

    print("\nthe core's own diag frames through the scorer (HOLD, schema 4)")
    # The rules above are judged on hand-built streams. Here the stream is
    # machine.py's own packing (host_diff/diag_stream.py, which diff_all
    # shows machine.cpp matches byte for byte) over the two release traces,
    # so the schema-4 HOLD fields are judged on what the core emits, before
    # any live scenario runs.
    import subprocess
    ds = HERE / "firmware" / "test" / "host_diff" / "diag_stream.py"
    for name in ("syn_override_release", "unit_dense_override_release"):
        res = subprocess.run([sys.executable, str(ds),
                              str(TRACES / ("%s.trace" % name))],
                             capture_output=True, text=True)
        diag = []
        for line in res.stdout.splitlines():
            p = line.split()
            if len(p) == 4 and p[0] == "D":
                diag.append((int(p[1]) / 1000.0, int(p[2], 16),
                             bytes.fromhex(p[3])))
        hold = [b for _t, f, b in diag if f == 0x7F4 and b[2] == 6]
        last = [b for _t, f, b in diag if f == 0x7F4]
        fstate = R.DIAG_STATES.get(last[-1][2], "?") if last else "?"
        ok, why = R._diag_frames_ok(diag, fstate)
        check(res.returncode == 0 and len(hold) >= 3 and ok,
              "%s: %d HOLD status frames, bit 5 = %s, B5 = %s; scorer -> %s"
              % (name, len(hold),
                 sorted(set((b[4] >> 5) & 1 for b in hold)),
                 sorted(set(b[5] for b in hold))[:6], why))
        # B5 "the hold setpoint in HOLD" (0.1 A units): equal, within one
        # count, to the last page-01 setpoint sent to the charger at or
        # before that status frame (the golden's E lines, side 1 = toward
        # the charger). One count, because 0.05 A page-01 steps fall
        # between 0.1 A ones.
        import protocol as P
        sent = []
        with open(str(TRACES / ("%s.golden" % name))) as g:
            for line in g:
                p = line.split()
                if (len(p) == 6 and p[0] == "E" and p[2] == "1"
                        and p[3].upper() == "18EFC000"
                        and p[5][:2] == "01"):
                    sent.append((int(p[1]),
                                 P.dec_setpoint(bytes.fromhex(p[5]))[1]))
        bad = n = 0
        for t, f, b in diag:
            if f != 0x7F4 or b[2] != 6:
                continue
            tm = int(round(t * 1000))
            # A status frame and a page 01 in the SAME millisecond have no
            # order in the trace, so either setpoint is accepted there: the
            # last one sent before that millisecond, or the one sent in it.
            cands = [a for ts, a in sent if ts == tm]
            before = [a for ts, a in sent if ts < tm]
            if before:
                cands.append(before[-1])
            if not cands:
                continue
            n += 1
            if all(abs(b[5] - a * 10.0) > 1.0 + 1e-6 for a in cands):
                bad += 1
        check(n >= 3 and bad == 0,
              "%s: B5 in HOLD equals the page-01 setpoint sent (%d frames "
              "compared, %d off by more than 0.1 A)" % (name, n, bad))

    print("\nspec 9 hold on the parts truck: hold_follows_loads (2026-10-09)")
    hold_follows_loads_cases()

    print("\nspec 9 at 96dd3703: clause 2's clamp, clause 3's later anchor")
    p3_anchor_cases()
    p3_clamp_step_cases()

    print("\nspec 9: the load steps placed by bcm_soc 100 (reviewer, "
          "2026-10-13)")
    p3_wire_anchor_cases()

    print("\nspec 826925f4: clause 1's walk-down and one-count "
          "tolerance")
    p3_walk_down_cases()

    print("\n--keep-traces: the L1 replay keeps the trace only when asked")
    keep_trace_cases()

    print("\ncheck_invariants over a scenario trace")
    ok, why = R.check_invariants(TRACES / "no_such_scenario.l1.trace")
    check(not ok, "a missing trace FAILS -> %s" % why)
    good = TRACES / "syn_override_release.trace"
    ok, why = R.check_invariants(good)
    # "no page-01 judged" as a whole number: a plain substring test read
    # "420 override page-01" as zero (2026-10-10, once E6's earlier release
    # put 420 there)
    import re as _re

    def judged_none(text):
        return _re.search(r"(?<!\d)0 override page-01", text) is not None
    check(judged_none("14546 steps, 0 override page-01 frames judged")
          and not judged_none("14546 steps, 420 override page-01 frames "
                              "judged"),
          "the zero-page-01 test reads 0 as zero and 420 as not")
    check(ok and not judged_none(why),
          "a known-good trace passes, having judged page-01 frames -> %s"
          % why)
    check(good.exists(), "and check_invariants did not delete it")

    print("\nthe 16 A scenario")
    sc16 = [s for s in R.SCENARIOS if s.name == "evap-override-16a"]
    check(len(sc16) == 1, "evap-override-16a exists")
    if sc16:
        kinds = [k for k, _v in sc16[0].expect]
        check("veh_current_max" not in kinds,
              "it no longer scores the charger sim's clamped current")

    print("\nspec 8.3 event keys (2026-10-14): TRIP codes, boot lock")
    # TRIP_CODE must be the PUBLISHED table: intp_trip_reason's comment in
    # the diag DBC, parsed here rather than trusted
    import cantools
    import paths
    db = cantools.database.load_file(
        os.path.join(paths.data_dir(), "vtrux-interposer-diag.dbc"))
    cm = db.get_message_by_frame_id(0x7F4).get_signal_by_name(
        "intp_trip_reason").comment
    bad = [k for k, c in R.TRIP_CODE.items() if (" %d %s" % (c, k)) not in cm]
    check(not bad, "TRIP_CODE agrees with intp_trip_reason's comment in the "
                   "diag DBC%s" % ((": not found " + repr(bad)) if bad else ""))
    import re as _re
    check(_re.search(R.trip("vmax over hard ceiling"), "[SAFE] TRIP a=1 b=0 c=0")
          and not _re.search(R.trip("vmax over hard ceiling"),
                             "[SAFE] TRIP a=10 b=1040 c=0"),
          "trip(): code 1 matches a=1 and not a=10 (BMS frames stale)")
    check(not _re.search(R.trip("charger fault"), "[SAFE] TRIP: charger fault"),
          "trip(): the OLD prose 'TRIP: charger fault' is no longer read")
    ok, why = R._fault_or_flow_first(
        "10:00:00 intp [SAFE] TRIP: charger fault\n", "", "", [], False,
        (600000, 600450))
    check(not ok and "neither" in why,
          "the fault-or-flow check does not read the old prose TRIP -> %s"
          % why)
    check(_re.search(R.BOOT_LOCKED, "[MONITOR] booted into a session already "
                     "running: PASSTHROUGH until the next session boundary "
                     "a=30 b=0 c=0")
          and not _re.search(R.BOOT_LOCKED, "booted into a session already "
                             "30 min old"),
          "BOOT_LOCKED reads the shared name with a=, not the old prose")

    print("\nfault vs flow drop, either order (reviewer, 2026-10-08)")
    for name in ("charger-fault-stop", "charger-fault-stop-flow-first",
                 "fault-during-override", "fault-during-override-flow-first"):
        sc = [s for s in R.SCENARIOS if s.name == name]
        kinds = [k for k, _v in sc[0].expect] if sc else []
        check(sc and "fault_or_flow_first" in kinds
              and ("intp_event", R.trip("charger fault")) not in sc[0].expect,
              "%s judges either order and no longer demands the trip" % name)

    def diag_frozen(state, mods):
        st = {"SAFE": 5, "TERMINATED": 4}[state]   # run_scenario.DIAG_STATES
        rows = [(0.0, 0x7F4, bytes([3, 4, 1, 0, 0, 0, 0, 0]))]
        rows.append((10.0, 0x7F4, bytes([3, 4, st, 0, 0, 0, 0, 0])))
        for i, m in enumerate(mods):
            rows.append((10.0 + i, 0x7F6,
                         m.to_bytes(4, "little") + bytes(4)))
        return rows
    intp_trip = ("10:00:00 intp [SAFE] TRIP a=8 b=0 c=0\n"
                 "10:00:01 intp [PASSTHROUGH] session over a=2 b=0 c=0\n")
    intp_flow = ("10:00:00 intp [TERMINATED] VCU dropped the flow bit: "
                 "transparent a=0 b=0 c=0\n"
                 "10:00:01 intp [PASSTHROUGH] session over a=2 b=0 c=0\n")
    fz_safe = diag_frozen("SAFE", [5, 5, 5])
    fz_term = diag_frozen("TERMINATED", [5, 5, 5])
    flag_first, drop_first = (600000, 600450), (600500, 600450)
    F = R._fault_or_flow_first
    ok, why = F(intp_trip, "", "", fz_safe, False, flag_first)
    check(ok and why.startswith("trip first"),
          "trip first, with the flag reaching the core first, passes -> %s"
          % why)
    ok, why = F(intp_trip, "", "", fz_safe, False, drop_first)
    check(not ok and "flow drop reached it first" in why,
          "a trip although the flow drop reached the core first FAILS -> %s"
          % why)
    ok, why = F(intp_flow, "", "", fz_term, False, drop_first)
    check(ok and why.startswith("flow drop first"),
          "transparent first, with the drop reaching the core first, passes "
          "-> %s" % why)
    ok, why = F(intp_flow, "", "", fz_term, False, flag_first)
    check(not ok and "fault flag reached it first" in why,
          "transparent although the fault flag reached the core first FAILS "
          "-> %s" % why)
    ok, why = F(intp_flow + "10:00:02 intp [SAFE] TRIP a=8 b=0 c=0\n",
                "", "", fz_term, False, drop_first)
    check(not ok and "TRIP after" in why,
          "a trip after going transparent FAILS -> %s" % why)
    ok, why = F(intp_trip, "", "", diag_frozen("SAFE", [5, 6, 7]), False,
                flag_first)
    check(not ok and "modified moved" in why,
          "rewriting after the trip FAILS -> %s" % why)
    ok, why = F(intp_trip, "", "", fz_safe, True, flag_first)
    check(not ok and "repeat" in why,
          "in an override, a trip with no 4.1 repeat FAILS -> %s" % why)
    ok, why = F(intp_flow, "", "", fz_term, True, drop_first)
    check(ok and "no repeat needed" in why,
          "in an override, transparent first needs no repeat -> %s" % why)
    ok, why = F(intp_trip, "", "", fz_safe, False, None)
    check(not ok and "input order is unknown" in why,
          "with no flight trace the order is not guessed: FAILS -> %s" % why)
    # the flow drop in the shared form (reviewer, 2026-10-14): c336a7aa
    # printed it with no fields when all three were 0, and the substring
    # key passed it
    bare = ("10:00:00 intp [TERMINATED] VCU dropped the flow bit: override "
            "ended, transparent\n")
    new = ("10:00:00 intp [TERMINATED] VCU dropped the flow bit: session "
           "ending, transparent a=0 b=0 c=0\n")
    check(re.search(R.FLOW_DROP, bare) is None,
          "FLOW_DROP refuses the field-less line c336a7aa printed")
    check(re.search(R.FLOW_DROP, new) is not None,
          "...and takes the shared form with zeros (f7451d5's text)")
    ok, why = F(bare + "10:00:01 intp [PASSTHROUGH] session over a=2 b=0 "
                "c=0\n", "", "", fz_term, False, drop_first)
    check(not ok and "neither" in why,
          "a field-less flow drop is no flow drop to the order check: "
          "FAILS -> %s" % why)
    ok, why = F(new, "", "", fz_term, False, drop_first)
    check(ok and why.startswith("flow drop first"),
          "the shared form with the new text passes -> %s" % why)

    # the order comes from the core's own flight trace, one clock
    import tempfile
    tmp = tempfile.mkdtemp(dir=str(HERE))
    try:
        import protocol as P
        tr = os.path.join(tmp, "x.l1.trace")
        flagged = P.encode(P.BEL, 0x18FFD4C0, {"BELINV_inverterFault": 1},
                           "BELINV_statusMultiplexer", 3)
        clear = P.encode(P.BEL, 0x18FFD4C0, {},
                         "BELINV_statusMultiplexer", 3)
        rows = ["V 1000 18EFC000 1 %s" % P.enc_master(1, 1).hex(),
                "C 1100 18FFD4C0 1 %s" % bytes(clear).hex(),
                "C 1500 18FFD4C0 1 %s" % bytes(flagged).hex(),
                "V 1700 18EFC000 1 %s" % P.enc_master(0, 1).hex(),
                "T 1800"]
        with open(tr, "w") as f:
            f.write("\n".join(rows) + "\n")
        got = R.fault_and_drop_order(tr)
        check(got == (1500, 1700),
              "fault_and_drop_order reads the first flagged mux-3 page and "
              "the first flow 1 -> 0 off the trace (%s)" % (got,))
        with open(tr, "w") as f:
            f.write("\n".join(rows[:2] + rows[3:]) + "\n")
        got = R.fault_and_drop_order(tr)
        check(got == (None, 1700), "a trace with no flagged page gives no "
              "flag time (%s)" % (got,))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\ntest_scorer_wiring: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
