"""Print `machine.py`'s spec 8.2 status frames over a trace, for `host_runner
--diag` to be diffed against.

Review item C1 lists C++ `diagFrames()` as having no test at any level. The
differential compares emitted CAN traffic and state names; the four status
frames are packed by a separate hand-written function in each core and
nothing has ever checked that the two produce the same bytes. The schema-3
change of 2026-10-03 -- the charger-silence bit in `0x7F5` B7 -- was made by
hand in both, which is exactly the kind of edit that agrees in intent and
differs in a bit position.

The I/O-supplied arguments (overflow, transmit failures, uptime, bridge
health, serial, build id) are fixed constants here and in `host_runner`, so
what is compared is the PACKING: what each core puts in those bytes from its
own state, which is the only part either core decides.

Emission points: every state change, and every 1000 ms of trace time. Both
sides use that rule, so the comparison is deterministic from the trace alone.

Usage (from this folder or anywhere):
    python3 diag_stream.py <trace>
`diff_all.sh` runs it against `host_runner --diag` for every pair.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "..")))

import machine as M          # noqa: E402

DIAG_IDS = (M.DIAG_STATUS_ID, M.DIAG_OBSERVED_ID,
            M.DIAG_COUNTERS_ID, M.DIAG_BUILD_ID)


def apply_k_lines(path, cfg):
    """Apply any leading `K <field> <value>` Config overrides from a trace.

    Returns the number applied. An unknown field raises: every reader of
    this format skips lines that are not V/C/T, so a K line honoured on one
    side and ignored on the other is a divergence that looks exactly like a
    port bug.
    """
    n = 0
    with open(path, "r") as f:
        for line in f:
            if not line.startswith("K"):
                break
            parts = line[1:].split()
            if len(parts) != 2:
                raise SystemExit("bad K line in %s: %r" % (path, line))
            name, value = parts
            if name not in M.Config.__slots__:
                raise SystemExit("unknown Config field %r in %s"
                                 % (name, path))
            setattr(cfg, name, int(value))
            n += 1
    return n


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: diag_stream.py <trace file>\n")
        return 2
    cfg = M.Config()
    apply_k_lines(sys.argv[1], cfg)
    core = M.InterposerCore(cfg, 0)
    last_state = core.state
    last_diag = 0
    started = False
    out = []

    with open(sys.argv[1], "r") as f:
        for line in f:
            k = line[:1]
            if k == "T":
                t_ms = int(line[1:].strip())
                core.tick(t_ms)
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

            changed = core.state != last_state
            last_state = core.state
            if changed or not started or t_ms - last_diag >= 1000:
                started = True
                last_diag = t_ms
                frames = dict(core.diag_frames(t_ms, 0, 0, t_ms, True,
                                               False, 0))
                for fid in DIAG_IDS:
                    out.append("D %d %X %s"
                               % (t_ms, fid, frames[fid].hex()))

    sys.stdout.write("\n".join(out) + ("\n" if out else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
