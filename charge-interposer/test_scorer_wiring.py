"""Tests the HARNESS, not the core: the scenario scorer's wiring in
run_scenario.py, as changed by the tester on 2026-10-07.

Each change there exists because the old form could not fail on a wrong
core, so each gets a case here that would fail if the change were undone:

  * spec 9 "every scenario decodes the diagnostic CAN frames": every
    scenario asks for diag_frames_ok(), and every termination scenario for
    no_rewrite_after_release();
  * the spec literals -- 20 frames, 50 ms, firmware 5, schema 4 (since
    2026-10-09; 4 / 3 before) -- are the harness's own, not read from
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


def _diag(fw7f4=5, sch7f4=4, fw7f7=(5, 5, 5), sch7f7=4, state=0,
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

    print("\nthe spec's literals, not machine.py's")
    check(R.BURST_FRAMES == 20 and R.BURST_MS == 1000,
          "a burst is 20 frames x 50 ms (got %d, %d ms)"
          % (R.BURST_FRAMES, R.BURST_MS))
    check(R.SPEC_FW_VER == 5 and R.SPEC_SCHEMA_VER == 4,
          "firmware 5, schema 4 -- the schema doc 7c866acd6f804161 (got "
          "%d, %d)" % (R.SPEC_FW_VER, R.SPEC_SCHEMA_VER))
    check(R.DIAG_STATES.get(6) == "HOLD",
          "0x7F4 B2 6 decodes as HOLD (schema 4)")
    src = (HERE / "run_scenario.py").read_text(encoding="utf-8")
    for needle in ("M.DIAG_FW_VER", "M.DIAG_SCHEMA_VER", "burst_frames"):
        check(needle not in src.replace("# ", "#"),
              "run_scenario.py no longer reads %s" % needle)

    print("\ndiag versions in every frame (reading 15)")
    ok, why = R._diag_frames_ok(_diag(), "PASSTHROUGH")
    check(ok, "a clean fw 5 / schema 4 stream passes -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(fw7f7=(5, 6, 5)), "PASSTHROUGH")
    check(not ok and "0x7F7" in why,
          "a MIDDLE 0x7F7 reporting fw 6 fails (rev 1 read only the last) "
          "-> %s" % why)
    ok, why = R._diag_frames_ok(_diag(sch7f4=3), "PASSTHROUGH")
    check(not ok and "schema" in why, "0x7F4 schema 3 fails -> %s" % why)
    ok, why = R._diag_frames_ok(_diag(fw7f4=4), "PASSTHROUGH")
    check(not ok and "firmware" in why, "0x7F4 firmware 4 fails -> %s" % why)
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

    print("\ncheck_invariants over a scenario trace")
    ok, why = R.check_invariants(TRACES / "no_such_scenario.l1.trace")
    check(not ok, "a missing trace FAILS -> %s" % why)
    good = TRACES / "syn_override_release.trace"
    ok, why = R.check_invariants(good)
    check(ok and "0 override page-01" not in why,
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
