"""Harness test for autoarm_boot_to_live: boot instant, clock offset, first live frame.

    py -3.14 gen-inhibit/tools/bench/test_autoarm_boot_to_live.py   (from the repo root)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autoarm_boot_to_live as A                                   # noqa: E402
import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)


def main():
    # the first read's response was slow (it trails its own uptime by 0.2 s): the
    # median must not follow it
    reads = [(1000.50, 300), (1000.52, 510), (1000.71, 720), (1001.0, None)]
    checks = [
        ("boot instant = median(read time - uptime); a read without uptime is ignored",
         abs(A.boot_instant(reads) - 1000.01) < 1e-6),
        ("no reads -> None, never 0", A.boot_instant([]) is None),
        ("clock offset: the minimum (an idle-queue frame) is the estimate, median/max kept",
         A.clock_offset([(10.0, 10.002), (11.0, 11.010), (12.0, 12.003)]) == (
             (10.002 - 10.0), (12.003 - 12.0), (11.010 - 11.0))),
        ("first device frame at or after the boot", A.first_after([5.0, 1.0, 7.0], 4.0) == 5.0),
        ("no device frame after the boot -> None", A.first_after([1.0, 2.0], 4.0) is None),
        # a restart 80 s after the last one: the reads before the reset have uptime < 120 s too
        ("reads after the reset only, even when the pre-reset uptime is short",
         A.after_last_reboot([(1.0, 80000), (1.2, 80200), (3.5, 300), (3.7, 500)]) == [(3.5, 300), (3.7, 500)]),
        ("no reset seen -> no post-boot reads", A.after_last_reboot([(1.0, 80000), (1.2, 80200)]) == []),
        ("reads without uptime are skipped, not taken as a reset",
         A.after_last_reboot([(1.0, 80000), (1.1, None), (3.5, 300)]) == [(3.5, 300)]),
        ("a read without uptime between two pre-reset reads is no reset",
         A.after_last_reboot([(1.0, 80000), (1.1, None), (1.2, 80200)]) == []),
        ("two resets in one run (e.g. a rollback): the reads after the LAST one",
         A.after_last_reboot([(1, 80000), (2, 300), (3, 500), (4, 200), (5, 400)]) == [(4, 200), (5, 400)]),
        ("restart plan: a reset 3 s BEFORE the edge -> POST 5 s before, bus silent >= 5 s",
         A.reboot_plan(-3.0, delay=2.0, margin=1.0) == (6.0, -5.0)),
        ("restart plan: a reset 4 s AFTER the edge -> POST 2 s after, only the margin of silence",
         A.reboot_plan(4.0, delay=2.0, margin=1.0) == (1.0, 2.0)),
        ("restart plan: the documented 2.0 s reboot timer", A.REBOOT_DELAY_S == 2.0),
    ]
    import cantools
    pt = cantools.database.load_file(gi_paths.vehicle_dbc("vtrux-powertrain-experimental.dbc"), strict=False)
    ep = cantools.database.load_file(gi_paths.vehicle_dbc("epri-pt-bus.dbc"), strict=False)

    def c051(t, rpm_ref, torque):
        return (t, 0x051, pt.encode_message(0x051, {"gen_rpm_ref": rpm_ref, "gen_torque_cmd": torque},
                                            strict=False), False)

    def c054(t, rpm):
        return (t, 0x054, ep.encode_message(0x054, {"GENE_RotSpd": rpm, "GENE_EmergcyStp_flg": 0},
                                            strict=False), False)
    fr = [c051(0.0, -1, 0), c051(1.0, -1, 0), c051(2.0, -1, 328), c054(2.5, 500), c051(3.0, -1, 0),
          c054(3.5, 100), c051(4.0, -1, 0), c051(5.0, 1500, 0)]
    win, q = A.gate_closed_windows(fr, pt, ep)
    vcm = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    W = [(2.0, 3.5), (5.0, 60.0)]
    checks += [
        ("gate closed while torque != 0 OR GENE >= 300 rpm, open between, closed again on gen_rpm_ref >= 0",
         win == [(2.0, 3.0), (5.0, 5.0)]),
        ("the first request is the third 0x051 (index 2)", q == 2),
        ("live inside a closed window", A.live_in_closed(2.5, W) and not A.live_in_closed(4.0, W)),
        ("never live -> not inside a window", not A.live_in_closed(None, W)),
        ("live exactly at the request frame is inside (inclusive edge)", A.live_in_closed(2.0, W)),
        ("held the request: a device frame between commands 1 and 2", A.held_first_request(vcm, [1.4], 2) is True),
        ("not held: the device's first frame after the request", A.held_first_request(vcm, [2.4], 2) is False),
        ("no request -> held is None", A.held_first_request(vcm, [1.4], None) is None),
        ("open spans between closed windows", A.open_spans(W, 0.0, 70.0) == [(0.0, 2.0), (3.5, 5.0), (60.0, 70.0)]),
        ("restart: live before the request and held -> as specified",
         A.restart_verdict("as specified", 1.5, W, True, 2.0, 0.9, 70.0) == "as specified"),
        ("restart: live before the request but not held -> NOT",
         A.restart_verdict("as specified", 1.5, W, False, 2.0, 0.9, 70.0).startswith("NOT")),
        ("restart: live inside a closed window -> NOT",
         A.restart_verdict("as specified", 2.5, W, None, 2.0, 1.9, 70.0).startswith("NOT")),
        ("restart: live after the request, in an open span -> as specified",
         A.restart_verdict("as specified", 4.0, W, None, 2.0, 3.6, 70.0) == "as specified"),
        ("restart: stale frames after the boot -> NOT, whatever the timing",
         A.restart_verdict("NOT as specified: 2 stale", 1.5, W, True, 2.0, 0.9, 70.0).startswith("NOT")),
        ("restart: never live although a 9.5 s open span follows the boot -> NOT",
         A.restart_verdict("VOID: never live", None, [(2.0, 50.0)], None, 2.0, 50.0, 60.0).startswith("NOT")),
        ("restart: never live, gate closed from the boot to the end -> as specified",
         A.restart_verdict("VOID: never live", None, [(7.741, 70.0)], None, 7.741, 9.0, 70.0).startswith("as specified")),
        ("restart: never live, booted with only 0.7 s open before the request -> as specified",
         A.restart_verdict("VOID: never live", None, [(7.741, 70.0)], None, 7.741, 7.0, 70.0).startswith("as specified")),
        ("restart: never live, booted 3 s before key-on, 7.7 s open -> NOT",
         A.restart_verdict("VOID: never live", None, [(7.741, 70.0)], None, 7.741, -3.0, 70.0).startswith("NOT")),
    ]
    checks += [
        ("send_summary: none failed", A.send_summary([]) == {"send_failures": 0, "first_send_failure_s": None}),
        ("send_summary: count and the earliest offset, whatever the order",
         A.send_summary([20.4, 20.35, 21.0]) == {"send_failures": 3, "first_send_failure_s": 20.35}),
        ("error_summary: errors near the boot counted within +/-1 s, first relative to the boot",
         A.error_summary([99.2, 100.4, 101.0, 105.0], 100.0)
         == {"witness_error_frames": 4, "near_boot": 3, "first_rel_boot_s": -0.8}),
        ("error_summary: no boot -> near_boot None, count kept",
         A.error_summary([1.0], None) == {"witness_error_frames": 1, "near_boot": None, "first_rel_boot_s": None}),
        ("sent_frames: only the frames whose send succeeded, in order, whole tuples",
         A.sent_frames([(0.0, 1, b"a", False), (0.1, 2, b"b", False), (0.2, 3, b"c", True)], [0, 2])
         == [(0.0, 1, b"a", False), (0.2, 3, b"c", True)]),
        ("sent_frames: nothing sent -> nothing", A.sent_frames([(0.0, 1, b"a", False)], []) == []),
        ("error_summary: no errors -> 0 near the boot, no first",
         A.error_summary([], 100.0) == {"witness_error_frames": 0, "near_boot": 0, "first_rel_boot_s": None}),
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
