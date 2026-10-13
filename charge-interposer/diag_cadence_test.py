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
        return {"intp_state": NAMES[data[2]]}


def body(state):
    return bytes([4, 7, state, 0, 0, 0, 0, 0])


def witness(n=20, period=1.0, lat=0.004, drift_ppm=0.0, state_at=None,
            extra=(), drop=None, intrude=None):
    """Bursts of the four IDs every `period` s from device t=10 s, 0.3 ms
    apart; host = device + 100 s + drift + `lat` (USB). `state_at(t)`
    gives the state byte; `extra` adds (device t, id, state) rows;
    `drop` = (burst, id) removes one frame; `intrude` = burst index after
    whose first frame a 0x7F5 of the next burst is inserted."""
    rows = []
    st = state_at or (lambda t: 1)

    def host(d):
        return d * (1 + drift_ppm / 1e6) + 100.0 + lat

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


def run(rows, serial):
    return D.score(rows, serial, db=FakeDb(), names=set(NAMES.values()))


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

    print("\ndiag_cadence_test: %s" % ("all checks OK" if not FAILS else
                                      "%d FAILED" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
