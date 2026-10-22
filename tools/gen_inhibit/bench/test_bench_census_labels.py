"""Harness test for bench_census.open_adapter(): an adapter's label must say what it does.

2026-10-09: the Kvaser Leaf Light v2 has no silent mode, so a census row that
labels it "silent" describes a node that ACKs. This opens each adapter against
a fake can.Bus that records the arguments, and requires the Kvaser to be
opened in normal mode and labelled ACKING, and the PCAN listen-only. A label
claiming silence for the Kvaser, or a Kvaser opened with driver_mode=False,
fails.

  py -3.14 tools/gen_inhibit/bench/test_bench_census_labels.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bench_census as BC                                       # noqa: E402


class FakeBus:
    calls = []

    def __init__(self, **kw):
        FakeBus.calls.append(kw)


def main():
    real = BC.can.Bus
    BC.can.Bus = FakeBus
    fails = []
    try:
        _, label = BC.open_adapter("kvaser")
        kw = FakeBus.calls[-1]
        ok = kw.get("driver_mode") is True and "ACKING" in label and "silent (" not in label
        print("  %-4s kvaser: normal mode, labelled ACKING   %s %r" % ("ok" if ok else "FAIL", kw, label))
        if not ok:
            fails.append("kvaser")
        _, label = BC.open_adapter("pcan")
        kw = FakeBus.calls[-1]
        ok = kw.get("state") == BC.can.BusState.PASSIVE and "listen-only" in label
        print("  %-4s pcan: listen-only                      %r" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append("pcan")
    finally:
        BC.can.Bus = real
    print("%s" % ("PASS" if not fails else "FAIL: %s" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
