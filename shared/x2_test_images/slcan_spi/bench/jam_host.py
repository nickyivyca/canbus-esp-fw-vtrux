"""The host side of slcan_spi's jam channel (x2_test_images SPEC T13; the
format is ../main/jam_frame.h and ../README.md "Jamming"). NOT FOR VEHICLE
(SPEC T14).

Written from the format, not from the image's code: commands are encoded
here and replies joined here independently of jam_frame.c, so a bench
check through them tests the image's side.

  command(kind, a=0, b=0, c=0) -> 8 data bytes for CMD_ID
  train(w_us, p_us, ms), pulses(w_us, n, interval_ms), stop(), status()
  Joiner                        feeds reply-ID frames, gives whole lines
  parse_reply(line)             "DONE TRAIN end=... a=b" -> dict

Its tests are test_jam_host.py, with their own mutants.
"""

import re
import struct

CMD_ID = 0x1F4A4D43
REPLY_ID = 0x1F4A4D52
SIG = b"JM"
LAST = 0x80
CHARS = 5
KINDS = ("ACK", "DONE", "PULSE", "ERR", "CAPPED", "STATUS")
KV_RE = re.compile(r"(\w+)=(\S+)")


def command(op, a=0, b=0, c=0):
    """The 8 data bytes of a command frame: J M op a(u8) b(u16) c(u16),
    big-endian. Out-of-range fields raise; the image's limits are for
    jam_pulse to apply, not this encoder."""
    if len(op) != 1:
        raise ValueError("op is one character")
    return SIG + op.encode("ascii") + struct.pack(">BHH", a, b, c)


def train(w_us, p_us, ms):
    return command("T", w_us, p_us, ms)


def pulses(w_us, n, interval_ms):
    return command("P", w_us, n, interval_ms)


def stop():
    return command("S")


def status():
    return command("?")


class Joiner:
    """Joins reply frames back into lines. feed(data) takes one frame's data
    bytes (frames on REPLY_ID only) and returns the finished line or None.
    A frame without the signature, out of order or too short is counted in
    .bad and drops the line in progress, so a broken reply is never read as
    a whole one."""

    def __init__(self):
        self.parts = []
        self.bad = 0
        self.lines = []

    def _drop(self):
        self.bad += 1
        self.parts = []

    def feed(self, data):
        data = bytes(data)
        if len(data) < 3 or data[:2] != SIG:
            self._drop()
            return None
        idx, last = data[2] & 0x7F, bool(data[2] & LAST)
        if idx != len(self.parts):
            self._drop()
            if idx != 0:
                return None
        if len(data) > 3 + CHARS:
            self._drop()
            return None
        self.parts.append(data[3:])
        if not last:
            return None
        line = b"".join(self.parts).decode("ascii", "replace")
        self.parts = []
        self.lines.append(line)
        return line


def parse_reply(line):
    """jam_pulse's reply line -> dict: kind, sub (TRAIN, PULSES, STOP, ...,
    or the ERR reason), and its key=value fields. None if not a reply."""
    words = line.split()
    if not words or words[0] not in KINDS:
        return None
    out = {"kind": words[0], "sub": words[1] if len(words) > 1 and "=" not in words[1] else ""}
    for k, v in KV_RE.findall(line):
        out[k] = v
    return out
