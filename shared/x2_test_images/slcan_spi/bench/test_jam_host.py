"""Tests of jam_host.py, the bench harness's side of slcan_spi's jam channel
(x2_test_images SPEC T13), and their can-fail. NOT FOR VEHICLE (SPEC T14).

    python3.13 -B test_jam_host.py            the tests: ALL PASS, exit 0
    python3.13 -B test_jam_host.py --mutants  each mutant of jam_host.py must
                                              fail them; exit 0 only if all do

The expected bytes are written out from the format in ../main/jam_frame.h,
not computed by jam_host. Mutants are made in memory (the source text with
one substitution, run as a module), so nothing is written to disk.
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "jam_host.py")


def run_tests(jh):
    """Returns a list of failure strings for module jh."""
    fails = []

    def check(cond, what):
        if not cond:
            fails.append(what)

    check(jh.CMD_ID == 0x1F4A4D43 and jh.REPLY_ID == 0x1F4A4D52, "identifiers")
    check(jh.train(20, 100, 5000) == bytes.fromhex("4A4D541400641388"), "TRAIN 20 100 5000")
    check(jh.train(50, 20000, 65535) == bytes.fromhex("4A4D54324E20FFFF"), "TRAIN big-endian")
    check(jh.pulses(12, 10, 1000) == bytes.fromhex("4A4D500C000A03E8"), "PULSES 12 10 1000")
    check(jh.stop() == bytes.fromhex("4A4D530000000000"), "STOP")
    check(jh.status() == bytes.fromhex("4A4D3F0000000000"), "STATUS")
    try:
        jh.train(256, 0, 0)
        check(False, "w_us 256 encoded")
    except Exception:
        pass

    def frames(text):
        """The image's frames for one line, written from the format."""
        b = text.encode()
        chunks = [b[i:i + 5] for i in range(0, len(b), 5)] or [b""]
        out = []
        for i, c in enumerate(chunks):
            idx = i | (0x80 if i == len(chunks) - 1 else 0)
            out.append(b"JM" + bytes([idx]) + c)
        return out

    j = jh.Joiner()
    got = [j.feed(f) for f in frames("ACK STOP busy=1")]
    check(got == [None, None, "ACK STOP busy=1"], "joined 15 characters: %r" % got)
    line = "DONE TRAIN end=complete stop_by=deadline_timer pulses=50000 chunks=25 " \
           "elapsed_us=5000123 over_deadline_us=123"
    got = [j.feed(f) for f in frames(line)]
    check(got[-1] == line and all(g is None for g in got[:-1]), "joined a long line")
    check(j.feed(frames("")[0]) == "", "empty line")
    check(j.bad == 0, "bad counted on good frames: %d" % j.bad)

    # broken replies are never read as whole lines
    j = jh.Joiner()
    fs = frames("ERR busy now")
    check(j.feed(fs[0]) is None and j.feed(fs[2]) is None, "skipped frame finished a line")
    check(j.bad == 1, "skipped frame not counted: %d" % j.bad)
    j = jh.Joiner()
    check(j.feed(b"JX" + fs[0][2:]) is None and j.bad == 1, "unsigned frame taken")
    j = jh.Joiner()
    check(j.feed(b"JM\x80" + b"123456") is None and j.bad == 1, "6 characters taken")
    j = jh.Joiner()
    check(j.feed(b"J") is None and j.bad == 1, "short frame taken")
    # a new line (index 0) after a broken one is read
    j = jh.Joiner()
    j.feed(fs[0])
    got = [j.feed(f) for f in frames("STATUS idle uptime_us=5")]
    check(got[-1] == "STATUS idle uptime_us=5" and j.bad == 1, "restart at index 0: %r" % got)

    r = jh.parse_reply(line)
    check(r is not None and r["kind"] == "DONE" and r["sub"] == "TRAIN"
          and r["stop_by"] == "deadline_timer" and r["over_deadline_us"] == "123",
          "parse DONE: %r" % r)
    r = jh.parse_reply("CAPPED ms 60000 -> 30000")
    check(r is not None and r["kind"] == "CAPPED", "parse CAPPED")
    r = jh.parse_reply("ERR bad_jam_frame")
    check(r is not None and r["sub"] == "bad_jam_frame", "parse ERR")
    check(jh.parse_reply("# NOT FOR VEHICLE slcan_spi") is None, "a console line parsed")
    return fails


MUTANTS = [
    ("cmd-id", "CMD_ID = 0x1F4A4D43", "CMD_ID = 0x1F4A4D44"),
    ("little-endian", 'struct.pack(">BHH", a, b, c)', 'struct.pack("<BHH", a, b, c)'),
    ("no-signature", "return SIG + op.encode", "return b'  ' + op.encode"),
    ("sig-unchecked", "if len(data) < 3 or data[:2] != SIG:", "if len(data) < 3:"),
    ("order-unchecked", "if idx != len(self.parts):", "if False:"),
    ("last-ignored", "        if not last:\n            return None\n", ""),
    ("long-frame-taken", "if len(data) > 3 + CHARS:", "if False:"),
    ("parse-any", "if not words or words[0] not in KINDS:", "if not words:"),
]


def load(text):
    m = types.ModuleType("jam_host_under_test")
    exec(compile(text, SRC, "exec"), m.__dict__)
    return m


def main():
    with open(SRC, encoding="utf-8") as f:
        original = f.read()
    fails = run_tests(load(original))
    if "--mutants" not in sys.argv:
        for x in fails:
            print("FAIL", x)
        print("%d FAILED" % len(fails) if fails else "ALL PASS")
        return 1 if fails else 0
    if fails:
        print("ORIGINAL FAILS:", fails)
        return 1
    bad = 0
    for name, old, new in MUTANTS:
        if original.count(old) != 1:
            print("%-18s BROKEN MUTANT (matches %d times)" % (name, original.count(old)))
            bad += 1
            continue
        try:
            f = run_tests(load(original.replace(old, new)))
        except Exception as e:      # a crash is a failure of the tests too
            f = ["raised %r" % e]
        if f:
            print("%-18s killed" % name)
        else:
            print("%-18s SURVIVED" % name)
            bad += 1
    print("%d of %d mutants survived or broken" % (bad, len(MUTANTS)) if bad
          else "all %d mutants killed" % len(MUTANTS))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
