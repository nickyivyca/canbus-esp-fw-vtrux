#!/usr/bin/env python3
"""E4: sweep the stall length and report WHERE loss first appears.

    python3 load_margin.py
    python3 load_margin.py --depth 5 --depth 32

"CLEAN AT 2.39 ms" IS NOT A MARGIN. It is one point, and a scenario that passes
at one point cannot tell you whether it passes by a hair or by a factor of six.
Spec 5's whole complaint about the bench numbers was that they were taken at a
single operating point that was not the truck's; repeating that mistake one
level down would be poor form.

So this sweeps two stall mechanisms against the RX buffer and prints the
shortest stall at which a frame is lost:

  preempt     the worker is off-CPU, but the TWAI ISR still runs, so frames
              keep reaching the driver's software queue. Its depth is the
              buffer, and a deeper queue moves this boundary out.

  cachestall  the instruction cache is off, so the ISR cannot run either
              (CONFIG_TWAI_ISR_IN_IRAM unset, per spec 5's decision). The
              software queue is not being filled at all and the only buffer is
              the controller's hardware FIFO -- so a deeper software queue
              moves this boundary NOT AT ALL. Spec 9.3 puts a flash erase's
              cache-off block at up to 20 ms.

The two columns being different is the point of the table. Measured against the
2.39 ms worst preemption and the 20 ms erase block, they say which remedy
actually buys margin and which only looks like it does.
"""

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "host_runner")

S = 1000000
MS = 1000

# Spec 5 and 9.3, the two numbers the sweep is judged against.
#
# THE DESIGN-CASE WORST PREEMPTION (user's ruling, 2026-09-26): normal truck
# operation, in which nothing is driving heavy WiFi traffic to this device while
# it is inhibiting. Spec 5.1 item 5 measures its 3x margin against this figure.
#
# ITS PROVENANCE IS STILL THE ~100 FRAMES/S BENCH RUN, and the truck-rate replay
# of 2026-09-26 did not change that. An earlier revision of this comment claimed
# the replay had checked the constant on hardware and found it conservative by
# better than 2x. THAT CLAIM IS WITHDRAWN. The reason is worth the space, because
# the mistake was invisible from inside the measurement:
#
#   NEITHER DEVICE INSTRUMENT CAN SEE THE WAIT THIS CONSTANT MODELS. The worker
#   timestamps a frame with esp_timer_get_time() only after twai_receive() has
#   returned it (main/gen_inhibit.c). Time the frame spent waiting in the
#   driver's receive queue -- which is precisely the wait the queue depth buffers
#   and precisely what "preemption" means here -- has already passed before
#   either clock starts. RESPOND's RX-to-queue (1611 us worst) and INHIBIT's
#   RX-to-completion (6796 us worst) are both blind to it, so neither is a
#   preemption figure and the 1111 us that was quoted as one is not.
#
# WHAT THE REPLAY DID ESTABLISH, and it argued the other way. Under a WiFi flood
# -- ~50 status-JSON requests/s, outside the design case -- the INHIBIT arm
# reached 6796 us RX-to-completion. On this bus a 0x051 can lose at most ~590 us
# to arbitration (only 0x050 outranks it, never more than one frame in any 7 ms;
# see e4_arbitration_check.py in the reverse-it repo), so at least ~6.2 ms of it
# was the worker off the CPU after dequeue: a LOWER BOUND on one stall. With WiFi
# idle the same figure was 690 us. Nothing was lost in either arm.
#
# That is what moved the receive queue from 32 to 64 (spec 5.1 item 1, amended):
# headroom taken against a stall larger than the design case, for about 1 KB.
#
# WHAT WILL SETTLE IT is the direct measurement, spec 12.4's measurement-only
# build: the driver's own RX backlog at each dequeue (msgs_to_rx + 1), high-water
# and large-backlog counts, on a full-replay bus in the design case. Backlog
# divided by arrival rate IS the off-CPU time -- ~423 us per slot at 2364
# frames/s -- so it measures the quantity instead of a proxy for it. Until that
# has run, this number is an assumption and this comment says so.
#
# Artifacts: projects/vtrux/notes/artifacts/gen-inhibit/e4_device_calibration.py,
# e4_arbitration_check.py, runs/e4_device_{calibration,loaded,respond}.json, in
# the reverse-it repo. Spec 5 carries the table and the withdrawal.
WORST_PREEMPT_US = 2390
ERASE_BLOCK_US = 20000

