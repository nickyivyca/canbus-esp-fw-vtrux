"""Harness test for tec_busoff_sweep.judge_bus_on() (spec 10/11 bus_on).

Synthetic records, no bus. Each case states the verdict the judge must give;
the FAIL and INCONCLUSIVE cases are what show the check can fail.

Run from the repo root:
  py -3.14 gen-inhibit/tools/bench/test_busoff_bus_on_judge.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tec_busoff_sweep as T                                    # noqa: E402

# 0x7F1 on HEAD's schema (B0; gi_diag refuses a schema no DBC in git states, and these
# read schema 6 until 2026-10-24, when B3 was read by hand).
_S = "%02x" % T.gi_diag.schemas_for().head_version
BIT1 = _S + "010406000000ff"      # 0x7F1 with diag_flags = 0x06: bit1 set
NOBIT1 = _S + "010404000000ff"    # diag_flags 0x04: bit1 clear


def sn(state, bus_on):
    return {"drv_present": state is not None, "drv_state": state,
            "bus_on": bus_on}


def rec(samples, wire, pre=None):
    r = {"baseline": {"samples": [{"pt": pt, "snap": s} for pt, s in samples]},
         "wire_0x7F1": wire}
    if pre is not None:
        r["pre"] = pre
    return r


def good_samples():
    return [(1.0, sn("running", True)), (2.0, sn("running", True)),
            (3.0, sn("bus_off", False)), (4.0, sn("bus_off", False)),
            (5.0, sn(None, False)), (6.0, sn("running", True))]


CASES = [
    ("clean run with a bus-off interval", rec(good_samples(),
     [(0.5, BIT1), (2.5, BIT1), (5.5, BIT1)], pre=sn(None, False)),
     "PASS", lambda o: o["wire_gap_across_busoff_s"] == 3.0
     and o["wire_0x7F1_inside_busoff"] == 0),
    ("page says bus_on true while bus_off", rec(
        good_samples()[:2] + [(3.0, sn("bus_off", True))], [(0.5, BIT1)]),
     "FAIL", None),
    ("page says bus_on true with drv null", rec(
        good_samples() + [(7.0, sn(None, True))], [(0.5, BIT1)]),
     "FAIL", None),
    ("page says bus_on false while running", rec(
        [(1.0, sn("running", False)), (3.0, sn("bus_off", False))], []),
     "FAIL", None),
    ("untimed pre snapshot disagrees", rec(good_samples(), [(0.5, BIT1)],
     pre=sn(None, True)), "FAIL", None),
    ("never reached bus_off", rec([(1.0, sn("running", True)),
     (2.0, sn(None, False))], [(0.5, BIT1)]), "INCONCLUSIVE", None),
    ("bit1-clear frame on the wire is reported, not failed",
     rec(good_samples(), [(0.5, BIT1), (5.8, NOBIT1)]), "PASS",
     lambda o: len(o["wire_bit1_clear"]) == 1
     and o["wire_bit1_clear"][0]["rel_busoff_last_s"] == 1.8),
]


def main():
    failures = []
    for label, r, want, extra in CASES:
        out = T.judge_bus_on(r)
        ok = out["verdict"] == want and (extra is None or extra(out))
        print("  %-4s %-52s got %s" % ("ok" if ok else "FAIL", label,
                                       out["verdict"]))
        if not ok:
            failures.append(label)
            print("       %s" % out)
    print("\n=== %s ===" % ("PASS" if not failures else
                            "FAIL: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
