"""Print `machine.py`'s event log over a trace, for `host_runner --events` to
be diffed against, field by field.

Spec 8.3's event line is `[t] STATE event a= b= c=`: a fixed name and three
numbers. Until 2026-10-13 the model wrote its values into the name's prose
while machine.cpp's eventName() returned a fixed string, so the two cores
could not be compared, nothing compared them, and they drifted: the model's
top of charge read vmax, vmin, chg_max in amps against the board's vmax,
chg_max in centiamps, vmin, and boot-mid-session failed on the board at its
first bench run. The frame diff cannot see any of this -- events change no
emitted byte. This prints `X <t_ms> <a> <b> <c> <name>`, exactly
host_runner's --events format, so a field-order or unit drift on either
side is a DIFFERS (reviewer, 2026-10-13).

The log is drained after every input line, as host_runner drains machine.cpp's
every pass, so neither side's event cap is reached on a long trace.

Usage:
    python3 event_stream.py [--core-dir <dir holding machine.py>] <trace>
`diff_all.sh` runs it against `host_runner --events` for every pair.
`--core-dir` exists for the can-fail: a copy of machine.py with one event's
fields swapped must read DIFFERS.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    args = sys.argv[1:]
    core_dir = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
    if len(args) >= 2 and args[0] == "--core-dir":
        core_dir = args[1]
        args = args[2:]
    if len(args) != 1:
        sys.stderr.write("usage: event_stream.py [--core-dir <dir>] "
                         "<trace file>\n")
        return 2
    sys.path.insert(0, core_dir)
    import machine as M          # noqa: E402
    if os.path.dirname(os.path.abspath(M.__file__)) != \
            os.path.abspath(core_dir):
        sys.stderr.write("machine.py came from %s, not %s\n"
                         % (M.__file__, core_dir))
        return 2
    sys.path.insert(0, _HERE)
    from diag_stream import apply_k_lines   # noqa: E402

    cfg = M.Config()
    apply_k_lines(args[0], cfg)
    core = M.InterposerCore(cfg, 0)
    out = []

    def drain():
        for ev in core.events:
            a, b, c = ev.fields()
            out.append("X %d %d %d %d %s" % (ev[0], a, b, c, ev[2]))
        del core.events[:]

    drain()      # anything logged at construction
    with open(args[0], "r") as f:
        for line in f:
            k = line[:1]
            if k == "T":
                core.tick(int(line[1:].strip()))
            elif k in ("V", "C"):
                parts = line[1:].split()
                t_ms = int(parts[0])
                arb = int(parts[1], 16)
                ext = parts[2] != "0"
                data = bytes.fromhex(parts[3])
                if k == "V":
                    core.on_vehicle_frame(arb, ext, data, t_ms)
                else:
                    core.on_charger_frame(arb, ext, data, t_ms)
            else:
                continue
            drain()

    sys.stdout.write("\n".join(out) + ("\n" if out else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
