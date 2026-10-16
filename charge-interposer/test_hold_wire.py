"""Offline test: a board run's spec 9 hold judged on the wire, and the
vehicle stimulus premise (reviewer, 2026-10-15).

Every case builds the records a board run leaves -- the PCAN witness's rows
(host<TAB>device<TAB>id<TAB>hex) and charger_sim's --sp-log receipts -- and
runs the same functions run_scenario's evaluate() calls on them
(judge_hold_on_wire, vehicle_stimulus). The passing record comes first, so
each can-fail is one change to a record that otherwise passes, and is
refused for that change.

    py -3.14 test_hold_wire.py
    python3 test_hold_wire.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import protocol as P                                        # noqa: E402
import run_scenario as R                                    # noqa: E402

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


# --- the hold record --------------------------------------------------------
# Host ms throughout. HOLD from H0 to H1; chg_max 2.0 A, then 0 A from C0;
# bcm_soc reads 100 from SOC100_T (vehicle_sim logs it at sim 650 s, so sim
# t maps to host ms by SOC100_T - 650 s); the 12 V load drops 266 -> 136 W at
# sim 900 s (host DROP_T) and returns at sim 1200 s (host RISE_T); the pack
# current -0.3 A, -0.1 A after the drop (a drop raises it), -0.3 A after the
# return. The override sends 6.10 A for the 3 s before the release (the
# setpoint the hold starts from, spec 5.2); the setpoint enters at 6.10 A,
# walks to 2.9 A from C0, down to 2.0 A after the drop and back to 2.9 A
# after the return, 0.1 A per frame.
H0, C0, H1 = 50000, 100000, 900000
SOC100_T = 50000
DROP_T, RISE_T = SOC100_T + 250000, SOC100_T + 550000
VEH = ("BMS: bcm_soc reads 100 % on the bus at t=650.0s\n"
       "12 V load step: 266.0 -> 136.0 W at t=900.0s (+200.0s after "
       "chg_max reached 0 A)\n"
       "12 V load step: 136.0 -> 266.0 W at t=1200.0s (+500.0s after "
       "chg_max reached 0 A)\n")
VEH_ONE_STEP = VEH.rsplit("12 V load step", 1)[0]
LAT_MS = 5                       # charger receipt after its VCU page 01


LEGS = ((C0, 2.9), (DROP_T, 2.0), (RISE_T, 2.9))


def sp_path(every=300, legs=LEGS, start=6.1, pre=None, pts=()):
    """Receipts: the override's `pre` (default `start`) every 0.3 s for the
    3 s before H0; then `pts`, explicit (ms after H0, amps) receipts; then
    from H0 (or 0.3 s after the last of `pts`, from its value) every
    `every` ms, walking to each leg's target in 0.1 A steps."""
    pre = start if pre is None else pre
    out = [(H0 - 300 * k, pre) for k in range(10, 0, -1)]
    out += [(H0 + dt, a) for dt, a in pts]
    out_hold, a, legs = [], (pts[-1][1] if pts else start), list(legs)
    target, t = None, (H0 + pts[-1][0] + 300 if pts else H0)
    while t <= H1:
        while legs and t >= legs[0][0]:
            target = legs.pop(0)[1]
        if target is not None and abs(target - a) > 1e-6:
            a = round(a + (0.1 if target > a else -0.1), 2)
        out_hold.append((t, a))
        t += every
    return out + out_hold


def ibat_at(t):
    return -0.3 if t < DROP_T else (-0.1 if t < RISE_T else -0.3)


