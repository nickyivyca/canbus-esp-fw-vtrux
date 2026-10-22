"""Harness test for x2_hold: the read-mem parser and the TX pin verdict.

    py -3.14 tools/gen_inhibit/bench/test_x2_hold.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import x2_hold as H                                              # noqa: E402

BASE = {"GPIO_ENABLE": 0, "GPIO_IN": (1 << 7) | (1 << 6), "GPIO7_OUT_SEL": 0x100,
        "IO_MUX_GPIO7": (1 << 9) | (1 << 8) | (1 << 12), "IO_MUX_GPIO6": (1 << 9) | (1 << 12)}


def R(**kw):
    d = dict(BASE)
    d.update(kw)
    return d


def main():
    out = "Connecting....\n0x60004020 = 0x00000080\nStaying in bootloader.\n"
    checks = [
        ("parse: value for the asked address", H.parse_read_mem(out, 0x60004020) == 0x80),
        ("parse: another address is not taken", H.parse_read_mem(out, 0x6000403C) is None),
        ("parse: upper-case hex", H.parse_read_mem("0x6000403C = 0x000000C0", 0x6000403C) == 0xC0),
        ("not driven, reads 1 -> recessive", H.decode(R())["verdict"] == "recessive"),
        ("output enabled on GPIO7 -> driven",
         H.decode(R(GPIO_ENABLE=1 << 7))["verdict"] == "driven"),
        ("output enabled on another GPIO only -> still recessive",
         H.decode(R(GPIO_ENABLE=1 << 8))["verdict"] == "recessive"),
        ("not driven, reads 0 -> dominant", H.decode(R(GPIO_IN=1 << 6))["verdict"] == "dominant"),
        ("bit 6 (RX) is not read as TX", H.decode(R(GPIO_IN=1 << 6))["tx_level"] == 0
         and H.decode(R(GPIO_IN=1 << 7))["rx_level"] == 0),
        ("pull-up bit 8 decoded", H.decode(R())["tx_iomux"]["pullup"] is True
         and H.decode(R(IO_MUX_GPIO7=0))["tx_iomux"]["pullup"] is False),
        ("input disabled (the ROM's state, 2026-10-10 read) -> level unreadable, never dominant",
         (lambda d: d["verdict"] == "not driven, level unreadable" and d["tx_level"] is None
          and d["rx_level"] is None)(H.decode(R(GPIO_IN=0x3c00000d, IO_MUX_GPIO7=0x800,
                                                IO_MUX_GPIO6=0x800)))),
        ("input disabled but output enabled -> driven",
         H.decode(R(GPIO_ENABLE=1 << 7, IO_MUX_GPIO7=0x800))["verdict"] == "driven"),
        ("a register not read -> unknown, never recessive",
         H.decode(R(GPIO_IN=None))["verdict"] == "unknown"),
        ("addresses are the IDF v5.4.1 ones",
         H.REGS == {"GPIO_ENABLE": 0x60004020, "GPIO_IN": 0x6000403C,
                    "GPIO7_OUT_SEL": 0x60004570, "IO_MUX_GPIO7": 0x60009020,
                    "IO_MUX_GPIO6": 0x6000901C} and (H.TX, H.RX) == (7, 6)),
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
