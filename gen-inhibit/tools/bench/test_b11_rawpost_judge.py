"""Harness test for b11_rawpost_judge.py's pure parts (no records, no capture, no device).

    py -3.14 gen-inhibit/tools/bench/test_b11_rawpost_judge.py   (from the repo root)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import b11_rawpost_judge as J                                      # noqa: E402

DEV = J.DEVICE_IP
LAP = "192.168.80.2"


def pkt(t, src, sport, dport, seq=1, ack=1, ln=0, fin=False):
    return {"t": t, "src": src, "sport": sport, "dport": dport, "seq": seq, "ack": ack, "len": ln, "fin": fin}


def main():
    # VCM 0x051 commands every 10 ms at x.000; the device answers 0.5 ms after each.
    cmds = [round(0.01 * k, 4) for k in range(100)]
    ans = lambda upto: [round(c + 0.0005, 4) for c in cmds if c <= upto]
    arr = 0.5002                                       # after the command at 0.500, before its answer
    held_and_wake = ans(0.51)                          # answers to 0.500 (held) and 0.510 (wake)
    one_late = ans(0.52)                               # ... and to 0.520: a second command after arrival
    t_end = 1.0
    # the ACK comes after the second command: strict FAIL, PASS on ACK
    ack_late = 0.5150
    # capture: the POST on port 5000, a page read on port 5001 still open at the arrival
    cap = [pkt(10.000, LAP, 5000, 80, seq=1, ln=120), pkt(10.002, DEV, 80, 5000, ack=121),
           pkt(10.000, LAP, 5000, 80, seq=1, ln=120), pkt(10.030, DEV, 80, 5000, ln=1440),
           pkt(9.990, LAP, 5001, 80, seq=1, ln=127), pkt(10.050, DEV, 80, 5001, ln=900),
           pkt(9.000, LAP, 5002, 80, seq=1, ln=127), pkt(9.010, DEV, 80, 5002, ln=900, fin=True)]
    capr = [pkt(10.000, LAP, 5000, 80, seq=1, ln=100), pkt(10.001, LAP, 5000, 80, seq=101, ln=20),
            pkt(10.002, DEV, 80, 5000, ack=101),       # ACKs only the first segment
            pkt(10.004, LAP, 5000, 80, seq=101, ln=20), pkt(10.006, DEV, 80, 5000, ack=121),
            pkt(10.040, DEV, 80, 5000, ln=1440)]
    checks = [
        ("command count: a held answer is 0, the wake frame's is 1, the next command's 2",
         J.answers_after(arr, cmds, one_late, t_end) == [0, 1, 2]),
        ("command count: a command on the wire at the arrival instant counts as before it (held)",
         J.answers_after(0.51, cmds, ans(0.52), t_end) == [0, 1]),
        ("PASS: answers to the held command and the wake frame only",
         J.verdict(arr, arr + 0.002, cmds, held_and_wake, t_end)[0] == J.STRICT_PASS),
        ("FAIL: an answer to a second command after arrival, with the ACK no help",
         J.verdict(arr, arr + 0.002, cmds, one_late, t_end)[0] == J.STRICT_FAIL),
        ("strict FAIL, PASS on ACK: its own category when only the ACK as arrival passes",
         J.verdict(arr, ack_late, cmds, one_late, t_end)[0] == J.ACK_ONLY),
        ("no ACK: a strict fail stays a fail", J.verdict(arr, None, cmds, one_late, t_end)[0] == J.STRICT_FAIL),
        ("no arrival: undetermined, never a pass", J.verdict(None, None, cmds, held_and_wake, t_end)[0] == J.NO_DATA),
        ("answers after the span's end are not counted",
         J.verdict(arr, None, cmds, one_late, 0.515)[0] == J.STRICT_PASS),
        ("not applicable: no device 0x051 before arrival (PASSIVE) is never a pass",
         J.verdict(arr, None, cmds, [], t_end)[0] == J.NOT_APPLICABLE
         and J.verdict(arr, None, cmds, ans(0.30), t_end)[0] == J.NOT_APPLICABLE),
        ("applicable: an answer within 100 ms before arrival", J.verdict(arr, None, cmds, ans(0.45), t_end)[0] == J.STRICT_PASS),
        ("no device frame at all after arrival passes (silent at once)",
         J.verdict(arr, None, cmds, ans(0.49), t_end)[0] == J.STRICT_PASS),
        ("request time: the laptop's last request segment, first time on the air, and the device's ACK of it",
         J.request_times(cap, 5000) == (10.000, 10.002)),
        ("request time: a retransmitted last segment counts from its first send; the ACK must cover the end",
         J.request_times(capr, 5000) == (10.001, 10.006)),
        ("request time: no request on that port reads None", J.request_times(cap, 6000) == (None, None)),
        ("in progress: a page read still unanswered at the arrival is listed, a finished one is not",
         J.in_progress(cap, 5000, 10.000) == [5001]),
        ("in progress: nothing before the page read's request", J.in_progress(cap, 5000, 9.980) == []),
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
