"""shared/diag: the frame packer's Python twin (SPEC.md D4).

The same packer as diag_pack.c, over the same table: the Python module that
tools/diag_table_gen.py writes beside the C table holds MESSAGES, a tuple of
dicts with the fields of diag_message_t and diag_signal_t. It takes raw
values, the integers on the wire, before any DBC scale and offset.

A value that does not fit its signal is never truncated: its bits stay 0 and
it is reported (D3). Ranges are diag_pack.c's, which takes int64_t: an
unsigned 64-bit signal takes 0 to INT64_MAX, and a value outside int64 does
not fit any signal.

Stdlib only. No non-ASCII output.
"""

MUX_NONE = -1
MUX_SELECTOR = -2
MAX_DLC = 8

OK = 0
MISFIT = 1
BAD_SIGNAL = 2

INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1


def _length_ok(s):
    return 1 <= s["length"] <= 64


def signal_min(s):
    if not _length_ok(s) or not s["is_signed"]:
        return 0
    return -(1 << (s["length"] - 1))


def signal_max(s):
    if not _length_ok(s):
        return 0
    if s["is_signed"]:
        return (1 << (s["length"] - 1)) - 1
    return min((1 << s["length"]) - 1, INT64_MAX)


def _positions(s):
    """The signal's bit positions in the order diag_pack.c writes them:
    Intel from the value's LSB up, Motorola from its MSB down."""
    pos = s["start"]
    out = []
    for _ in range(s["length"]):
        out.append(pos)
        if not s["big_endian"]:
            pos += 1
        elif pos % 8 == 0:
            pos += 15
        else:
            pos -= 1
    return out


def _inside(s, dlc):
    if not _length_ok(s) or dlc > MAX_DLC:
        return False
    return all(p < dlc * 8 for p in _positions(s))


def pack_signal(s, raw, data, dlc):
    """Packs one raw value into data (a bytearray of at least dlc bytes).
    -> OK, MISFIT (nothing written) or BAD_SIGNAL (nothing written)."""
    if not _inside(s, dlc):
        return BAD_SIGNAL
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise TypeError("raw value for %s must be an int, got %r" % (s["name"], raw))
    if raw < signal_min(s) or raw > signal_max(s):
        return MISFIT
    u = raw & ((1 << 64) - 1)
    n = s["length"]
    for i, pos in enumerate(_positions(s)):
        bit = (n - 1 - i) if s["big_endian"] else i
        mask = 1 << (pos % 8)
        if (u >> bit) & 1:
            data[pos // 8] |= mask
        else:
            data[pos // 8] &= ~mask & 0xFF
    return OK


def _message_ok(m):
    """-> (ok, selector index or -1)."""
    sel = -1
    if m["dlc"] > MAX_DLC or len(m["signals"]) > 64:
        return False, -1
    muxed = False
    for i, s in enumerate(m["signals"]):
        if not _inside(s, m["dlc"]):
            return False, -1
        if s["mux"] == MUX_SELECTOR:
            if sel >= 0:
                return False, -1
            sel = i
        elif s["mux"] >= 0:
            muxed = True
        elif s["mux"] != MUX_NONE:
            return False, -1
    return not (muxed and sel < 0), sel


def pack_message(m, raw):
    """Fills a frame from raw, one value per signal in the table's order.
    -> (n, data, misfits): n is the number of signals that did not fit, each
    left 0, with bit i of misfits set for signal i; or -1, with data all 0,
    when the table entry is not packable. data is bytes of length dlc."""
    dlc = min(m["dlc"], MAX_DLC)
    out = bytearray(dlc)
    ok, sel = _message_ok(m)
    if not ok:
        return -1, bytes(out), 0
    if len(raw) != len(m["signals"]):
        raise ValueError("%s has %d signals, got %d raw values"
                         % (m["name"], len(m["signals"]), len(raw)))
    sel_value = 0
    if sel >= 0:
        sel_value = raw[sel]
        if pack_signal(m["signals"][sel], sel_value, out, m["dlc"]) != OK:
            return -1, bytes(dlc), 0
    n = 0
    misfits = 0
    for i, s in enumerate(m["signals"]):
        if i == sel or (s["mux"] >= 0 and s["mux"] != sel_value):
            continue
        if pack_signal(s, raw[i], out, m["dlc"]) == MISFIT:
            n += 1
            misfits |= 1 << i
    return n, bytes(out), misfits


def raw_values(m, **by_name):
    """A raw list for pack_message in the table's order, 0 for any signal not
    named. An unknown name raises."""
    names = [s["name"] for s in m["signals"]]
    unknown = sorted(set(by_name) - set(names))
    if unknown:
        raise KeyError("%s has no signal %s" % (m["name"], ", ".join(unknown)))
    return [by_name.get(n, 0) for n in names]


def message(messages, name):
    """The message dict called name in a generated MESSAGES."""
    for m in messages:
        if m["name"] == name:
            return m
    raise KeyError("no message %s" % name)
