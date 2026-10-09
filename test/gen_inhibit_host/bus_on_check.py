#!/usr/bin/env python3
"""Judge 0x7F1's bus_on bit against the SPEC, not against a golden.

Gen-inhibit tester, 2026-10-09. Spec 11: bus_on is true only when the
controller reports TWAI_STATE_RUNNING, false when it is stopped, bus-off or
recovering. Section 10: the bus_on bit on 0x7F1 (diag_flags, B3 bit1) has the
same meaning. Before this check no golden had the bit clear, so the meaning
was unchecked on the host.

WHAT THE HOST CAN AND CANNOT SHOW. The core takes "is the controller running"
as an input (the "bus" directive's first field; host_runner starts it at 1).
The shim derives it from twai_get_status_info() on the device, and the
status page's bus_on is built by the shim too, so the mapping from
stopped / bus-off / recovering to false, and the page field, are device-only.
What this checks is that the 0x7F1 bit follows that input, armed or live.

THE JUDGEMENT. For every 0x7F1 in the trace, bit1 must equal the controller
input in force at that time. A 0x7F1 less than SLACK_US after a change of the
input is skipped: which read the frame carries is a matter of tick order, and
the spec sets no bound on it. Verdicts:
  PASS          every judged 0x7F1 agrees, and there was at least one judged
                frame while running before the stop, while stopped, and while
                running again after it;
  FAIL          any judged 0x7F1 disagrees;
  INCONCLUSIVE  a phase had no judged frame, so the check could not fail there.

    make && python3 make_scenarios.py && python3 bus_on_check.py
    python3 bus_on_check.py --selftest
Exit status 0 only if every row PASSes.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "host_runner")
SCN = os.path.join(HERE, "scenarios")
ROWS = ("bus-on-stopped-live", "bus-on-stopped-armed")
SLACK_US = 100000           # test tolerance, not spec
BUS_ON = 0x02               # diag_flags bit1 (schema doc, 0x7F1 B3)

TX_RE = re.compile(r"^(\d+) TX DIAG id=7F1 dlc=8 data=([0-9A-Fa-f]{16})")


def controller_input(scn_lines):
    """[(t, running)] from the .scn; host_runner starts with the controller running."""
    out = [(0, True)]
    for ln in scn_lines:
        f = ln.split()
        if len(f) >= 2 and f[0] == "bus":
            out.append((int(f[1]), f[2] != "0"))
    out.sort(key=lambda x: x[0])
    return out


def running_at(inp, t):
    r = inp[0][1]
    for ti, v in inp:
        if ti <= t:
            r = v
    return r


def status_frames(trace_lines):
    """[(t, flags byte)] for every 0x7F1 in the trace."""
    out = []
    for ln in trace_lines:
        m = TX_RE.match(ln)
        if m:
            out.append((int(m.group(1)), int(m.group(2)[6:8], 16)))
    return out


def judge(scn_lines, trace_lines):
    """(verdict, detail)."""
    inp = controller_input(scn_lines)
    changes = [t for (t, v), (_, pv) in zip(inp[1:], inp) if v != pv]
    stops = [t for t, v in inp[1:] if not v]
    if not stops:
        return "INCONCLUSIVE", "the scenario never stops the controller"
    t_stop = min(stops)
    t_back = next((t for t, v in inp if v and t > t_stop), None)
    phases = {"running before": 0, "stopped": 0, "running after": 0}
    bad = []
    for t, flags in status_frames(trace_lines):
        if any(0 <= t - c < SLACK_US for c in changes):
            continue
        want = running_at(inp, t)
        got = bool(flags & BUS_ON)
        if got != want:
            bad.append("t=%.3f s bit1=%d, controller %s"
                       % (t / 1e6, got, "running" if want else "not running"))
        if t < t_stop:
            phases["running before"] += 1
        elif not want:
            phases["stopped"] += 1
        elif t_back is not None and t >= t_back:
            phases["running after"] += 1
    if bad:
        return "FAIL", "; ".join(bad[:4]) + (" (+%d more)" % (len(bad) - 4) if len(bad) > 4 else "")
    empty = [k for k, n in phases.items() if n == 0 and (k != "running after" or t_back)]
    if empty:
        return "INCONCLUSIVE", "no judged 0x7F1 while %s" % ", ".join(empty)
    return "PASS", "judged 0x7F1: %s" % ", ".join("%s %d" % kv for kv in phases.items())


# ------------------------------------------------------------------ selftest --
def _fake(stop, back, end, period, bit_of):
    """A .scn and a trace with a 0x7F1 every `period` us; bit_of(t, running)."""
    scn = ["mode 0 3 500", "bus %d 0 1 1 0" % stop, "bus %d 1 1 1 0" % back, "end %d" % end]
    inp = controller_input(scn)
    tr = []
    for t in range(0, end, period):
        b = 0x04 | (BUS_ON if bit_of(t, running_at(inp, t)) else 0)
        tr.append("%d TX DIAG id=7F1 dlc=8 data=060103%02X00028813 ok=1" % (t, b))
    return scn, tr


def selftest():
    S = 1000000
    cases = [
        ("bit follows the controller", lambda t, r: r, 1200000, "PASS"),
        ("bit stuck set (the old 'driver installed' meaning)", lambda t, r: True, 1200000, "FAIL"),
        ("bit stuck clear", lambda t, r: False, 1200000, "FAIL"),
        ("bit inverted", lambda t, r: not r, 1200000, "FAIL"),
        ("bit late by one frame after the stop",
         lambda t, r: r or 5 * S <= t < 6500000, 1200000, "FAIL"),
        ("no 0x7F1 while stopped", lambda t, r: r, 4000000, "INCONCLUSIVE"),
    ]
    fails = 0
    for label, fn, period, want in cases:
        scn, tr = _fake(5 * S, 8 * S, 11 * S, period, fn)
        got, detail = judge(scn, tr)
        ok = got == want
        fails += not ok
        print("  %-4s %-52s want %-12s got %-12s %s" % ("ok" if ok else "FAIL", label, want, got,
                                                       "" if ok else detail))
    # A frame inside the slack after a change is not judged either way.
    scn, tr = _fake(5 * S, 8 * S, 11 * S, 1200000, lambda t, r: r)
    tr.append("%d TX DIAG id=7F1 dlc=8 data=0601030600028813 ok=1" % (5 * S + 50000))
    got, _ = judge(scn, sorted(tr, key=lambda x: int(x.split()[0])))
    ok = got == "PASS"
    fails += not ok
    print("  %-4s %-52s want %-12s got %s" % ("ok" if ok else "FAIL",
                                             "stale bit 50 ms after the stop is skipped",
                                             "PASS", got))
    print("selftest: %s" % ("PASS" if not fails else "FAIL (%d)" % fails))
    return 1 if fails else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    worst = 0
    for name in ROWS:
        path = os.path.join(SCN, name + ".scn")
        if not os.path.exists(path):
            print("[ERROR       ] %-24s no scenario; run make_scenarios.py" % name)
            worst = 1
            continue
        with open(path) as fh:
            scn = [ln for ln in fh.read().splitlines() if ln and not ln.startswith("#")]
        with open(path) as fh:
            r = subprocess.run([RUNNER], stdin=fh, capture_output=True, timeout=300)
        if r.returncode != 0:
            print("[ERROR       ] %-24s host_runner rc %d" % (name, r.returncode))
            worst = 1
            continue
        verdict, detail = judge(scn, r.stdout.decode("ascii", "replace").splitlines())
        print("[%-12s] %-24s %s" % (verdict, name, detail))
        if verdict != "PASS":
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main())
