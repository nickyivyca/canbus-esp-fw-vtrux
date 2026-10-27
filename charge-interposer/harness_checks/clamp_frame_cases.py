"""Lists every HOLD page-01 frame that owes a clamp (setpoint above the EVSE
cap) in a trace, with what the 0.1 A law asked for on that frame and whether
a step was due by the 0.3 s cadence. Written 2026-10-12 for the reviewer's
question: does either clamp synthetic have a due step (down, or up) on a
clamp frame?

Usage: py -3.14 clamp_frame_cases.py <trace> [<trace> ...]
Needs REVERSE_IT_DIR; the code is the tree this file is in.
"""
import os
import sys

# charge-interposer/, found from this file (port step 0, 2026-10-26):
# paths.code_dir() refuses an INTERPOSER_CODE naming another tree.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths                                           # noqa: E402
CODE = paths.code_dir()
import test_invariants as TI
from test_invariants import M, P

TOL = TI.PAGE01_COUNT_A / 2.0


def law(bus):
    ib, perm, vm = bus.ibat_a, bus.perm_a, bus.vmax_v
    if ib is None or perm is None or vm is None:
        return "unjudged"
    down = ib > perm + TI.HOLD_EDGE_A or vm >= TI.HOLD_VMAX_DOWN_V - TI.HOLD_EDGE_V
    up = (ib < perm - TI.HOLD_BAND_A - TI.HOLD_EDGE_A
          and vm < TI.HOLD_VMAX_UP_V - TI.HOLD_EDGE_V and not down)
    return "down" if down else ("up" if up else "hold")


for path in sys.argv[1:]:
    cfg, steps = TI._read_trace(path)
    c = TI.Checker(os.path.basename(path), cfg)
    n = 0
    print("==", os.path.basename(path))
    for st in steps:
        prev, step_t = c.last_sp_a, c.hold_step_t
        is_p01 = (st[0] == "V" and st[2] == M.CMD_ID and st[4] is not None
                  and len(st[4]) >= 5 and st[4][0] == 0x01)
        caps = c.bus.caps()
        state = c.core.state
        c.step(*st)
        if not is_p01 or state != M.S_HOLD and c.core.state != M.S_HOLD:
            continue
        caps = c.bus.caps()
        cap = caps[0] if caps else None
        if cap is None or prev is None or prev <= cap + TOL:
            continue
        n += 1
        since = None if step_t is None else st[1] - step_t
        due = since is None or since >= TI.HOLD_GAP_MS
        print("  t=%d prev %.2f cap %.2f sent %.2f | law %s, %s ms since the "
              "last step/release -> step %s"
              % (st[1], prev, cap, c.last_sp_a, law(c.bus), since,
                 "DUE" if due else "not due"))
    print("  clamp frames:", n, "| problems:", len(c.problems))
    for p in c.problems[:5]:
        print("   ", p[:200])
