"""Harness test for observe_ack_probe: the verdict, and the PCAN status reading.

    py -3.14 gen-inhibit/tools/bench/test_observe_ack_probe.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import observe_ack_probe as O                                    # noqa: E402


def A(mode, err, read=None):
    return {"mode": mode, "mode_read": mode if read is None else read, "ack_errors": err}


def main():
    from can.interfaces.pcan import basic as B
    good = [A(0, True), A(1, True), A(2, False), A(1, True), A(0, True)]
    J = O.judge
    checks = [
        ("spec shape: OFF/OBSERVE errors, RESPOND none -> as specified",
         J(good)["verdict"] == "as specified"),
        ("OBSERVE ACKed -> NOT as specified",
         J([A(0, True), A(1, False), A(2, False)])["verdict"] == "NOT as specified"),
        ("RESPOND with ACK errors -> NOT as specified",
         J([A(0, True), A(1, True), A(2, True)])["verdict"] == "NOT as specified"),
        ("mode-0 control ACKed -> VOID, whatever OBSERVE did",
         J([A(0, False), A(1, True), A(2, False)])["verdict"].startswith("VOID")),
        ("no mode-0 control -> VOID", J([A(1, True), A(2, False)])["verdict"].startswith("VOID")),
        ("page not in the requested mode -> arm INVALID, and RESPOND untested -> INCOMPLETE",
         J([A(0, True), A(1, True), A(2, False, read=0)])["rows"][2]["result"].startswith("INVALID")
         and J([A(0, True), A(1, True), A(2, False, read=0)])["verdict"].startswith("INCOMPLETE")),
        ("status OK -> no ack errors", O.status_name(B.PCAN_ERROR_OK) == "OK"
         and not O.ack_errors(O.status_name(B.PCAN_ERROR_OK))),
        ("status BUSHEAVY -> ack errors", O.ack_errors(O.status_name(B.PCAN_ERROR_BUSHEAVY))),
        ("status BUSLIGHT -> ack errors", O.ack_errors(O.status_name(B.PCAN_ERROR_BUSLIGHT))),
        ("status QRCVEMPTY alone is not a bus error",
         not O.ack_errors(O.status_name(B.PCAN_ERROR_QRCVEMPTY))),
    ]
    for args, label in ((["--power", "x"], "--x2 required"), (["--x2", "held"], "--power required")):
        try:
            O.build_parser().parse_args(args)
            checks.append((label, False))
        except SystemExit:
            checks.append((label, True))
    fails = []
    for label, ok in checks:
        print("  %-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            fails.append(label)
    print("PASS" if not fails else "FAIL: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