def record(sp=None, jitter=None, ibat=ibat_at,
           with_sp=True, caps=None, vcu_body=None):
    """-> (witness rows, sp rows). `jitter`: {sp t: extra ms} on that
    frame's charger receipt (its VCU page 01 stays), so u > 0. `caps`:
    [(host ms, amps)] steps of the charger's maxAvailableChargingCurrent,
    sent every 0.1 s (the Oracle's EVSE cap); none sent when None.
    `vcu_body`: {sp t: bytes} replacing that receipt's VCU page 01 on the
    witness (a different frame)."""
    sp = sp if sp is not None else sp_path()
    rows = []

    def add(t, fid, data):
        rows.append((t / 1000.0, t / 1000.0, fid, bytes(data)))
    for t in range(0, H1 + 20001, 1000):
        state = 6 if H0 <= t <= H1 else 1
        add(t, 0x7F4, [4, 9, state, 0, 0xc0, 0, 0, 0])
    for t in range(0, H1 + 20001, 100):
        add(t, 0x410, P.encode(P.EPRI, 0x410, {
            "bcm_ibat": ibat(t), "bcm_soc": 100.0 if t >= SOC100_T
            else 99.5}))
        add(t, 0x420, P.encode(P.EPRI, 0x420, {
            "bcm_chg_max": 2.0 if t < C0 else 0.0}))
        if caps:
            cap = [a for tc, a in caps if tc <= t]
            if cap:
                add(t, 0x18FFD9C0, P.encode(P.BEL, 0x18FFD9C0, {
                    "BELINV_maxAvailableChargingCurrent": cap[-1]}))
    sp_rows = []
    for t, a in sp:
        vt = t - LAT_MS
        body = bytes(P.enc_setpoint(8000, int(round(a * 20))))
        add(vt, 0x18EFC000, (vcu_body or {}).get(t, body))
        j = (jitter or {}).get(t, 0)
        if with_sp:
            sp_rows.append(((t + j) / 1000.0, a, body))
    rows.sort(key=lambda r: r[0])
    return rows, sp_rows


def judge(rows, sp_rows, veh=VEH):
    return R.judge_hold_on_wire(rows, sp_rows, veh)


