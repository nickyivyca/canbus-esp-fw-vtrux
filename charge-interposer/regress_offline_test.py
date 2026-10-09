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


def path_cases(RG):
    """regress.check_state_path (2026-10-09, partstruck P1): the whole path
    from core.state, exactly, each transition in its window."""
    want = RG.BY_NAME["partstruck"].expect["state_path"]
    real = [(0, "PASSTHROUGH"), (27870, "MONITOR"),
            (6582624, "TERMINATED"), (34217348, "PASSTHROUGH")]
    ok, why = RG.check_state_path(real, want)
    check(ok, "the decoded partstruck path passes -> %s" % why)

    def bad(states, what):
        ok, why = RG.check_state_path(states, want)
        check(not ok, "%s fails -> %s" % (what, why))

    bad(real[:2] + [(3000000, "SAFE"), (3000100, "MONITOR")] + real[2:],
        "a SAFE and back inside the MONITOR stretch (unlogged)")
    bad(real[:2] + [(6582624, "OVERRIDE")] + real[2:],
        "an OVERRIDE before the TERMINATED")
    bad([x for x in real if x[1] != "TERMINATED"],
        "TERMINATED missing")
    bad(real[:2] + [(6584000, "TERMINATED")] + real[3:],
        "TERMINATED at 6584.0 s, outside its window")
    bad(real[:3] + [(34219000, "PASSTHROUGH")],
        "PASSTHROUGH at 34219.0 s, after the STAND_BY window")
    bad([(0, "MONITOR")] + real[1:], "a wrong starting state")
    bad([], "an empty path")


def states_sampling_cases():
    """replay.replay_offline's `states` is read from core.state after every
    frame and tick, so a change the core never logs is still recorded."""
    import machine as MM
    import replay as RP

    class Quiet(object):
        """A core that changes state and logs nothing."""
        def __init__(self):
            self.state = 0
            self.events = []
            self.n = 0

        def on_vehicle_frame(self, arb, ext, data, t_ms):
            self.n += 1
            if self.n == 3:
                self.state = 5          # SAFE, unlogged
            if self.n == 4:
                self.state = 1          # MONITOR, unlogged
            return [(MM.TO_CHARGER, arb, ext, data)]

        on_charger_frame = on_vehicle_frame

        def tick(self, t_ms):
            if t_ms == 100:
                self.state = 4          # TERMINATED on a tick, unlogged
            return []

    frames = [(i * 0.02, 0x410, False, bytes(8)) for i in range(8)]
    saved = RP.iter_pt
    RP.iter_pt = lambda path, bus_filter=None, ids=None: iter(frames)
    try:
        res = RP.replay_offline("unused", Quiet(), bus_filter=2)
    finally:
        RP.iter_pt = saved
    got = [s for _, s in res["states"]]
    check(got == ["PASSTHROUGH", "SAFE", "MONITOR", "TERMINATED"],
          "unlogged changes on frames and on a tick are all recorded, with "
          "the starting state first -> %s" % res["states"])


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
        path_cases(RG)
        states_sampling_cases()
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
