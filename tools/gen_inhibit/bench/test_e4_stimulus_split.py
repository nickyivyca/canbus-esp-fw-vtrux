"""Harness test for e4_device_calibration.vcm_commands_on_wire (the stimulus check).

    py -3.14 tools/gen_inhibit/bench/test_e4_stimulus_split.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import e4_device_calibration as E                               # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)


def poll_rate_ok():
    """Run a Hammer at 10 Hz for 0.55 s against a fake gi(): ~6 polls, each recorded.
    The flood (period 0) against the same fake must make far more."""
    import time
    calls = []

    def fake_gi(path, method="GET", timeout=6.0):
        calls.append(path)
        return 200, '{"drv":{"msgs_to_rx":%d,"rx_missed":0}}' % len(calls)
    real = E.gi
    E.gi = fake_gi
    try:
        h = E.Hammer(period=0.1)
        h.start()
        time.sleep(0.55)
        h.stop.set()
        h.join(2)
        light = (h.n, len(h.series), h.series[-1][1] if h.series else None)
        calls.clear()
        f = E.Hammer(period=0.0)
        f.start()
        time.sleep(0.05)
        f.stop.set()
        f.join(2)
        flood = f.n
    finally:
        E.gi = real
    return 5 <= light[0] <= 7 and light[1] == light[0] and light[2] == light[0] and flood > 50


def attach_failure_recorded():
    """A run record whose capture cannot be loaded: the arm must say NOT RUN, why, and
    still carry the superseded note. Writes a scratch record in runs/ and removes it."""
    import json
    path = gi_paths.runs("_test_attach_provenance_tmp.json")
    out = {"capture": "no_such_capture.log", "window_start": 0.0, "seconds": 1.0,
           "mode": "inhibit", "arms": {"idle": {"device": {"tx_ok": 1}}}}
    try:
        with open(path, "w") as fh:
            json.dump(out, fh)
        E.attach_provenance(path, out)
        rec = json.load(open(path))["arms"]["idle"]
        return (rec["provenance"]["verdict"] == "NOT RUN" and rec["provenance"].get("error")
                and "superseded" not in rec["provenance"] and "old trail-window" in
                rec["superseded_classifier"])
    finally:
        if os.path.exists(path):
            os.remove(path)


def main():
    f = E.vcm_commands_on_wire
    checks = [
        ("INHIBIT: 3670 on the wire, 670 ours -> 3000 VCM", f(E.MODE_INHIBIT, 3670, 670) == 3000),
        ("RESPOND: tx_ok counts probes, so all 2999 are the VCM's (the 2026-10-10 arm)",
         f(E.MODE_RESPOND, 2999, 2993) == 2999),
        ("INHIBIT with tx_ok missing -> nothing subtracted", f(E.MODE_INHIBIT, 3000, None) == 3000),
        ("PASSIVE: the device sends no 0x051, so nothing is subtracted whatever tx_ok reads",
         f(E.MODE_PASSIVE, 3000, 12) == 3000),
        ("a real short in RESPOND is still seen: 2990 of 2999",
         2999 - f(E.MODE_RESPOND, 2990, 2990) == 9),
        ("setter path carries the offset given",
         E.set_mode_path(2, 100) == "/gen_inhibit_set?mode=2&offset=100"
         and E.set_mode_path(0, 1000) == "/gen_inhibit_set?mode=0&offset=1000"),
        ("no hard-coded offset left in the setter calls (the sweep would not vary it)",
         "offset=500" not in open(E.__file__, encoding="utf-8").read()),
        ("page_queue_fields reads drv.msgs_to_rx and rx_missed",
         E.page_queue_fields('{"mode":2,"drv":{"msgs_to_rx":7,"rx_missed":16}}') == [7, 16]),
        ("page_state_fields reads mode and self_off (the quiesce reason)",
         E.page_state_fields('{"mode":1,"self_off":"forced quiesce -- x","drv":null}')
         == [1, "forced quiesce -- x"] and E.page_state_fields("{cut") == [None, None]),
        ("page_queue_fields: drv null or a cut page -> [None, None], no exception",
         E.page_queue_fields('{"drv":null}') == [None, None]
         and E.page_queue_fields('{"drv":{"msgs_to') == [None, None]),
        ("a 1 Hz Hammer polls about once a second and records each answer", poll_rate_ok()),
        ("provenance that cannot run is recorded NOT RUN with the reason, old figures marked "
         "superseded", attach_failure_recorded()),
    ]
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