def hold_cases():
    # the record itself: the series is what the wire says
    rows, sp_rows = record()
    series, ends = R.hold_series_from_wire(rows, sp_rows)
    check(series["hold"] == [(H0, H1)] and ends == [(1000, 1000)],
          "HOLD spans from the witness's 0x7F4, bounded inward, with the "
          "unjudged ends reported -> %s %s" % (series["hold"], ends))
    check(series["sp"][0] == (H0, 6.1) and series["soc100"] == SOC100_T
          and any(a == 0.0 for _t, a in series["chgmax"]),
          "setpoints from the receipts, bcm_soc 100 and chg_max 0 from the "
          "witness's 0x410 / 0x420")
    check(series["entry"] == [(H0, H0 - 300, 6.1)],
          "the entry: the release and the override's last receipt before "
          "it -> %s" % series["entry"])
    u, n, _v = R.step_timing(rows, sp_rows, series["hold"])
    check(u == 0 and n == len(series["sp"]),
          "u: every receipt pairs with its VCU page 01; constant latency "
          "gives u = 0 -> u=%s n=%d" % (u, n))

    v, why = judge(rows, sp_rows)
    check(v == "PASS", "a hold that enters at 6.10 A, settles at 2.9 A and "
          "answers both load steps PASSES on the wire -> %s %s"
          % (v, why[:160]))

    # an empty record never passes
    v, why = judge(*record(with_sp=False))
    check(v == "UNRESOLVED" and "no page 01" in why,
          "can-fail: the setpoint lines removed -> UNRESOLVED, not PASS -> %s"
          % why[:100])
    v, why = judge(*record(sp=sp_path(start=2.0)))
    check(v == "UNRESOLVED" and "below the 6.10 A floor" in why,
          "can-fail: the override's last setpoint 2.0 A, under the 6.10 A "
          "floor -> UNRESOLVED -> %s" % why[:110])
    v, why = judge(*record(sp=[r for r in sp_path() if r[0] >= H0]))
    check(v == "UNRESOLVED" and "no setpoint received before" in why,
          "can-fail: no receipt before the HOLD -> UNRESOLVED -> %s"
          % why[:90])
    v, why = judge(*record(sp=sp_path(pts=((46, 5.0),))))
    check(v == "UNRESOLVED" and "neither the override" in why,
          "can-fail: a first HOLD receipt of 5.0 A, neither the override's "
          "6.10 A, the cap, nor a step -> UNRESOLVED -> %s" % why[:120])
    v, why = judge(rows, sp_rows, veh=VEH_ONE_STEP)
    check(v == "UNRESOLVED" and "not the two" in why,
          "can-fail: only one load step in the record -> UNRESOLVED -> %s"
          % why[:90])
    v, why = judge(*record(sp=sp_path(legs=((C0, 2.9), (DROP_T, 2.0)))))
    check(v == "UNRESOLVED" and "after the load step" in why,
          "can-fail: no setpoint change after the 136 -> 266 W step -> "
          "UNRESOLVED -> %s" % why[:90])

    # the spec 9 rules, judged on the receipts
    zero = [(t, 0.0 if DROP_T + 30000 <= t < DROP_T + 31000 else a)
            for t, a in sp_path()]
    v, why = judge(*record(sp=zero))
    check(v == "FAIL" and "0 A" in why, "can-fail: a 0 A setpoint received "
          "-> FAIL -> %s" % why[:120])
    v, why = judge(*record(sp=sp_path(every=270)))
    check(v == "FAIL" and "270 ms apart" in why,
          "can-fail: steps 0.27 s apart, u = 0 -> FAIL -> %s"
          % why[why.find("2:"):][:80])
    v, why = judge(*record(sp=sp_path(every=280)))
    check(v == "PASS", "the boundary: steps exactly 0.28 s apart, u = 0 -> "
          "PASS -> %s %s" % (v, why[:80]))
    v, why = judge(*record(ibat=lambda t: 1.5 if C0 + 60000 <= t < C0 + 70000
                           else ibat_at(t)))
    check(v == "FAIL" and "1: pack current above" in why,
          "can-fail: pack current above chg_max past the grace -> FAIL -> %s"
          % why[:100])
    v, why = judge(*record(sp=sp_path(legs=((C0, 2.0),))))
    check(v in ("FAIL", "UNRESOLVED") and v != "PASS",
          "can-fail: never settles in 2.6-3.3 A -> not PASS -> %s %s"
          % (v, why[:100]))
    v, why = judge(*record(sp=sp_path(legs=((C0, 2.0), (DROP_T, 1.5),
                                            (RISE_T, 2.0)))))
    check(v == "FAIL" and "3:" in why,
          "can-fail: answers the load steps but never settles -> FAIL on "
          "clause 3 -> %s" % why[why.find("3:"):][:90])

    # the entry (5.2; reviewer 2026-10-15, R9): a Low Power release
    low = [(0, 16.0), (H0, 6.0)]
    v, why = judge(*record(sp=sp_path(pts=((46, 6.0), (345, 5.9)),
                                      legs=((C0, 2.9), (DROP_T, 2.0),
                                            (RISE_T, 2.9))), caps=low))
    check(v == "PASS" and "1 clamp(s)" in why,
          "(a) a Low Power release: 6.10 A, the cap falls to 6.00 A, 6.00 A "
          "at +46 ms (the clamp), 5.90 A at +345 ms -> PASS with 1 clamp -> "
          "%s %s" % (v, why[why.find("2:"):][:110]))
    v, why = judge(*record(sp=sp_path(pts=((46, 6.0), (345, 5.9))),
                           caps=[(0, 16.0), (H0, 6.1)]))
    check(v == "FAIL" and "46 ms apart" in why,
          "(b) can-fail: the same receipts with the cap at 6.10 A: 6.00 A at "
          "+46 ms is a step, not a clamp, 46 ms after the release -> FAIL -> "
          "%s" % why[why.find("2:"):][:90])
    v, why = judge(*record(sp=sp_path(pts=((46, 6.1), (200, 6.0)))))
    check(v == "FAIL" and "200 ms apart" in why,
          "(c) can-fail: the first 0.1 A step 200 ms after the release -> "
          "FAIL -> %s" % why[why.find("2:"):][:90])
    v, why = judge(*record(sp=sp_path(pts=((46, 6.1), (300, 6.0)))))
    check(v == "PASS", "...and 300 ms after it -> PASS -> %s %s"
          % (v, why[why.find("2:"):][:80]))
    v, why = judge(*record(sp=sp_path(pts=((46, 5.9),)), caps=low))
    check(v == "FAIL" and "46 ms apart" in why,
          "(d) can-fail: the clamp frame with a step from the cap (5.90 A) "
          "at +46 ms is a step, timed from the release -> FAIL -> %s"
          % why[why.find("2:"):][:90])
    later = low + [(H0 + 10000, 5.0)]
    for gap, want in ((200, "FAIL"), (300, "PASS")):
        pts = ((46, 6.0), (10046, 4.9), (10046 + gap, 4.8))
        v, why = judge(*record(sp=sp_path(pts=pts), caps=later))
        check(v == want and (want == "PASS" or "%d ms apart" % gap in why),
              "(d) a later clamp frame with a step from the cap (cap 5.00, "
              "4.90 A) is the step the cadence is timed from: the next step "
              "%d ms later -> %s -> %s %s"
              % (gap, want, v, why[why.find("2:"):][:90]))

    # pairing (reviewer 2026-10-15, R9): the nearest witness VCU page 01
    # within +/-150 ms with the same bytes 0-1 and 4-7
    sp = sp_path()
    t_e = sp[20][0]
    rows, sp_rows = record(jitter={t_e: -(LAT_MS + 1)})
    series, _e = R.hold_series_from_wire(rows, sp_rows)
    u, n, vof = R.step_timing(rows, sp_rows, series["hold"])
    check(n == len(series["sp"]) and vof.get(t_e - LAT_MS - 1) == t_e - LAT_MS
          and u == LAT_MS + 1,
          "a receipt stamped 1 ms BEFORE its own VCU frame pairs with it, not "
          "with the one 300 ms earlier -> n=%d of %d, u=%s"
          % (n, len(series["sp"]), u))
    rows, sp_rows = record(vcu_body={t_e: bytes([0x01, 0x99, 0, 0, 0, 0, 0,
                                                 0])})
    series, _e = R.hold_series_from_wire(rows, sp_rows)
    u, n, vof = R.step_timing(rows, sp_rows, series["hold"])
    check(n == len(series["sp"]) - 1 and t_e not in vof,
          "can-fail: a receipt whose time-nearest VCU page 01 differs in "
          "byte 1 does not pair with it (none matching within 150 ms: "
          "unpaired, counted) -> n=%d of %d" % (n, len(series["sp"])))
    v, why = judge(rows, sp_rows)
    check("of %d receipts" % len(series["sp"]) in why and "1 unpaired" in why,
          "...and the verdict reports it -> %s" % why[why.find("u ="):][:70])
    rows, sp_rows = record(jitter={sp[20][0]: -(LAT_MS + 1.6),
                                   sp[30][0]: 15.3 - LAT_MS})
    series, _e = R.hold_series_from_wire(rows, sp_rows)
    u, n, _v = R.step_timing(rows, sp_rows, series["hold"])
    check(u == 16.9, "u in float ms: receipts at -1.6 and +15.3 ms from "
          "their VCU frames -> u = 16.9 ms -> %s" % u)

    # u > 0: a spacing inside [0.28 s - u, 0.28 s) is UNRESOLVED unless the
    # VCU frames that carried it are >= 0.28 s apart on the witness
    sp = sp_path(every=300)
    # two consecutive step frames in the walk to 2.9 A after C0
    k = next(i for i, ((ta, a), (tb, b)) in enumerate(zip(sp, sp[1:]))
             if tb > C0 and abs(b - a) > 0.05) + 1
    t_k = sp[k][0]
    # (a) the step frame itself 25 ms late, at the VCU as at the charger:
    # receipts AND VCU page 01s 275 ms apart; one receipt elsewhere 10 ms
    # late, so u = 10 ms and 275 ms is inside [270, 280)
    s2 = list(sp)
    s2[k] = (t_k + 25, sp[k][1])
    rows, sp_rows = record(sp=s2, jitter={H0: 10})
    v, why = judge(rows, sp_rows)
    check(v == "UNRESOLVED" and "within u" in why and "u = 10 ms" in why,
          "can-fail, u > 0: a 275 ms spacing with u = 10 ms, its VCU page "
          "01s also 275 ms apart -> UNRESOLVED -> %s" % why[:150])
    # (b) only the charger's receipt 25 ms late (the path's queue), the VCU
    # page 01s 300 ms apart: u = 25 ms, the 275 ms spacing is resolved
    rows, sp_rows = record(jitter={t_k: 25})
    v, why = judge(rows, sp_rows)
    check(v == "PASS" and "resolved" in why and "u = 25 ms" in why,
          "...and a 275 ms spacing at the charger whose VCU page 01s are "
          "300 ms apart on the witness is resolved -> PASS -> %s %s"
          % (v, why[-170:]))
    # (c) (a) with u = 0: the 275 ms spacing FAILS
    rows, sp_rows = record(sp=s2)
    v, why = judge(rows, sp_rows)
    check(v == "FAIL" and "275 ms apart" in why,
          "...while with u = 0 the 275 ms spacing FAILS -> %s"
          % why[why.find("2:"):][:70])


