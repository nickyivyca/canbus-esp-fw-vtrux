"""Harness test for slcan_refusal_probe.judge (spec 3.2).

    py -3.14 gen-inhibit/tools/bench/test_slcan_refusal_probe.py   (from the repo root)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slcan_refusal_probe as P                                    # noqa: E402


def st(**kw):
    s = {"mode": 3, "bus_on": True, "drv_state": "running", "inhibit_live": True, "self_off": "",
         "disabled": False, "arm_block": "", "abort_reason": "", "tx_ok": 100, "keys": ["mode"]}
    s.update(kw)
    return s


def main():
    off = dict(mode=0, bus_on=False, drv_state=None, inhibit_live=False, tx_ok=0)
    checks = [
        ("armed, nothing changes, device keeps answering -> PASS",
         P.judge("armed", st(), st(tx_ok=150), {0x051})["verdict"] == "PASS"),
        ("armed, a close forces mode OFF -> FAIL",
         P.judge("armed", st(), st(mode=0, inhibit_live=False, tx_ok=150), set())["verdict"] == "FAIL"),
        ("armed, the device stops answering -> FAIL",
         P.judge("armed", st(), st(tx_ok=100), {0x051})["verdict"] == "FAIL"),
        ("the injected frame on the wire -> FAIL",
         P.judge("armed", st(), st(tx_ok=150), {0x6A6})["verdict"] == "FAIL"),
        ("OFF, an SLCAN open installs the driver -> FAIL",
         P.judge("off", st(**off), st(**dict(off, drv_state="running", bus_on=True)), set())["verdict"] == "FAIL"),
        ("OFF, a WiCAN frame appears -> FAIL", P.judge("off", st(**off), st(**off), {0x7F1})["verdict"] == "FAIL"),
        ("OFF, nothing changes -> PASS", P.judge("off", st(**off), st(**off), {0x123})["verdict"] == "PASS"),
        ("OFF: the X2's pages (0x7F4-0x7F7) and the replayed VCM's 0x051 are not the WiCAN's",
         P.judge("off", st(**off), st(**off), {0x051, 0x7F4, 0x7F5, 0x7F6, 0x7F7})["verdict"] == "PASS"),
        ("a page key that appears after the command is reported",
         P.judge("off", st(**off), st(**dict(off, keys=["mode", "slcan_refused"])), set())["new_page_keys"]
         == ["slcan_refused"]),
        ("an unread page is UNREAD, never PASS", P.judge("off", None, st(**off), set())["verdict"] == "UNREAD"),
    ]
    C = P.judge_counted
    r0 = dict(refused_slcan=4, refused_can_send=1)
    checks += [
        ("counted: refused_slcan +1 -> PASS",
         C(st(**r0), st(**dict(r0, refused_slcan=5)))["counted"] == "PASS"),
        ("counted: refused_can_send +1 instead (wrong path booked) -> FAIL",
         C(st(**r0), st(**dict(r0, refused_can_send=2)))["counted"].startswith("FAIL")),
        ("counted: both +1 (counted twice) -> FAIL",
         C(st(**r0), st(**dict(r0, refused_slcan=5, refused_can_send=2)))["counted"].startswith("FAIL")),
        ("counted: refused_slcan +2 for one command -> FAIL",
         C(st(**r0), st(**dict(r0, refused_slcan=6)))["counted"].startswith("FAIL")),
        ("counted: neither rises -> FAIL (silently dropped)", C(st(**r0), st(**r0))["counted"].startswith("FAIL")),
        ("counted: one down, one up (sum 0) -> FAIL",
         C(st(**r0), st(**dict(r0, refused_slcan=5, refused_can_send=0)))["counted"].startswith("FAIL")),
        ("counted: a counter going down by a reboot is FAIL, even if the other rose by 2",
         C(st(**r0), st(**dict(r0, refused_slcan=6, refused_can_send=0)))["counted"].startswith("FAIL")
         and "went down" in C(st(**r0), st(**dict(r0, refused_slcan=6, refused_can_send=0)))["counted"]),
        ("counted: the delta per counter is recorded",
         C(st(**r0), st(**dict(r0, refused_slcan=6)))["refused_delta"]
         == {"refused_slcan": 2, "refused_can_send": 0}),
        ("counted: a page without the counters (old build) -> UNRESOLVED, never PASS",
         C(st(), st())["counted"].startswith("UNRESOLVED")),
        ("counted: one counter missing after -> UNRESOLVED",
         C(st(**r0), st(refused_slcan=5))["counted"].startswith("UNRESOLVED")),
        ("counted: unread page -> UNREAD", C(None, st(**r0))["counted"] == "UNREAD"),
        ("state(): the counters are copied from the page when present, absent when not",
         P.state({"mode": 0, "refused_slcan": 3, "refused_can_send": 0}).get("refused_slcan") == 3
         and "refused_slcan" not in P.state({"mode": 0})),
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
