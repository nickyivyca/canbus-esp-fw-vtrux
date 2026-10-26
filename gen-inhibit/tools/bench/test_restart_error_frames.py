"""Harness test for restart_error_frames' analysis (no bus).

    py -3.14 tools/gen_inhibit/bench/test_restart_error_frames.py   (from the project root)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import restart_error_frames as R                                   # noqa: E402


def f(t, i, d):
    return (t, i, bytes.fromhex(d))


def main():
    # a burst: 3 frames 100 us apart, a 5 ms gap, 2 more 200 us apart; boot at 10.0, POST at 7.6
    burst = [9.76, 9.7601, 9.7602, 9.7652, 9.7654]
    far = [1.0, 30.0]                        # outside the restart window
    b = R.burst_summary(burst + far, post_wire=7.6, boot_wire=10.0)
    sent = [f(0.0, 0x100, "01"), f(1.0, 0x100, "01"), f(2.0, 0x200, "02"), f(3.0, 0x051, "080080ff7f01"),
            f(4.0, 0x051, "080080ff7f02")]
    seen_all = list(sent)
    seen_lost = [x for x in sent if x[0] != 1.0]                  # one of the two 0x100#01 lost
    seen_dev = sent + [f(3.1, 0x051, "080080ff7f02")]           # a device answer, equal to the VCM's next
    # page reads (t, ..., hook last): off, then on twice before the POST at 10.0; from the
    # POST instant on: off, off again, the field absent, on
    hook_reads = [(8.0, 1, 0, 0, "SW", None, None, None, 0), (9.0, 1, 0, 0, "SW", None, None, None, 1),
                  (9.5, 1, 0, 0, "SW", None, None, None, 1), (10.0, 1, 0, 0, "SW", None, None, None, 0),
                  (12.0, 1, 0, 0, "SW", None, None, None, 0), (13.0, 1, 0, 0, "SW", None, None, None, None),
                  (14.0, 1, 0, 0, "SW", None, None, None, 1)]
    checks = [
        ("clumps: split at gaps over 1 ms, inclusive at 1 ms",
         [n for _s, _e, n in R.clumps([0.0, 0.0005, 0.0015, 0.0040])] == [3, 1]),
        ("clumps: unsorted input is sorted (late frame first must not swallow the early ones)",
         [n for _s, _e, n in R.clumps([0.010, 0.0, 0.0005])] == [2, 1]),
        ("burst: an error up to 1 s BEFORE the POST is in the window",
         (lambda x: x["in_window"] == 6 and x["first_rel_post_s"] == -0.4)(
             R.burst_summary([7.2] + burst, post_wire=7.6, boot_wire=10.0))),
        ("burst: only the restart window counts (POST - 1 s .. boot + 3 s)",
         b["in_window"] == 5 and b["outside_window"] == 2),
        ("burst: first/last relative to the boot", b["first_rel_boot_s"] == -0.24 and b["last_rel_boot_s"] == -0.2346),
        ("burst: first relative to the POST", b["first_rel_post_s"] == 2.16),
        ("burst: two clumps, sizes 3 and 2, largest gap 5 ms",
         b["clumps"] == 2 and b["clump_sizes"] == [3, 2] and b["largest_gap_ms"] == 5.0),
        ("burst: none in the window -> in_window 0, no timing invented",
         R.burst_summary(far, 7.6, 10.0) == {"in_window": 0, "outside_window": 2}),
        ("burst: no boot instant -> window ends POST + 3 s; rel-boot fields None",
         (lambda x: x["in_window"] == 5 and x["first_rel_boot_s"] is None)(
             R.burst_summary(burst, post_wire=7.6, boot_wire=None))),
        ("loss: everything seen -> 0", R.multiset_loss(sent, seen_all)["lost"] == 0),
        ("loss: one of two identical frames missing -> 1 (a multiset, not a set)",
         R.multiset_loss(sent, seen_lost)["lost"] == 1),
        ("loss: 0x051 left out of the main count", R.multiset_loss(sent, seen_all)["sent"] == 3),
        ("0x051 lower bound: a device answer does not count as a lost or extra VCM frame",
         R.cmd_loss_lower_bound(sent, seen_dev) == {"sent": 2, "seen": 3, "lost_lower_bound": 0}),
        ("0x051 lower bound: a lost command is counted",
         R.cmd_loss_lower_bound(sent, [x for x in sent if x[0] != 3.0])["lost_lower_bound"] == 1),
        ("window loss: a loss outside the window is not counted in it",
         R.window_loss(sent, seen_lost, 0.0, 1.5, 5.0)["lost"] == 0
         and R.window_loss(sent, seen_lost, 0.0, 0.5, 5.0)["lost"] == 1),
        ("window loss: the offset maps send times to the wire clock",
         R.window_loss([(t - 5.0, i, d) for t, i, d in sent], seen_lost, 5.0, 0.5, 5.0)["lost"] == 1),
        ("hook state: bool, 0/1 and on/off/true/false text; anything else is None",
         [R.hook_state(x) for x in (True, False, 1, 0, "on", "OFF", " true ", "0")]
         == [True, False, True, False, True, False, True, False]
         and [R.hook_state(x) for x in (None, 2, "maybe", 1.0, "")] == [None] * 5),
        ("hook judged by the LAST readback before the POST, not the first",
         R.hook_judged(hook_reads, 10.0, True)["readback_before_restart"] is True),
        ("hook as requested only when that readback equals the request",
         R.hook_judged(hook_reads, 10.0, True)["as_requested"] is True
         and R.hook_judged(hook_reads, 10.0, False)["as_requested"] is False),
        ("hook readings after the restart: each distinct one once, in order, the read at the POST included",
         R.hook_judged(hook_reads, 10.0, True)["readings_after_restart"] == [False, None, True]),
        ("hook: no read before the POST -> not as requested (an absent readback is not a pass)",
         R.hook_judged(hook_reads[3:], 10.0, False)["as_requested"] is False),
        ("hook: no POST time -> nothing judged",
         R.hook_judged(hook_reads, None, True)["readback_before_restart"] is None),
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
