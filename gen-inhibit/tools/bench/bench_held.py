#!/usr/bin/env python3
"""Whether the gen-inhibit tester holds the bench lock, for the bench tools that check it
themselves before they transmit (interlock_bench.py run, x2_pins_noreset_check.py).

The lock is the bench log, firmware/bench-log.jsonl, since the cutover of 2026-10-24
(AGENTS.md "The shared bench"). It is replayed here by shared/bench's own
vtrux_bench.bench_lock, the code that writes it, so vtrux_bench must be importable:
the pinned core's shared/bench on PYTHONPATH, as for the chain runner. bench-holder.md's
Holder line is the frozen history from before the cutover and is not read.

Fails closed: the lock is ours only when the log replays and its holder is ROLE. A
missing log, one that does not replay, the wrong host, a free lock, the other tester
or an implementor (a loan is the implementor's own take) are each not ours, with the
reason in the second value.

    py -3.14 -B gen-inhibit/tools/bench/bench_held.py     prints the answer; exit 0 ours, 2 not

Gen-inhibit tester, 2026-10-25: B20 of the 6926fb0 bench run refused at "bench-holder.md
names 'free' as the holder" with the runner holding the lock in the log.
"""
import sys

import gi_paths  # noqa: E402  SeaDrive paths (goal-20 move, 2026-10-22)

ROLE = "tester (gen-inhibit)"


def held_by_us(log=None):
    """(ok, who): ok when the bench log's holder is ROLE; who names the holder, or why
    the log could not say."""
    try:
        from vtrux_bench import bench_lock
    except ImportError as e:
        return False, "vtrux_bench is not importable (%s): put the pinned core's shared/bench on PYTHONPATH" % e
    try:
        state = bench_lock.read_state(log or gi_paths.bench_log())
    except Exception as e:  # noqa: BLE001  any failure to read the lock is "not ours"
        return False, "the bench log could not be read: %s" % e
    h = state["holder"]
    if h is None:
        return False, "free"
    return h["who"] == ROLE, h["who"]


if __name__ == "__main__":
    ok, who = held_by_us()
    print("%s: %s" % ("ours" if ok else "NOT OURS", who))
    sys.exit(0 if ok else 2)
