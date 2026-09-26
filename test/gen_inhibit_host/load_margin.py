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
WORST_PREEMPT_US = 2390
ERASE_BLOCK_US = 20000

sys.path.insert(0, HERE)
import make_scenarios as mk                                    # noqa: E402


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

    if base and base[1] is not None:
        print("At the shipped depth of 5, preemption loses a frame from %d us "
              "-- inside the %d us worst measured preemption, which is why "
              "load-preempt-overflows-queue loses one."
              % (base[1], WORST_PREEMPT_US))
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
    print("This is a MODEL. Review A4: the full-replay bench run is what "
          "calibrates it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
