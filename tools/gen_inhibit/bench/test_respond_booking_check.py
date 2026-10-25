"""Harness test for respond_booking_check.judge (spec 11 (l)).

    py -3.14 tools/gen_inhibit/bench/test_respond_booking_check.py   (from the project root)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import respond_booking_check as R                                  # noqa: E402

def raises(fn, cls):
    try:
        fn()
    except cls:
        return True
    except Exception:       # noqa: BLE001
        return False
    return False


# A 0x7F1 on HEAD's schema before each fixture's probes, as on the wire (gi_diag, 2026-10-24).
S7 = (-1.0, 0x7F1, bytes([R.gi_diag.schemas_for().head_version, 1, 2, 0x06, 0, 0, 0, 0]))


def sc(on, unv, mx, sent=2993):
    return {"ontime": on, "ontime_unverified": unv, "unverified_max_backlog": mx,
            "cls": {"probe": {"sent": sent}}}


def main():
    J = lambda m, s: R.judge(m, s)[0]                              # noqa: E731
    checks = [
        ("RESPOND: probes booked, some unverified with a backlog -> PASS", J("respond", sc(2900, 80, 3)) == "PASS"),
        ("RESPOND: all verified, no backlog -> PASS", J("respond", sc(2993, 0, 0)) == "PASS"),
        ("RESPOND: nothing booked with probes sent (the cc34c80 shape) -> FAIL", J("respond", sc(0, 0, 0)) == "FAIL"),
        ("RESPOND: only unverified booked still counts as booked -> PASS", J("respond", sc(0, 5, 2)) == "PASS"),
        ("RESPOND: booked more than sent -> FAIL", J("respond", sc(2993, 1, 1)) == "FAIL"),
        ("RESPOND: booked exactly sent -> PASS", J("respond", sc(2990, 3, 1)) == "PASS"),
        ("RESPOND: unverified without a max backlog -> FAIL", J("respond", sc(2900, 5, 0)) == "FAIL"),
        ("RESPOND: a max backlog with no unverified -> FAIL", J("respond", sc(2900, 0, 2)) == "FAIL"),
        ("RESPOND: no probes sent, nothing booked -> PASS", J("respond", sc(0, 0, 0, sent=0)) == "PASS"),
        ("RESPOND: unbooked is reported", "unbooked 13" in R.judge("respond", sc(2900, 80, 3))[1]),
        ("OBSERVE: all 0 -> PASS", J("observe", sc(0, 0, 0)) == "PASS"),
        ("OBSERVE: ontime 1 -> FAIL", J("observe", sc(1, 0, 0)) == "FAIL"),
        ("PASSIVE: unverified 1 -> FAIL", J("passive", sc(0, 1, 0)) == "FAIL"),
        ("PASSIVE: max backlog 1 -> FAIL", J("passive", sc(0, 0, 1)) == "FAIL"),
        ("INHIBIT is not judged here", J("inhibit", sc(2900, 80, 3)) == "NOT JUDGED"),
        ("no sched -> UNRESOLVED, never PASS", J("respond", None) == "UNRESOLVED"
         and J("observe", {"skipped": 0}) == "UNRESOLVED"),
        ("the arm's mode is the page's: a re-arm run named 'observe' with an INHIBIT page is inhibit",
         R.arm_mode("observe", {"mode": 3}) == "inhibit" and R.arm_mode("respond", {"mode": 2}) == "respond"),
        ("no mode on the page -> the run's mode", R.arm_mode("passive", {}) == "passive"),
        ("balance: unbooked 13 against 13 late probes on the wire -> balanced",
         R.balance(sc(2900, 80, 3), 13) == {"balanced": True, "unbooked": 13, "late_on_wire": 13}),
        ("balance: against 12 -> not balanced", R.balance(sc(2900, 80, 3), 12)["balanced"] is False),
        ("balance: unverified probes count as booked (unbooked = sent - ontime - unverified)",
         R.balance(sc(2900, 93, 3), 0)["balanced"] is True),
        ("balance: a withdrawn probe -> None, with the numbers kept",
         (lambda r: r["balanced"] is None and r["unbooked"] == 13)(
             R.balance(dict(sc(2900, 80, 3), cls={"probe": {"sent": 2993, "withdrawn": 1}}), 13))),
        ("balance: no witness count -> None", R.balance(sc(2900, 80, 3), None)["balanced"] is None),
        ("late_probes: each probe right after its own command -> 0 late of 3",
         R.late_probes([S7, (0.0, 0x051, bytes([8, 0, 0, 0, 0, 0x0F])), (0.001, 0x7F0, bytes([0] * 6 + [0x0F, 0x5A])),
                        (0.010, 0x051, bytes([8, 0, 0, 0, 0, 0x00])), (0.011, 0x7F0, bytes([0] * 6 + [0x00, 0x5A])),
                        (0.020, 0x051, bytes([8, 0, 0, 0, 0, 0x01])), (0.021, 0x7F0, bytes([0] * 6 + [0x01, 0x5A]))])
         == (0, 3, 0, 0)),
        ("late_probes: a probe after the next command (answers 0x0F with 0x00 already on the wire) is late",
         R.late_probes([S7, (0.0, 0x051, bytes([8, 0, 0, 0, 0, 0x0F])), (0.010, 0x051, bytes([8, 0, 0, 0, 0, 0x00])),
                        (0.011, 0x7F0, bytes([0] * 6 + [0x0F, 0x5A])), (0.012, 0x7F0, bytes([0] * 6 + [0x00, 0x5A]))])
         == (1, 2, 0, 0)),
        ("late_probes: input out of time order is sorted first",
         R.late_probes([S7, (0.001, 0x7F0, bytes([0] * 6 + [0x0F, 0x5A])), (0.0, 0x051, bytes([8, 0, 0, 0, 0, 0x0F]))])
         == (0, 1, 0, 0)),
        ("late_probes: a probe before any 0x051 is not judged; a short frame is skipped",
         R.late_probes([S7, (0.0, 0x7F0, bytes([0] * 6 + [0x0F, 0x5A])), (0.001, 0x051, bytes([8, 0, 0])),
                        (0.002, 0x7F0, bytes([0] * 6 + [0x01, 0x5A]))]) == (0, 0, 0, 0)),
        ("late_probes: other ids are ignored",
         R.late_probes([S7, (0.0, 0x051, bytes([8, 0, 0, 0, 0, 0x03])), (0.001, 0x7F1, S7[2]),
                        (0.002, 0x7F0, bytes([0] * 6 + [0x03, 0x5A]))]) == (0, 1, 0, 0)),
        ("late_probes: the 0x051 counter is B5's low nibble",
         R.late_probes([S7, (0.0, 0x051, bytes([8, 0, 0, 0, 0, 0x13])), (0.001, 0x7F0, bytes([0] * 6 + [0x03, 0x5A]))])
         == (0, 1, 0, 0)),
        ("late_probes: B6 outside 0-15 is malformed, never judged (the published probe_vcm_ctr is 0-15)",
         R.late_probes([S7, (0.0, 0x051, bytes([8, 0, 0, 0, 0, 0x03])), (0.001, 0x7F0, bytes([0] * 6 + [0x23, 0x5A]))])
         == (0, 0, 1, 0)),
        ("late_probes: a marker other than 0x5A is malformed, never judged",
         R.late_probes([S7, (0.0, 0x051, bytes([8, 0, 0, 0, 0, 0x03])), (0.001, 0x7F0, bytes([0] * 6 + [0x03, 0x5B]))])
         == (0, 0, 1, 0)),
        ("late_probes: a 7-byte probe is malformed",
         R.late_probes([S7, (0.0, 0x051, bytes([8, 0, 0, 0, 0, 0x03])), (0.001, 0x7F0, bytes([0] * 6 + [0x03]))])
         == (0, 0, 1, 0)),
        ("late_probes: B6 is read from the DBC's probe_vcm_ctr (bit 48, 8 bits)",
         (lambda m: m.get_signal_by_name("probe_vcm_ctr").start == 48
          and m.get_signal_by_name("probe_vcm_ctr").length == 8)(
             R.gi_diag.schemas_for().head().db.get_message_by_frame_id(0x7F0))),
        ("late_probes: a probe before the first schema page is undecodable, never judged (reading Q2)",
         R.late_probes([(0.0, 0x051, bytes([8, 0, 0, 0, 0, 0x03])), (0.001, 0x7F0, bytes([0] * 6 + [0x03, 0x5A]))])
         == (0, 0, 0, 1)),
        ("late_probes: an unknown schema byte raises",
         raises(
             lambda: R.late_probes([(0.0, 0x7F1, bytes([0xEE] + [0] * 7))]), R.gi_diag.UnknownSchema)),
        ("judge_balance: balanced -> PASS", R.judge_balance(R.balance(sc(2900, 80, 3), 13))[0] == "PASS"),
        ("judge_balance: 13 unbooked against 12 late -> FAIL",
         R.judge_balance(R.balance(sc(2900, 80, 3), 12))[0] == "FAIL"),
        ("judge_balance: a withdrawn probe -> UNRESOLVED, never PASS",
         R.judge_balance(R.balance(dict(sc(2900, 80, 3), cls={"probe": {"sent": 2993, "withdrawn": 1}}), 13))[0]
         == "UNRESOLVED"),
        ("judge_balance: no witness count -> UNRESOLVED", R.judge_balance(R.balance(sc(2900, 80, 3), None))[0]
         == "UNRESOLVED"),
        ("RESPOND without cls.probe.sent -> UNRESOLVED", J("respond", {"ontime": 1, "ontime_unverified": 0,
                                                                      "unverified_max_backlog": 0}) == "UNRESOLVED"),
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
