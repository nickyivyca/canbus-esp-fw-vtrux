"""Tests regress.check_state_events offline (tester, 2026-10-08).

The 83 % replay case (spec 9, user 2026-10-08) asserts two transitions in
order and in time: TERMINATED on the charger's plug-out report at 57.9 s,
PASSTHROUGH on the STAND_BY at 59 s. This proves the check passes the events
the replay produced on 2026-10-08 and fails each way it should -- an event
missing, out of its window, out of order, or with the wrong state -- without
the multi-GB capture.

regress.py resolves VTRUX_DATA at import, so a throwaway data folder is
built for it and removed.

Run:  python charge-interposer/regress_offline_test.py
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

failures = 0


def check(cond, what):
    global failures
    print(("  ok  " if cond else "FAIL  ") + what)
    if not cond:
        failures += 1


# The replay of vtrux_partstruck_chargeagain3 at a9c139b (madhouse-debian,
# 2026-10-08), as (t_ms, state, text).
REAL = [
    (200, "PASSTHROUGH", "VCU mode 0, session over"),
    (9100, "MONITOR", "charge established (charger in state 12, contactors "
                      "closed, ibat -1.6 A)"),
    (12400, "MONITOR", "LOW_POWER seen at soc=83.0% -- waiting out the arm "
                       "delay (3.2 s into the charge)"),
    (57900, "TERMINATED", "charger reports the plug out (vehicleConnected "
                          "1 -> 0): transparent"),
    (59100, "PASSTHROUGH", "VCU mode 2, session over"),
]


def bus_cases():
    """replay.choose_pt_bus (rev 2): the powertrain bus is identified only
    when the evidence separates, else refused with the reason."""
    import replay as RP
    A = sorted(RP.PT_ANCHORS)
    k = RP.PT_MIN_ANCHORS

    def ev(**chans):
        # chans: name -> (ids seen, total frames); as pt_bus_evidence does,
        # only PT_ANCHORS ids are recorded in the set
        out = {}
        for b, (ids, tot) in chans.items():
            got = set(ids) & RP.PT_ANCHORS
            out[b] = (len(got) * 10, got, tot)
        return out

    def refused(e):
        try:
            RP.choose_pt_bus(e)
        except RP.BusUnresolved as exc:
            return str(exc)
        return None

    bus, why = RP.choose_pt_bus(ev(c1=(A, 5000), c2=([0x1E5, 0x500], 4000)))
    check(bus == "c1" and "8 of 8" in why,
          "all anchors on one channel, none on the other -> that channel")
    bus, _ = RP.choose_pt_bus(ev(c3=(A[:k], 900)))
    check(bus == "c3", "a single-channel capture with %d distinct anchors "
                       "-> accepted on its own evidence" % k)
    r = refused(ev(c3=(A[:k - 1], 900)))
    check(r is not None and "fewer than %d" % k in r,
          "one anchor short of the threshold -> refused, not selected "
          "(the single-candidate null discriminator) -> %s" % r)
    r = refused(ev(c1=(A, 5000), c2=([A[0]], 4000)))
    check(r is not None and "do not separate" in r,
          "one stray anchor on a second channel -> refused, not decided by "
          "frame count")
    r = refused(ev(c1=([0x1E5], 100), c2=([0x500], 100)))
    check(r is not None and "no powertrain anchor" in r,
          "no anchor anywhere -> refused; rev 1 returned None and fed every "
          "bus to the core")
    check(refused({}) is not None, "an empty capture -> refused")


def main():
    tmp = tempfile.mkdtemp(dir=HERE, prefix="regress_test_")
    saved = os.environ.get("VTRUX_DATA")
    try:
        os.makedirs(os.path.join(tmp, "notes", "artifacts"))
        os.environ["VTRUX_DATA"] = tmp
        import regress as RG
        want = RG.BY_NAME["above_ceiling_start"].expect["state_events"]
        check(len(want) == 2, "the 83 %% case declares two transitions (%s)"
              % (want,))

        ok, why = RG.check_state_events(REAL, want)
        check(ok, "the events the replay produced pass -> %s" % why)

        def mut(fn):
            return RG.check_state_events(fn(list(REAL)), want)

        ok, why = mut(lambda e: [x for x in e if x[1] != "TERMINATED"])
        check(not ok and "no TERMINATED" in why,
              "the plug-out transition missing fails -> %s" % why)
        ok, why = mut(lambda e: e[:3] + [(56500,) + e[3][1:]] + e[4:])
        check(not ok and "outside" in why,
              "the plug-out at 56.5 s, outside its window, fails -> %s" % why)
        ok, why = mut(lambda e: e[:3] + [e[4], e[3]])
        check(not ok, "PASSTHROUGH before TERMINATED fails -> %s" % why)
        ok, why = mut(lambda e: e[:3] + [(57900, "SAFE") + e[3][2:]] + e[4:])
        check(not ok, "the plug-out logged in SAFE, not TERMINATED, fails "
              "-> %s" % why)
        ok, why = mut(lambda e: e[:4] + [(61000,) + e[4][1:]])
        check(not ok and "outside" in why,
              "PASSTHROUGH at 61.0 s fails -> %s" % why)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if saved is None:
            os.environ.pop("VTRUX_DATA", None)
        else:
            os.environ["VTRUX_DATA"] = saved

    bus_cases()

    if failures:
        print("\n%d failure(s)" % failures)
        return 1
    print("\nregress_offline_test: all checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