# Spec 5.1 item 5: the sweep must report its first loss at "at least 3x the
# measured worst (>= 7.2 ms)". 3 x 2390 is 7170; the spec states 7200 and that
# is the figure this enforces.
REQUIRED_MARGIN_US = 7200

sys.path.insert(0, HERE)
import make_scenarios as mk                                    # noqa: E402

# The depth the requirement is about is the FIRMWARE's, not whichever depths the
# sweep happens to be asked for. Taken from make_scenarios.RX_DEPTH because that
# is what is synced to the machine this runs on; main/can.c's CAN_RX_QUEUE_LEN is
# the real article and test/build_checks/build_output.py pins the two together,
# which is the only reason reading one here is sound.
FIRMWARE_DEPTH = mk.RX_DEPTH


def build(depth, kind, length_us):
    """One scenario: truck load, with a stall of `length_us` at t = 2.5 s."""
    lines = ["mode 0 3 500", "load 0 %d 500" % depth]
    frames = mk._at_truck_rate(1 * S, 4 * S)
    lines += frames

    # Against a real command arrival, as the E4 scenario does: the queue fills
    # on surrounding traffic and the command meets it full.
    cmd_t = None
    for ln in frames:
        p = ln.split()
        if len(p) >= 3 and p[0] == "f" and p[2].upper() == "051":
            t = int(p[1])
            if t >= 2500 * MS:
                cmd_t = t
                break

    start = cmd_t - length_us + 200
    lines.append("%s %d %d" % (kind, start, start + length_us))
    lines.append("end %d" % (5 * S))
    return mk.sorted_directives(lines)


