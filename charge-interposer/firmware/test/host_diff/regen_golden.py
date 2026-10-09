"""Regenerate a .golden from its .trace by replaying it through machine.py.

`make_golden.py` builds a trace/golden pair from a capture, and
`make_synthetic.py` builds the synthetic pairs from their generators. Neither
helps when the SPEC changes and the capture-derived goldens have to be
refreshed: the capture may not be on this machine, and re-deriving the trace
risks changing the input as well as the expected output, which would hide a
port bug rather than expose one.

This replays the EXISTING trace -- the input is untouched, byte for byte --
and rewrites only what machine.py now produces from it. The differential then
still means what it is supposed to mean: machine.cpp is compared against
machine.py over identical input.

The trace and golden formats are make_golden.py's, and this file must be kept
in step with the `record` / state-logging in make_synthetic.py's `write()`:

    trace   V|C <t_ms> <ID hex> <ext 0|1> <payload hex>     a frame
            T <t_ms>                                        a tick
    golden  E <t_ms> <side> <ID hex> <ext 0|1> <payload hex>  an emitted frame
            S <t_ms> <STATE>                                  a state change

Usage (from the repo root):
    py -3.11 projects/vtrux/tools/interposer/firmware/test/host_diff/regen_golden.py \\
        projects/vtrux/notes/artifacts/interposer-firmware/evap80.trace ...
    ... --check    report what would change without writing; exit 1 if any
                   golden differs from what machine.py produces now
    ... --reason "why"   REQUIRED to write. Appended, with the traces and
                   the date, to golden_regen_log.txt beside the goldens.

Rev 2 (charge-interposer tester, 2026-10-07). Every golden is machine.py's
output, so regenerating one turns whatever machine.py now does into the
expected behaviour -- including a regression. Rev 1 did that silently and
exited 0 either way, and diff_all.sh never asked whether the goldens were
current, so machine.cpp could match a STALE golden while the two cores
disagreed. Now --check gates (diff_all.sh runs it first), and a rewrite has
to say why, on the record.
"""

import argparse
import os
import sys

sys.path.insert(0, ".")

_HERE = os.path.dirname(os.path.abspath(__file__))
_INTERPOSER = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, _INTERPOSER)

import machine as M          # noqa: E402


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


def replay(trace_path):
    """-> list of golden lines produced by machine.py from this trace."""
    cfg = M.Config()
    apply_k_lines(trace_path, cfg)
    core = M.InterposerCore(cfg, 0)
    golden = []
    last_state = core.state

    def record(out, t_ms):
        for side, oid, oext, odata in out:
            golden.append("E %d %d %X %d %s"
                          % (t_ms, side, oid, 1 if oext else 0, odata.hex()))

    with open(trace_path, "r", encoding="ascii", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if not parts:
                continue
            kind = parts[0]
            if kind == "T":
                t_ms = int(parts[1])
                record(core.tick(t_ms), t_ms)
            elif kind in ("V", "C"):
                t_ms = int(parts[1])
                arb = int(parts[2], 16)
                ext = parts[3] == "1"
                data = bytes.fromhex(parts[4])
                if kind == "C":
                    out = core.on_charger_frame(arb, ext, data, t_ms)
                else:
                    out = core.on_vehicle_frame(arb, ext, data, t_ms)
                record(out, t_ms)
            elif kind == "K":
                pass      # Config overrides, already applied above
            else:
                raise ValueError("unknown trace line: %r" % line)

            if core.state != last_state:
                golden.append("S %d %s" % (t_ms, M.STATE_NAMES[core.state]))
                last_state = core.state
    return golden


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--stdout", action="store_true",
                    help="print one trace's golden instead of writing it. "
                         "For diffing a trace that has no golden on disk -- "
                         "the spec 9.1 L1 scenario recordings, which are "
                         "generated per run and deliberately not kept.")
    ap.add_argument("--check", action="store_true",
                    help="report differences without writing; exit 1 if any")
    ap.add_argument("--reason", default=None,
                    help="why the goldens are being rewritten (required to "
                         "write; logged to golden_regen_log.txt)")
    a = ap.parse_args()
    if not a.stdout and not a.check and not (a.reason or "").strip():
        raise SystemExit("refusing to rewrite goldens without --reason: "
                         "every golden is machine.py's output, so a rewrite "
                         "makes its current behaviour the expected one")

    if a.stdout:
        if len(a.traces) != 1:
            raise SystemExit("--stdout takes exactly one trace")
        sys.stdout.write("\n".join(replay(a.traces[0])) + "\n")
        return 0
    changed = 0
    for tp in a.traces:
        gp = tp[:-6] + ".golden" if tp.endswith(".trace") else tp + ".golden"
        new = replay(tp)
        old = []
        if os.path.exists(gp):
            with open(gp, "r", encoding="ascii", errors="replace") as fh:
                old = fh.read().split("\n")
                if old and old[-1] == "":
                    old.pop()
        name = os.path.basename(tp)[:-6]
        if old == new:
            print("  %-28s unchanged (%d lines)" % (name, len(new)))
            continue
        changed += 1
        print("  %-28s %d -> %d lines" % (name, len(old), len(new)))
        for i in range(min(len(old), len(new))):
            if old[i] != new[i]:
                print("      first diff at line %d" % (i + 1))
                print("        was: %s" % old[i])
                print("        now: %s" % new[i])
                break
        if not a.check:
            with open(gp, "w", encoding="ascii", newline="\n") as fh:
                fh.write("\n".join(new) + "\n")
    print("%d of %d golden files %s"
          % (changed, len(a.traces), "differ" if a.check else "rewritten"))
    if a.check:
        return 1 if changed else 0
    if changed:
        import datetime
        logp = os.path.join(os.path.dirname(os.path.abspath(a.traces[0])),
                            "golden_regen_log.txt")
        with open(logp, "a", encoding="ascii", errors="replace",
                  newline="\n") as fh:
            fh.write("%s  %d rewritten  reason: %s\n    %s\n"
                     % (datetime.datetime.now().isoformat(timespec="seconds"),
                        changed, a.reason.strip(),
                        " ".join(os.path.basename(t) for t in a.traces)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