# --- the vehicle stimulus premise ------------------------------------------

def vrows(gap=None, trip_at=None, ids=R.VEH_BMS_IDS, until=60.0):
    """Witness rows: MONITOR 0x7F4 at 1 Hz (SAFE with a=10 from trip_at),
    each BMS id every 0.1 s, with `gap` = (start, end) host s missing."""
    rows = []
    t = 0.0
    while t <= until:
        safe = trip_at is not None and t >= trip_at
        rows.append((t, t, 0x7F4, bytes([4, 9, 5 if safe else 1,
                                         10 if safe else 0, 0, 0, 0, 0])))
        t += 1.0
    if trip_at is not None:
        rows.append((trip_at, trip_at, 0x7F4, bytes([4, 9, 5, 10, 0, 0, 0,
                                                     0])))
    for fid in ids:
        k = 0
        while k * 0.1 <= until:
            tt = round(k * 0.1, 3)
            if not (gap and gap[0] < tt < gap[1]):
                rows.append((tt, tt, fid, bytes(8)))
            k += 1
    rows.sort(key=lambda r: r[0])
    return rows


def stall(ms, end):
    return ("12:00:00 veh STALL %d ms wall at sim t=17s host=%.6f  gc "
            "collections during it gen0/1/2=(0, 0, 0)\n" % (ms, end))