def run(lines):
    txt = "\n".join(lines) + "\n"
    r = subprocess.run([RUNNER], input=txt, capture_output=True, text=True,
                       cwd=HERE, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("runner exited %d" % r.returncode)
    m = re.search(r"E4 rx_dropped=(\d+) rx_dropped_cmd=(\d+) "
                  r"rxq_high_water=(\d+) tx_behind=(\d+) "
                  r"tx_late_past_next=(\d+) stall_dropped=(\d+) "
                  r"stall_dropped_cmd=(\d+)", r.stdout)
    if not m:
        raise RuntimeError("no E4 line in the output")
    keys = ("dropped", "dropped_cmd", "high_water", "behind", "late",
            "stall_dropped", "stall_dropped_cmd")
    return dict(zip(keys, (int(x) for x in m.groups())))


def first_loss(depth, kind, lengths):
    for us in lengths:
        got = run(build(depth, kind, us))
        if got["dropped"]:
            return us, got
    return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--depth", type=int, action="append",
                    help="RX queue depth to sweep (repeatable)")
    args = ap.parse_args()
    depths = args.depth or [5, 8, 16, 32]
    # The firmware's depth is always swept, whatever was asked for: the spec
    # 5.1 item 5 verdict below is about that depth and nothing else, and a
    # --depth list that omitted it would otherwise skip the check silently.
    if FIRMWARE_DEPTH not in depths:
        depths = sorted(depths + [FIRMWARE_DEPTH])

    # Fine near the interesting region, coarse after it.
    lengths = ([500 * i for i in range(1, 12)]
               + [6000, 8000, 10000, 14000, 20000, 30000, 45000])

    print("Sweeping the shortest stall that loses a frame, at ~2250 fps.")
    print("Reference points: worst measured preemption %d us (spec 5), "
          "flash erase block %d us (spec 9.3).\n" % (WORST_PREEMPT_US,
                                                     ERASE_BLOCK_US))
    print("%-7s %-28s %-28s" % ("depth", "preempt: first loss at",
                                "cachestall: first loss at"))
    print("%-7s %-28s %-28s" % ("-" * 5, "-" * 26, "-" * 26))

    rows = []
    for d in depths:
        p_us, _ = first_loss(d, "preempt", lengths)
        c_us, _ = first_loss(d, "cachestall", lengths)
        rows.append((d, p_us, c_us))

        def fmt(us, ref):
            if us is None:
                return "no loss up to %d us" % lengths[-1]
            return "%6d us  (%.1fx the %s)" % (us, us / float(ref),
                                               "2.39 ms" if ref == WORST_PREEMPT_US
                                               else "20 ms")

        print("%-7d %-28s %-28s"
              % (d, fmt(p_us, WORST_PREEMPT_US), fmt(c_us, ERASE_BLOCK_US)))

    print()
    base = next((r for r in rows if r[0] == 5), None)
    deep = rows[-1]

    step = lengths[1] - lengths[0]
    if base and base[1] is not None:
        # STATED AS A BRACKET, because that is all a sweep on a fixed grid can
        # say. The previous wording -- "loses a frame from 2500 us, inside the
        # 2390 us worst measured preemption" -- read as though 2500 had been
        # found to be below 2390. What the sweep establishes is that the
        # boundary lies between the last clean step and the first lossy one,
        # and 2390 falls inside that interval rather than above it.
        print("At the stock depth of %d, the preemption boundary is between "
              "%d and %d us. The %d us worst measured preemption falls INSIDE "
              "that interval, so whether it loses a frame is not resolved at "
              "this grid -- which is why load-preempt-rides-through is written "
              "about the deeper queue and not about this one."
              % (base[0], max(0, base[1] - step), base[1], WORST_PREEMPT_US))
    if deep[1] is None or (base and base[1] and deep[1] and deep[1] > base[1]):
        print("A deeper software queue moves the PREEMPTION boundary out, as "
              "expected: the ISR keeps filling the queue, so depth is the "
              "buffer.")
    if base and deep[2] is not None and base[2] is not None and deep[2] == base[2]:
        print("It moves the CACHE-STALL boundary not at all (%d us at depth %d "
              "and at depth %d), because the ISR is not running and the "
              "hardware FIFO is the only buffer. A deeper queue is necessary "
              "and not sufficient." % (base[2], base[0], deep[0]))

    print()
    print("This is a MODEL, and review A4 asked for the full-replay bench run "
          "that calibrates it. The rig half (2026-09-26) found this model's air "
          "time conservative at every DLC and its bus occupancy slightly above "
          "the truck's. THE DEVICE HALF DID NOT CALIBRATE THE %d us ABOVE, "
          "though an earlier version of this line said it had: both device "
          "instruments start their clock after twai_receive() returns, so the "
          "queue wait this model is about is invisible to them. Spec 12.4's "
          "measurement-only build measures the backlog directly and is what "
          "confirms or revises it. The figure is the DESIGN CASE -- normal truck "
          "operation, no heavy WiFi while inhibiting; a >= 6.2 ms device-side "
          "stall was seen under a WiFi flood outside that case, which is why the "
          "queue is now %d deep." % (WORST_PREEMPT_US, FIRMWARE_DEPTH))

    # ---------------------------------------------------------------- verdict
    #
    # SPEC 5.1 ITEM 5 IS A REQUIREMENT, AND UNTIL NOW NOTHING ENFORCED IT.
    # This script always exited 0 and no suite invoked it, so the margin was
    # a number a person had to read and compare by eye. Mutations that moved
    # the boundary -- removing the queue-depth override, or setting it back to
    # 5 -- survived every suite for exactly that reason (review Q1/Q2,
    # 2026-09-26).
    row = next((r for r in rows if r[0] == FIRMWARE_DEPTH), None)
    print()
    if row is None:
        print("FAIL: the firmware depth (%d) was not swept, so spec 5.1 item 5 "
              "is unchecked." % FIRMWARE_DEPTH)
        return 1

    got = row[1]
    if got is None:
        print("PASS (spec 5.1 item 5): at the firmware's depth of %d no "
              "preemption up to %d us loses a frame, against the >= %d us "
              "required." % (FIRMWARE_DEPTH, lengths[-1], REQUIRED_MARGIN_US))
        return 0
    if got >= REQUIRED_MARGIN_US:
        print("PASS (spec 5.1 item 5): at the firmware's depth of %d the first "
              "loss is at %d us, %.1fx the %d us worst measured preemption, "
              "against the >= %d us (3x) required."
              % (FIRMWARE_DEPTH, got, got / float(WORST_PREEMPT_US),
                 WORST_PREEMPT_US, REQUIRED_MARGIN_US))
        return 0

    print("FAIL (spec 5.1 item 5): at the firmware's depth of %d the first "
          "loss is at %d us, only %.1fx the %d us worst measured preemption. "
          "The spec requires at least 3x (>= %d us)."
          % (FIRMWARE_DEPTH, got, got / float(WORST_PREEMPT_US),
             WORST_PREEMPT_US, REQUIRED_MARGIN_US))
    return 1


if __name__ == "__main__":
    sys.exit(main())
