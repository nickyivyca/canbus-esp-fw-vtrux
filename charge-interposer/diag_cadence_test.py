"""diag_cadence's own test, no hardware (tester, 2026-10-13).

Each spec 8.2 rule, as the reviewer read it, driven to pass and to fail on
synthetic witness and serial rows; the host mapping shown to absorb a
drifting adapter clock; and the real diag DBC's intp_state decoded.

Run: python diag_cadence_test.py   (exit 0 = all checks passed)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import diag_cadence as D                                    # noqa: E402

FAILS = []
NAMES = {0: "PASSTHROUGH", 1: "MONITOR", 2: "OVERRIDE", 4: "TERMINATED",
         5: "SAFE", 6: "HOLD"}


def check(cond, what):
    print("  %s  %s" % ("ok  " if cond else "FAIL", what))
    if not cond:
        FAILS.append(what)


class FakeDb(object):
    def decode_message(self, fid, data, decode_choices=True):
        # 0x7F7's uptime in byte 7 (the reset cases); state in byte 2
        return {"intp_state": NAMES.get(data[2], NAMES[1]),
                "intp_uptime_min": data[7]}


def body(state):
    return bytes([4, 7, state, 0, 0, 0, 0, 0])


CHG_ID = 0x18FFD4C0                 # the Bel's status frame, SA 0xC0


def witness(n=20, period=1.0, lat=0.004, drift_ppm=0.0, state_at=None,
            extra=(), drop=None, intrude=None, charger="all"):
    """Bursts of the four IDs every `period` s from device t=10 s, 0.3 ms
    apart; host = device + 100 s + drift + `lat` (USB). `state_at(t)`
    gives the state byte; `extra` adds (device t, id, state) rows;
    `drop` = (burst, id) removes one frame; `intrude` = burst index after
    whose first frame a 0x7F5 of the next burst is inserted. `charger`:
    the forwarded charger frames (spec 2.3's mode), every 100 ms -- "all"
    from 9.9 s to past the last burst, a list of (from, to) spans, or
    None."""
    rows = []
    st = state_at or (lambda t: 1)

    def host(d):
        return d * (1 + drift_ppm / 1e6) + 100.0 + lat

    spans = ([(9.9, 10.0 + n * period)] if charger == "all"
             else list(charger or []))
    for a, b in spans:
        k = 0
        while a + k * 0.1 <= b + 1e-9:
            d = round(a + k * 0.1, 4)
            rows.append((host(d), d, CHG_ID, bytes(8)))
            k += 1

    for k in range(n):
        t0 = 10.0 + k * period
        for j, fid in enumerate(D.STATUS_IDS):
            if drop == (k, fid):
                continue
            d = t0 + j * 0.0003
            rows.append((host(d), d, fid, body(st(d))))
            if intrude == k and j == 0:
                d2 = d + 0.0001
                rows.append((host(d2), d2, 0x7F5, body(st(d2))))
    for d, fid, s in extra:
        rows.append((host(d), d, fid, body(s)))
    return rows


def ev(host_t, state, text="LOW_POWER overridden a=1 b=2 c=3"):
    return (host_t, "[%d] %s %s" % (int(host_t * 1000), state, text))


def run(rows, serial, **kw):
    return D.score(rows, serial, db=FakeDb(), names=set(NAMES.values()),
                   **kw)


def reset_rows(pre_up=2, post_up=0, round_ids=None, round_after=0.328):
    """boot-mid-session on c336a7aa (item 6, 2026-10-14): bursts every 1 s
    from device 10 s, a board reset at host 119.006 (6 ms after the burst
    at device 19 s), the new boot's round `round_after` s after that burst,
    then bursts every 1 s from the round. Charger frames throughout, so
    one normal-mode window. 0x7F7 carries the uptime in byte 7."""
    rows = []

    def host(d):
        return d + 100.0 + 0.001

    d = 9.9
    while d <= 26.0:
        rows.append((host(d), round(d, 4), CHG_ID, bytes(8)))
        d += 0.1

    def burst(t0, up, ids=D.STATUS_IDS):
        for j, fid in enumerate(D.STATUS_IDS):
            if fid not in ids:
                continue
            dd = t0 + j * 0.0003
            b = bytearray(body(1))
            if fid == 0x7F7:
                b[7] = up
            rows.append((host(dd), dd, fid, bytes(b)))
    for k in range(10):
        burst(10.0 + k, pre_up)
    t_r = 19.0 + round_after
    burst(t_r, post_up, round_ids or D.STATUS_IDS)
    for k in range(1, 6):
        burst(t_r + k, post_up)
    return rows, 119.006


def reset_cases():
    """A board reset mid-capture is a power-up (reviewer, 2026-10-14): the
    first of each id after it is 2.3's round and the cadence restarts --
    only if the board shows it: all four within 2 s, and 0x7F7's uptime
    reset to 0 from >= 1 min."""
    rows, rh = reset_rows()
    ok, why = run(rows, [], reset_host=rh)
    check(ok and "power-up round of all four" in why and "2 -> 0" in why,
          "boot-mid-session's shape, the reset given: PASS -> %s" % why)
    ok, why = run(rows, [])
    check(not ok and "gap(s) outside" in why,
          "the same frames with NO reset known: the 0.328 s gap FAILS as "
          "cadence -> %s" % why)
    rows2, rh2 = reset_rows(round_ids=(0x7F4, 0x7F5, 0x7F7))
    ok, why = run(rows2, [], reset_host=rh2)
    check(not ok and "missing 0x7F6" in why,
          "a round missing 0x7F6: no round, judged as cadence: FAILS -> %s"
          % why)
    rows3, rh3 = reset_rows(post_up=2)
    ok, why = run(rows3, [], reset_host=rh3)
    check(not ok and "no uptime reset shown" in why,
          "a reset time present but the uptime continuing (2 -> 2): FAILS "
          "-> %s" % why)
    rows4, rh4 = reset_rows(pre_up=0, post_up=0)
    ok, why = run(rows4, [], reset_host=rh4)
    check(not ok and "no uptime reset shown" in why,
          "a board up under a minute (0 -> 0) cannot show the reset: FAILS "
          "-> %s" % why)


def main():
    print("cadence and burst shape")
    ok, why = run(witness(), [])
    check(ok and "19 bursts" not in why and "20 bursts" in why,
          "20 bursts a second apart pass -> %s" % why)
    ok, why = run(witness(period=1.09), [])
    check(ok, "1.09 s apart passes (within 10 %%) -> %s" % why)
    ok, why = run(witness(period=1.11), [])
    check(not ok and "outside 1.0 s +/- 10 %" in why,
          "1.11 s apart fails -> %s" % why)
    rows = witness()
    rows = [r for r in rows if not (12.9 < r[1] < 13.1)]   # burst 3 gone
    ok, why = run(rows, [])
    check(not ok and "2.000 s after its last" in why,
          "a missing burst is a 2 s gap and fails -> %s" % why)
    ok, why = run(witness(drop=(5, 0x7F6)), [])
    check(not ok and "outside a complete burst" in why,
          "a burst with 0x7F6 missing fails -> %s" % why)
    ok, why = run(witness(intrude=7), [])
    check(not ok and "outside a complete burst" in why,
          "another burst's frame inside a burst fails -> %s" % why)
    imm = witness(extra=[(15.5, 0x7F4, 2)])
    ok, why = run(imm, [])
    check(ok, "an immediate 0x7F4 between bursts is not in the cadence "
          "(ruling 1) -> %s" % why)

    print("the immediate 0x7F4 against the event line (host clock)")
    # state goes MONITOR -> OVERRIDE at device 15.5 s; host = device+100.004
    st = (lambda t: 2 if t >= 15.5 else 1)
    base = witness(state_at=st, extra=[(15.5, 0x7F4, 2)])
    line_h = 115.5 + 0.004
    serial = [ev(105.0, "MONITOR", "boot"), ev(line_h + 0.030, "OVERRIDE")]
    ok, why = run(base, serial)
    check(ok and "1 state change(s)" in why,
          "the line 30 ms after the immediate frame passes -> %s" % why)
    ok, why = run(base, [ev(105.0, "MONITOR"), ev(line_h - 0.045, "OVERRIDE")])
    check(ok, "...and the frame 45 ms AFTER the line passes (either "
          "direction, ruling 3) -> %s" % why)
    ok, why = run(base, [ev(105.0, "MONITOR"), ev(line_h + 0.060, "OVERRIDE")])
    check(not ok and "within 50 ms" in why and "+-60.0" not in why,
          "the line 60 ms after the frame fails -> %s" % why)
    old = witness(state_at=st, extra=[(15.5, 0x7F4, 1)])
    ok, why = run(old, [ev(105.0, "MONITOR"), ev(line_h, "OVERRIDE")])
    check(not ok and "carried MONITOR" in why,
          "an immediate 0x7F4 in time with the OLD state fails (ruling 4) "
          "-> %s" % why)
    noimm = witness(state_at=st)
    ok, why = run(noimm, [ev(105.0, "MONITOR"), ev(line_h, "OVERRIDE")])
    check(not ok and "nearest 0x7F4 with OVERRIDE" in why,
          "no immediate frame (only the next periodic burst) fails -> %s"
          % why)
    ok, why = run(noimm, [ev(105.0, "MONITOR"), ev(line_h, "MONITOR",
                                                     "REPEAT: 20 frames"),
                          ev(line_h + 0.5, "MONITOR", "REPEAT ended")])
    check(ok, "event lines with the SAME state (REPEAT) are not state "
          "changes -> %s" % why)
    sim = [(105.0, "16:39:14 intp [MONITOR] armed"),
           (line_h, "16:41:38 intp [OVERRIDE] LOW_POWER overridden")]
    ok, why = run(base, sim)
    check(ok and "1 state change(s)" in why,
          "interposer_sim's '[STATE] event' form is read too -> %s" % why)
    ok, why = run(base, [(line_h, "16:39:13 intp diag st=OVERRIDE trip=0")])
    check(ok and "0 state change(s)" in why,
          "a diag status line is not an event line -> %s" % why)
    # after the witness stopped (item 6, restart-after-standby: the BMS
    # stale TRIP came 0.4 s after the witness's last frame). The witness's
    # last frame here is at host ~130.004 (charger frames to device 30 s).
    end = max(r[0] for r in base)
    ok, why = run(base, [ev(105.0, "MONITOR"), ev(line_h, "OVERRIDE"),
                         ev(end + 0.4, "SAFE", "TRIP a=10 b=1040 c=0")])
    check(ok and "1 state change(s) after the witness stopped, not judged"
          in why, "a state change 0.4 s after the witness's last frame is "
          "not judged -> %s" % why)
    ok, why = run(base, [ev(105.0, "MONITOR"), ev(line_h, "OVERRIDE"),
                         ev(end - 0.3, "SAFE", "TRIP a=10 b=1040 c=0")])
    check(not ok and "OVERRIDE -> SAFE" in why,
          "...but one 0.3 s BEFORE it, with no 0x7F4, still fails -> %s"
          % why)

    print("host mapping under adapter drift")
    st2 = (lambda t: 2 if t >= 3000.0 else 1)
    long_rows = witness(n=3600, drift_ppm=80.0, state_at=st2,
                        extra=[(3000.0, 0x7F4, 2)])
    h_line = 3000.0 * (1 + 80e-6) + 100.0 + 0.004 + 0.010
    ok, why = run(long_rows, [ev(105.0, "MONITOR"), ev(h_line, "OVERRIDE")])
    check(ok, "80 ppm adapter drift over an hour: the windowed map still "
          "pairs the frame within 50 ms -> %s" % why[:160])
    f = D.host_map(long_rows)
    check(abs(f(3000.0) - (3000.0 * (1 + 80e-6) + 100.004)) < 0.002,
          "the map lands within 2 ms of the true host time after 50 min")

    print("spec 2.3: listen-only while the charger is silent")
    # charger talking 10-15 s and 25-30 s; bursts every second 10-39 s
    two = [(10.0, 15.0), (25.0, 30.0)]

    def in_two(rows, boot=True):
        """keep the bursts the board may send under 2.3: inside a window
        (last charger frame + 2 s), plus a power-up round at 9.0 s"""
        keep = [r for r in rows if r[2] not in D.STATUS_IDS
                or any(a <= r[1] <= b + 2.0 for a, b in two)]
        if boot:
            keep += [(r[1] + 100.004 - 1.0, r[1] - 1.0, r[2], r[3])
                     for r in rows if r[2] in D.STATUS_IDS and r[1] < 10.01]
        return keep
    base = witness(n=30, charger=two)
    good = in_two(base)
    ok, why = run(good, [])
    check(ok and "2 normal-mode window(s)" in why and "4 in the power-up "
          "round, 0 outside" in why,
          "bursts only inside the windows and one power-up round pass -> %s"
          % why)
    ok, why = run(base, [])
    check(not ok and "2.3:" in why and "listen-only" in why,
          "the port always normal (1 Hz throughout, as E6a) fails 2.3 -> %s"
          % why[:200])
    ok, why = run(base, [], listen_only=False)
    check(ok, "...and passes under the rule before d9f49ea6 -> %s"
          % why[:120])
    stray = good + [(118.504, 18.5, 0x7F4, body(1))]
    ok, why = run(stray, [])
    check(not ok and "1 status frame(s) while the vehicle port should be "
          "listen-only" in why and "18.500" in why,
          "ONE 0x7F4 at 18.5 s, 3.5 s after the charger stopped, fails -> %s"
          % why[:200])
    late = [r for r in good if not (r[2] in D.STATUS_IDS and 16.5 < r[1])
            or r[1] > 24.0] + [(117.154, 17.15, f, body(1))
                               for f in D.STATUS_IDS]
    ok, why = run(sorted(late, key=lambda r: r[1]), [])
    check(not ok and "4 status frame(s) while the vehicle port should be "
          "listen-only" in why and "17.150" in why,
          "a burst 2.15 s after the last charger frame (past 2 s + the "
          "0.1 s slack) fails -> %s" % why[:200])
    edge = [r for r in good if not (r[2] in D.STATUS_IDS and 16.5 < r[1])
            or r[1] > 24.0] + [(117.054, 17.05, f, body(1))
                               for f in D.STATUS_IDS]
    ok, why = run(sorted(edge, key=lambda r: r[1]), [])
    check(ok, "a burst 2.05 s after the last charger frame is inside the "
          "slack -> %s" % why[:160])
    twice = good + [(r[0] + 0.5, r[1] + 0.5, r[2], r[3])
                    for r in good if r[2] in D.STATUS_IDS and r[1] < 9.5]
    ok, why = run(twice, [])
    check(not ok and "2.3:" in why,
          "a second power-up round fails -> %s" % why[:160])
    ok, why = run(in_two(base, boot=False), [])
    check(ok and "0 in the power-up round" in why,
          "no power-up round on the witness passes (the capture may start "
          "after it), recorded -> %s" % why[:200])
    # boot_in_capture (2026-10-13, spec 2.3 at 4f5168ae): a capture known
    # to contain the boot must show all four in the power-up round
    ok, why = run(good, [], boot_in_capture=True)
    check(ok, "boot in capture, all four in the power-up round passes -> %s"
          % why[:120])
    one = [r for r in good if not (r[2] in D.STATUS_IDS and r[1] < 9.5
                                   and r[2] != 0x7F4)]
    ok, why = run(one, [], boot_in_capture=True)
    check(not ok and "carried 1 of the four" in why and "0x7F5" in why,
          "boot in capture, ONE of four (0x7F4 only, the bench boot of "
          "6b54c67d) fails -> %s" % why[:200])
    ok, why = run(one, [])
    check(ok, "...and the same capture NOT known to contain the boot "
          "passes, as before (recorded)")
    ok, why = run(in_two(base, boot=False), [], boot_in_capture=True)
    check(not ok and "carried 0 of the four" in why,
          "boot in capture, no power-up round at all fails -> %s" % why[:160])
    # the cadence is judged inside a window only: the jump from the last
    # burst of window 1 to the first of window 2 is not a gap
    ok, why = run(good, [])
    check(ok and "outside 1.0 s" not in why,
          "the 10 s from window 1's last burst to window 2's first is not a "
          "cadence gap -> %s" % why[:160])
    # a state change while listen-only needs no 0x7F4; one in normal does
    ok, why = run(good, [ev(105.0, "MONITOR"), ev(120.0, "OVERRIDE")])
    check(ok and "1 state change(s) in listen-only, not judged" in why,
          "a state change at 20 s (listen-only) is not judged -> %s"
          % why[:220])
    ok, why = run(good, [ev(105.0, "MONITOR"), ev(112.0, "OVERRIDE")])
    check(not ok and "without a 0x7F4" in why,
          "...but one at 12 s (normal) still needs its 0x7F4 -> %s"
          % why[:200])
    mw = D.ModeWindows()
    check([mw.status(1.0, 0x7F4), mw.status(1.0, 0x7F5)] == ["boot", "boot"]
          and mw.status(1.5, 0x7F4) == "outside",
          "ModeWindows: the first of each ID before any charger frame is "
          "the power-up round, a repeat is not")
    mw.charger(2.0)
    check(mw.status(4.05, 0x7F4) == "normal"
          and mw.status(4.2, 0x7F4) == "outside" and mw.window == 1,
          "ModeWindows: normal to 2 s + 0.1 s after a charger frame")
    check(D.is_charger_frame(0x18FFD4C0) and not D.is_charger_frame(0x7C0)
          and not D.is_charger_frame(0x18FFD4C1),
          "a charger frame is extended with SA 0xC0; a standard 0x7C0 and "
          "SA 0xC1 are not")

    print("the real diag DBC")
    db, codes = D.load_state_names()
    check(codes.get(6) == "HOLD" and codes.get(0) == "PASSTHROUGH",
          "intp_state's value table: %s" % codes)
    check(D.state_of(db, body(6)) == "HOLD",
          "cantools decodes state byte 6 as HOLD")
    ok, why = D.score(witness(), [])
    check(ok, "score() with the real DBC on clean rows -> %s" % why)

    print("run_scenario carries the result")
    import shutil
    import tempfile
    import run_scenario as R
    tmp = Path(tempfile.mkdtemp(dir=str(Path(__file__).resolve().parent)))
    try:
        logs = [tmp / n for n in ("v.log", "c.log", "i.log")]
        for p in logs:
            p.write_text("")
        sc = R.Scenario("x", "test", vehicle=[])
        for verdict in (True, False):
            r = R.evaluate(sc, *logs, timed_out=False,
                           diag82=(verdict, "8.2: planted"))
            hit = [ok for ok, why in r["results"]
                   if why.startswith("spec 8.2 on the wire")]
            check(hit == [verdict], "evaluate() reports the 8.2 result "
                  "(%s) as its own line -> %s" % (verdict, hit))
        r = R.evaluate(sc, *logs, timed_out=False)
        check(not any(why.startswith("spec 8.2") for _ok, why in
                      r["results"]),
              "...and adds none when no witness ran")
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)

    print("spec 2.3: the window before the first charger frame, after a "
          "scenario whose charger stopped recently (2026-10-15)")
    # R2's shape: two rounds before the first charger frame, while the port
    # was normal on the previous scenario's tail
    tail = good + [(r[0] - 1.2, r[1] - 1.2, r[2], r[3])
                   for r in good if r[2] in D.STATUS_IDS and r[1] < 9.5]
    stop, open_t = 1000.0, 1001.9
    why_nj = R.pre_charger_premise(stop, open_t)
    ok, why = run(tail, [], prechg_unjudged=why_nj)
    check(why_nj is not None and ok and "NOT JUDGED" in why
          and "8 status frame(s) in it" in why,
          "the previous charger 1.9 s before the witness opened: the "
          "pre-charger window is NOT JUDGED, not FAILED -> %s" % why[-170:])
    why_j = R.pre_charger_premise(1000.0, 1003.0)
    ok, why = run(tail, [], prechg_unjudged=why_j)
    check(why_j is None and not ok and "listen-only" in why,
          "can-fail: 3 s before, a second round before the first charger "
          "frame FAILS -> %s" % why[:150])
    check(R.pre_charger_premise(None, 1001.0) is None
          and R.pre_charger_premise(1005.0, 1001.0) is None,
          "no record of a previous charger, or one from after the witness "
          "opened (a perf clock from before a reboot): judged as before")
    ok, why = run(good, [], prechg_unjudged=why_nj)
    check(ok and "4 status frame(s) in it" in why and "0 in the power-up "
          "round" in why, "...and a clean start under the premise counts "
          "its power-up round as not judged -> %s" % why[-120:])

    print("a board reset mid-capture (2026-10-14)")
    reset_cases()

    print("\ndiag_cadence_test: %s" % ("all checks OK" if not FAILS else
                                      "%d FAILED" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