def vehicle_cases():
    v, why = R.vehicle_stimulus(vrows(), "")
    check(v == "OK", "a clean record -> OK -> %s" % why)
    # #17's shape: a 1.37 s stall, the gap inside it, the board trips 500 ms
    # into the gap (plus its 0x7F4)
    g0, g1 = 20.0, 21.4
    v, why = R.vehicle_stimulus(vrows(gap=(g0, g1), trip_at=g0 + 0.53),
                                stall(1370, g1 - 0.02))
    check(v == "VOID", "an injected sim stall covering the gap, trip on time "
          "-> VOID -> %s" % why)
    v, why = R.vehicle_stimulus(vrows(trip_at=30.0), "")
    check(v == "FAIL" and "not before 500 ms" in why,
          "can-fail: a stale-BMS trip with no gap and no stall -> FAIL -> %s"
          % why[:110])
    v, why = R.vehicle_stimulus(vrows(gap=(g0, g1)), stall(1370, g1 - 0.02))
    check(v == "FAIL" and "never tripped" in why,
          "can-fail: a stall over 500 ms, the trip suppressed -> FAIL (spec "
          "6, not voided) -> %s" % why[:110])
    v, why = R.vehicle_stimulus(vrows(gap=(g0, g1), trip_at=g0 + 0.3),
                                stall(1370, g1 - 0.02))
    check(v == "FAIL" and "not before 500 ms" in why,
          "can-fail: tripped 300 ms into the gap, before 500 ms -> FAIL -> %s"
          % why[:110])
    v, why = R.vehicle_stimulus(vrows(gap=(g0, g1), trip_at=g0 + 0.53), "")
    check(v == "FAIL" and "inside no vehicle_sim stall" in why,
          "can-fail: the same gap with no stall to cover it -> FAIL -> %s"
          % why[:110])
    v, why = R.vehicle_stimulus(vrows(gap=(g0, g1), trip_at=g0 + 0.53),
                                stall(600, g1 - 0.6))
    check(v == "FAIL" and "inside no vehicle_sim stall" in why,
          "can-fail: a stall that covers only part of the gap -> FAIL")
    v, why = R.vehicle_stimulus(vrows(gap=(g0, g0 + 0.45)),
                                stall(470, g0 + 0.45))
    check(v == "VOID", "a 450 ms gap inside a 470 ms stall, no trip owed "
          "(under 500 ms) -> VOID -> %s" % why[:90])
    v, why = R.vehicle_stimulus(vrows(ids=()), "")
    check(v == "FAIL" and "cannot be judged" in why,
          "can-fail: no BMS frames on the witness -> FAIL, not OK")


