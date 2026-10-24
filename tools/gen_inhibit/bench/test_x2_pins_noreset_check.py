#!/usr/bin/env python3
"""Tests for x2_pins_noreset_check.py (version 2): its judge, serial parsing and lock
check, on made-up readings. No hardware.

    py -3.14 -B tools/gen_inhibit/bench/test_x2_pins_noreset_check.py

Gen-inhibit tester, 2026-10-19.
"""
import os
import sys
import gi_paths  # noqa: E402  scratch/ folders (harness-defects row, 2026-10-24)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import x2_pins_noreset_check as T   # noqa: E402

fails = []
oks = 0

RND = [0x7F4, 0x7F5, 0x7F6, 0x7F7]
SB = ["diag st=IDLE x=1", "status:", "  t=120s up", "diag st=IDLE x=1"]
SA = ["diag st=IDLE x=1", "  t=125s up"]


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print("ok   %s" % name)
    else:
        fails.append(name)
        print("FAIL %s  -- %s" % (name, detail))


def main():
    for args, want, name in (
            (([], [], RND, SB, SA), "PASS", "quiet, no round after, t= forward, control round seen"),
            (([0x100], [0x100], RND, SB, SA), "PASS", "other ids on the bus are not round frames"),
            (([], [], RND + RND, SB, ["diag st=X", "t=120s"]), "PASS", "the same t= after"),
            (([], [], [0x7F4, 0x7F5, 0x7F6], SB, SA), "VOID", "a control round without 0x7F7"),
            (([], [], [0x7F5, 0x7F6, 0x7F7], SB, SA), "VOID", "a control round without 0x7F4"),
            (([], [], [], SB, SA), "VOID", "no control round (a deaf witness)"),
            (([], [0x7F4], [], SB, SA), "VOID", "a deaf control outranks a round after"),
            (([0x7F4], [], RND, SB, SA), "INCONCLUSIVE", "a round frame in the quiet window"),
            (([0x7F7], [], RND, SB, SA), "INCONCLUSIVE", "0x7F7 in the quiet window"),
            (([], [], RND, ["t=120s"], SA), "INCONCLUSIVE", "no diag line before"),
            (([], [], RND, ["diag st=IDLE"], SA), "INCONCLUSIVE", "no t= before"),
            (([], [], RND, SB + ["rst:0x15 (USB_UART_CHIP_RESET),boot:0x29"], SA), "INCONCLUSIVE",
             "a boot banner before"),
            (([], [], RND, SB, SA + ["rst:0x15 (USB_UART_CHIP_RESET),boot:0x29"]), "FAIL", "an rst: banner after"),
            (([], [], RND, SB, SA + ["Vtrux charge interposer fw 9"]), "FAIL", "the name banner after"),
            (([], [], RND, ["OUGH trip=0 t=9", "diag st=X", "t=120s state=P"],
              ["s state=P", "diag st=X", "t=125s state=P"]), "PASS", "cut lines at the window edges are not values"),
            (([], [], RND, [], SA), "INCONCLUSIVE", "nothing on serial before"),
            (([], [0x7F4], RND, SB, SA), "FAIL", "0x7F4 after the pin read"),
            (([], [0x7F7], RND, SB, SA), "FAIL", "0x7F7 alone after the pin read"),
            (([], RND, RND, SB, SA), "FAIL", "a whole round after the pin read"),
            (([], [], RND, SB, []), "FAIL", "the app not heard after (reset into ROM and held)"),
            (([], [], RND, SB, ["t=125s"]), "FAIL", "no diag line after"),
            (([], [], RND, SB, ["diag st=IDLE"]), "FAIL", "no t= after"),
            (([], [], RND, SB, ["diag st=X", "t=3s"]), "FAIL", "t= back near 0 after"),
            (([], [], RND, SB, ["diag st=X", "t=119s"]), "FAIL", "t= one second lower after"),
            (([], [], RND, SB, ["diag st=X", "t=2s", "t=130s"]), "FAIL", "a low t= after counts even if a later one is not"),
            (([], [], RND, ["diag st=X", "t=100s", "t=120s"], ["diag st=X", "t=110s"]), "FAIL",
             "the last t= before is the reference, not the first"),
    ):
        try:
            v, why = T.judge(*args)
        except Exception as e:      # a crash is not a kill: report it against this case
            v, why = "RAISED", repr(e)
        check("%s -> %s" % (name, want), v == want, (v, why))
    for lines, want, name in (
            (["diag st=IDLE a=1"], (True, [], False), "a diag line"),
            (["xdiag st=IDLE"], (False, [], False), "diag inside a word is not the line"),
            (["t=85s state=PASSTHROUGH vmax=0mV"], (False, [85], False), "a status line"),
            (["  t=42s up 3"], (False, [42], False), "a t= value after spaces"),
            (["at=42s", "t=42"], (False, [], False), "t= inside a word, and t= without s"),
            (["diag st=X boot=12444us"], (True, [], False), "st= and boot= are not t="),
            (["5s state=P", "=85s state=P"], (False, [], False), "a line cut before its t= is no value"),
            (["t=1s", "t=6s t=11s"], (False, [1, 6, 11], False), "every t= in order"),
            (["rst:0x15 (USB_UART_CHIP_RESET),boot:0x29"], (False, [], True), "the rst: banner"),
            (["Vtrux charge interposer fw 9"], (False, [], True), "the name banner"),
            (["first=0x15 (x)", "Vtrux charge"], (False, [], False), "not a banner"),
    ):
        got = T.serial_facts(lines)
        check("serial: %s" % name, tuple(got) == want, got)
    marks = {"quiet": 10, "pins": 13, "after": 20, "serial_after": 23, "control": 30, "control_after": 31, "end": 34}
    seen = [(9.9, 1), (10, 2), (12.9, 3), (13, 4), (16, 5), (20.5, 6), (22.9, 7), (23, 8), (29.9, 9),
            (30, 10), (31.5, 11), (33.9, 12), (34, 13)]
    q, af, c = T.windows(seen, marks)
    check("windows: quiet is [quiet, pins)", q == [2, 3], q)
    check("windows: after is [pins, serial_after), the pin read included", af == [4, 5, 6, 7], af)
    check("windows: control is [control, end)", c == [10, 11, 12], c)
    d = gi_paths.scratch("pins-noreset-test")
    os.makedirs(d, exist_ok=True)
    for text, want, name in (
            ("## Lock\n\n**Holder:** tester (gen-inhibit)\n**Since:** x\n", True, "us"),
            ("## Lock\n\n**Holder:** tester (charge-interposer)\n", False, "the other tester"),
            ("## Lock\n\n**Holder:** -- (free)\n", False, "free"),
            ("## Lock\n\nHolder: tester (gen-inhibit)\n", False, "not the Holder field"),
    ):
        p = os.path.join(d, "bh.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        check("lock: %s -> %s" % (name, want), T.held_by_us(p) is want)
    os.remove(p)
    os.rmdir(d)
    print("\n%s, %d ok, %d FAIL" % ("PASS" if not fails else "FAIL", oks, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
