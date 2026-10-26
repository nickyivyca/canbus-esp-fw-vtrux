"""Harness test for restore_unarmed_check.judge.

    py -3.14 gen-inhibit/tools/bench/test_restore_unarmed_check.py   (from the repo root)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import restore_unarmed_check as R                                  # noqa: E402


def cmd(c):
    return bytes([8, 0, 0x80, 0xFF, 0x7F, c & 0x0F])


SENT = [(k * 0.01, 0x051, cmd(k)) for k in range(100)] + [(k * 0.01 + 0.003, 0x617, bytes(8)) for k in range(100)]
SHA = "dace9470" + "1f" * 28
OK_PAGE = {"mode": 0, "drv": None, "elf_sha256": SHA}   # /gen_inhibit's; never /check_status (2026-10-25)


def main():
    J = lambda seen, b=OK_PAGE, a=OK_PAGE, e="dace9470": R.judge(SENT, seen, b, a, e)[0]   # noqa: E731
    seen = [(t + 0.0002, i, d) for t, i, d in SENT]
    # an answer: the device steals the next counter, right after the VCM's frame
    answered = sorted(seen + [(0.5003, 0x051, cmd(51))], key=lambda f: f[0])
    checks = [
        ("silent, all seen -> PASS", J(seen) == "PASS"),
        ("one answer (next counter, early) -> FAIL", J(answered) == "FAIL"),
        ("an answer in multiset terms too: a payload seen more often than sent -> FAIL",
         J(seen + [(0.9, 0x051, cmd(3))]) == "FAIL"),
        ("a WiCAN diag page on the wire -> FAIL", J(seen + [(0.5, 0x7F1, bytes(8))]) == "FAIL"),
        ("the probe id on the wire -> FAIL", J(seen + [(0.5, 0x7F0, bytes(8))]) == "FAIL"),
        ("mode 3 after -> FAIL", J(seen, a=dict(OK_PAGE, mode=3)) == "FAIL"),
        ("driver installed before -> FAIL", J(seen, b=dict(OK_PAGE, drv={"state": "running"})) == "FAIL"),
        ("the wrong build -> FAIL", J(seen, a=dict(OK_PAGE, elf_sha256="dace9471" + SHA[8:])) == "FAIL"),
        ("the whole elf_sha256 expected -> PASS", J(seen, e=SHA) == "PASS"),
        ("an expectation of 7 hex digits -> FAIL (too short to name an image)", J(seen, e="dace947") == "FAIL"),
        ("a page with no elf_sha256 -> FAIL", J(seen, b={"mode": 0, "drv": None}) == "FAIL"),
        ("a git_version where elf_sha256 belongs is not read -> FAIL",
         J(seen, a={"mode": 0, "drv": None, "git_version": "dace9470"}) == "FAIL"),
        ("page unread -> UNREAD", J(seen, b=None) == "UNREAD"),
        ("a deaf witness -> VOID, never PASS", J([]) == "VOID"),
        ("witness missing 6 percent -> VOID", J([f for k, f in enumerate(seen) if not (f[1] == 0x051 and k % 16 == 0)])
         == "VOID"),
        ("witness missing 4 percent -> PASS", J([f for k, f in enumerate(seen) if not (f[1] == 0x051 and k % 25 == 0)])
         == "PASS"),
        ("a witness that missed frames (every other one) is not read as an answer",
         J([f for k, f in enumerate(seen) if k % 2 == 0]) == "VOID"),
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