def recording_cases():
    """The record's sources: charger_sim's --sp-log line and the wiring."""
    import inspect
    import io
    import charger_sim as CS
    import diag_cadence as DC
    fh = io.StringIO()
    wrote = CS.sp_record(fh, P.enc_setpoint(8000, 58), 123.456789)
    line = fh.getvalue().rstrip("\n").split("\t")
    check(wrote and line[0] == "123.456789" and abs(float(line[1]) - 2.9)
          < 1e-6, "sp_record: a page 01 received -> 'host<TAB>amps<TAB>hex' "
          "-> %r" % fh.getvalue().strip())
    fh2 = io.StringIO()
    check(not CS.sp_record(fh2, P.enc_master(1, 3), 1.0)
          and fh2.getvalue() == "",
          "sp_record: a page 00 is not a setpoint and writes nothing")
    src = inspect.getsource(R.run_one)
    check('"--sp-log", str(sp_log)' in src and "vehicle_stimulus(wrows" in src
          and "read_sp_log(sp_log)" in src,
          "run_one starts charger_sim with --sp-log and hands the record, "
          "and the vehicle premise, to evaluate()")
    src = inspect.getsource(R.evaluate)
    check("judge_hold_on_wire(hold_wire[0], hold_wire[1]" in src
          and "bench premise: the vehicle" in src,
          "evaluate() judges a board run's hold on the wire and reports the "
          "vehicle premise")
    check(set(R.VEH_BMS_IDS) == set(DC.BMS_IDS),
          "the witness logs exactly the BMS ids the premise reads")
    check("rows = [r for r in every if r[2] in STATUS_IDS]"
          in inspect.getsource(DC.score),
          "8.2's score keeps the status ids only, so the BMS rows now on the "
          "witness cannot count as stray frames")


def main():
    print("the record's sources")
    recording_cases()
    print("\nspec 9 hold on a board run, on the wire")
    hold_cases()
    print("\nthe vehicle stimulus premise")
    vehicle_cases()
    if failures:
        print("\ntest_hold_wire: %d FAILED" % failures)
        return 1
    print("\ntest_hold_wire: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
