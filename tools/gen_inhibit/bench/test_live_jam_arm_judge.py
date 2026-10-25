"""Harness test for live_jam_arm.judge() -- criteria A, B, C. Synthetic, no bus.

Every criterion has at least one case where it must FAIL, so none of them is
a check that cannot fail.

Run from the project root:
  py -3.14 tools/gen_inhibit/bench/test_live_jam_arm_judge.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import live_jam_arm as L                                        # noqa: E402
SCHEMA = L.gi_diag.schemas_for().head_version
import gi_paths                                                 # noqa: E402

# The fixture's record, in SeaDrive since the move (goal 20, 2026-10-22): the
# 2026-10-12 livetrain runs went into the baseline folder (reviewer, 2026-10-24),
# and this is the file's sha256 there and in madhouse's copy.
FIXTURE = ("baseline_c65c45e", "bench", "x2_jam_livetrain_20261012T185212.json")
FIXTURE_SHA256 = "d4f9c7c5106a02a13fd48ea317439c41f6e5f49ec7b378a847681a8487420d66"

TRIP6 = "error-frame rate exceeded"


def s(pt, live, latched=False, reason="", state="running", berr=0):
    return {"pt": pt, "snap": {"inhibit_live": live, "abort_latched": latched,
                               "abort_reason": reason, "drv_state": state,
                               "drv_bus_error": berr}}


def vcm(pt, ctr):
    return (pt, 0x051, "080080ff7f%02x" % ctr)


def f7f1(pt, flags):
    """A 0x7F1 on HEAD's schema (gi_diag refuses a schema no DBC in git states; it read
    schema 6 until 2026-10-24, when byte 3 was read by hand)."""
    return (pt, 0x7F1, "%02x0103%02x000000ff" % (SCHEMA, flags))


def base():
    """Live from t=4, jam 10-15, trip 6 seen at 10.2, clean afterwards."""
    wire = []
    ctr = 0
    t = 0.0
    while t < 30.0:
        ctr = (ctr + 1) & 0x0F
        wire.append(vcm(t, ctr))
        if 4.0 <= t < 10.1:
            wire.append(vcm(t + 0.001, (ctr + 1) & 0x0F))   # device answer
        t += 0.01
    wire = [w for w in wire]
    wire.append(f7f1(9.0, 0x66))           # live, bus_on
    wire.append(f7f1(10.5, 0x86))          # after latch: bit7 set, bit5 clear
    samples = [s(x / 10.0, x >= 40) for x in range(0, 100)]
    samples += [s(10.2, False, True, TRIP6, berr=40)]
    samples += [s(10.3 + x / 10.0, False, True, TRIP6, berr=60)
                for x in range(0, 150)]
    return {"phases": {"replay_start": 0.0, "arm_posted": 3.0,
                       "jam_start": 10.0, "jam_stop": 15.0},
            "samples": samples, "wire": wire,
            "witness_error_pts": [10.0 + i * 0.003 for i in range(1500)]}


def mut(fn):
    r = copy.deepcopy(base())
    fn(r)
    return r


def no_latch(r):
    for x in r["samples"]:
        x["snap"]["abort_latched"] = False
        x["snap"]["abort_reason"] = ""


def other_reason(r):
    for x in r["samples"]:
        if x["snap"]["abort_latched"]:
            x["snap"]["abort_reason"] = "bus lost -- no 0x051"


def late_answer(r):
    r["wire"].append(vcm(12.0, 3))
    r["wire"].append(vcm(12.0005, 3))


def bad_flags(r):
    r["wire"] = [w for w in r["wire"] if w[0] != 10.5] + [f7f1(10.5, 0xA6)]


def relive(r):
    r["samples"].append(s(14.0, True, True, TRIP6))


def neg_control(r):
    # Between base frames (they fall on 10 ms steps), so the pair is adjacent.
    r["wire"].append(vcm(1.005, 7))
    r["wire"].append(vcm(1.0055, 7))


def busoff_then_live(r):
    r["samples"].append(s(11.0, False, True, TRIP6, state="bus_off"))
    r["samples"].append(s(13.0, True, True, TRIP6))


def busoff_clean(r):
    r["samples"].append(s(11.0, False, True, TRIP6, state="bus_off"))


def reset_before_jam(r):
    for x in r["samples"]:
        x["snap"]["mode"] = 3
        x["snap"]["tx_ok"] = int(x["pt"] * 100)
        if 7.0 <= x["pt"] < 9.0:
            x["snap"] = None
        elif 9.0 <= x["pt"] < 10.0:
            x["snap"].update(mode=0, tx_ok=0, inhibit_live=False)


def never_live(r):
    for x in r["samples"]:
        x["snap"]["inhibit_live"] = False


# VCM duplicates (reviewer's ruling, 2026-10-12): a same-counter pair after the
# latch, between base frames (12.00 carries counter 1, 12.01 counter 2), with tx_ok
# on every page reading: rising while live, flat at 1100 from the latch.
def with_tx(r, bump_after=None):
    for x in r["samples"]:
        x["snap"]["tx_ok"] = int(x["pt"] * 100) if x["pt"] < 10.2 else 1100
        if bump_after is not None and x["pt"] >= bump_after:
            x["snap"]["tx_ok"] = 1101


def pair(r, gap, first="080080ff7f09", second="080080ff7f09"):
    r["wire"].append((12.0035, 0x051, first))
    r["wire"].append((12.0035 + gap, 0x051, second))


def dup_ok(r):
    with_tx(r)
    pair(r, 0.00025)


def dup_too_wide(r):
    with_tx(r)
    pair(r, 0.00040)


def dup_payload_differs(r):
    with_tx(r)
    pair(r, 0.00025, first="0b0080ff7f09")


def dup_tx_moved(r):
    with_tx(r, bump_after=12.05)
    pair(r, 0.00025)


def dup_no_tx(r):
    pair(r, 0.00025)


CASES = [
    ("clean: trip 6, ended, stays ended", base(),
     {"A": "PASS", "B": "PASS", "C": "NOT REACHED"}),
    ("A: no abort despite >= 10 errors", mut(no_latch), {"A": "FAIL"}),
    ("A: other trip first is a finding", mut(other_reason), {"A": "FINDING"}),
    ("B: device 0x051 after the latch", mut(late_answer), {"B": "FAIL"}),
    ("B: next 0x7F1 bit5 set / bit7 clear", mut(bad_flags), {"B": "FAIL"}),
    ("B: page live again after the latch", mut(relive), {"B": "FAIL"}),
    ("B: negative control not zero", mut(neg_control),
     {"B": "INCONCLUSIVE"}),
    ("C: live again after bus-off", mut(busoff_then_live), {"C": "FAIL"}),
    ("C: bus-off and stays ended", mut(busoff_clean), {"C": "PASS"}),
    ("B: VCM duplicate 0.25 ms, tx_ok flat: not judged", mut(dup_ok), {"B": "PASS"}),
    ("B: same pair 0.40 ms apart is a device frame", mut(dup_too_wide), {"B": "FAIL"}),
    ("B: same B5, payloads differ: device frame", mut(dup_payload_differs), {"B": "FAIL"}),
    ("B: tx_ok rose across the pair: device frame", mut(dup_tx_moved), {"B": "FAIL"}),
    ("B: no tx_ok on the page: device frame", mut(dup_no_tx), {"B": "FAIL"}),
]


def fixture_record(raw):
    """The fixture's record from its bytes, refused (ValueError) unless they hash to
    FIXTURE_SHA256."""
    import hashlib
    import json
    got = hashlib.sha256(raw).hexdigest()
    if got != FIXTURE_SHA256:
        raise ValueError("%s has sha256 %s, not the fixture's %s" % ("/".join(FIXTURE), got, FIXTURE_SHA256))
    return json.loads(raw.decode("ascii"))


def fixture_185212_arm2(bump=False):
    """The real pair: x2_jam_livetrain_20261012T185212.json arm 2 (FIXTURE), shaped as
    x2_jam_cases.case_livetrain shapes it for the judge. bump=True raises tx_ok by
    one on every reading from +1.30 s after the jam start: the same frames with the
    device having completed a transmit must read as a device frame. A file whose
    sha256 is not FIXTURE_SHA256 is refused, not judged."""
    with open(gi_paths.runs(*FIXTURE), "rb") as f:
        r = fixture_record(f.read())
    arm = r["arms"][2]
    ph = arm["phases"]
    lo, hi = ph["replay_start"] - 1, ph["mode0_posted"] + 1
    samples = []
    for t_, s_ in arm["samples"]:
        s_ = copy.deepcopy(s_)
        if bump and s_ and t_ >= ph["jam_start"] + 1.30 and s_.get("tx_ok") is not None:
            s_["tx_ok"] += 1
        samples.append({"pt": t_, "snap": s_})
    return {"phases": ph, "samples": samples,
            "wire": [tuple(x) for x in arm["wire"] if lo <= x[0] <= hi],
            "witness_error_pts": [x for x in arm["witness_error_times"] if lo <= x <= hi],
            "witness_sees_replay": True}


def main():
    failures = []
    for label, r, want in CASES:
        out = L.judge(r)
        ok = all(str(out.get(k, "")).startswith(v) for k, v in want.items())
        print("  %-4s %-40s %s" % ("ok" if ok else "FAIL", label,
                                  {k: str(out.get(k))[:40] for k in want}))
        if not ok:
            failures.append(label)
    for label, fn, extra in (
            ("never live", never_live, None),
            # The 2026-10-07 23:39 shape: live, page unanswered, then back in
            # mode 0 with tx_ok 0 before the jam. Must not score as (A) FAIL.
            ("reset before the jam", reset_before_jam,
             lambda o: o["device_reset_seen_rel_jam_s"]
             and o["A"] == "NOT EXERCISED")):
        out = L.judge(mut(fn))
        ok = out["verdict"] == "INCONCLUSIVE" and (extra is None or extra(out))
        print("  %-4s %-40s %s %s" % ("ok" if ok else "FAIL", label,
                                      out["verdict"],
                                      out.get("device_reset_seen_rel_jam_s")))
        if not ok:
            failures.append(label)
    with open(gi_paths.runs(*FIXTURE), "rb") as f:
        raw = f.read()
    refused = []
    for what, bad in (("one byte changed", raw[:-2] + bytes([raw[-2] ^ 1]) + raw[-1:]), ("one byte added", raw + b" ")):
        try:
            fixture_record(bad)
        except ValueError:
            refused.append(what)
    ok = len(refused) == 2 and fixture_record(raw)["arms"][2]
    print("  %-4s %-40s %s" % ("ok" if ok else "FAIL", "fixture: other bytes are refused", refused))
    if not ok:
        failures.append("fixture: other bytes are refused")
    out = L.judge(fixture_185212_arm2())
    ok = (out["B"] == "PASS" and len(out["vcm_duplicates_not_judged"]) == 1
          and abs(out["vcm_duplicates_not_judged"][0] - 1.2698) < 0.001)
    print("  %-4s %-40s %s %s" % ("ok" if ok else "FAIL", "fixture 185212 arm 2: duplicate",
                                  out["B"], out["vcm_duplicates_not_judged"]))
    if not ok:
        failures.append("fixture 185212 arm 2")
    out = L.judge(fixture_185212_arm2(bump=True))
    ok = out["B"].startswith("FAIL") and not out["vcm_duplicates_not_judged"]
    print("  %-4s %-40s %s" % ("ok" if ok else "FAIL", "fixture, tx_ok rising: device frame",
                               out["B"][:50]))
    if not ok:
        failures.append("fixture 185212 arm 2, tx_ok rising")
    print("\n=== %s ===" % ("PASS" if not failures else
                            "FAIL: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
