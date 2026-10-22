#!/usr/bin/env python3
"""Independent cross-check of shared/bench's layout_hash (the charge-interposer tester's
vtrux_bench/decode.py), written from the line format agreed by message on 2026-10-19,
without reading that code.

    py -3.14 -B shared/bench/tests/layout_hash_crosscheck.py DBC [GOLDEN_TXT]

Moved here from reverse-it gen-inhibit artifacts on 2026-10-22, its code
unchanged (source sha256 d86bbd92...); test_decode.py's test_crosscheck runs
it against decode.layout_hash.

Prints this implementation's canonical text hash for DBC. With GOLDEN_TXT, also the hash
of that file's bytes, and the first line where this implementation's text and the file
differ. The agreed format, per message (sorted by frame id), then per signal (sorted by
(start, name)), then per choice (sorted by value):
    M|<frame_id decimal>|<name>|<dlc>|<ext 0/1>
    S|<name>|<start as written>|<length>|<le/be>|<u/s/f>|<repr(float(scale))>|<repr(float(offset))>|<mux>
    C|<value>=<name>
mux: "-", "M" for a multiplexer, or "m<id>[,<id>...]@<multiplexer signal name>".
Lines joined with "\n" plus a final "\n", UTF-8; hash = sha256(text).hexdigest()[:16].
A choice name holding "|", "=", "\n" or "\r" is refused. Gen-inhibit tester.
"""
import hashlib
import sys

import cantools

BAD = ("|", "=", "\n", "\r")


def mux(s):
    if s.is_multiplexer:
        return "M"
    if s.multiplexer_ids:
        return "m%s@%s" % (",".join(str(i) for i in sorted(s.multiplexer_ids)), s.multiplexer_signal)
    return "-"


def canonical(db):
    out = []
    for m in sorted(db.messages, key=lambda m: m.frame_id):
        out.append("M|%d|%s|%d|%d" % (m.frame_id, m.name, m.length, 1 if m.is_extended_frame else 0))
        for s in sorted(m.signals, key=lambda s: (s.start, s.name)):
            kind = "f" if s.is_float else ("s" if s.is_signed else "u")
            out.append("S|%s|%d|%d|%s|%s|%r|%r|%s" % (
                s.name, s.start, s.length, "le" if s.byte_order == "little_endian" else "be", kind,
                float(s.scale), float(s.offset), mux(s)))
            for v, n in sorted((s.choices or {}).items(), key=lambda kv: kv[0]):
                n = str(n)
                if any(b in n for b in BAD):
                    raise ValueError("choice name %r of %s holds a refused character" % (n, s.name))
                out.append("C|%s=%s" % (v, n))
    return "\n".join(out) + "\n"


def h(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def main():
    text = canonical(cantools.database.load_file(sys.argv[1]))
    print("cantools %s; %s: %s (%d lines)" % (cantools.__version__, sys.argv[1], h(text), text.count("\n")))
    if len(sys.argv) > 2:
        gold = open(sys.argv[2], "rb").read()
        print("golden file bytes: %s" % hashlib.sha256(gold).hexdigest()[:16])
        mine, theirs = text.split("\n"), gold.decode("utf-8").split("\n")
        for i, (a, b) in enumerate(zip(mine, theirs), 1):
            if a != b:
                print("first difference, line %d:\n  mine   %r\n  golden %r" % (i, a, b))
                return 1
        if len(mine) != len(theirs):
            print("same lines, different counts: mine %d, golden %d" % (len(mine), len(theirs)))
            return 1
        print("texts identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
