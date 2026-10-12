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


def _diag(fw7f4=7, sch7f4=4, fw7f7=(7, 7, 7), sch7f7=4, state=0,
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
DROP_LINE = ("12 V load step: 266.0 -> 136.0 W at t=900.0s (+200.0s after "
             "chg_max reached 0 A)")


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


def _hs(hold=(50000, 500000), chg0=C0, sp=None, ibat=None, chg_after=None):
    """A series in hold_series_from_trace()'s shape: chg_max 2.0 A, then
    0 A from chg0 (None: never); pack current -0.1 A every second; the
    setpoint 2.0 A walking up to 2.9 A from chg0."""
    h0, h1 = hold
    chg = [(0, 2.0)] + ([(chg0, 0.0)] if chg0 is not None else [])
    if chg_after:
        # a 0x420 every 100 ms, as on a trace, with extra (t, A) changes
        pts = sorted(chg + list(chg_after))
        chg = [(t, [a for tp, a in pts if tp <= t][-1])
               for t in range(0, h1 + 1, 100)]
    return dict(hold=[hold], chgmax=chg,
                ibat=ibat if ibat is not None else
                [(t, -0.1) for t in range(h0, h1 + 1, 1000)],
                sp=sp if sp is not None else
                _sp_path(h0, h1, 2.0, [(chg0 or h1 + 1, 2.9)]),
                t_last=h1)


def hold_follows_loads_cases():
    def run(series, veh=""):
        return R._hold_follows_loads(series, veh)

    ok, why = run(_hs())
    check(ok, "a hold that settles at 2.9 A 2.7 s after chg_max 0 passes "
          "-> %s" % why)
    base_ib = [(t, -0.1) for t in range(50000, 500001, 1000)]
    ok, why = run(_hs(ibat=base_ib + [(C0 + 201000, 0.5)]), DROP_LINE)
    check(ok, "pack current above chg_max 1 s after an injected load DROP "
          "passes (the 2 s exception) -> %s" % why)
    ok, why = run(_hs(ibat=base_ib + [(C0 + 202500, 0.5)]), DROP_LINE)
    check(not ok and why.startswith("1:"),
          "...2.5 s after the drop fails on clause 1 -> %s" % why)
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
    ok, why = run(_hs(ibat=base_ib + [(C0 + 201000, 0.5)]), up_line)
    check(not ok and "1:" in why,
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
        return "top of charge reached: vmax=%d mV vmin=3440 mV\n" % vmax

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

    print("\nspec 5.1 / 9 (47c31278): the release on the leader, before "
          "SoC 100 on the high-cell scenarios")
    leader_release_cases()

    print("\nspec 9, P3a: the simulation against the truck's holds")
    p3a_cases()

    print("\nthe spec's literals, not machine.py's")
    check(R.BURST_FRAMES == 20 and R.BURST_MS == 1000,
          "a burst is 20 frames x 50 ms (got %d, %d ms)"
          % (R.BURST_FRAMES, R.BURST_MS))
    check(R.SPEC_FW_VER == 7 and R.SPEC_SCHEMA_VER == 4,
          "firmware 7, schema 4 -- the schema doc c42bc96535aed3b2 (got "
          "%d, %d)" % (R.SPEC_FW_VER, R.SPEC_SCHEMA_VER))
    check(R.DIAG_STATES.get(6) == "HOLD",
          "0x7F4 B2 6 decodes as HOLD (schema 4)")
    src = (HERE / "run_scenario.py").read_text(encoding="utf-8")
    for needle in ("M.DIAG_FW_VER", "M.DIAG_SCHEMA_VER", "burst_frames"):
        check(needle not in src.replace("# ", "#"),
              "run_scenario.py no longer reads %s" % needle)

    print("\ndiag versions in every frame (reading 15)")
    ok, why = R._diag_frames_ok(_diag(), "PASSTHROUGH")
    check(ok, "a clean fw 7 / schema 4 stream passes -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(fw7f7=(7, 6, 7)), "PASSTHROUGH")
    check(not ok and "0x7F7" in why,
          "a MIDDLE 0x7F7 reporting fw 6 (E5's) fails (rev 1 read only the last) "
          "-> %s" % why)
    ok, why = R._diag_frames_ok(_diag(sch7f4=3), "PASSTHROUGH")
    check(not ok and "schema" in why, "0x7F4 schema 3 fails -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(fw7f4=6), "PASSTHROUGH")
    check(not ok and "firmware" in why,
          "0x7F4 firmware 6 (E5's) fails -> %s" % why)
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

    print("\nfault vs flow drop, either order (reviewer, 2026-10-08)")
    for name in ("charger-fault-stop", "charger-fault-stop-flow-first",
                 "fault-during-override", "fault-during-override-flow-first"):
        sc = [s for s in R.SCENARIOS if s.name == name]
        kinds = [k for k, _v in sc[0].expect] if sc else []
        check(sc and "fault_or_flow_first" in kinds
              and ("intp_event", r"TRIP: charger fault") not in sc[0].expect,
              "%s judges either order and no longer demands the trip" % name)

    def diag_frozen(state, mods):
        st = {"SAFE": 5, "TERMINATED": 4}[state]   # run_scenario.DIAG_STATES
        rows = [(0.0, 0x7F4, bytes([3, 4, 1, 0, 0, 0, 0, 0]))]
        rows.append((10.0, 0x7F4, bytes([3, 4, st, 0, 0, 0, 0, 0])))
        for i, m in enumerate(mods):
            rows.append((10.0 + i, 0x7F6,
                         m.to_bytes(4, "little") + bytes(4)))
        return rows
    intp_trip = ("10:00:00 intp [SAFE] TRIP: charger fault\n"
                 "10:00:01 intp [PASSTHROUGH] VCU mode 2, session over\n")
    intp_flow = ("10:00:00 intp [TERMINATED] VCU dropped the flow bit: "
                 "transparent\n"
                 "10:00:01 intp [PASSTHROUGH] VCU mode 2, session over\n")
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
    ok, why = F(intp_flow + "10:00:02 intp [SAFE] TRIP: charger fault\n",
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
