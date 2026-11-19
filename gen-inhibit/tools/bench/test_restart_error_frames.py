"""Harness test for restart_error_frames' analysis and its poll read (no bus).

    python3.13 -B gen-inhibit/tools/bench/test_restart_error_frames.py [--mutants]   (from the repo root)

--mutants applies each edit in MUTANTS to a copy of restart_error_frames.py in the worktree's scratch/
and requires a FAIL line from this test on each.
"""
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gi_paths  # noqa: E402  scratch/ folders
SCRIPT = os.environ.get("RESTART_ERROR_FRAMES") or os.path.join(HERE, "restart_error_frames.py")
_spec = importlib.util.spec_from_file_location("restart_error_frames", SCRIPT)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


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

    # the poll thread's read: the page kept, or after the restart POST the hotspot's rejoin (2026-11-19)
    class LFp(object):
        def __init__(self, page):
            self.p = page

        def page(self):
            return self.p

    class Spy(object):
        def __init__(self):
            self.calls = []

        def unreadable(self, t_post):
            self.calls.append(t_post)
    page = {"uptime_ms": 5, "mode": 0, "inhibit_live": False, "reset_reason": "SW",
            "drv": {"state": "running", "bus_error": 0, "tec": 0}, "gi_hook": 1}
    reads, spy = [], Spy()
    R.poll_once(LFp(page), reads, {"reboot_epoch": 100.0}, spy, "gi_hook")
    checks.append(("poll: a page read is kept with the hook field, and no rejoin",
                   len(reads) == 1 and reads[0][1:] == (5, 0, False, "SW", "running", 0, 0, 1) and spy.calls == []))
    reads, spy = [], Spy()
    try:
        R.poll_once(LFp(None), reads, {}, spy, None)
        raised = None
    except Exception as e:      # noqa: BLE001  a read that raises before the restart is a FAIL here
        raised = e
    checks.append(("poll: no page before the restart POST: no rejoin, nothing kept, no raise",
                   raised is None and reads == [] and spy.calls == []))
    R.poll_once(LFp(None), reads, {"reboot_epoch": 100.0}, spy, None)
    checks.append(("poll: no page after the restart POST: the rejoin asked, from the POST's time",
                   reads == [] and spy.calls == [100.0]))
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


MUTANTS = [
    ("no rejoin after the restart, as in run cb08", '        rejoin.unreadable(ev["reboot_epoch"])\n', "        pass\n"),
    ("a rejoin before the restart too", '    elif ev.get("reboot_epoch"):\n', "    else:\n"),
]


def mutants():
    src = open(SCRIPT, encoding="utf-8", newline="").read()
    base = gi_paths.scratch("mutants-restart_error_frames")
    killed = errors = 0
    for n, (name, old, new) in enumerate(MUTANTS, 1):
        if src.count(old) != 1:
            print("ERROR    m%d %s: edit matched %d times" % (n, name, src.count(old)))
            errors += 1
            continue
        d = os.path.join(base, "m%d" % n)
        gi_paths.remove_tree(d)
        os.makedirs(d)
        path = os.path.join(d, "restart_error_frames.py")
        open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)], capture_output=True, text=True,
                           env=dict(os.environ, RESTART_ERROR_FRAMES=path), timeout=120)
        fl = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("FAIL ")]
        dead = bool(fl) and "Traceback" not in r.stderr
        killed += dead
        print("%s m%d %s  (exit %d, %d FAIL)" % ("KILLED  " if dead else "SURVIVED", n, name, r.returncode, len(fl)))
    gi_paths.remove_tree(base)
    print("\n%d of %d killed by a check, %d errors" % (killed, len(MUTANTS), errors))
    return 0 if killed == len(MUTANTS) and not errors else 1


if __name__ == "__main__":
    sys.exit(mutants() if "--mutants" in sys.argv else main())
